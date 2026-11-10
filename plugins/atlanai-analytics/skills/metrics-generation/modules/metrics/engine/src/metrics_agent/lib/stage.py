# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The shared stage harness.

ONE CLI convention, no exceptions: every stage takes `--run <dir>` and `--policy <path>`,
reads its named inputs from the run directory, writes EXACTLY ONE artifact, and appends
EXACTLY ONE manifest entry. A stage that writes two artifacts is two stages.

Putting it here rather than in each script is what keeps that convention true: a stage cannot
forget to record itself, and a stage that raises still lands a `blocked` entry — a gate that
stopped the run is the most important thing the manifest can say.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from metrics_agent import observe as _observe

from . import artifacts, manifest, pii as _pii, policy as _policy, redact as _redact
from . import run_record as _run_record


# THE ANSWERS WERE REFUSED, NOT THE RUN. A stage that refuses the authored answers it was
# given (an ingest's `--from`), or, at d4b, the cards they produced, exits with this code: the
# fix is a corrected answer, so `metrics-agent run` pauses at the gate instead of closing the
# run, and its trace, as failed. A crash still exits 1. (3 is a pause, 4 a stopped trace.)
ANSWER_REFUSED = 5


class AnswerRefused(SystemExit):
    """A refusal whose fix is a corrected answer, raised where the stage is not an ingest."""


def _ingesting(args) -> bool:
    return any(getattr(args, d, None) for d in ("answers", "from_file"))


def _sha_file(path: pathlib.Path) -> str | None:
    import hashlib
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except Exception:
        return None


def parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--run", required=True, help="the run directory")
    p.add_argument("--policy", default=None,
                   help="path to agent_policy.yml; else $METRICS_AGENT_POLICY, else the one the "
                        "package ships. A missing policy RAISES.")
    p.add_argument("--as", dest="stage_label", default=None, metavar="LABEL",
                   help="the name this invocation is recorded under in the manifest. A "
                        "script run twice in one pipeline (m1_merge before and after the "
                        "describe gate; a gate's emit and its ingest) is two stages, and "
                        "under one name the second entry replaced the first.")
    return p


