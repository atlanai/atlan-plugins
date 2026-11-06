# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a run records about itself, with or without tracing.

* `run_identity`: the run an artifact points back at (its id, the code version, and the
  session, experiment, trace and agent ids when a tracing observer recorded them). Read from
  the run directory, so it works in any stage process; with tracing off the tracing fields are
  empty, never invented.
* `record_handoff` / `load_handoffs`: every point where the run waited for a person and what
  was answered, kept in the run directory (`.trace_handoffs.jsonl`). Only the FACT of the
  hand-off is kept (kind, stage, file, size, a short answer, hashes), never an authored
  answer's content.
* `code_version`: the commit the pipeline ran from (an installed package: its version).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import subprocess
from typing import Any

from metrics_agent._paths import ROOT

HANDOFFS = ".trace_handoffs.jsonl"
# Written by a tracing observer: the run's trace context, shared by every stage process.
CTX_FILE = ".trace_context.json"
ENV_AGENT = "METRICS_AGENT_TRACE_AGENT_ID"


def code_version() -> str:
    """The commit this pipeline ran from. A derivation is only reproducible against the code
    that produced it, and 'which version was this?' is unanswerable from an artifact.

    Only a source checkout has a commit. An installed package records its release version
    instead: asking git there would answer for whatever repository happens to enclose the
    environment, which is not this code."""
    if not (ROOT / ".git").exists():
        from metrics_agent import __version__  # noqa: PLC0415

        return __version__
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        sha = (out.stdout or "").strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{sha[:12]}{'+dirty' if dirty else ''}" if sha else "unknown"
    except Exception:
        return "unknown"


def _read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def run_identity(run_dir: pathlib.Path) -> dict:
    run_dir = pathlib.Path(run_dir)
    ctx = _read_json(run_dir / CTX_FILE)
    run_id = _read_json(run_dir / "run_inputs.json").get("run_id") or run_dir.name
    return {
        "run_id": run_id,
        "session_key": ctx.get("session_key") or "",
        "experiment_id": ctx.get("experiment_id") or "",
        "trace_id": ctx.get("trace_id") or "",
        # The agent the run reports as: from the environment, or the trace context a tracing
        # plug left (its own registration stage records it there).
        "agent_id": os.environ.get(ENV_AGENT, "").strip() or str(ctx.get("agent_id") or ""),
        "commit": code_version(),
    }


def record_handoff(run_dir: pathlib.Path, kind: str, question: str, answer: Any,
                   stage_name: str, decided_by: str = "", extra: dict | None = None) -> None:
    extra = extra or {}
    short = answer if isinstance(answer, (int, float, bool)) or answer is None else \
        str(answer)[:200]
    row = {"at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
           "stage": stage_name, "kind": kind, "question": str(question)[:300],
           "answer": short, "decided_by": decided_by or "",
           "source_path": extra.get("source_path"), "bytes": extra.get("bytes"),
           "answer_sha256": extra.get("answer_sha256"),
           "prompt_sha256": extra.get("prompt_sha256"), "model": extra.get("model")}
    try:
        with (pathlib.Path(run_dir) / HANDOFFS).open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


def load_handoffs(run_dir: pathlib.Path) -> list:
    """Every handoff recorded so far. A line that does not read (a write cut short) costs
    that line, never the rest: one bad row used to drop every handoff of the run."""
    try:
        lines = (pathlib.Path(run_dir) / HANDOFFS).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
