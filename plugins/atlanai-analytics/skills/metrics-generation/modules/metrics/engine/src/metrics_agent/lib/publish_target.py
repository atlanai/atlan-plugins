# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The run's PUBLISH TARGET — where its output may go, declared once by the operator.

The target is the set of LANES the run's output plugs declare (`publish` in run.yml): each
output plug names the lane that admits it (`sockets.OutputPlug.lane`), and `files` is the
core's own, meaning nothing leaves the run directory. The core names no other lane: which
destinations exist is the installed plugs' business (AGENT_GUARDRAILS rule 22).

THE OPERATOR DECIDES; THE AGENT NEVER DOES. There is no default: `publish` is required in
run.yml, and the set-up interview asks it every time. A target inferred from which
credentials happen to be set would be a guess about where a customer's metrics end up, which
is exactly the decision that must not be guessed.

Enforced at the WRITE, not described: every stage that writes outside the run directory calls
`require()` before it reaches for a credential, and a dry run is never blocked — it sends
nothing, and seeing what WOULD be sent is how an operator makes the choice.
"""
from __future__ import annotations

FILES = "files"


def lanes(target) -> set[str]:
    """The lanes a recorded target admits. `files` admits nothing outside the run."""
    return {str(t) for t in (target or [])} - {FILES}


def describe(target) -> str:
    """The target in words: its lanes, or `none` when nothing leaves the run directory."""
    return ", ".join(sorted(lanes(target))) or "none"


def require(ctx, lane: str, action: str) -> None:
    """Refuse `action` unless the run's declared target includes `lane`. Called only on the
    write path (a dry run sends nothing and is never blocked)."""
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    target = ri.get("publish_target")
    if target is None:
        raise SystemExit(
            f"{action}: REFUSED — this run declares no publish target (run_inputs.json "
            "predates it). Re-run s0_inputs from a run.yml whose `publish` lists where the "
            "output goes; the operator decides it.")
    if lane not in lanes(target):
        raise SystemExit(
            f"{action}: REFUSED — this run publishes to {describe(target)}, which does not "
            f"include {lane!r}. The target was the operator's decision (`publish` in "
            "run.yml); to change it, change `publish` and re-run s0_inputs.")
    # NO USER NAME LEAVES THE RUN DIRECTORY, whatever `pii.redact_user_names` says: every
    # artifact a destination could be fed from is checked here, on every write path.
    from metrics_agent.lib import pii as _pii  # noqa: PLC0415
    _pii.refuse_user_names(ctx.run, _pii.outbound_paths(ctx.run), action)