class Ctx:
    """What a stage body is handed."""

    def __init__(self, run: pathlib.Path, pol: _policy.Policy, entry: manifest.Entry,
                 args: argparse.Namespace, span=None, stage_name: str = "") -> None:
        self.run = run
        self.policy = pol
        self.entry = entry
        self.args = args
        self._span = span
        self._said: list[str] = []
        self.entry_stage = stage_name

    # -- artifact IO that records itself in the manifest -------------------
    def read(self, name: str, schema: str | None = None) -> Any:
        data = artifacts.read(self.run, name, schema)
        rel = str(self.run / name)
        if rel not in self.entry.inputs:
            self.entry.inputs.append(rel)
        return data

    def write(self, name: str, data: Any, schema: str) -> pathlib.Path:
        path = artifacts.write(self.run, name, data, schema)
        self.entry.outputs.append(str(path))
        # EVERY ARTIFACT REPORTS ITSELF, with no stage having to remember. A stage writes
        # exactly one artifact and that artifact is its decision, so tracing every write is
        # what makes the trace complete by construction rather than by diligence. An observer
        # sends what it sees somewhere else, so it never sees a user name: a history
        # artifact's name lists reach it as counts (lib/pii).
        _observe.get().artifact(name, path, _pii.without_user_names(data),
                                stage_name=self.entry_stage)
        return path

    def note_input_path(self, path: str | pathlib.Path) -> None:
        """Record an EXTERNAL input by path. Rule 7: paths, never contents."""
        self.entry.inputs.append(str(path))

    def authored(self, path: str | pathlib.Path, *, kind: str = "authored_answer") -> None:
        """Record an answer a PERSON wrote, with its content.

        Rule 7 keeps CONTENTS out of the manifest and it is right to: a query log is the
        customer's data and a path is enough. An authored answer is the opposite case. It is
        the one artifact in the run that a human produced rather than the pipeline, it is
        small, and it is the whole reason a later reader can ask "who decided this metric is
        called that?" — a path to a file on somebody's laptop answers nothing once the laptop
        is gone.

        So the path goes to the manifest, as always, and the CONTENT goes to the trace, where
        the rest of the run's evidence already lives.
        """
        p = pathlib.Path(path).expanduser()
        self.note_input_path(p)
        try:
            data = json.loads(p.read_text()) if p.suffix == ".json" else p.read_text()
        except Exception as exc:
            data = f"<unreadable: {type(exc).__name__}>"
        # WHO ANSWERED, WITH WHAT, TO WHICH QUESTION. The content alone cannot tie a card to
        # the answer that authored it: the answer's sha can, the bundle's sha says which
        # question it answered, and the model and author say who wrote it.
        model = getattr(self.args, "model", None) or self.entry.model
        if model and not self.entry.model:
            self.entry.model = model
        self.gate(kind, f"answer ingested from {p.name}", "accepted",
                  decided_by=getattr(self.args, "author", None) or "",
                  source_path=str(p), content=data,
                  bytes=(p.stat().st_size if p.is_file() else 0),
                  answer_sha256=_sha_file(p), prompt_sha256=self._prompt_sha(),
                  model=model or "unrecorded")

    def _prompt_sha(self) -> str | None:
        """The sha of the bundle this stage emitted — `<lane>_prompt.json`, e.g. x1_prompt.json
        for x1_propose — so an answer can be matched to the question it answered."""
        lane = pathlib.Path(self.entry_stage or "").name.split("_")[0]
        return _sha_file(self.run / f"{lane}_prompt.json") if lane else None

    def count(self, **kw: Any) -> None:
        self.entry.counts.update(kw)

    def say(self, msg: str) -> None:
        """Stdout is part of the contract: every stage that degrades says so out loud.

        It goes to the trace as well. What a stage says out loud is usually the one sentence
        explaining a number that surprises someone later — "115 refused as label
        pass-throughs", "the corpus dropped 40% as automation" — and an operator reading the
        registry months afterwards does not have the terminal it was printed to.
        """
        # A message can quote what a driver was given (a DSN, a URL with a password in it);
        # no credential value in the environment is ever printed or traced (guardrail 19).
        msg = _redact.text(msg)
        print(msg, file=sys.stderr)
        self._said.append(msg)
        _observe.get().update(self._span, said=self._said)
        # Also kept per stage, so the session turn can quote it: the span holds it for a
        # trace reader, the turn holds it for the Sessions panel.
        _observe.get().remember_said(self.entry_stage, self._said)

    def gate(self, kind: str, question: str, answer, *, options=None,
             decided_by: str = "", **extra) -> None:
        """Record that the run waited for a person here, and what they answered.

        Used by the stages that stop: an interactive confirmation, an authoring bundle, a
        publish. The answer is recorded even when it is no — a declined action exits the
        stage, and that is exactly the moment a trace must not go blank.
        """
        _run_record.record_handoff(self.run, kind, question, answer, self.entry_stage,
                                   decided_by, extra)
        _observe.get().gate(kind, question, answer, stage_name=self.entry_stage,
                            options=options, decided_by=decided_by, **extra)

    def decide(self, kind: str, subject: str, verdict: str, *, because: str = "",
               policy_key: str = "", policy_value=None, evidence=None, **extra) -> None:
        """Record one admission, refusal, promotion or choice — with the rule that decided it.

        A stage's `counts` say 115 were refused; they cannot say which, or why, or against
        what threshold. This is where that goes, and it is the difference between a layer
        somebody can audit and a layer they have to take on trust.
        """
        _observe.get().decision(kind, subject, verdict, because=because,
                                policy_key=policy_key, policy_value=policy_value,
                                evidence=evidence, stage=self.entry_stage, **extra)


@contextmanager
def run_stage(stage_name: str, script: str, args: argparse.Namespace) -> Iterator[Ctx]:
    stage_name = getattr(args, "stage_label", None) or stage_name
    run = pathlib.Path(args.run).expanduser().resolve()
    pol = _policy.load(getattr(args, "policy", None))
    run_id = run.name
    try:
        existing = artifacts.read(run, "run_inputs.json")
        run_id = existing.get("run_id", run_id)
    except artifacts.ArtifactError:
        pass  # s0 itself runs before run_inputs.json exists
    # TRACED HERE, NOT IN EACH STAGE. This is the one place every stage passes through, so
    # wiring it here is what makes the coverage total: a stage cannot forget to report
    # itself, exactly as it cannot forget to record a manifest entry. The run span is opened
    # lazily by the first stage of the run and closed by the last, because a pipeline is a
    # chain of separate processes with no outer scope to hold it.
    ri = {}
    try:
        ri = artifacts.read(run, "run_inputs.json")
    except artifacts.ArtifactError:
        pass
    prior = manifest.entry_of(run, stage_name)
    with manifest.record(run, run_id, stage_name, script, pol) as entry:
        try:
            _note_untraced_decision(run, run_id, stage_name, prior)
            obs = _observe.get(ri)
            obs.start_run(run, run_id, ri, pol)
            with obs.stage_span(stage_name, script, entry, run_id) as _sp:
                ctx = Ctx(run, pol, entry, args, span=_sp, stage_name=stage_name)
                # WHAT THIS STAGE WAS ASKED TO DO. Recorded before the body runs, so a stage
                # that refuses still leaves the instruction it refused.
                obs.human_args(args, stage_name)
                try:
                    yield ctx
                    # A gate's emit stops the run for a person; the manifest says so, so the
                    # entry point and the session can tell "waiting" from "done".
                    if getattr(args, "emit_prompt", False) and entry.notes is None and \
                            entry.verdict == "ok":
                        entry.notes = "awaiting_decision"
                    # The experiment is created as soon as run_inputs exists (end of S0),
                    # with everything its frozen `config` needs.
                    obs.ensure_experiment(run, pol)
                except BaseException:
                    # SET HERE, INNERMOST, so every layer that reads the verdict sees it. The
                    # manifest used to set `blocked` on its way out, AFTER the stage span and
                    # the run span had already recorded `ok` for a stage that had just raised.
                    if entry.verdict == "ok":
                        entry.verdict = "blocked"
                    # THE REASON TRAVELS WITH THE VERDICT. A stage that refuses by message
                    # (`raise SystemExit("a1: no .tmdl under …")`) left only `blocked` on the
                    # manifest, and the driver had nothing to say but "the engine refused".
                    # Resume reads `notes` only for its own markers, which are never
                    # overwritten here, and a blocked stage always runs again.
                    exc = sys.exc_info()[1]
                    if entry.notes is None and isinstance(exc, SystemExit) and \
                            isinstance(exc.code, str) and exc.code.strip():
                        entry.notes = _redact.text(exc.code)[:2000]
                    raise
                finally:
                    # Every stage flushes its own spans: each is its own process, and a
                    # batched exporter loses whatever is still buffered when one exits.
                    obs.finish_run(verdict=entry.verdict)
                    # A terminal experiment is frozen, so a close the stage asked for is done
                    # only now, after every span it opened has been flushed.
                    if entry.verdict == "ok":
                        obs.close_pending()
        except _observe.TracingRequired:
            # REQUIRED TRACING FAILED: the stage stops and says so on the manifest, so a
            # re-run resumes HERE and a re-run without the requirement is recognised as the
            # operator's decision to go on untraced.
            entry.verdict = "blocked"
            entry.notes = _observe.STOPPED_NOTE
            raise


