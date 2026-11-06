#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""D1 — lift metric candidates out of the verified queries.

Every ALIASED AGGREGATE PROJECTION becomes a candidate: `SUM(units_sold) AS units`,
`COUNT(DISTINCT customer_id) AS active`, and ratio forms like `SUM(a)/NULLIF(SUM(b),0) AS pct`.
An UNALIASED aggregate is skipped, never auto-named — a generated name is a business claim
nobody made. In a set operation (UNION / EXCEPT / INTERSECT) EVERY branch is read, as H2 reads
them: a later branch's aggregate is named by the column the first branch puts at its position
and takes its source table from its own branch.

THE FILTER SPLIT IS THE HARD PART, AND IT IS RULE-FIRST. Every predicate in the enclosing
query is classified:

  * on a column that also appears in the query's GROUP BY  -> SLICING
  * in a HAVING/QUALIFY clause                             -> SLICING (it filters the
    aggregate for this question, not the metric's population)
  * repeated across >= k verified queries computing the same expression -> DEFINITIONAL
  * anything else                                          -> ESCALATED to D3 with its
    evidence attached, never guessed

k and the id-column pattern live in policy. Policy governs admission and ranking; it never
makes a claim true — an escalation stays an escalation.
"""
from __future__ import annotations

import pathlib
import sys
from collections import Counter, defaultdict


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import measures as M  # noqa: E402
from metrics_agent.lib import sqlshape as S  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "d1_candidates"
OUT = "metric_candidates.json"


def body(ctx: stage.Ctx) -> None:
    layer = ctx.read("verified_queries.json", "verified_queries.schema.json")
    dialect = layer["dialect"]
    min_support = ctx.policy.get("metrics.definitional_filter_min_support")

    # Pass 1 — lift the aliased aggregates and record which predicates co-occur with each
    # expression, so support can be counted ACROSS queries rather than guessed within one.
    raw: list[dict] = []
    support: dict[str, Counter] = defaultdict(Counter)
    # Distinct VALUES seen per (expression, column op). A predicate whose column/op
    # repeats but whose VALUE varies across queries is a PARAMETER, not a pinned literal:
    # `season_code = 'AW25'` and `= 'SS25'` are the same metric asked of two seasons.
    values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    n_unaliased = 0

    for o in layer["objects"]:
        tree = S.parse(o["sql"], dialect)
        # EVERY BRANCH OF A SET OPERATION, as H2 reads them. Reading the first branch alone
        # lost every number a later branch computes; each later-branch aggregate is resolved
        # against its OWN branch's FROM (`scope_sql`), not the first branch's.
        aggs = S.extract_aggregations(tree, dialect, every_branch=True)
        amap = S.alias_map(tree)
        group_cols = {g.lower() for g in o["group_by"]}
        prov = o.get("provenance") or {}
        pop, pop_basis = M.popularity_of(prov)
        for a in aggs:
            if not a["alias"]:
                n_unaliased += 1
                continue
            expr_key = a["expr"].strip().lower()
            for f in o["filters"]:
                col = f["column"].lower()
                in_group = col in group_cols or any(col.endswith("." + g) or g.endswith("." + col)
                                                    for g in group_cols)
                stage_is_having = (f.get("stage") or "").lower() in ("having", "qualify")
                if not in_group and not stage_is_having:
                    claim = f"{col} {f['op']}"
                    support[expr_key][claim] += 1
                    if f.get('value') is not None:
                        values[expr_key][claim].add(str(f['value']))
            raw.append({
                "candidate_id": f"mc-{o['vq_id']}-{a['alias']}",
                "vq_id": o["vq_id"],
                "alias": a["alias"],
                "expr": a["expr"],
                "fn": a["fn"],
                "tables": o["tables"],
                # Resolved from the expression's OWN column qualifiers. Sorting the candidate
                # tables alphabetically picked a dimension table for a metric computed
                # entirely over a fact table — wrong in a way that only shows up at execution.
                "source_table": S.source_table_of(a["expr"], amap, dialect, o["tables"],
                                                  statement_sql=a.get("scope_sql")
                                                  or o.get("sql")),
                "domain": o["domain"],
                "grain": o["grain"],
                "group_by": o["group_by"],
                "filters": o["filters"],
                "expr_key": expr_key,
                # PROVENANCE RIDES ON THE CANDIDATE. `pick_winner` ranks on lane, popularity
                # and recency, and none of them reached this artifact — so the sort fell
                # through to the id string, and `h-` beat `bi-` on every contested measure.
                # The lane is the first key; the rest break ties.
                "lane": prov.get("lane"),
                "popularity": pop,
                "popularity_basis": pop_basis,
                "last_seen": (prov.get("history") or {}).get("last_seen"),
                # The witness verdict is NOT a rank key — a BI statement the warehouse refused
                # still speaks for its measure. It rides so D2b can see the one case that
                # means the compiler is wrong.
                "witness_result": (o.get("witness") or {}).get("result"),
                "witness_error": (o.get("witness") or {}).get("error"),
            })

    # Pass 2 — apply the split now that support is known across the whole layer.
    out = []
    n_def = n_slice = n_esc = n_param = 0
    for c in raw:
        group_cols = {g.lower() for g in c["group_by"]}
        definitional, slicing, escalated, parameters = [], [], [], []
        for f in c["filters"]:
            col = f["column"].lower()
            claim = f"{col} {f['op']}"
            in_group = col in group_cols or any(col.endswith("." + g) or g.endswith("." + col)
                                                for g in group_cols)
            stage_is_having = (f.get("stage") or "").lower() in ("having", "qualify")
            _sup = support[c["expr_key"]][claim]
            if in_group or stage_is_having:
                slicing.append(f)
                # EVERY PREDICATE SAYS WHY IT LANDED WHERE IT DID. A count of "17,803
                # definitional" cannot answer "why is fiscal_year part of this metric's
                # population?" — the answer is the rule that fired and the support it was
                # measured against, so both travel with the verdict.
                ctx.decide("filter", f"{c['candidate_id']}:{claim}", "slicing",
                           because=("on a GROUP BY column" if in_group
                                    else f"in {f.get('stage')}"),
                           policy_key="metrics.definitional_filter_min_support",
                           policy_value=min_support,
                           evidence={"support": _sup, "column": f["column"], "op": f["op"],
                                     "expr_key": c["expr_key"], "vq_id": c.get("vq_id")})
            elif _sup >= min_support:
                seen_values = values[c["expr_key"]][claim]
                tunable = f.get("value_kind", "literal") == "literal"
                if len(seen_values) > 1 and not f.get("negated") and tunable:
                    # Repeated column/op, VARYING value: the metric is the same question
                    # asked of different periods/segments. Pinning one literal would
                    # publish one question's answer as the metric's definition.
                    parameters.append({**f, "observed_values": sorted(seen_values)})
                    ctx.decide("filter", f"{c['candidate_id']}:{claim}", "parameter",
                               because="repeated column/op with a VARYING value — the same "
                                       "question asked of different periods or segments, so "
                                       "pinning one literal would publish one answer as the "
                                       "definition",
                               policy_key="metrics.definitional_filter_min_support",
                               policy_value=min_support,
                               evidence={"support": _sup, "observed_values": sorted(seen_values),
                                         "column": f["column"], "op": f["op"],
                                         "vq_id": c.get("vq_id")})
                else:
                    # NEGATED PREDICATES ON ONE COLUMN ARE A CONJUNCTION, NOT A CHOICE.
                    # `NOT (stage ILIKE '%Closed Won%' OR stage ILIKE '1 - %')` excludes both
                    # at once; it is not the same question asked of two segments. Reading the
                    # varying values as a knob published open pipeline with no stage
                    # exclusions at all, and three benchmark questions came back ~19% over.
                    # Each exclusion is definitional and stands with the others. The same
                    # goes for a correlated comparison: `close_date_as_of >= close_quarter_start`
                    # has no value to vary, so offering it as a knob invites a caller to
                    # replace "within this row's quarter" with a fixed date.
                    definitional.append(f)
                    ctx.decide("filter", f"{c['candidate_id']}:{claim}", "definitional",
                               because=("negated predicate — a conjunction, not a choice"
                                        if f.get("negated") else
                                        "no value to vary" if not tunable else
                                        "one observed value across the layer"),
                               policy_key="metrics.definitional_filter_min_support",
                               policy_value=min_support,
                               evidence={"support": _sup,
                                         "observed_values": sorted(seen_values),
                                         "negated": bool(f.get("negated")),
                                         "value_kind": f.get("value_kind"),
                                         "column": f["column"], "op": f["op"],
                                         "vq_id": c.get("vq_id")})
            else:
                ctx.decide("filter", f"{c['candidate_id']}:{claim}", "escalated",
                           because=f"seen {_sup} time(s), below the support floor; D3 "
                                   f"decides with the evidence rather than guessing",
                           policy_key="metrics.definitional_filter_min_support",
                           policy_value=min_support,
                           evidence={"support": _sup, "column": f["column"], "op": f["op"],
                                     "vq_id": c.get("vq_id")})
                escalated.append({
                    **f,
                    "why": f"seen {support[c['expr_key']][claim]} time(s) across the layer, "
                           f"below metrics.definitional_filter_min_support={min_support}; "
                           f"D3 decides with the evidence attached rather than guessing",
                })
        n_def += len(definitional)
        n_slice += len(slicing)
        n_esc += len(escalated)
        n_param += len(parameters)
        out.append({**{k: v for k, v in c.items() if k != "filters"},
                    "definitional_filters": definitional,
                    "slicing_filters": slicing,
                    "escalated_filters": escalated,
                    "parameter_filters": parameters})

    # A candidate_id must identify one EXPRESSION, and `mc-{vq}-{alias}` does not: SQL permits
    # two output columns to carry the same alias, and a real estate uses that — an all-accounts
    # and a net-new variant of `accounts_grt_500` in one statement. Left colliding, the two
    # different expressions share a key, so D2's partition silently drops one and every later
    # stage keyed on the id conflates them. Disambiguate deterministically, ordered by the
    # expression text so the suffix is reproducible across runs, and only where the collision
    # is real — an id that was already unique keeps its readable form.
    by_id: dict[str, list[dict]] = defaultdict(list)
    for c in out:
        by_id[c["candidate_id"]].append(c)
    n_disambiguated = 0
    for cid, group in by_id.items():
        if len(group) < 2:
            continue
        # The first keeps the readable id; every later one is suffixed. Slicing off the head
        # says "all but the first" without a bare numeric comparison, which in a d* script is
        # indistinguishable from a threshold that belongs in policy.
        ordered = sorted(group, key=lambda x: x["expr"])
        for i, c in enumerate(ordered[1:], start=2):
            c["candidate_id"] = f"{cid}#{i}"
            n_disambiguated += 1
    if n_disambiguated:
        ctx.say(f"d1: {n_disambiguated} candidate id(s) collided on (vq_id, alias) with a "
                f"DIFFERENT expression and were suffixed. One alias reused for two expressions "
                f"is legal SQL; sharing a key would have let D2 drop one silently.")

    out.sort(key=lambda c: c["candidate_id"])
    artifacts.dump(ctx.run / OUT,
                   {"contract_version": "1.0", "dialect": dialect,
                    "layer_sha": layer["layer_sha"], "candidates": out},
                   "metric_candidates.schema.json")
    ctx.entry.outputs.append(str(ctx.run / OUT))
    ctx.count(candidates=len(out), unaliased_skipped=n_unaliased,
              definitional=n_def, slicing=n_slice, escalated=n_esc,
              parameters=n_param)
    ctx.say(f"d1: {len(out)} metric candidates ({n_unaliased} unaliased aggregates skipped — "
            f"a generated name is a claim nobody made). Filters: {n_def} definitional, "
            f"{n_slice} slicing, {n_param} parameterized (repeated column/op with a "
            f"varying value), {n_esc} escalated to D3.")


def main() -> None:
    stage.main(STAGE, "d1_candidates.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
