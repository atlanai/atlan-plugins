#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""X2 — merge the proposals into ONE metric per thing the business measures.

WHY THIS IS NOT A GROUP-BY. Deduplication used `measure_identity(formula, entity)`: same
expression over same table means same measure. That is wrong in both directions, and neither
direction is fixable with a better key.

  * It FUSES. `COUNT(DISTINCT id)` over an accounts table was a success-plan eligible base in
    one statement and a book size per CSM in another. Identical formula, identical entity, two
    different metrics — published as one, under a name that described neither.
  * It SPLITS. The same metric written `COUNT(DISTINCT a.ID)` in one statement and
    `COUNT(DISTINCT acc.id)` in another is two measures to a key and one thing to a person.

The hard part is not spotting the duplicate. It is that two statements computing the same
metric rarely agree on their restrictions: one is scoped to a quarter, one to a segment, one
to neither. A merge that keeps every filter publishes a metric nobody can use; one that drops
them publishes a number that is not what either statement ran. So the filters SPLIT — the ones
without which the metric is a different metric stay mandatory, the rest become optional knobs
carrying the statements that wanted them.

And nothing is lost on the way. Every proposal is accounted for in exactly one merged metric,
naming what it contributed, so a reader holding a card can get back to the statements behind
it — including the ones whose dimension or filter is the only reason the card has it.

