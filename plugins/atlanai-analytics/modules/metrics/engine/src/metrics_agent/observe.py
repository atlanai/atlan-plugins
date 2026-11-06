# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Observers: something that watches a run (tracing), always optional.

The stage harness tells the active observer about the run, each stage, each artifact, each
decision and each gate. The default observer does nothing, so a run with none installed or
selected runs untraced and is otherwise identical. What the run must record whatever happens
(its identity, the answers people gave at its gates) is in `lib/run_record.py`, not here.

An observer plug is any installed plug of kind `observer` (the contract is
`sockets.Observer`); the core names no tracing backend. A plug is selected when
`run_inputs.plugs.observers` lists it, or when its own `wanted()` says its environment is
configured, so an operator's existing setup keeps working. Every selected observer hears
every event: a run can trace to two backends at once.

An observer never stops a run — unless the operator made tracing REQUIRED
(`METRICS_AGENT_TRACE_REQUIRED=1`, and tracing not turned off with `METRICS_AGENT_TRACE_OFF=1`). Then a failure
of any selected observer, an observer that is listed but cannot load, or no observer at all,
raises `TracingRequired`: the stage stops with exit `TRACING_STOPPED`, and the operator
decides — fix tracing and re-run (a `metrics-agent run` resumes from that stage), or unset
`METRICS_AGENT_TRACE_REQUIRED` (or set it to 0) to continue untraced, which the manifest records as
their decision. Tracing is never skipped silently while it is required.