def _note_untraced_decision(run: pathlib.Path, run_id: str, stage_name: str,
                            prior: dict | None) -> None:
    """Required tracing stopped this stage last time, and it now runs without the
    requirement: the operator chose to go on untraced. Recorded on the manifest by name, with
    what the environment said: it is a decision, not a force flag, and it must be visible."""
    if not prior or prior.get("notes") != _observe.STOPPED_NOTE or _observe.required():
        return
    import os  # noqa: PLC0415

    req = os.environ.get(_observe.REQUIRED_ENV)
    off = os.environ.get(_observe.OFF_ENV)
    manifest.record_decision(run, run_id, {
        "stage": stage_name, "kind": "continue_untraced",
        "detail": (f"tracing failed under {_observe.REQUIRED_ENV}=1 at this stage; the "
                   f"operator re-ran with {_observe.REQUIRED_ENV}="
                   f"{'unset' if req is None else repr(req)}"
                   + (f" and {_observe.OFF_ENV}={off!r}" if off else "")
                   + ", so the run goes on untraced"),
    })


def main(stage_name: str, script: str, description: str, body, extra=None) -> None:
    """Wire a stage up. `body(ctx)` does the work; `extra(parser)` adds stage-specific flags.

    A raised exception is reported by name and exits non-zero. No stage swallows an error:
    missing artifacts raise, and degraded modes are explicit choices recorded in the manifest.
    """
    p = parser(description)
    if extra:
        extra(p)
    # EVERY AUTHORED STAGE NAMES ITS MODEL AND ITS AUTHOR. Only a3b and c3 used to; an
    # answer to m2 or x1–x4 reached the trace with its content and nothing saying who or what
    # wrote it. Added here so no authored stage can be without them.
    if any("--from" in a.option_strings for a in p._actions):
        have = {o for a in p._actions for o in a.option_strings}
        if "--model" not in have:
            p.add_argument("--model", default=None,
                           help="the model that wrote the answer being ingested")
        if "--author" not in have:
            p.add_argument("--author", default=None,
                           help="who reviewed the answer, recorded as given (never "
                                "filled in from the environment)")
    args = p.parse_args()
    try:
        with run_stage(stage_name, script, args) as ctx:
            body(ctx)
    except SystemExit as exc:
        # A refusal is said by its message, which may quote what the stage was given.
        if isinstance(exc.code, str):
            msg = _redact.text(exc.code)
            if isinstance(exc, AnswerRefused) or _ingesting(args):
                print(msg, file=sys.stderr)
                raise SystemExit(ANSWER_REFUSED) from None
            raise SystemExit(msg) from None
        raise
    except _observe.TracingRequired as exc:
        print(f"{script}: {_redact.text(_observe.stop_message(str(exc)))}", file=sys.stderr)
        raise SystemExit(_observe.TRACING_STOPPED) from None
    except Exception as exc:
        print(f"{script}: {type(exc).__name__}: {_redact.text(exc)}", file=sys.stderr)
        raise SystemExit(1)
