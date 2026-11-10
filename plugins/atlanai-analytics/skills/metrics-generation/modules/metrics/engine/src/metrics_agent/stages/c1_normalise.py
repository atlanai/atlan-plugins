#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""C1 — normalise both lanes into one candidate shape.

This is the seam. Both lanes already emit the CANDIDATES contract, so C1 has nothing to
translate — it only has to put them in one file and record which lanes were actually present.
That is deliberate: no stage after this point knows or cares how many lanes ran, which is what
keeps the three estate profiles from becoming three code paths.

An absent lane is a NAMED mode, not a silent skip. Its candidate list is empty and the fact is
recorded on the artifact and in the manifest, with a disclaimer saying the emptiness is a
property of the run's inputs rather than a finding about the estate.
"""
from __future__ import annotations

import pathlib
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "c1_normalise"
OUT = "candidates.json"


def _maybe(ctx: stage.Ctx, name: str) -> list[dict]:
    try:
        doc = ctx.read(name, "candidates.schema.json")
    except artifacts.ArtifactError:
        return []
    return doc["candidates"]


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    profile = ri["profile"]

    bi = _maybe(ctx, "bi_candidates.json")
    qh = _maybe(ctx, "qh_candidates.json")

    # A LANE THE PROFILE DOES NOT NAME IS NOT CONVERGED. run_inputs is the record of what this
    # run read; candidates from a lane it never declared (a file left in the run directory by
    # an earlier run with other inputs) would enter the layer outside every check S0 made.
    # Left out, counted, and said out loud.
    n_ignored = 0
    if profile == "history-only" and bi:
        n_ignored += len(bi)
        ctx.say(f"c1: WARNING — the profile is history-only but bi_candidates.json holds "
                f"{len(bi)} candidate(s). The run declared no BI input, so they are NOT "
                f"converged. Re-run s0 with --bi if they belong to this run.")
        bi = []
    if profile == "bi-only" and qh:
        n_ignored += len(qh)
        ctx.say(f"c1: WARNING — the profile is bi-only but qh_candidates.json holds "
                f"{len(qh)} candidate(s). The run declared no history input, so they are NOT "
                f"converged. Re-run s0 with the history input if they belong to this run.")
        qh = []

    if profile in ("both", "bi-only") and not bi:
        ctx.say("c1: the profile names a BI lane but bi_candidates.json is absent or empty. "
                "Run a1/a2/a3, or re-run s0 with the profile the inputs actually support.")
    if profile in ("both", "history-only") and not qh:
        # NOT A MISSING STAGE. The history lane (H) produces the verified-query layer
        # directly, so on a history-only run this convergence has nothing to converge and
        # that is the expected shape.
        ctx.say("c1: no history candidates. The history arm is lane H (h0..h5), which writes "
                "verified_queries.json directly and does not pass through this stage.")

    # Sorted by candidate_id so the merged file is byte-stable regardless of lane order.
    merged = sorted(bi + qh, key=lambda c: c["candidate_id"])
    doc = {"contract_version": "1.0", "dialect": ri["dialect"], "candidates": merged}
    ctx.write(OUT, doc, "candidates.schema.json")

    if not merged:
        ctx.entry.verdict = "stub"
        ctx.entry.notes = ("No candidates from either lane. This is a property of the run's "
                           "inputs, not a finding that the estate has no analytical workload.")
    ctx.count(bi=len(bi), history=len(qh), total=len(merged), profile=profile)
    if n_ignored:
        ctx.count(ignored_undeclared_lane=n_ignored)
    ctx.say(f"c1: profile={profile} bi={len(bi)} history={len(qh)} total={len(merged)}")


def main() -> None:
    stage.main(STAGE, "c1_normalise.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
