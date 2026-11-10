# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The `jsonl-trace` example observer: every run event as one line of JSON.

A minimal tracing backend, to copy when writing one for another system (Langfuse,
OpenTelemetry, a log pipeline). It defines the six required observer methods and none of the
optional hooks, and it writes to `<run>/.trace/events.jsonl`, or to `$METRICS_AGENT_TRACE_FILE`
when that is set. List it in `run.yml` to use it:

    observers: [jsonl-trace]

Paths, never contents: an artifact event records where the artifact is, not what is in it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
from contextlib import contextmanager
from typing import Any, Iterator

ENV_FILE = "METRICS_AGENT_TRACE_FILE"


def _stage_of(extra: dict) -> str | None:
    """The stage an event belongs to. The harness names it `stage_name` on a gate and
    `stage` on a decision; reading both keeps every event attributed whichever it sends."""
    return extra.get("stage_name") or extra.get("stage")


class JsonlTrace:
    name = "jsonl-trace"
    kind = "observer"
    requires_env: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.path: pathlib.Path | None = None
        self.run_id = ""

    def _write(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        row = {"at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "run_id": self.run_id,
               "event": event, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def start_run(self, run_dir: pathlib.Path, run_id: str, run_inputs: dict,
                  policy: Any) -> None:
        self.run_id = run_id
        self.path = pathlib.Path(os.environ.get(ENV_FILE) or
                                 pathlib.Path(run_dir) / ".trace" / "events.jsonl")

    @contextmanager
    def stage_span(self, stage_name: str, script: str, entry: Any,
                   run_id: str) -> Iterator[None]:
        self._write("stage_start", stage=stage_name)
        try:
            yield None
        except BaseException as exc:
            self._write("stage_end", stage=stage_name, error=type(exc).__name__)
            raise
        self._write("stage_end", stage=stage_name)

    def artifact(self, name: str, path: pathlib.Path, data: Any, stage_name: str = "") -> None:
        self._write("artifact", stage=stage_name, name=name, path=str(path))

    def gate(self, kind: str, question: str, answer: Any, **extra: Any) -> None:
        self._write("gate", kind=kind, question=question, answer=answer,
                    stage=_stage_of(extra))

    def decision(self, kind: str, subject: str, verdict: str, **extra: Any) -> None:
        self._write("decision", kind=kind, subject=subject, verdict=verdict,
                    because=extra.get("because"), stage=_stage_of(extra))

    def finish_run(self, verdict: str = "completed", **extra: Any) -> None:
        self._write("stage_verdict", verdict=verdict)
