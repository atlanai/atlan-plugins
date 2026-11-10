# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""H2 — derive the estate's MEASURES from the canonical shapes. Deterministic; no author.

THIS STAGE EXISTS BECAUSE AN AUTHORED LAYER IS NOT REPRODUCIBLE. The origin asked a model to
read each query and emit whatever certified objects it saw. Two authors reading the same 23
statements produced 41 cards and 11 — not because either misread them, but because "is this
one metric or three?" is a judgement, and a judgement re-made on every run is a layer that
changes size every time it is rebuilt.

So the agent decides HOW MANY cards there are and what each one computes; the author decides
only what to CALL them and what they mean. A measure is one formula over one entity — the same
identity lane D already groups on, which is what took that layer from 787 near-duplicate cards
to 455. On those same 23 statements it yields 8 measures, and it yields 8 every time.

The formula and the entity are read off the statement with the parser, never from prose, so
this stage cannot drift: same corpus in, same measures out, whoever is at the keyboard.
"""
from __future__ import annotations

import json
import pathlib
import sys


from metrics_agent.lib import measures as M, sqlshape as S, stage  # noqa: E402

STAGE = "h2_measures"
IN = "query_shapes.json"
OUT = "history_measures.json"


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    dialect = ri["dialect"]
    doc = ctx.read(IN, "query_shapes.schema.json")
    shapes = doc["shapes"]

    by_id: dict[str, dict] = {}
    n_parse_fail = n_labels = n_foreign = 0
    for sh in shapes:
        try:
            tree = S.parse(sh["sql"], dialect)
        except Exception:
            n_parse_fail += 1
            continue
        amap = S.alias_map(tree)
        # EVERY BRANCH OF A SET OPERATION. Reading the first branch alone lost every number a
        # later branch computes; each is lifted and resolved against its own branch's FROM.
        for a in S.extract_aggregations(tree, dialect, every_branch=True):
            if not a.get("expr"):
                continue
            # A LABEL CARRIED THROUGH A GROUP BY IS NOT A MEASURE. `SELECT account_id,
            # MAX(account_name) ... GROUP BY account_id` wraps the label so the engine will
            # project it; nobody is measuring anything. Lane D already refuses these — 114 of
            # 514 published metrics were this shape — and lane H applying a different rule to
            # the same question is how one agent ends up with two vocabularies.
            if M.is_label_passthrough(a["expr"]):
                n_labels += 1
                # A REFUSAL IS A DECISION AND IT NAMES ITS SUBJECT. "refused 115" tells
                # nobody WHICH aggregate was refused, so a person who expected a metric and
                # cannot find it has nothing to look up.
                ctx.decide("measure", f"{sh['shape_id']}:{a['expr'][:80]}", "refused",
                           because="an aggregate wrapping one bare column is a label carried "
                                   "through a GROUP BY, not a measurement",
                           evidence={"expr": a["expr"], "shape_id": sh["shape_id"],
                                     "alias": a.get("alias"), "fn": a.get("fn")})
                continue
            entity = S.source_table_of(a["expr"], amap, dialect, sh["tables"],
                                       statement_sql=a.get("scope_sql") or sh.get("sql"))
            # A SCALAR SUBQUERY BRINGS ITS OWN POPULATION. Refused here rather than at D4b:
            # the gate is right to block a card whose entity is not a table its statement
            # reads, but by then the card exists and the whole layer stops. A measure that
            # counts another table's rows was never a measure over this entity.
            if M.is_foreign_population(a["expr"], entity):
                n_foreign += 1
                ctx.decide("measure", f"{sh['shape_id']}:{a['expr'][:80]}", "refused",
                           because=f"the formula is a scalar subquery over a different "
                                   f"physical table — it computes its own population, not "
                                   f"one over {entity}",
                           evidence={"expr": a["expr"], "entity": entity,
                                     "shape_id": sh["shape_id"], "alias": a.get("alias")})
                continue
            mid = M.measure_identity(a["expr"], entity)
            if mid not in by_id:
                # The birth of a measure: the formula and the entity that give it its
                # identity, which is the key a published metric carries for the rest of its life.
                ctx.decide("measure", mid, "derived",
                           because="one formula over one entity — the identity a published "
                                   "metric carries, so a later run revises this metric rather "
                                   "than replacing it",
                           evidence={"formula": M.normalise_formula(a["expr"]),
                                     "entity": entity, "expr": a["expr"],
                                     "aggregation_type": a.get("fn") or "",
                                     "shape_id": sh["shape_id"], "alias": a.get("alias")})
            rec = by_id.setdefault(mid, {
                "measure_id": mid,
                "formula": M.normalise_formula(a["expr"]),
                "entity": entity,
                "aggregation_type": a.get("fn") or "",
                "slices": set(), "shape_ids": [], "aliases": set(),
                "users": 0, "exec": 0,
            })
            rec["slices"].add(sh["slice"])
            if sh["shape_id"] not in rec["shape_ids"]:
                rec["shape_ids"].append(sh["shape_id"])
            if a.get("alias"):
                rec["aliases"].add(str(a["alias"]).strip())
            # THE MOST-ADOPTED STATEMENT SPEAKS FOR THE MEASURE, which is the rule already
            # settled for examples: popularity, ties to the most recent.
            # The FIRST statement leads until another outranks it. Comparing only with `>`
            # left a measure with no lead — and no `sql` — whenever every count was zero,
            # which is every row of a lakehouse source.
            if "sql" not in rec or (sh["users"], sh["exec"]) > (rec["users"], rec["exec"]):
                rec["users"], rec["exec"] = sh["users"], sh["exec"]
                rec["lead_shape_id"] = sh["shape_id"]
                rec["sql"] = sh["sql"]
                rec["tables"] = sh["tables"]

    out = []
    for mid, r in sorted(by_id.items()):
        r["slices"] = sorted(r["slices"])
        r["aliases"] = sorted(r["aliases"])[:8]
        r["n_shapes"] = len(r["shape_ids"])
        out.append(r)
    # Ordered by adoption so an authoring session that runs out of budget has described the
    # measures the estate actually uses, not an alphabetical prefix of them.
    out.sort(key=lambda r: (-r["users"], -r["exec"], r["measure_id"]))

    ctx.write(OUT, {"contract_version": "1.0", "dialect": dialect, "measures": out},
              "history_measures.schema.json")
    multi = sum(1 for r in out if r["n_shapes"] > 1)
    ctx.count(shapes=len(shapes), measures=len(out), parse_failed=n_parse_fail,
              labels_refused=n_labels, foreign_population_refused=n_foreign,
              measures_from_several_shapes=multi)
    ctx.say(f"h2: {len(shapes)} shape(s) -> {len(out)} measure(s). {multi} are computed by "
            f"more than one statement and are ONE card, not several — the consolidation is "
            f"the parser's, so a re-run produces the same count.")
    if n_labels:
        ctx.say(f"h2: refused {n_labels} aggregate(s) that wrap one bare text column — a "
                f"label carried through a GROUP BY, not a measure. Same rule as lane D.")
    if n_parse_fail:
        ctx.say(f"h2: {n_parse_fail} shape(s) did not parse and contributed no measure. "
                f"They are a corpus defect reported at h1, not an absence of measures.")


def main() -> None:
    stage.main(STAGE, "h2_measures.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
