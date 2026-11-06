# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Folding a history measure into the BI measure it reproduces — on CITABLE evidence only.

Ranking (lib/measures.rank_key) only helps when both lanes land in the SAME bucket, and
`measure_identity` keys on the normalised formula and the entity TABLE. So an
analyst's `SUM(net_rev)` over `MARTS.FACT_SALES` and the BI-compiled `SUM(net_revenue)` over
`MARTS.V_FACT_SALES` are two identities, two cards, and no competition — the dedupe the merge
promises is not met by ranking alone.

This module proposes pairings. Every fold names the evidence that licensed it, because a fold
is a claim that two statements compute one measure, and an uncited claim is the class of
thing this agent refuses to publish (evidence rule).

| signal                                                                  | verdict      |
| same normalised formula; entities lineage-equivalent (one is a view over the other, read  |
|   from the warehouse's own view definition; or the BI model name resolves to that table   |
|   through `bi_facts` `source_lineage`)                                    | FOLD         |
| same entity; column names resolve to the same physical column via `source_column`         |
|                                                                          | FOLD         |
| same entity + same aggregation fn, formulas otherwise differ             | ESCALATE     |
| a history candidate that would fold into MORE THAN ONE BI measure        | ESCALATE     |
| name similarity, string distance, "looks like"                          | REFUSE       |

THE HARD RULE INSIDE THE FOLD is enforced downstream by construction, not here: a folded
history candidate lands in the BI bucket as a LOSER (its lane ranks below), so the canonical
body, the population and the filters are the BI statement's. History contributes adoption,
observed values, examples and an alternate source — never a `WHERE`.

A fold never changes the BI metric's `source_id`; the history card it suppresses is named so
a prior publication of it can be archived deliberately rather than orphaned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import measures as M

FOLD_SAME_FORMULA_LINEAGE = "same formula; entities lineage-equivalent"
FOLD_SAME_ENTITY_COLUMNS = "same entity; columns resolve to the same physical column"
ESCALATE_FORMULA_DIFFERS = "same entity and aggregation; formulas differ"
ESCALATE_AMBIGUOUS = "would fold into more than one BI measure"
REFUSE_NAME = "a name is not an identity"


class FoldPolicyError(ValueError):
    """The fold policy asks for a loosening this agent does not support."""


def assert_policy(require_lineage_citation: bool, allow_name_similarity: bool) -> None:
    """The two knobs exist so the decision is VISIBLE in policy; neither may be loosened.

    A fold on an uncited lineage claim, or on a name, is exactly the "looks like the same
    metric" merge the whole layer exists to refuse. The values are asserted rather than read,
    the same way the lane order is (lib/measures.assert_lane_rank).
    """
    if not require_lineage_citation:
        raise FoldPolicyError(
            "metrics.provenance.fold.require_lineage_citation=false is not supported: a fold "
            "is a claim that two statements compute one measure, and an uncited claim is not "
            "published.")
    if allow_name_similarity:
        raise FoldPolicyError(
            "metrics.provenance.fold.allow_name_similarity=true is not supported: a name is "
            "not an identity. Six different measures on this estate were called `arr`.")


def _lower(s: Any) -> str:
    return str(s or "").strip().strip('"').lower()


def _leaf(fqn: str) -> str:
    return _lower(fqn).split(".")[-1]


@dataclass
class LineageEvidence:
    """What the fold may cite. Each map is empty when its source was not consulted, and
    `sources` says why — an absent source is recorded, never inferred as 'no relation'."""

    # view fqn (lower) -> the base tables its definition reads (lower). A fold needs EXACTLY
    # one: a view over two tables is a join, and a measure over a join is not the same measure.
    view_sources: dict[str, set[str]] = field(default_factory=dict)
    # BI model table name (lower) -> warehouse fqn (lower), from `bi_facts` `source_lineage`.
    bi_aliases: dict[str, str] = field(default_factory=dict)
    # (warehouse fqn or BI table name, BI column name) -> physical column, from `source_column`.
    column_sources: dict[tuple[str, str], str] = field(default_factory=dict)
    # source name -> what happened ("read", "unavailable (witness mode parse)", ...).
    sources: dict[str, str] = field(default_factory=dict)

    def resolve_entity(self, entity: Any) -> str:
        """A BI model table name becomes the warehouse table it reads; anything else is itself."""
        e = _lower(entity)
        if e in self.bi_aliases:
            return self.bi_aliases[e]
        leaf = _leaf(e)
        if "." not in e and leaf in self.bi_aliases:
            return self.bi_aliases[leaf]
        return e

    def equivalent(self, a: Any, b: Any) -> str | None:
        """A citation when the two entities are one table for the purpose of a measure."""
        ra, rb = self.resolve_entity(a), self.resolve_entity(b)
        if ra == rb:
            if ra != _lower(a) or rb != _lower(b):
                return f"{_lower(a)} and {_lower(b)} both resolve to {ra} (bi_facts source_lineage)"
            return "same entity"
        for view, base in ((ra, rb), (rb, ra)):
            reads = self.view_sources.get(view)
            if reads is not None and len(reads) == 1 and next(iter(reads)) == base:
                return f"{view} is a view over {base} (warehouse view definition)"
        return None

    def physical_formula(self, formula: Any, entity: Any) -> tuple[str, bool]:
        """The normalised formula with BI column names replaced by the physical columns they
        read. Returns (formula, changed)."""
        norm = M.normalise_formula(formula)
        ent = self.resolve_entity(entity)
        raw = _lower(entity)
        changed = False
        for (tbl, col), phys in self.column_sources.items():
            if tbl not in (ent, raw, _leaf(raw)):
                continue
            if col in norm and phys != col:
                # whole-token replace: `units_sold` must not rewrite `units_sold_ly`
                import re
                new = re.sub(rf"(?<![\w]){re.escape(col)}(?![\w])", phys, norm)
                if new != norm:
                    norm, changed = new, True
        return norm, changed