Rule 3: the agent holds no model key. `--emit-prompt` bundles, `--from` ingests and checks.

    x2_merge.py --run R --emit-prompt
    x2_merge.py --run R --from answers/*.json
"""
from __future__ import annotations

import glob
import json
import re
import pathlib
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import consistency as K  # noqa: E402
from metrics_agent.lib import declared as _declared  # noqa: E402
from metrics_agent.lib import bi_tags as _bi_tags  # noqa: E402
from metrics_agent.lib import slugs  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "x2_merge"
IN = "metric_proposals.json"
IN_LAYER = "verified_queries.json"
OUT = "merged_metrics.json"
PROMPT = "x2_prompt.json"

REQUIRED = ("name", "definition", "expression", "entity", "sources", "merge_reasoning")
OPTIONAL = ("aggregation", "grain", "unit", "mandatory_filters", "optional_filters",
            "dimensions")


def _extra(p) -> None:
    p.add_argument("--emit-prompt", action="store_true",
                   help="write the merge bundle and stop")
    p.add_argument("--from", dest="answers", nargs="*", default=None,
                   help="reviewed answer file(s); globs allowed")


def _emit(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "metric_proposals.schema.json")
    props = doc["proposals"]

    # THE STATEMENT TRAVELS WITH THE PROPOSAL. Whether two proposals are the SAME METRIC is
    # not answerable from their formulas — half the estate spells a metric `COUNT(*)` — and
    # it is barely answerable from a definition either, because a definition is one author's
    # summary. It is answerable from what the statements DO: what they join, what they
    # restrict to, what the analyst who wrote them was looking at. So each proposal carries
    # its statement's SQL and its authored question, and the merge is judged on those.
    layer = ctx.read(IN_LAYER, "verified_queries.schema.json")
    stmts = {o["vq_id"]: o for o in layer["objects"]}
    # D1's aggregates per statement (alias and expression): which of a statement's columns a
    # proposal reads, so its BI definitions are that column's. Advisory: absent is empty.
    aggs: dict[str, list] = {}
    mc = ctx.run / "metric_candidates.json"
    if mc.is_file():
        for c in json.loads(mc.read_text()).get("candidates") or []:
            aggs.setdefault(c.get("vq_id"), []).append(c)

    def _context(p: dict) -> dict:
        o = stmts.get(p["vq_id"]) or {}
        return {
            "vq_id": p["vq_id"],
            "statement_name": o.get("name"),
            "question": o.get("question"),
            "description": o.get("description"),
            "sql": o.get("sql"),
            "tables": o.get("tables") or [],
            "group_by": o.get("group_by") or [],
            # Compiled from a metric its BI model declares: that metric, by the model's name.
            **({"declared": _declared.brief(d)} if (d := _declared.of_statement(o)) else {}),
            # Where the BI tool defines the number this proposal reads, and how (a copy, an
            # implicit aggregate, a report's own measure, a KPI's target), with the model's own
            # metadata: context for choosing the name and description, never the SQL.
            **({"bi_definitions": defs} if (defs := _bi_tags.definitions(
                [(p["vq_id"], o)], {p["vq_id"]: _bi_tags.aliases_for(
                    [p.get("expression")], aggs.get(p["vq_id"]) or [])})) else {}),
            **({"conversion_quality": o["quality"]["grade"]} if o.get("quality") else {}),
        }

    # GROUPED BY ENTITY AS A READING ORDER, NOT AS A BOUNDARY. Proposals over the same table
    # are the likeliest duplicates and belong side by side — but a staging table and the mart
    # built on it are two spellings of one thing, so the skill says to merge across these
    # groups where that is what they are. Partitioning here would make that impossible.
    # Keyed case-insensitively: Snowflake folds unquoted names to upper case, and one author
    # writing `sales.marts.fct_opportunity` beside another's `SALES.MARTS.FCT_OPPORTUNITY` put one
    # table in two groups, so the same metric reached the merge twice (found live).
    by_entity: dict[str, list] = {}
    for p in props:
        by_entity.setdefault(str(p.get("entity") or "unknown").upper(), []).append(
            {**p, "statement": _context(p)})

    groups = [{"entity": e, "n": len(v), "proposals": v}
              for e, v in sorted(by_entity.items(), key=lambda kv: -len(kv[1]))]
    hints = same_number_hints(groups)

    artifacts.dump(ctx.run / PROMPT, {
        "contract_version": "1.0", "stage": STAGE,
        "instruction": "See skills/merge-metrics/SKILL.md. Return the merged metric "
                       "set. EVERY proposal_id must appear in exactly one metric's `sources` "
                       "— nothing dropped, nothing counted twice. Filters split into "
                       "mandatory (the metric is a different metric without them) and "
                       "optional (a knob, carrying which statements wanted it). Dimensions "
                       "union, carrying the same. Entity groups are a reading order, not a "
                       "boundary: merge across them where two tables are one thing. Each "
                       "proposal carries the `statement` it came from — read the SQL and the "
                       "question, not only the formula. Two metrics are the same metric when "
                       "the statements mean the same thing, never because they spell it the "
                       "same way.",
        "n_proposals": len(props),
        "fields": {"required": list(REQUIRED), "optional": list(OPTIONAL)},
        **({"same_number_hints": hints} if hints else {}),
        "groups": groups}, "x2_prompt.schema.json", indent=1)
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.count(proposals=len(props), entity_groups=len(groups))
    ctx.say(f"x2: wrote {PROMPT} — {len(props)} proposal(s) across {len(groups)} entity "
            f"group(s). Answer with the `merge-metrics` skill, then re-run with --from. "
            f"THE AGENT HOLDS NO MODEL KEY.")



def same_number_hints(groups: list[dict]) -> list[dict]:
    """ONE NUMBER, SEVERAL SPELLINGS: the proposals of the bundle's groups that are one metric to
    a reader of the card, each list with why (two metrics publishing one formula with the same
    mandatory filters are refused at ingest): a measure's copy, a format-string variant, an
    implicit aggregate of the column, a report's own measure, a calculation item beside its
    copied measure, the same measure on two visuals filtered differently (a visual's filter is a
    cut, an optional filter of the metric). Two keys, either enough, joined transitively: the
    same aggregate over the same table in the SQL, or the same formula in the same BI model (a
    report's own measure repeating a model measure, which one visual compiles beside time
    intelligence and another alone). A hint: the skill still reads what each statement means."""
    keyed: dict[tuple, list[str]] = {}
    why_of: dict[tuple, str] = {}
    order: list[str] = []
    for g in groups:
        for p in g["proposals"]:
            order.append(p["proposal_id"])
            key = _shape(p.get("expression") or "", p.get("entity") or "")
            if key[0] and not _reads_derived(p.get("expression") or "",
                                             (p.get("statement") or {}).get("sql") or ""):
                keyed.setdefault(("sql",) + key, []).append(p["proposal_id"])
                why_of[("sql",) + key] = f"the same aggregate ({key[0][:80]}) over {key[1]}"
            defs = (p.get("statement") or {}).get("bi_definitions") or []
            items = (defs[0].get("calculation_items") or []) if len(defs) == 1 else []
            if len(defs) == 1 and defs[0].get("formula") and not any(
                    i.get("stacked") for i in items):
                # A calculation item applied to the measure is the item's number: keyed by
                # the item's formula beside the measure's.
                f = re.sub(r"\s+", "", str(defs[0]["formula"])).lower() + "".join(
                    "|" + re.sub(r"\s+", "", str(i.get("formula") or "")).lower()
                    for i in items)
                k = ("bi", str(defs[0].get("model") or ""), f)
                keyed.setdefault(k, []).append(p["proposal_id"])
                why_of[k] = f"the same formula in the BI model {defs[0].get('model')!r}"
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    for ids in keyed.values():
        for i in ids[1:]:
            if i != ids[0]:
                parent[find(i)] = find(ids[0])
    reasons: dict[str, set] = {}
    for k, ids in keyed.items():
        if len(set(ids)) > 1:
            reasons.setdefault(find(ids[0]), set()).add(why_of[k])
    joined: dict[str, list[str]] = {}
    for pid in dict.fromkeys(order):
        if pid in parent:
            joined.setdefault(find(pid), []).append(pid)
    return [{"proposals": ids,
             "why": "; ".join(sorted(reasons.get(root) or [])) + ": one number, however many "
                    "measures, visuals or reports spell it; a filter only some of its "
                    "statements apply is an optional filter"}
            for root, ids in sorted(joined.items(), key=lambda kv: kv[1][0]) if len(ids) > 1]


def _reads_derived(expr: str, sql: str) -> bool:
    """Whether the expression reads columns of a subquery of its statement (`MAX(u.s1)` over a
    statement that joins several parts on the visual's rows): placeholders that say nothing
    about the number, so two such expressions agreeing is no evidence they are one."""
    try:
        import sqlglot  # noqa: PLC0415
        from sqlglot import exp as _exp  # noqa: PLC0415
        quals = {c.table.lower() for c in sqlglot.parse_one(expr).find_all(_exp.Column)
                 if c.table}
        tree = sqlglot.parse_one(sql)
        derived = {str(s.alias).lower() for s in tree.find_all(_exp.Subquery) if s.alias}
    except Exception:  # noqa: BLE001 - unreadable text is compared as written
        return False
    return bool(quals & derived)


def _shape(expr: str, entity: str) -> tuple[str, str]:
    """The key the consistency check compares on: table aliases stripped, spacing flattened,
    and redundant parentheses dropped (the parsed expression printed again), so `SUM(t.x)`
    and `(SUM(t.x))` are one formula, as a reader of the card sees them. An expression that
    does not parse is compared as written."""
    text = str(expr or "")
    try:
        import sqlglot  # noqa: PLC0415
        from sqlglot import exp as _exp  # noqa: PLC0415
        tree = sqlglot.parse_one(text)
        def _drop(n):
            # Only a parenthesis that groups nothing: around the whole expression, a
            # function's argument, or a single value. One around an operation inside another
            # operation keeps its grouping.
            if isinstance(n, _exp.Paren) and (
                    not isinstance(n.parent, (_exp.Binary, _exp.Unary))
                    or isinstance(n.this, (_exp.Func, _exp.Column, _exp.Literal, _exp.Paren))):
                return n.this
            return n
        for _ in range(8):
            new = tree.transform(_drop)
            if new.sql() == tree.sql():
                break
            tree = new
        text = tree.sql()
    except Exception:  # noqa: BLE001 - not every authored expression is SQL a parser reads
        pass
    return (re.sub(r"\b[A-Za-z_]\w*\.", "", re.sub(r"\s+", " ", text)).strip().lower(),
            str(entity or "").rsplit(".", 1)[-1].lower())


def _agrees(proposal: dict, expr: str, entity: str, stmts: dict) -> bool:
    """True when this proposal's statement reads `entity` and this is the proposal's own
    expression.

    NOT A SECOND CONTAINMENT CHECK AGAINST THE SQL. X1 already proved every proposal's
    expression belongs to its statement — including the ones the parser PRINTED rather than
    copied, which differ from the source text in spacing, aliasing and case. Re-deriving that
    here from the raw SQL would reject the evidence the bundle itself handed over: measured,
    8 metrics whose entity every source reads and whose formula every source computes.
    """
    st = stmts.get(proposal.get("vq_id")) or {}
    tabs = {str(t).rsplit(".", 1)[-1].lower() for t in (st.get("tables") or [])}
    if entity.rsplit(".", 1)[-1].lower() not in tabs:
        return False
    sq = lambda t: re.sub(r"\s+", " ", str(t or "")).strip()
    return sq(proposal.get("expression")) == sq(expr)


def _as_source(s, props: dict):
    """A source in the object form the contract records.

    `"p1"` becomes `{"proposal_id": "p1", "vq_id": <its statement>}`, and an object that
    names a known proposal but not its statement has the statement filled in from the
    proposal. Anything else is returned as written, for the checks to refuse by name.
    """
    pid = s if isinstance(s, str) else s.get("proposal_id") if isinstance(s, dict) else None
    if not isinstance(pid, str) or pid not in props:
        return s
    if isinstance(s, str):
        return {"proposal_id": pid, "vq_id": props[pid]["vq_id"]}
    return s if s.get("vq_id") else {**s, "vq_id": props[pid]["vq_id"]}


def _ingest(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "metric_proposals.schema.json")
    props = {p["proposal_id"]: p for p in doc["proposals"]}
    # The statements themselves, so a merged metric can be checked against the SQL it claims
    # rather than against its own summary of it.
    stmts = {o["vq_id"]: o
             for o in ctx.read(IN_LAYER, "verified_queries.schema.json")["objects"]}

    paths: list[pathlib.Path] = []
    for pat in (ctx.args.answers or []):
        paths.extend(pathlib.Path(p) for p in sorted(glob.glob(pat)))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        ctx.gate("metric_merge", "have the proposals been merged?", "not yet",
                 options=["ingest", "not yet"],
                 evidence={"prompt": str(ctx.run / PROMPT)})
        ctx.entry.verdict = "blocked"
        raise SystemExit(
            f"x2: nothing to ingest. This run is WAITING on a person, not broken — "
            f"{ctx.run / PROMPT}\n  holds the proposals; answer it with the `merge-metrics` "
            f"skill and re-run.")

    metrics: list[dict] = []
    shapes: dict[tuple[str, str], list[str]] = {}
    problems: list[str] = []
    claimed: dict[str, str] = {}      # proposal_id -> metric name that claimed it
    names: dict[str, int] = {}

    for path in paths:
        ctx.authored(path, kind="authored_metric_merge")
        ctx.note_input_path(path)
        payload = json.loads(path.read_text())
        rows = payload.get("metrics") if isinstance(payload, dict) else payload
        for i, m in enumerate(rows or []):
            nm = str(m.get("name") or "").strip()
            where = f"{path.name} metric {i}" + (f" ({nm})" if nm else "")
            for f in REQUIRED:
                if f in ("sources",):
                    continue
                if not str(m.get(f) or "").strip():
                    problems.append(f"{where}: no `{f}`")
            srcs = m.get("sources") or []
            if not srcs:
                problems.append(f"{where}: no `sources` — a metric with no statement behind "
                                f"it is not merged, it is invented")
                continue
            # A NAME IS SPOKEN, NEVER AN IDENTIFIER (anything with an underscore in it).
            if re.search(r"[A-Za-z0-9]_+[A-Za-z0-9]", nm):
                problems.append(f"{where}: the name is an identifier. Name what it measures "
                                f"the way a person says it.")
            # A BARE PROPOSAL ID IS THE SAME CLAIM AS AN OBJECT NAMING IT, and it is held to
            # the same checks. Every rule below used to read only the object form, so a
            # source written as `"p1"` skipped the expression and the triple checks and was
            # then refused for the wrong reason, or not at all. Each one is normalised to
            # the object the contract records, its statement taken from the proposal.
            srcs = [_as_source(s, props) for s in srcs]
            m = {**m, "sources": srcs}
            for s in srcs:
                pid = s.get("proposal_id") if isinstance(s, dict) else s
                # A SOURCE IS A PROPOSAL AND THE STATEMENT IT CAME FROM, quoted verbatim.
                # A `vq_id` that is not the proposal's own points a reader at a statement
                # that never computed this number.
                if (isinstance(s, dict) and isinstance(pid, str) and pid in props
                        and s.get("vq_id") and s["vq_id"] != props[pid].get("vq_id")):
                    problems.append(
                        f"{where}: source {pid} names vq_id {s['vq_id']!r}, but that "
                        f"proposal came from {props[pid].get('vq_id')!r}. Copy both verbatim.")
                if not isinstance(pid, str):
                    problems.append(f"{where}: source {s!r} names no proposal_id")
                elif pid not in props:
                    problems.append(f"{where}: unknown proposal_id {pid!r}")
                elif pid in claimed:
                    problems.append(f"{where}: proposal {pid} already merged into "
                                    f"{claimed[pid]!r}. A proposal belongs to exactly one "
                                    f"metric — two claims means one of them is wrong.")
                else:
                    claimed[pid] = nm or where
            # `from` TRACES A KNOB OR A CUT BACK TO THE STATEMENT THAT WANTED IT, so it can only
            # name one of this metric's own sources — by vq_id or by proposal_id. Naming any
            # other statement sends a reader to SQL that does not compute this number.
            own = {str(s.get(k)) for s in srcs if isinstance(s, dict)
                   for k in ("proposal_id", "vq_id") if s.get(k)}
            for field, label in (("optional_filters", "filter"), ("dimensions", "dimension")):
                for item in (m.get(field) or []):
                    if not isinstance(item, dict):
                        continue
                    stray = [str(f) for f in (item.get("from") or []) if str(f) not in own]
                    if stray:
                        problems.append(
                            f"{where}: {label} {item.get(label)!r} is traced `from` "
                            f"{', '.join(stray[:3])}, which is not one of this metric's "
                            f"sources.")
            # EACH DECLARED METRIC IS ITS OWN METRIC. A model that declares two metrics
            # publishes two; merging them publishes one number under two definitions. The same
            # declaration twice (two models declaring one formula over one table) is one.
            decls = {}
            for s in srcs:
                if isinstance(s, dict) and s.get("proposal_id") in props:
                    d = _declared.of_statement(stmts.get(props[s["proposal_id"]]["vq_id"]))
                    if d:
                        decls.setdefault(_declared.identity(d), []).append(_declared.key_of(d))
            if len(decls) > 1:
                problems.append(
                    f"{where}: merges {len(decls)} different declared metrics "
                    f"({', '.join(sorted(k for ks in decls.values() for k in ks))}). Each "
                    f"metric a model declares is its own: give each its own merged metric.")
            # THE CANONICAL EXPRESSION MUST BE ONE THE SOURCES ACTUALLY RAN.
            expr = str(m.get("expression") or "").strip()
            src_exprs = {str(props[s.get("proposal_id")].get("expression") or "").strip()
                         for s in srcs if isinstance(s, dict)
                         and s.get("proposal_id") in props}
            if expr and src_exprs and expr not in src_exprs:
                problems.append(
                    f"{where}: the canonical expression is not one any source proposed. "
                    f"Pick one of the statements' own; do not synthesise a third.")
            # A LITERAL IS NOT AN EXPRESSION. Picking the shortest spelling a source offered
            # can land on a constant a statement used as a default; `0` is not a formula, and
            # a card carrying one says the metric computes nothing.
            if expr and not re.search(r"[A-Za-z_]\w*\s*\(", expr) \
                    and len(re.sub(r"\W", "", expr)) < 3:
                problems.append(
                    f"{where}: the canonical expression {expr!r} is a literal, not a formula.")

            # THE TRIPLE MUST AGREE WITH ITSELF. A formula, the table it runs over and the
            # statements it is cited to are ONE claim. Choosing each by majority separately
            # produces a card whose formula is absent from its own SQL — which reads as a
            # metric over a table that does not compute it.
            ent = str(m.get("entity") or "").strip()
            if expr and ent:
                agrees = [s.get("proposal_id") for s in srcs if isinstance(s, dict)
                          and s.get("proposal_id") in props
                          and _agrees(props[s["proposal_id"]], expr, ent, stmts)]
                if not agrees:
                    problems.append(
                        f"{where}: no source both reads {ent!r} and contains this expression. "
                        f"The formula, its table and the statement behind it are one claim — "
                        f"take all three from the same source.")

            # THE AGGREGATION MUST BE WHAT THE FORMULA DOES.
            agg = str(m.get("aggregation") or "").strip().lower()
            if agg in ("sum", "count", "count_distinct", "min", "max", "avg"):
                head = "count" if agg.startswith("count") else agg
                if not re.search(rf"\w*{head}\w*\s*\(",
                                 re.sub(r"\b[A-Za-z_]\w*\.", "", expr).lower()):
                    problems.append(
                        f"{where}: declares {agg!r} but the expression calls no {head}().")

            # TWO METRICS PUBLISHING ONE FORMULA OVER ONE TABLE ARE INDISTINGUISHABLE ON THE
            # CARD, whatever their names say. The key strips table aliases, because that is
            # what the consistency check compares and a difference it cannot see is no
            # difference at all.
            # ...UNLESS WHAT THEY COUNT DIFFERS. The mandatory filters are part of a metric's
            # identity, exactly as the consistency check now reads them.
            # ...AND UNLESS THE FORMULA IS A PLACEHOLDER. A statement that joins several parts
            # on the visual's rows reads each number as `MAX(u.s1)` of a subquery: two such
            # formulas agreeing says nothing about the numbers (seen live: a calculation
            # item's YTD and a copied prior-year measure), so they are not compared here.
            placeholder = any(
                _reads_derived(expr, (stmts.get(s.get("vq_id")) or {}).get("sql") or "")
                for s in srcs if isinstance(s, dict))
            if not placeholder:
                shapes.setdefault(_shape(expr, ent) + (K.identity_filters(m),), []).append(
                    nm or where)
            names[nm.lower()] = names.get(nm.lower(), 0) + 1
            metrics.append({
                "metric_id": slugs.artifact_name(nm) if nm else f"metric-{i}",
                **{k: m.get(k) for k in REQUIRED + OPTIONAL if m.get(k) is not None},
            })

    for key, who in shapes.items():
        if len(who) > 1 and key[0]:
            problems.append(
                f"{', '.join(who)} publish the same formula over the same table, "
                f"restricted the same way "
                f"({key[0][:50]!r} over {key[1]}) — a reader cannot tell them apart. Give "
                f"each the spelling that names what it counts, or merge them.")

    for nm, ct in names.items():
        if ct > 1:
            problems.append(f"the name {nm!r} is used by {ct} merged metrics — if they are "
                            f"the same thing they are one metric, and if they are not they "
                            f"need different names")

    # NOTHING MAY GO MISSING. A proposal nobody claimed is a metric the estate measures and
    # the layer does not publish, and it is silent: the merged set still looks complete.
    orphans = [pid for pid in props if pid not in claimed]
    if orphans:
        problems.append(
            f"{len(orphans)} proposal(s) were not merged into any metric — "
            f"{', '.join(orphans[:6])}. Every proposal belongs somewhere, even if that is a "
            f"metric of its own.")

    if problems:
        raise SystemExit("x2: the merge failed validation:\n  " + "\n  ".join(problems[:40]))

    n_multi = sum(1 for m in metrics if len(m.get("sources") or []) > 1)
    ctx.write(OUT, {"contract_version": "1.0",
                    "dialect": doc.get("dialect"),
                    "layer_sha": doc.get("layer_sha"),
                    "n_proposals_in": len(props),
                    "n_metrics_out": len(metrics),
                    "metrics": metrics}, "merged_metrics.schema.json")
    ctx.count(proposals_in=len(props), metrics_out=len(metrics), merged=n_multi)
    ctx.say(f"x2: {len(props)} proposal(s) -> {len(metrics)} metric(s); {n_multi} of them "
            f"drew on more than one statement. Every proposal is accounted for.")


def body(ctx: stage.Ctx) -> None:
    if ctx.args.emit_prompt:
        _emit(ctx)
    elif ctx.args.answers:
        _ingest(ctx)
    else:
        raise SystemExit("x2: pass --emit-prompt to author, or --from to ingest")


def main() -> None:
    stage.main(STAGE, "x2_merge.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
