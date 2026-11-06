#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""C2 — cross-lane dedup, merge, and the variant split.

Two candidates are the same question when their SEMANTIC SHAPE KEY matches: sorted(tables) +
sorted((fn, expr)) + sorted(group_by) + the normalised, parameterized predicate set.

  exact match  -> MERGE
  near match   -> VARIANTS, kept separate and cross-linked, never merged
  no match     -> each admitted alone

THE PRIORITY RULE IS MEASURED, NOT A PREFERENCE. On a merge the BI definition governs the
DECISIONS — population, filters, grain — and the history candidate contributes ADOPTION
evidence plus its own statement as an observed variant. Adding a partial-but-wide second
source to a layer regressed two strong domains (82%->73%, 57%->43%) by displacing objects in
domains it did not cover, so a merge happens ONLY on an exact key and neither lane is allowed
to leak past it.

WHY VARIANTS ARE NEVER MERGED: polarity opposites (churned vs retained) and period variants
(MTD vs rolling 12) share a formula but are different questions. Merging them is how a layer
starts answering the wrong one.
"""
from __future__ import annotations

import pathlib
import sys
from collections import defaultdict


from metrics_agent.lib import observed as _observed  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "c2_merge"
IN = "candidates.json"
OUT = "merged_candidates.json"


def _predicate_set(c: dict) -> set[str]:
    return {f"{f['column']} {f['op']}" for f in c.get("filters", [])}


def _near(a: dict, b: dict, max_delta: int) -> bool:
    """A near match differs only by a small number of predicates OR by grain, with the same
    tables and the same aggregations. Anything further apart is a different question, not a
    variant of this one."""
    if sorted(a["tables"]) != sorted(b["tables"]):
        return False
    aggs_a = sorted((x["fn"], x["expr"]) for x in a["aggregations"])
    aggs_b = sorted((x["fn"], x["expr"]) for x in b["aggregations"])
    if aggs_a != aggs_b:
        return False
    pa, pb = _predicate_set(a), _predicate_set(b)
    delta = len(pa.symmetric_difference(pb))
    grain_differs = sorted(a["group_by"]) != sorted(b["group_by"])
    return delta <= max_delta or (delta == 0 and grain_differs)


def _merge_pair(bi: dict, hist: dict) -> dict:
    """BI governs the decisions; history contributes evidence and an observed variant."""
    out = dict(bi)
    out["lane"] = "merged"
    prov = dict(bi["provenance"])
    prov["lane"] = "merged"
    prov["history"] = hist["provenance"].get("history")
    prov["merged_from"] = sorted([bi["candidate_id"], hist["candidate_id"]])
    prov["evidence_citations"] = (bi["provenance"].get("evidence_citations", [])
                                  + hist["provenance"].get("evidence_citations", []))
    # The business already named this concept, so BI supplies the name; history supplies the
    # corroboration that people actually ask it.
    prov["ranking_basis"] = "bi-definition governs decisions; history adoption corroborates"
    out["provenance"] = prov
    variants = list(bi.get("variants", []))
    if hist["sql"].strip() != bi["sql"].strip():
        variants.append({
            "label": "as observed in query history",
            "scope_when": "the analyst spelling of the same question",
            "sql": hist["sql"],
            "evidence": f"query-log shape {hist['provenance'].get('history', {}).get('shape_key')} "
                        f"({hist['provenance'].get('history', {}).get('n_distinct_users')} "
                        f"distinct analysts)",
        })
    out["variants"] = variants
    # UNION both lanes' observed values. This is the case that is easy to miss: a BI candidate
    # merging with a history candidate must not drop the history literals, or every value an
    # analyst ever typed for this question becomes unfindable the moment a dashboard also
    # asks it.
    out["observed_values"] = _observed.merge(bi.get("observed_values"),
                                             hist.get("observed_values"))
    return out


def body(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "candidates.schema.json")
    cands = doc["candidates"]
    max_delta = ctx.policy.get("convergence.near_match_max_predicate_delta")
    require_exact = ctx.policy.get("convergence.merge_requires_exact_shape_key")
    bi_governs = ctx.policy.get("convergence.bi_governs_decisions")
    if not require_exact:
        raise SystemExit(
            "convergence.merge_requires_exact_shape_key=false would merge near matches, which "
            "collapses polarity opposites and period variants into one object. Refused."
        )

    by_key: dict[str, list[dict]] = defaultdict(list)
    for c in cands:
        by_key[c["semantic_shape_key"]].append(c)

    merged: list[dict] = []
    n_merged = n_alone = 0
    for key in sorted(by_key):
        group = sorted(by_key[key], key=lambda c: c["candidate_id"])
        bis = [c for c in group if c["lane"] == "bi"]
        hists = [c for c in group if c["lane"] == "history"]
        if bi_governs and bis and hists:
            out = _merge_pair(bis[0], hists[0])
            merged.append(out)
            n_merged += 1
            # Any extra members of the same key are duplicates within a lane; keep the first
            # and record the rest as merged_from so nothing silently disappears.
            for extra in group:
                if extra is bis[0] or extra is hists[0]:
                    continue
                if ((extra["provenance"].get("bi") or {}).get("declared")
                        and extra["lane"] == "bi"):
                    # A statement compiled from a DECLARED metric is that metric's only
                    # statement: folded into another, the metric would reach no card. Two
                    # models declaring one formula are merged at X2, where both are named.
                    alone = dict(extra)
                    alone["provenance"] = {**extra["provenance"],
                                           "ranking_basis": "bi dashboard placement"}
                    merged.append(alone)
                    n_alone += 1
                    continue
                out["provenance"]["merged_from"].append(extra["candidate_id"])
        else:
            for c in group:
                c = dict(c)
                c["provenance"] = dict(c["provenance"])
                c["provenance"].setdefault(
                    "ranking_basis",
                    "bi dashboard placement" if c["lane"] == "bi" else "query-log adoption")
                merged.append(c)
                n_alone += 1

    # Cross-link near matches as scoped variants WITHOUT merging them.
    n_variant_links = 0
    for i, a in enumerate(merged):
        for b in merged[i + 1:]:
            if a["semantic_shape_key"] == b["semantic_shape_key"]:
                continue
            if _near(a, b, max_delta):
                for src, dst in ((a, b), (b, a)):
                    src.setdefault("variants", []).append({
                        "label": f"variant: {dst['candidate_id']}",
                        "scope_when": "same tables and aggregations, differing filter or grain",
                        "sql": None,
                        "evidence": "c2 near-match; kept separate because a differing filter "
                                    "or grain is a different question",
                    })
                n_variant_links += 1

    merged.sort(key=lambda c: c["candidate_id"])
    ctx.write(OUT, {"contract_version": "1.0", "dialect": doc["dialect"],
                    "candidates": merged}, "candidates.schema.json")
    ctx.count(input=len(cands), output=len(merged), merged_pairs=n_merged,
              admitted_alone=n_alone, variant_links=n_variant_links)
    ctx.say(f"c2: {len(cands)} in -> {len(merged)} out ({n_merged} cross-lane merges, "
            f"{n_variant_links} near-match variant links kept separate).")


def main() -> None:
    stage.main(STAGE, "c2_merge.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