@dataclass
class FoldResult:
    folds: list[dict] = field(default_factory=list)
    escalations: list[dict] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    already_competing: int = 0


def _authored(c: dict) -> bool:
    return M.lane_of(c) in ("bi", "merged")


def propose(candidates: Iterable[dict], evidence: LineageEvidence) -> FoldResult:
    """Pair each history candidate with at most one BI measure, on citable evidence only."""
    cands = [c for c in candidates if c]
    bi_by_measure: dict[str, list[dict]] = {}
    for c in cands:
        if _authored(c):
            key = M.measure_identity(c.get("expr") or c.get("formula") or "",
                                     c.get("source_table") or c.get("entity") or "")
            bi_by_measure.setdefault(key, []).append(c)

    out = FoldResult()
    for h in cands:
        if _authored(h):
            continue
        h_key = M.measure_identity(h.get("expr") or "", h.get("source_table") or "")
        if h_key in bi_by_measure:
            # Same identity already: ranking puts the BI statement first. Nothing to fold.
            out.already_competing += 1
            continue
        h_formula = M.normalise_formula(h.get("expr"))
        h_entity = evidence.resolve_entity(h.get("source_table"))
        matches: list[dict] = []
        escalate: list[dict] = []
        refuse: list[dict] = []
        for key, group in sorted(bi_by_measure.items()):
            rb = group[0]
            same_fn = _lower(h.get("fn")) == _lower(rb.get("fn"))
            b_formula = M.normalise_formula(rb.get("expr"))
            b_physical, mapped = evidence.physical_formula(rb.get("expr"), rb.get("source_table"))
            cite = evidence.equivalent(h.get("source_table"), rb.get("source_table"))
            if same_fn and cite and (h_formula == b_formula or h_formula == b_physical):
                if h_formula != b_formula and mapped:
                    rule = FOLD_SAME_ENTITY_COLUMNS
                    citation = f"{cite}; BI columns resolve via source_column to {b_physical}"
                else:
                    rule = FOLD_SAME_FORMULA_LINEAGE
                    citation = cite
                matches.append({"into_measure_id": key, "into_candidate_id": rb.get("candidate_id"),
                                "into_vq_id": rb.get("vq_id"), "rule": rule, "citation": citation})
            elif same_fn and cite:
                escalate.append({"bi_candidate_id": rb.get("candidate_id"),
                                 "bi_measure_id": key, "why": ESCALATE_FORMULA_DIFFERS,
                                 "history_formula": h_formula, "bi_formula": b_formula,
                                 "citation": cite})
            elif _lower(h.get("alias")) and _lower(h.get("alias")) == _lower(rb.get("alias")):
                refuse.append({"bi_candidate_id": rb.get("candidate_id"), "bi_measure_id": key,
                               "why": REFUSE_NAME, "shared_name": _lower(h.get("alias"))})

        base = {"history_candidate_id": h.get("candidate_id"), "history_vq_id": h.get("vq_id"),
                "history_measure_id": h_key, "history_entity": h_entity}
        if len(matches) == 1:
            out.folds.append({**base, **matches[0]})
        elif len(matches) > 1:
            out.escalations.append({**base, "why": ESCALATE_AMBIGUOUS,
                                    "candidates": [m["into_measure_id"] for m in matches]})
        else:
            out.escalations.extend({**base, **e} for e in escalate)
            out.refused.extend({**base, **r} for r in refuse)
    return out
