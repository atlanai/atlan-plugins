# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The run manifest — one entry per stage, so two runs are comparable and one is reproducible.

Inputs are recorded as PATHS (rule 7). The policy path and sha go on every entry, because two
runs are only comparable if you can see they were built under the same thresholds.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import artifacts

NAME = "manifest.json"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load(run: pathlib.Path, run_id: str) -> dict[str, Any]:
    path = run / NAME
    if path.is_file():
        return json.loads(path.read_text())
    return {"contract_version": "1.0", "run_id": run_id, "stages": []}


class Entry:
    """Mutable accumulator handed to the stage body."""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.outputs: list[str] = []
        self.counts: dict[str, Any] = {}
        self.verdict: str = "ok"
        self.notes: str | None = None
        self.model: str | None = None


def entry_of(run: pathlib.Path, stage: str) -> dict[str, Any] | None:
    """The manifest entry `stage` left last time, or None."""
    path = run / NAME
    if not path.is_file():
        return None
    try:
        stages = json.loads(path.read_text()).get("stages") or []
    except ValueError:
        return None
    return next((s for s in stages if s.get("stage") == stage), None)


def record_decision(run: pathlib.Path, run_id: str, decision: dict[str, Any]) -> None:
    """Append an operator's decision to the manifest's `decisions` list. Kept beside the
    stage entries, not in one, because a stage's entry is replaced each time it runs and a
    decision must outlive that."""
    doc = _load(run, run_id)
    doc.setdefault("decisions", []).append({"at": _now(), **decision})
    artifacts.ensure_run(run)
    artifacts.validate(doc, "manifest.schema.json", what=NAME)
    (run / NAME).write_text(json.dumps(doc, indent=2) + "\n")


@contextmanager
def record(
    run: pathlib.Path, run_id: str, stage: str, script: str, policy: Any
) -> Iterator[Entry]:
    """Append exactly one manifest entry for this stage.

    A stage that raises still records its entry with verdict `blocked`, because a gate that
    stopped the run is the most important thing the manifest can say.

    CATCHING `BaseException`, NOT `Exception`, IS THE WHOLE POINT. Stages signal failure with
    `raise SystemExit("missing X; run Y first")` — that is the house idiom for a refusal, and
    SystemExit does not inherit from Exception. Catching only Exception meant every one of
    those failures recorded verdict `ok`: the manifest, which is the run's evidence that a
    stage completed, reported success for a stage that had just refused to run. A later stage
    then consumed its absent or partial output on the strength of that entry.

    An explicitly set verdict is preserved — a gate that says `blocked` and then exits has
    already said the true thing.
    """
    entry = Entry()
    started = _now()
    t0 = time.monotonic()
    try:
        yield entry
    except BaseException:
        if entry.verdict == "ok":
            entry.verdict = "blocked"
        raise
    finally:
        doc = _load(run, run_id)
        doc["stages"] = [s for s in doc["stages"] if s["stage"] != stage]
        doc["stages"].append(
            {
                "stage": stage,
                "script": script,
                "started_at": started,
                "duration_s": round(time.monotonic() - t0, 3),
                "inputs": entry.inputs,
                "outputs": entry.outputs,
                "policy_path": str(policy.path),
                "policy_sha256": policy.sha256,
                "counts": entry.counts,
                "verdict": entry.verdict,
                "notes": entry.notes,
                "model": entry.model,
            }
        )
        artifacts.ensure_run(run)
        artifacts.validate(doc, "manifest.schema.json", what=NAME)
        (run / NAME).write_text(json.dumps(doc, indent=2) + "\n")