When tracing is not required, what an observer raises is said once, naming the plug, and the
run and the other observers go on.
"""
from __future__ import annotations

import os
import sys
import pathlib
from contextlib import contextmanager
from typing import Any, Iterator

from metrics_agent.sockets import OBSERVER_OPTIONAL

REQUIRED_ENV = "METRICS_AGENT_TRACE_REQUIRED"
OFF_ENV = "METRICS_AGENT_TRACE_OFF"
# A stage stopped because tracing is required and failed. Not a failed stage and not a
# refusal: `metrics-agent run` turns it into a pause (exit 3), like a human gate.
TRACING_STOPPED = 4
# The manifest note on the stage tracing stopped; what a re-run without the requirement reads
# to know it is the operator's decision to go on untraced.
STOPPED_NOTE = "tracing_required_failed"


class TracingRequired(RuntimeError):
    """Tracing is required for this run and it failed. Never swallowed by the fan-out."""


def required() -> bool:
    """Tracing is required: the operator set `METRICS_AGENT_TRACE_REQUIRED=1` and did not turn tracing
    off. `METRICS_AGENT_TRACE_OFF=1` is the explicit opt-out and wins."""
    return os.environ.get(REQUIRED_ENV) == "1" and os.environ.get(OFF_ENV) != "1"


def stop_message(detail: str) -> str:
    """What the operator reads when required tracing failed: the failure, and the two ways on."""
    return (f"STOPPED — tracing is required ({REQUIRED_ENV}=1) and it failed: {detail}\n"
            "  Choose one, then re-run the same command:\n"
            "    1. fix tracing (the failure above) and re-run: a `metrics-agent run` resumes "
            "from this stage;\n"
            f"    2. unset {REQUIRED_ENV} (or set it to 0) to continue untraced. That is your "
            "decision, and the run's manifest records it.")


def _not_traced(msg: str) -> None:
    """An observer that cannot be used: a stop when tracing is required, a warning otherwise."""
    if required():
        raise TracingRequired(msg)
    _warn(f"{msg}; the run goes on without it")

class NullObserver:
    name = "none"
    kind = "observer"
    requires_env: tuple[str, ...] = ()

    def start_run(self, run_dir: pathlib.Path, run_id: str, run_inputs: dict, policy: Any) -> None:
        pass

    @contextmanager
    def stage_span(self, stage_name: str, script: str, entry: Any, run_id: str) -> Iterator[Any]:
        yield None

    def human_args(self, args: Any, stage_name: str = "") -> None:
        pass

    def ensure_experiment(self, run_dir: pathlib.Path, policy: Any) -> None:
        pass

    def finish_run(self, verdict: str = "completed", **extra: Any) -> None:
        pass

    def close_pending(self) -> None:
        pass

    def update(self, span: Any, **metadata: Any) -> None:
        pass

    def remember_said(self, stage_name: str, said: list) -> None:
        pass

    def gate(self, kind: str, question: str, answer: Any, **extra: Any) -> None:
        pass

    def decision(self, kind: str, subject: str, verdict: str, **extra: Any) -> None:
        pass

    def artifact(self, name: str, path: pathlib.Path, data: Any, stage_name: str = "") -> None:
        pass


def _warn(msg: str) -> None:
    print(f"tracing: {msg}", file=sys.stderr)


class _Spans(dict):
    """The span each observer opened for one stage, by observer position."""


class Fanout:
    """The selected observers as one: each event goes to every one of them, an optional hook
    only to those that have it, and a failure in one is said and contained."""

    kind = "observer"
    requires_env: tuple[str, ...] = ()

    def __init__(self, observers: list[Any]) -> None:
        self.observers = list(observers)
        self.name = "+".join(o.name for o in self.observers)
        self._failed: set[tuple[str, str]] = set()

    def _fail(self, obs: Any, method: str, exc: BaseException) -> None:
        if required():
            raise TracingRequired(f"observer {obs.name!r} failed in {method} "
                                  f"({type(exc).__name__}: {exc})") from exc
        key = (obs.name, method)
        if key not in self._failed:
            self._failed.add(key)
            _warn(f"observer {obs.name!r} failed in {method} "
                  f"({type(exc).__name__}: {exc}); the run goes on")

    def _each(self, method: str, *args: Any, **kwargs: Any) -> None:
        for obs in self.observers:
            fn = getattr(obs, method, None)
            if fn is None and method in OBSERVER_OPTIONAL:
                continue
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - only required tracing stops a run
                self._fail(obs, method, exc)

    def start_run(self, run_dir, run_id, run_inputs, policy) -> None:
        self._each("start_run", run_dir, run_id, run_inputs, policy)

    @contextmanager
    def stage_span(self, stage_name, script, entry, run_id) -> Iterator[_Spans]:
        spans, entered = _Spans(), []
        for i, obs in enumerate(self.observers):
            try:
                cm = obs.stage_span(stage_name, script, entry, run_id)
                spans[i] = cm.__enter__()
                entered.append((obs, cm))
            except Exception as exc:  # noqa: BLE001
                self._fail(obs, "stage_span", exc)
        exc_info: tuple = (None, None, None)
        try:
            yield spans
        except BaseException:
            exc_info = sys.exc_info()
            raise
        finally:
            for obs, cm in reversed(entered):
                try:
                    cm.__exit__(*exc_info)
                except Exception as exc:  # noqa: BLE001
                    if exc is not exc_info[1]:
                        self._fail(obs, "stage_span", exc)

    def update(self, span: Any, **metadata: Any) -> None:
        for i, obs in enumerate(self.observers):
            fn = getattr(obs, "update", None)
            if fn is None:
                continue
            try:
                fn(span.get(i) if isinstance(span, _Spans) else span, **metadata)
            except Exception as exc:  # noqa: BLE001
                self._fail(obs, "update", exc)

    def human_args(self, args, stage_name: str = "") -> None:
        self._each("human_args", args, stage_name)

    def ensure_experiment(self, run_dir, policy) -> None:
        self._each("ensure_experiment", run_dir, policy)

    def finish_run(self, verdict: str = "completed", **extra: Any) -> None:
        self._each("finish_run", verdict=verdict, **extra)

    def close_pending(self) -> None:
        self._each("close_pending")

    def remember_said(self, stage_name: str, said: list) -> None:
        self._each("remember_said", stage_name, said)

    def gate(self, kind, question, answer, **extra: Any) -> None:
        self._each("gate", kind, question, answer, **extra)

    def decision(self, kind, subject, verdict, **extra: Any) -> None:
        self._each("decision", kind, subject, verdict, **extra)

    def artifact(self, name, path, data, stage_name: str = "") -> None:
        self._each("artifact", name, path, data, stage_name=stage_name)


_active: Any = None

def get(run_inputs: dict | None = None) -> Any:
    """The observer for this process: chosen once, on first use."""
    global _active
    if _active is not None:
        return _active
    _active = _choose(run_inputs or {})
    return _active

def _choose(ri: dict) -> Any:
    named = [e["name"] for e in ((ri.get("plugs") or {}).get("observers") or [])]
    from metrics_agent import plugs as _plugs  # noqa: PLC0415

    try:
        installed = _plugs.installed()
    except Exception as exc:  # noqa: BLE001 - an observer never stops a run
        # A plug registered twice is a PlugError; broken package metadata can raise anything.
        # Either way no observer can be trusted, and that is no reason to stop the run.
        detail = str(exc) if isinstance(exc, _plugs.PlugError) else \
            f"the installed plugs could not be listed ({type(exc).__name__}: {exc})"
        if required():
            raise TracingRequired(detail) from exc
        _warn(f"{detail}; the run is untraced")
        return NullObserver()
    for name in named:
        if name not in installed:
            _not_traced(f"observer {name!r} is listed but not installed")
    chosen = []
    for name, item in sorted(installed.items()):
        try:
            cls = item.load()
        except _plugs.PlugError as exc:
            if name in named:
                _not_traced(str(exc))
            continue
        if cls.kind != "observer":
            if name in named:
                _not_traced(f"{name!r} is listed as an observer but is an {cls.kind} plug")
            continue
        try:
            if name in named or (callable(getattr(cls, "wanted", None)) and cls.wanted()):
                chosen.append(cls())
        except Exception as exc:  # noqa: BLE001 - only required tracing stops a run
            _not_traced(f"observer {name!r} could not start ({type(exc).__name__}: {exc})")
    if not chosen and required():
        raise TracingRequired("no tracing observer is installed and selected (list one "
                              "under `observers` in run.yml, or install a tracing plug)")
    return Fanout(chosen) if chosen else NullObserver()

def reset() -> None:
    """Forget the chosen observer (tests)."""
    global _active
    _active = None
