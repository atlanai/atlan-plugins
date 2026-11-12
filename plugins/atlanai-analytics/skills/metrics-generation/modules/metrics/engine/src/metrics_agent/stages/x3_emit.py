#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""X3 — author the metric card, then project it. A HUMAN GATE, not a transform.

WHY THIS IS NOT DETERMINISTIC ANY MORE. The card is what a person and a model both read to
decide whether a number means what they want. The old X3 copied fields across from the merge:
`description` became the formula followed by a list of the questions the source statements
asked, `domain` became the warehouse schema the tables happen to live in, `population` echoed
the definition, `filters` restated the prose, and `joins` and `dimensions` were left empty
because nothing had read the SQL to find them. Every field was true and none of it helped.

A card is a reading of the query. Reading is the one thing a projection cannot do, so this
stage does what M2, X1 and X2 do: it bundles everything an author needs — the merged metric,
and for EVERY source the full statement with its authored question and description — refuses
to continue, and ingests the answer. Rule 3: the agent holds no model key.

    x3_emit.py --run R --emit-prompt            # bundle the merged metrics and stop
    x3_emit.py --run R --emit-prompt --name N   # bundle one, to settle the contract first
    x3_emit.py --run R --from 'answers/*.json'  # validate and project onto the card layer
"""
from __future__ import annotations

import glob
import json
import pathlib
import re
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import declared as _declared  # noqa: E402
from metrics_agent.lib import dialect as _dialect
from metrics_agent.lib import dimensions as dims  # noqa: E402
from metrics_agent.lib import card_sql as _card_sql  # noqa: E402
from metrics_agent import plug_runtime as _runtime  # noqa: E402
from metrics_agent.lib import sqlshape  # noqa: E402
from metrics_agent.lib import measures, slugs, stage  # noqa: E402
from metrics_agent.lib import quality as _quality  # noqa: E402
from metrics_agent.lib import bi_tags as _bi_tags  # noqa: E402

STAGE = "x3_emit"
IN_MERGED = "merged_metrics.json"
IN_LAYER = "verified_queries.json"
IN_PROPOSALS = "metric_proposals.json"
IN_CANDS = "metric_candidates.json"
IN_REVISIONS = "metric_revisions.json"
PROMPT = "x3_prompt.json"
REPAIR = "x3_repair.json"
OUT = "metric_cards.json"

# The fields the author writes. Everything else on the card is bookkeeping this stage fills in.
# These are the Registry's metric vocabulary. Outside them the gateway publishes the
# artifact and records a `draft_vocabulary` warning against it, which never clears.
AGGREGATIONS = ("sum", "count", "count_distinct", "min", "max", "ratio", "custom")
DATATYPES = ("integer", "decimal", "string", "boolean", "date", "timestamp")

REQUIRED = ("name", "description", "domain", "formula", "population",
            "aggregation", "output", "source")
# `grain` may be empty: "if nothing in the statement shows what makes a row unique, leave it
# empty" (the skill), and the registry holds it as optional. Requiring a value forced the
# author to invent a key.
OPTIONAL = ("grain", "exclusions", "filters", "mandatory_filters", "joins", "dimensions",
            "examples", "depends_on", "unit", "notes")


def _bracket_depth(sql: str, pos: int) -> int:
    """How many brackets are open at `pos` in `sql`, not counting any inside a quoted string."""
    depth, quote = 0, ""
    for ch in sql[:pos]:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
    return depth

def _evidence(m: dict, sources: list[dict], stmts: dict, dialect: str,
              bi_rel: list[dict], cands: dict, columns=None, *, max_values: int) -> dict:
    """The joins and dimension observations the source statements evidence.

    Advisory, and never blocking: a statement the parser cannot read contributes nothing and
    the author still has its SQL in full. `declare_joins` and `build` are the same functions
    R1 uses to emit a record's dimensions, so what the author is shown is what the registry
    would accept.
    """
    entity = str(m.get("entity") or "")
    # The LAYER objects, not the trimmed copies in the bundle: `build` reads observed values
    # and provenance off them, and a dimension published without the values the estate was
    # seen filtering on is a dimension a consumer cannot use.
    parents = {s["vq_id"]: stmts[s["vq_id"]] for s in sources if s["vq_id"] in stmts}
    try:
        joins, alias_to_join = dims.declare_joins(
            parents.values(), dialect, cardinality_of=dims.cardinality_of(bi_rel),
            entity=entity)
        # THE CANDIDATE, NOT THE NAME. `build` reads two things off each member: the SQL
        # alias of the aggregate, so a projection beside it is not mistaken for a dimension,
        # and `parameter_filters` — D1's name for a predicate whose VALUE varies across the
        # statements. That second one is the whole `filtered` role: a column the estate was
        # seen asking about two ways is a slicer, and it is the only role that arrives with
        # its values already known. Matched on the expression, alias-blind.
        want = _squash_expr(m.get("expression"))
        members = []
        for v in parents:
            mine = [c for c in cands.get(v, []) if _squash_expr(c.get("expr")) == want] \
                or cands.get(v, [])
            members.extend({"vq_id": v, "alias": c.get("alias"),
                            "parameter_filters": c.get("parameter_filters") or []}
                           for c in mine) or members.append({"vq_id": v, "alias": ""})
        declared, report = dims.build(
            members, parents, entity=entity, dialect=dialect,
            alias_to_join=alias_to_join,
            columns=columns, max_values=max_values,
            observed_by_column=dims.observed_by_column(parents.values()))
    except Exception as exc:  # the bundle is never worth failing the stage over
        return {"joins": [], "dimensions": [], "note": f"not derivable: {exc}"}

    # QUALIFIED BEFORE IT IS HANDED OVER. `build` spells a dimension the way its parent
    # statement did — `m.channel`, `j1.department_name` — and the card contract refuses a
    # query alias outside `examples`. Handing the author something the gate would reject
    # invites them to copy it across and be refused for it, so the aliases are resolved here:
    # the metric's own to the entity, each join's to the table it joins to.
    to_table = {j["name"]: j["to"] for j in joins}
    rename = {"m": entity, **{a: to_table.get(n, n) for a, n in alias_to_join.items()}}
    # And every alias the parent statements themselves declare: `declare_joins` rewrites the
    # entity's alias to `m`, but a statement that aliases a joined table `t` keeps it, and the
    # ON clause carries it into the declared join.
    for o in parents.values():
        try:
            rename.update({a: t for a, t in
                           sqlshape.alias_map(sqlshape.parse(o["sql"], dialect)).items()
                           if a not in rename})
        except Exception:
            continue

    def qualify(text: str) -> str:
        return re.sub(r"\b([A-Za-z_]\w*)\.(?=\w)",
                      lambda mo: f"{rename.get(mo.group(1), mo.group(1))}.", str(text or ""))

    # WHAT CANNOT BE QUALIFIED IS NOT OFFERED. An alias this statement never declared — a
    # compiled body naming a table the layer does not carry — leaves a reference the card gate
    # refuses. Handing the author a join they would be refused for copying is worse than
    # handing them nothing: they have the SQL in full either way, and now they also have a
    # note saying which ones the parse could not settle.
    withheld = []
    kept_joins, kept_dims = [], []
    for j in joins:
        j["sql"] = qualify(j["sql"])
        (kept_joins if not _unqualified(j["sql"]) else withheld).append(
            j if not _unqualified(j["sql"]) else f"join {j['name']}")
    keep_names = {j["name"] for j in kept_joins}
    for d in declared:
        d["sql"] = qualify(d["sql"])
        # The parse's own workings — `_raw_sql`, `_source_table`, `_bare_column`, `_caption`.
        # `_roles` stays: it is WHY this column looked like a dimension, which is the one
        # thing the author cannot read off the SQL at a glance.
        for k in [k for k in d if k.startswith("_") and k != "_roles"]:
            d.pop(k)
        if _unqualified(d["sql"]):
            withheld.append(f"dimension {d['name']}")
        elif d.get("required_join") and d["required_join"] not in keep_names:
            withheld.append(f"dimension {d['name']} (its join could not be qualified)")
        else:
            kept_dims.append(d)
    return {"joins": kept_joins, "dimensions": kept_dims,
            "not_qualifiable": withheld,
            # What the parse could and could not resolve, for the card's `dimension_report`.
            # Not shown to the author (the bundle drops it): it is a record, not evidence.
            "report": report,
            "note": report.get("note") or "",
            "how_to_read": "Evidence, not a draft. A column in the GROUP BY only to print a "
                           "name beside the number is not a dimension; decide from what the "
                           "query is FOR."}


def _emit(ctx: stage.Ctx) -> None:
    merged = ctx.read(IN_MERGED, "merged_metrics.schema.json")
    stmts = {o["vq_id"]: o for o in ctx.read(IN_LAYER, "verified_queries.schema.json")["objects"]}
    props = {p["proposal_id"]: p
             for p in ctx.read(IN_PROPOSALS, "metric_proposals.schema.json")["proposals"]}
    cands = _candidates_by_vq(ctx)
    columns = _catalogue(ctx, stmts)
    max_values = ctx.policy.get("metrics.dimensions.max_values_per_dimension")

    dialect, bi_rel = _dialect_and_relationships(merged, stmts)
    want = (ctx.args.name or "").strip().lower()
    metrics = [m for m in merged["metrics"]
               if not want or m["name"].strip().lower() == want]
    if want and not metrics:
        raise SystemExit(f"x3: no merged metric named {ctx.args.name!r}. Nothing to bundle.")

    tasks = []
    for m in metrics:
        seen, sources = set(), []
        for s in m.get("sources") or []:
            vq = s.get("vq_id")
            o = stmts.get(vq)
            if not o or vq in seen:
                continue
            seen.add(vq)
            p = props.get(s.get("proposal_id")) or {}
            sources.append({
                "vq_id": vq,
                # THE AUTHORED PROSE IS THE POINT OF CARRYING THE STATEMENT. The question and
                # description were written by somebody who had read this SQL; they say what the
                # query is FOR, which the SQL cannot.
                "statement_name": o.get("name"),
                "question": o.get("question"),
                "description": o.get("description"),
                "sql": o.get("sql"),
                "tables": o.get("tables") or [],
                "group_by": o.get("group_by") or [],
                "proposed_as": s.get("name_as_proposed"),
                "why_a_metric": p.get("why_a_metric"),
                "proposed_definition": p.get("definition"),
                "proposed_expression": p.get("expression"),
                "proposed_mandatory_filters": p.get("mandatory_filters") or [],
                "proposed_optional_filters": p.get("optional_filters") or [],
                "proposed_dimensions": p.get("dimensions") or [],
                # Where the BI tool defines the number and the model's own metadata about it
                # (context for the description and the name; it never changes the SQL), and how
                # close this statement is to the BI tool's number.
                **({"bi_definitions": defs} if (defs := _bi_tags.definitions(
                    [(vq, o)], {vq: _bi_tags.aliases_for(
                        [p.get("expression"), m.get("expression")], cands.get(vq) or [])}))
                   else {}),
                **({"conversion_quality": {k: o["quality"].get(k) for k in ("grade", "reason")}}
                   if o.get("quality") else {}),
            })
        # WHAT THE MODEL DECLARES about this metric, when a source statement was compiled from
        # a declared metric: its formula, entity, joins, dimensions (with their comments and
        # synonyms), grain and examples. Shown whole, so nothing the model says is lost on the
        # way to the card; X3 also completes the card from it, so none of it is retyped.
        declared = _declared_sources(m, stmts)
        tasks.append({
            "metric_id": m.get("metric_id"),
            "name": m["name"],
            **({"declared": declared[0]} if declared else {}),
            "merged": {k: m.get(k) for k in
                       ("definition", "expression", "entity", "aggregation", "grain", "unit",
                        "mandatory_filters", "optional_filters", "dimensions",
                        "merge_reasoning")},
            "n_sources": len(sources),
            "sources": sources,
            # WHAT THE SQL ITSELF EVIDENCES, PARSED. Not a draft of the answer — the author
            # decides what is a dimension and what is a label carried through a GROUP BY. But
            # the parse knows things that are tedious and error-prone to do by eye: which
            # joins exist and what each one is keyed on, which column a grouping expression
            # resolves to through its aliases and CTEs, and which values were observed in it.
            # Handing that over is the difference between the author reading SQL and the
            # author transcribing it.
            "evidence": {k: v for k, v in
                         _evidence(m, sources, stmts, dialect, bi_rel, cands, columns,
                                   max_values=max_values).items()
                         if k != "report"},
        })

    doc = {
        "contract_version": "1.0",
        "stage": STAGE,
        "instruction": (
            "See skills/author-metric-card/SKILL.md. Author one card per metric. "
            "READ THE SQL — every field is a reading of what the query does, not a restatement "
            "of the merge. Outside `examples`, every column reference is fully qualified "
            "against its real table: `DB.SCHEMA.TABLE.COLUMN`, never a query alias. `examples` "
            "keep the verified SQL exactly as it ran."),
        "fields": {"required": list(REQUIRED), "optional": list(OPTIONAL)},
        "n_metrics": len(tasks),
        "tasks": tasks,
    }
    # The bundle is a question, not an artifact of the layer — same as X1 and X2.
    artifacts.dump(ctx.run / PROMPT, doc, "x3_prompt.schema.json", indent=1, newline=False)
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.count(metrics=len(tasks), sources=sum(t["n_sources"] for t in tasks))
    ctx.say(f"x3: wrote {PROMPT} — {len(tasks)} metric(s) carrying "
            f"{sum(t['n_sources'] for t in tasks)} statement(s) in full. Answer it with the "
            f"`author-metric-card` skill, then re-run with --from. "
            f"THE AGENT HOLDS NO MODEL KEY.")


# A query alias is a letter or two and a dot: `m.amount`, `pb.CONTACT_ID`, `f.value`.
# A qualified reference has at least db.schema.table before the column.
# Not after a hyphen, `@` or `/`: `atlan-hq.co`, `ops@acme.io` and `/docs.v2` are domains,
# addresses and paths, never an alias and a column.
_ALIASED = re.compile(r"(?<![\w.\-@/])([A-Za-z_]\w{0,3})\.([A-Za-z_]\w*)")
# A quoted string is a value. `'atlan.co'` inside a filter is a literal domain, not `atlan.co`
# the column; a double-quoted or backticked identifier stays, since it IS a reference.
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
# SQL IS SYNTAX, NOT VOCABULARY. `where`, `from`, `having` and `join` are ordinary English —
# "Accounts where somebody wrote back" is a description, not a query. Matching the bare words
# rejects the sentences this field exists to hold. What is actually SQL: a function call, a
# qualified column reference, or the SELECT…FROM sequence itself.
_SQL_SHAPE = re.compile(
    r"(?is)\b(count|sum|avg|min|max|iff|coalesce|case\s+when|datediff|nullif|round)\s*\("
    r"|\bselect\b.{0,200}?\bfrom\b"
    r"|\b\w+\.\w+\.\w+\b")


def _technical(name: str) -> bool:
    """A handle rather than something somebody would say: snake_case, or a bare lower-case
    token that is just the column's own name."""
    n = str(name or "").strip()
    return bool(n) and ("_" in n or (n.islower() and " " not in n))


def vq_ids_of(m: dict) -> set[str]:
    """The statements this metric was merged from."""
    return {s.get("vq_id") for s in (m.get("sources") or []) if s.get("vq_id")}


def _strip_subqueries(sql: str) -> str:
    """The statement with every parenthesised block removed.

    A GROUP BY inside a subquery or a CTE is part of HOW the measure is computed — reducing a
    history table to one row per account before counting, say — and refusing it would refuse
    most correct metrics. What the rule is about is the OUTER grouping, which is the consumer's
    to choose.
    """
    out, depth = [], 0
    for ch in str(sql or ""):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _n_projections(select_list: str) -> int:
    """How many columns the SELECT returns, counting only top-level commas.

    `ROUND(SUM(x) / NULLIF(y, 0), 1)` is one column with three commas inside it, so a naive
    split reads a ratio as a four-column projection and refuses every ratio on the estate.
    """
    depth = n = 1
    for ch in str(select_list or ""):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 1:
            n += 1
    return n


def _without_subqueries(sql: str) -> str:
    """`sql` with every bracketed SELECT or WITH replaced by `(…)`."""
    out, i = [], 0
    while i < len(sql):
        if sql[i] == "(" and re.match(r"\(\s*(select|with)\b", sql[i:], re.I):
            depth = 0
            for j in range(i, len(sql)):
                depth += {"(": 1, ")": -1}.get(sql[j], 0)
                if depth == 0:
                    break
            out.append("(…)")
            i = j + 1
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def _unqualified(text: str) -> list[str]:
    """Column references still wearing a query alias instead of their table."""
    out = []
    # Blank every single-quoted literal to the same length, so offsets still line up.
    text = _STRING_LITERAL.sub(lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", str(text or ""))
    for m in _ALIASED.finditer(text):
        head, col = m.group(1), m.group(2)
        # A fully-qualified reference has dots before it; an alias does not.
        start = m.start(1)
        if start > 0 and str(text)[start - 1] == ".":
            continue
        # AND DOTS AFTER IT. `RAW.SALES.ORDERS.ID` opens with a short database name, which is
        # the head of a qualified reference, not an alias: an alias names a column and stops.
        if str(text)[m.end():m.end() + 1] == ".":
            continue
        if head.lower() in ("db", "no", "e", "g"):
            continue
        out.append(f"{head}.{col}")
    return sorted(set(out))


def _table_names(tables) -> set[str]:
    """Tables lower-cased, by every name SQL resolves them by: the full name and its trailing
    SCHEMA.TABLE (`site.projects` for SITE.SITE.PROJECTS)."""
    full = {str(t).lower() for t in tables}
    return full | {".".join(t.split(".")[-2:]) for t in full if t.count(".") >= 2}


def _statement_tables(metric: dict, stmts: dict) -> set[str]:
    """The tables the metric's own statements read, by every name SQL resolves them by."""
    return _table_names(t for s in (metric.get("sources") or []) if isinstance(s, dict)
                        for t in ((stmts.get(s.get("vq_id")) or {}).get("tables") or []))


_MARKER = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}")
_DATE_LITERAL = re.compile(r"(?i)(?:\bDATE\s*)?'\d{4}-\d{2}-\d{2}(?:[ T][0-9:.]+)?'")
_IDENT_PART = r'(?:[A-Za-z_][\w$]*|"[^"]+"|`[^`]+`|\[[^\]]+\])'
_BARE_COLUMN = re.compile(rf"^{_IDENT_PART}(?:\.{_IDENT_PART})*$")


def _compiler_field_problems(a: dict, atype: str, dialect: str) -> list[str]:
    """The refusals the author-metric-card skill promises for the fields the compiler reads
    ("Your card is compiled into SQL"): the aggregation agrees with the formula, every
    `{{parameter}}` is declared and every declaration is used, no date literal in a mandatory
    filter, a time dimension on a bare column, and `grain` is a key rather than a sentence."""
    out: list[str] = []
    formula = str(a.get("formula") or "")

    # `aggregation.type` MUST AGREE WITH `formula`. A count_distinct over a formula with no
    # DISTINCT re-aggregates as a plain count, and a type that claims to aggregate over a
    # formula that aggregates nothing tells the compiler to roll up a value that is not one.
    if formula and atype == "count_distinct" and "distinct" not in formula.lower():
        out.append(f"aggregation.type is count_distinct but the formula {formula[:60]!r} has no "
                   f"DISTINCT. Say what the formula does: `count`, or COUNT(DISTINCT …).")
    elif formula and atype in ("sum", "count", "count_distinct", "min", "max"):
        try:
            import sqlglot
            from sqlglot import exp
            tree = sqlglot.parse_one(formula, read=dialect)
            aggregates = tree is not None and any(True for _ in tree.find_all(exp.AggFunc))
        except Exception:        # an unparseable formula is reported by the fragment check
            aggregates = True
        if not aggregates:
            out.append(f"aggregation.type is {atype} but the formula {formula[:60]!r} "
                       f"aggregates nothing. A value aggregated upstream is `custom`.")

    # EVERY `{{name}}` IS DECLARED, AND EVERY DECLARATION IS USED. The second is the one that
    # lies: it advertises a control that changes nothing when it is set.
    src = a.get("source") or {}
    declared = [str(p.get("name") or "") for p in (src.get("parameters") or [])
                if isinstance(p, dict)]
    scan = {k: v for k, v in a.items() if k not in ("examples", "source")}
    scan["source"] = {k: v for k, v in src.items() if k != "parameters"}
    used = set(_MARKER.findall(json.dumps(scan)))
    for name in sorted(used - set(declared)):
        out.append(f"{{{{{name}}}}} is used but not declared in source.parameters.")
    for name in [n for n in declared if n not in used]:
        out.append(f"parameter {name!r} is declared but no expression uses {{{{{name}}}}}. A "
                   f"declaration nothing reads is a control that changes nothing.")

    # NO DATE LITERALS. `>= '2026-04-22'` was right the day it was written and narrows every
    # day after; the date is a parameter's default.
    for k, f in enumerate(a.get("mandatory_filters") or []):
        hit = _DATE_LITERAL.search(str(f))
        if hit:
            out.append(f"mandatory_filters[{k}] carries the date literal {hit.group(0)}. "
                       f"Declare a parameter with that date as its default.")

    # A TIME DIMENSION IS A BARE COLUMN; the compiler writes the DATE_TRUNC at any level.
    for d in (a.get("dimensions") or []):
        if isinstance(d, dict) and str(d.get("kind") or "").lower() == "time":
            sql = str(d.get("sql") or "").strip()
            if sql and not _BARE_COLUMN.match(sql):
                out.append(f"time dimension {d.get('name')!r} is {sql[:60]!r}, not a bare "
                           f"column. Declare the date column; the level is the consumer's.")

    # `grain` IS THE KEY of the base table — fully qualified columns, comma-separated — or
    # empty for unknown. A sentence describes the output, which the field does not mean.
    grain = str(a.get("grain") or "").strip()
    if grain:
        bad = [g.strip() for g in grain.split(",")
               if not (_BARE_COLUMN.match(g.strip()) and "." in g)]
        if bad:
            out.append(f"grain {grain[:60]!r} is not a key. Name the fully qualified column(s) "
                       f"that make one row of the base table unique, or leave it empty.")
    return out


def _ingest(ctx: stage.Ctx) -> None:
    merged = ctx.read(IN_MERGED, "merged_metrics.schema.json")
    by_name = {m["name"].strip().lower(): m for m in merged["metrics"]}
    stmts = {o["vq_id"]: o for o in ctx.read(IN_LAYER, "verified_queries.schema.json")["objects"]}
    # THE IDENTITY IS SCOPED TO THE RUN. A measure_id is only (name, table), so two estates that
    # publish the same name over the same table would otherwise share one artifact, and a push
    # would append one estate's definition onto the other's.
    run_inputs = ctx.read("run_inputs.json", "run_inputs.schema.json")
    scope = run_inputs["scope"]
    dialect, bi_rel = _dialect_and_relationships(merged, stmts)
    # Every table the run is bounded to is a table, not an alias, by any name SQL resolves it by:
    # a card may join one its own statements did not read (a dimension's table).
    estate_tables = _table_names((run_inputs.get("bounded_tables") or {}).get("tables") or [])
    cands = _candidates_by_vq(ctx)
    columns = _catalogue(ctx, stmts)
    max_values = ctx.policy.get("metrics.dimensions.max_values_per_dimension")
    # Each proposal's own expression: which of its statement's columns a card's BI definitions
    # are read from. Advisory: absent is empty.
    pp = ctx.run / IN_PROPOSALS
    expr_of = ({p["proposal_id"]: p.get("expression")
                for p in json.loads(pp.read_text()).get("proposals") or []}
               if pp.is_file() else {})

    paths: list[pathlib.Path] = []
    for pat in (ctx.args.answers or []):
        paths.extend(pathlib.Path(p) for p in sorted(glob.glob(pat)))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        ctx.gate("metric_cards", "have the cards been authored?", "not yet",
                 options=["ingest", "not yet"], evidence={"prompt": str(ctx.run / PROMPT)})
        ctx.entry.verdict = "blocked"
        raise SystemExit(
            f"x3: nothing to ingest. This run is WAITING on a person, not broken — "
            f"{ctx.run / PROMPT}\n  holds the merged metrics; answer it with the "
            f"`author-metric-card` skill and re-run.")

    cards, problems, seen, lead_named = [], [], {}, []
    faults: dict[str, list[dict]] = {}       # card -> what is wrong with it
    authored: dict[str, dict] = {}           # card -> exactly what was written
    declared_of: dict[str, dict] = {}        # card -> the declaration it was completed from
    keys_of: dict[str, set] = {}             # card -> the declared metrics it carries
    completed: dict[str, int] = {}
    for path in paths:
        ctx.authored(path, kind="authored_metric_cards")
        ctx.note_input_path(path)
        payload = json.loads(path.read_text())
        # THE SHAPE FIRST, THEN THE READING. `authored_metric_cards.schema.json` mirrors the
        # registry's own metric contract, which is `deny_unknown_fields` all the way down: an
        # extra key anywhere fails typed deserialisation before a single gateway rule runs and
        # comes back as a bare 422 naming no field. Checking it here names the field and the
        # card instead, and it happens before the readings below so a malformed file does not
        # produce forty confusing complaints about one structural mistake.
        try:
            artifacts.validate(payload, "authored_metric_cards.schema.json",
                               what=f"authored cards in {path.name}")
        except artifacts.ArtifactError as exc:
            problems.append(str(exc))
            continue
        for i, a in enumerate(payload.get("metrics") or []):
            nm = str(a.get("name") or "").strip()
            where = f"{path.name} card {i} ({nm or 'unnamed'})"
            key = nm.lower()
            if key not in by_name:
                problems.append(f"{where}: no merged metric by that name")
                continue
            if key in seen:
                problems.append(f"{where}: authored twice")
                continue
            seen[key] = where
            authored[nm] = a
            m = by_name[key]

            # ── A METRIC ITS MODEL DECLARES IS HELD TO THE DECLARATION ───────────
            # A source statement compiled from a declared metric carries what the model
            # declares (provenance.bi.declared). The card computes exactly that — the
            # declared formula over the declared entity — and is then COMPLETED from it, so
            # every check below runs on the card as it will be published. Keyed on the
            # declaration, never on a vendor.
            decls = _declared_sources(m, stmts)
            if len({_declared.identity(d) for d in decls}) > 1:
                problems.append(
                    f"{where}: carries {len(decls)} declared metrics "
                    f"({', '.join(sorted(_declared.key_of(d) for d in decls))}). Each metric a "
                    f"model declares is its own metric: X2 must give each its own merged "
                    f"metric.")
            elif decls:
                problems.extend(f"{where}: {x}" for x in _declared.held_to(a, decls[0]))
                a, got = _declared.complete(a, decls[0], dialect=dialect)
                for k, v in got.items():
                    completed[k] = completed.get(k, 0) + v
                problems.extend(f"{where}: {x}" for x in _declared.unreachable(a))
                declared_of[nm] = decls[0]
                keys_of[nm] = {_declared.key_of(d) for d in decls}

            for f in REQUIRED:
                if a.get(f) in (None, "", [], {}):
                    problems.append(f"{where}: no `{f}`")

            # A DESCRIPTION IS A SENTENCE, NOT THE FORMULA AND NOT A LIST OF QUESTIONS.
            # `description` is the third retrieval tier; spending it on SQL or on the questions
            # the source statements happened to ask puts the words somebody would type out of
            # reach and tells a reader nothing the other fields do not already say.
            desc = str(a.get("description") or "")
            hit = _SQL_SHAPE.search(desc)
            if hit:
                problems.append(f"{where}: the description contains SQL ({hit.group(0)[:40]!r}). "
                                f"Say what the number means in business words; the formula has "
                                f"its own field.")
            if desc.count("?") > 1:
                problems.append(f"{where}: the description is a list of questions. Write one "
                                f"description of the metric.")

            # A BUSINESS DOMAIN IS NOT A WAREHOUSE SCHEMA.
            dom = str(a.get("domain") or "")
            # An all-caps word is a schema name (`MARTS`, `GTM_OPS`) unless it is a short
            # acronym the business says as letters (`GTM`, `HR`, `IT`, `CS`, `R&D`).
            acronym = dom.isupper() and " " not in dom and len(dom.replace("&", "")) <= 4
            if "." in dom or "_" in dom or (dom.isupper() and not acronym):
                problems.append(f"{where}: domain {dom!r} is a technical path. Name the part of "
                                f"the business this metric belongs to.")

            # AN ALIAS IS MEANINGLESS AWAY FROM THE QUERY THAT DECLARED IT. Outside `examples`,
            # every column reference names its own table, or a reader cannot tell what
            # `m.amount` is without the SQL that is no longer beside it.
            # A DERIVED TABLE SCOPES ITS OWN ALIASES. `source.sql` may hold one ("in brackets
            # and aliased"), and the aliases inside it name nothing outside it; only the text
            # around it is the card's own and must be qualified.
            # THE NAME IS NOT SCANNED. It was fixed at the merge and is spoken, not SQL; a name
            # can carry a dotted brand (`Wins Against acme.io`), which the alias pattern reads
            # as `acme.io` and which the card cannot rename (found live).
            scan_card = {k: v for k, v in _authored_words(a, authored[nm]).items()
                         if k not in ("examples", "name", "synonyms")}
            if isinstance(scan_card.get("source"), dict) and scan_card["source"].get("sql"):
                scan_card = {**scan_card, "source": {**scan_card["source"],
                             "sql": _without_subqueries(str(scan_card["source"]["sql"]))}}
            scan = json.dumps(scan_card)
            # A NAME THE CARD'S OWN FROM CLAUSE GIVES A DERIVED TABLE is declared on the card,
            # not borrowed from a query: a table that plays two roles is written that way, and
            # its role name is the only thing that tells the two apart.
            derived = _derived_aliases(str(_source_of(a).get("sql") or ""))
            # A TABLE THE CARD'S OWN STATEMENTS READ IS NOT AN ALIAS either, in any dialect: a
            # two-part name they read (`site.projects`) is a table, not an alias and its column.
            known = _statement_tables(m, stmts) | estate_tables
            loose = [x for x in _unqualified(scan) if x.split(".", 1)[0].lower() not in derived
                     and x.lower() not in known]
            if loose:
                problems.append(f"{where}: {len(loose)} reference(s) still wear a query alias — "
                                f"{', '.join(loose[:6])}. Qualify them: DB.SCHEMA.TABLE.COLUMN.")

            agg = a.get("aggregation")
            atype = str((agg or {}).get("type") if isinstance(agg, dict) else agg or "").lower()
            if atype not in AGGREGATIONS:
                problems.append(
                    f"{where}: aggregation {atype!r} is outside the card contract's vocabulary "
                    f"({', '.join(AGGREGATIONS)}). A value read off a row that was aggregated "
                    f"upstream is 'custom'.")
            dtype = str((a.get("output") or {}).get("datatype") or "").lower()
            if dtype not in DATATYPES:
                problems.append(
                    f"{where}: output.datatype {dtype!r} is outside the card contract's vocabulary "
                    f"({', '.join(DATATYPES)}). Say what the number IS — a count is an "
                    f"integer, a rate is a decimal.")

            src = a.get("source") or {}
            # THE SOURCE IS A RUNNABLE QUERY, AND THE MAIN TABLE IS `entity`.
            # The contract allows exactly one of `table` and `sql`, and declaring the table
            # spends that slot on something `entity` already says — leaving the card with no
            # query at all. A consumer composing SQL needs a statement it can run and adapt,
            # so `sql` is the one that is declared and `table` stays absent.
            q = str(src.get("sql") or "").strip()
            # `source.sql` IS THE FROM CLAUSE. The registry's `Source` is "a table or SQL
            # source" and takes exactly one of `table` and `sql`, so this is the text after
            # FROM — not a worked example of the query. It is OPTIONAL: a metric over one
            # plain table says so in `source.entity` and leaves this empty.
            if str(src.get("table") or "").strip():
                problems.append(
                    f"{where}: source.table is set. The contract allows exactly one of table "
                    f"and sql; the main table belongs in source.entity and this slot holds the "
                    f"FROM clause when the source is more than one table.")
            if q and re.match(r"(?is)^\s*select\b", q):
                problems.append(
                    f"{where}: source.sql starts with SELECT. It is the FROM clause, not a "
                    f"query — it is the text after `FROM `, not a statement. A derived table "
                    f"is fine, in brackets and aliased.")
            if q and str(src.get("entity") or "").rsplit(".", 1)[-1].lower() not in q.lower():
                problems.append(f"{where}: source.sql does not read source.entity "
                                f"{src.get('entity')!r}.")
            # AN ALIAS ON A NAMED TABLE MAKES EVERY QUALIFIED REFERENCE UNRESOLVABLE. The
            # card's expressions name tables in full, and a warehouse drops the original
            # name the moment the table is aliased. A derived table is the exception: it
            # has no name of its own, so an alias is the only way to refer to it.
            # ONLY IN FROM/JOIN POSITION. An alias in a derived table's SELECT list names
            # a column and is not only fine but necessary; it was the first thing this
            # check wrongly flagged.
            # `source.sql` IS THE FROM CLAUSE, so its first table has no FROM in front of it;
            # matching only after FROM/JOIN left the base table's alias unchecked, and the
            # base table is the one every formula on the card names.
            # ONLY AT THE TOP LEVEL. Inside a derived table — the two-pass measure the skill
            # sends here — an alias is that subquery's own business: nothing outside the
            # brackets can name it, so it cannot make a reference on the card unresolvable.
            for m_ in re.finditer(r"(?:^\s*|\bFROM\s+|\bJOIN\s+)"
                                  r"([A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+)\s+"
                                  r"(?:AS\s+)?([A-Za-z_]\w*)", q or "", re.I):
                if _bracket_depth(q, m_.start(1)):
                    continue
                if m_.group(2).lower() not in ("on", "inner", "left", "right", "full",
                                               "cross", "natural", "join", "where", "group",
                                               "order", "qualify", "using", "and", "or"):
                    problems.append(
                        f"{where}: source.sql aliases {m_.group(1)} as {m_.group(2)!r}. "
                        f"Name tables in full here — the card's own expressions are fully "
                        f"qualified and an alias makes them unresolvable.")
                    break

            # AND THE NAME CARRIES NO GROUPING EITHER. `Open Opportunities by Stage` is
            # `Open Opportunities`; `CSM Renewal ARR` is `Renewal ARR`. A name that names its
            # own cut cannot be found by somebody asking for the measure, and it multiplies:
            # the same measure by owner, by quarter and by segment become four cards nobody
            # can reconcile.
            dim_words = {w.lower() for d in (a.get("dimensions") or [])
                         if isinstance(d, dict) for w in str(d.get("name") or "").split()}
            nm_words = [w for w in re.findall(r"[A-Za-z][\w-]*", nm)]
            lead = nm_words[0].lower() if nm_words else ""
            # `by X` AND `per X` ARE NOT THE SAME CLAIM, and refusing both on the keyword
            # refuses correct metrics. Measured on this estate: `Open Opportunities by Stage`
            # names a cut — Stage is one of its own declared dimensions. `Account Assets
            # Updated by Automation` does not: Automation is not a dimension of that card, it
            # is WHAT IS COUNTED. And `Contacts Worked per Account` is a ratio whose
            # denominator is accounts; `per Account` is its unit, not a grouping.
            #
            # So the test is whether the qualifier names one of THIS card's dimensions, and
            # `per` on a ratio is left alone.
            qual = re.search(r"(?i)\b(by|per)\s+(.+)$", nm)
            if qual:
                word, tail = qual.group(1).lower(), qual.group(2).strip().lower()
                is_ratio = atype in ("ratio", "custom")
                names_a_dim = any(tail == d or tail in d or d in tail
                                  for d in {str(x.get("name") or "").strip().lower()
                                            for x in (a.get("dimensions") or [])
                                            if isinstance(x, dict)} if d)
                # A RATIO'S DENOMINATOR IS ALSO A DIMENSION, and that is not a cut.
                # `MCP Requests per User` is requests divided by users; `User` is declared as
                # a dimension too, because you can also group by it. Refusing the name on that
                # basis renames the ratio to `MCP Requests`, which is a different metric that
                # already exists on this estate.
                if names_a_dim and not (word == "per" and is_ratio):
                    problems.append(
                        f"{where}: the name says how the measure is cut — {tail!r} is one of "
                        f"this card's own dimensions. Name the measure and let the consumer "
                        f"choose the cut.")
                elif word == "by" and not is_ratio:
                    # AND `by` ON ITS OWN IS NOT EVIDENCE OF A CUT. The comment above names
                    # `Account Assets Updated by Automation` as the counter-example and then
                    # this branch refused it anyway: Automation is not a dimension of that
                    # card, it is WHAT IS COUNTED. The checkable test is the one above —
                    # does the qualifier name one of this card's own dimensions — and when
                    # it does not, the rest is a judgement, so it is reported.
                    lead_named.append(f"{nm} — reads as a cut, though {tail!r} is not one of "
                                      f"its dimensions")
            elif lead and lead in dim_words and len(nm_words) > 1:
                # A QUALIFIER THAT DISTINGUISHES TWO REAL METRICS IS NOT A CUT. `Campaign S1
                # Opportunities` counts LIST_FINAL_PROGRESSION.ID and `Event S1 Opportunities`
                # counts FCT_CAMPAIGN_MEMBER.OPPORTUNITY_ID — different tables,
                # different columns, two metrics whose leading word names the SOURCE. Strip it
                # from both and the layer publishes one name for two numbers, which is the
                # failure this whole rule exists to prevent.
                # Two metrics collide when their names strip to the SAME thing — which is
                # checked across the whole merged layer, not against the names as they stand.
                # Neither `Campaign S1 Opportunities` nor `Event S1 Opportunities` is called
                # `S1 Opportunities` today; the point is that both would be.
                # AND MOST LEADING WORDS ARE NEITHER — they name the entity the measure is
                # ABOUT. `Account Credits Burned` is credits burned by an account; `Agent
                # Escalation Rate` is the rate at which an agent escalates. The entity is
                # also a perfectly good dimension, so the test fires — and on this estate it
                # fired on 62 of 125 cards, which is not a handful of bad names, it is the
                # estate's dominant naming convention. Refusing it would force `Credits
                # Burned` and `Escalation Rate`, names that say less and collide with each
                # other across domains.
                #
                # The failure this rule exists for — a name that carries its own cut — is
                # caught above by `by`/`per`, which is explicit and checkable. What is left
                # here is a judgement no rule can make from the card alone, so it is reported
                # and a person decides, exactly like the restriction advisory below.
                stripped = " ".join(nm_words[1:]).lower()
                if stripped not in _ambiguous_when_stripped(by_name):
                    lead_named.append(f"{nm} — leads with {lead!r}, also a dimension")

            # A PLACEHOLDER IS A TEMPLATE, NOT A QUERY. `:campaign_name` cannot be run and
            # cannot be checked; a consumer receives something it must first fill in from
            # knowledge the card does not carry. Bind the values the statements actually used —
            # `parameters` still declares which of them are meant to be swapped.
            ph = re.findall(r"(?<!:):[A-Za-z_]\w*|\?(?=\s|,|\)|$)|\$\{[^}]+\}", q)
            if ph:
                problems.append(
                    f"{where}: source.sql carries {len(ph)} placeholder(s) — "
                    f"{', '.join(sorted(set(ph))[:4])}. It must run as written. Bind the values "
                    f"the source statements used; `parameters` says which are swappable.")
            # THE CARD'S SQL MUST COMPOSE INTO ONE PURE READ. `formula`, `source.sql` and the
            # mandatory filters are concatenated by every consumer into one statement (the
            # glossary's canonical statement is exactly that), so a `; DELETE …` riding in any
            # of them is published as part of the metric's definition. The words an author
            # writes are data from a source the agent does not control (guardrail 9): the same
            # allowlist the warehouse gate applies, applied to what the card would run.
            # AND EACH FRAGMENT MUST STAND ON ITS OWN. The composed check alone is beaten by
            # a comment opened in `formula` and closed in `source.sql`: the glued statement
            # reads clean while `source.sql` by itself still carries the write, and a consumer
            # that uses that field alone runs it. A fragment holds no comment, no `;`, and
            # leaves no string open for the next field to close.
            frags = [("formula", a.get("formula")), ("source.sql", src.get("sql"))]
            frags += [(f"mandatory_filters[{k}]", f)
                      for k, f in enumerate(a.get("mandatory_filters") or [])]
            frags += [(f"join {j.get('name')!r} sql", j.get("sql"))
                      for j in (a.get("joins") or []) if isinstance(j, dict)]
            frags += [(f"dimension {d.get('name')!r} sql", d.get("sql"))
                      for d in (a.get("dimensions") or []) if isinstance(d, dict)]
            for label, frag in frags:
                why = sqlshape.fragment_escape(frag, dialect) if isinstance(frag, str) else None
                if why:
                    problems.append(
                        f"{where}: {label} {why}. Each SQL field of a card is a fragment that "
                        f"must stand on its own; a comment or a separator can carry a second "
                        f"statement into whatever the fields are joined into.")
            composed = _card_sql.canonical_statement(
                {"formula": a.get("formula"), "source": src, "name": nm,
                 "mandatory_filters": a.get("mandatory_filters")})
            if composed and sqlshape.has_dml(composed, dialect):
                problems.append(
                    f"{where}: formula, source.sql and mandatory_filters do not compose into "
                    f"one statement that only reads. A card defines a number; it never "
                    f"carries a second statement or a write.")
            if composed:
                problems.extend(f"{where}: {problem}." for problem in
                                _card_sql.aggregation_problems(composed, dialect))
            # A MANDATORY FILTER ON A JOINED TABLE MAKES THAT JOIN MANDATORY. An inner join
            # filters, and a predicate on a left-joined table in the WHERE turns it into one.
            # Either way the rows the aggregate sees depend on it, so a query that omits the
            # join returns a different number from the one the card describes.
            for f in (a.get("mandatory_filters") or []):
                for tbl in re.findall(r"\b([A-Za-z_]\w*\.[A-Za-z_]\w*\.[A-Za-z_]\w*)\.",
                                      str(f)):
                    if q and tbl.lower() not in q.lower():
                        problems.append(
                            f"{where}: mandatory filter reads {tbl}, which source.sql never "
                            f"joins to. A filter that restricts the population must be in the "
                            f"query, with the join that reaches it.")
            problems.extend(f"{where}: {p}" for p in _compiler_field_problems(a, atype, dialect))
            al = str(src.get("alias") or "")
            if _technical(al):
                problems.append(f"{where}: source.alias {al!r} is a technical handle. "
                                f"Name it the way somebody would say it.")

            jnames = [str(j.get("name") or "") for j in (a.get("joins") or [])]
            if len(jnames) != len(set(jnames)) or any(not n.strip() for n in jnames):
                problems.append(f"{where}: join names must be present and unique.")
            for j in (a.get("joins") or []):
                miss = [f for f in ("name", "to", "sql", "cardinality", "origin")
                        if not str(j.get(f) or "").strip()]
                if miss:
                    problems.append(f"{where}: join {j.get('name')!r} has no {', '.join(miss)} — "
                                    f"the card contract refuses a join missing any of them.")

            # A DIMENSION NAME IS READ BY THE PERSON CHOOSING A BREAKDOWN, and by a model
            # composing one. `account_cohort_at_event` is the column; "Account Cohort at Event"
            # is what somebody asks for. The card carries the second and keeps the first in
            # `sql`, where it belongs.
            for d in (a.get("dimensions") or []):
                if _technical(d.get("name")):
                    problems.append(
                        f"{where}: dimension {d.get('name')!r} is a technical handle. Give it "
                        f"the name somebody would use to ask for that breakdown; the column "
                        f"stays in its `sql`.")
            dnames = [str(d.get("name") or "") for d in (a.get("dimensions") or [])]
            if len(dnames) != len(set(dnames)) or any(not n.strip() for n in dnames):
                problems.append(f"{where}: dimension names must be present and unique.")
            for d in (a.get("dimensions") or []):
                miss = [f for f in ("name", "kind", "sql", "datatype")
                        if not str(d.get(f) or "").strip()]
                if miss:
                    problems.append(f"{where}: dimension {d.get('name')!r} has no "
                                    f"{', '.join(miss)}.")
                rj = d.get("required_join")
                if rj and rj not in jnames:
                    problems.append(f"{where}: dimension {d.get('name')!r} needs join {rj!r}, "
                                    f"which is not declared.")

            # AN EXAMPLE IS THE STATEMENT, NOT A RETELLING OF IT. The author names which
            # statements are examples and what each one groups by; the SQL and the question
            # are TAKEN from the verified layer, never written here. Both already exist and
            # were already reviewed — re-typing 13KB of SQL or rewording a question that was
            # authored upstream can only introduce drift, and drift in an example is a claim
            # that somebody ran something they did not.
            for e in (a.get("examples") or []):
                if str(e.get("source") or "").startswith(_declared.EXAMPLE_PREFIX):
                    # The model's own example: held to the declaration above, carried whole.
                    for g in (e.get("group_by") or []):
                        if g not in dnames:
                            problems.append(f"{where}: example group_by {g!r} is not a "
                                            f"declared dimension — the card contract refuses "
                                            f"the reference.")
                    continue
                vq = str(e.get("source") or "").replace("verified_query:", "")
                if vq not in stmts:
                    problems.append(f"{where}: example names {vq!r}, which is not a verified "
                                    f"statement.")
                    continue
                if vq not in vq_ids_of(m):
                    problems.append(f"{where}: example {vq} is not one of this metric's "
                                    f"sources — an example must be a statement that computes it.")
                for field in ("sql", "question"):
                    if e.get(field) is not None and str(e[field]).strip() != str(
                            stmts[vq].get(field) or "").strip():
                        problems.append(
                            f"{where}: example {vq} supplies its own `{field}`, which differs "
                            f"from the verified statement. Do not write it — name the "
                            f"statement and the layer's own {field} is carried.")
                for g in (e.get("group_by") or []):
                    if g not in dnames:
                        problems.append(f"{where}: example group_by {g!r} is not a declared "
                                        f"dimension — the card contract refuses the reference.")

            cards.append(_project(a, m, stmts,
                                  _evidence_for(m, stmts, dialect, bi_rel, cands, columns,
                                                max_values=max_values), scope, dialect,
                                  policy=ctx.policy, cands=cands, expr_of=expr_of))

    # ── A NAME THAT HIDES A DEFINITIONAL RESTRICTION ────────────────────────
    # `Account Count` counts only `TYPE = 'Customer'`. Both the formula and the statement
    # say so; the name does not, and the name is what a consumer picks from a list. This
    # is ADVISORY rather than a refusal because it is a judgement each time: "Outreach
    # Touches" restricted to outbound activity is saying it already, while "Account Count"
    # restricted to customers is not — and no rule this stage can run tells the two apart.
    if lead_named:
        ctx.say(f"x3: ADVISORY — {len(lead_named)} card(s) lead with a word they also offer "
                f"as a dimension. That is how the estate names a measure after the entity it "
                f"is about; check none of them is a cut in disguise.")
        for u in lead_named[:8]:
            ctx.say(f"x3:   {u}")

    _lit = re.compile(r"\.([A-Z0-9_]+)\s*=\s*'([^']{2,40})'")
    _hygiene = re.compile(r"\b(IS_DELETED|IS_ACTIVE|IS_CLOSED|DELETED|ACTIVE)\b", re.I)
    unnamed = []
    for a in cards:
        nm_words = set(re.findall(r"[a-z]+", a.get("name", "").lower()))
        for f in a.get("mandatory_filters") or []:
            if not isinstance(f, str) or _hygiene.search(f):
                continue
            for col, lit in _lit.findall(f):
                lw = set(re.findall(r"[a-z]+", lit.lower()))
                if lw and not (lw & nm_words):
                    unnamed.append(f"{a['name']} — restricted to {col} = {lit!r}")
                    break
            else:
                continue
            break
    if unnamed:
        ctx.say(f"x3: ADVISORY — {len(unnamed)} card(s) carry a definitional restriction "
                f"their name does not mention. A consumer choosing from a list sees only "
                f"the name.")
        for u in unnamed[:8]:
            ctx.say(f"x3:   {u}")

    # ── TWO METRICS, ONE IDENTITY ───────────────────────────────────────────
    # `measure_id` is the metric: its table, its FROM clause, its measure and the
    # restrictions that define its population. Two cards that agree on all four are not two
    # metrics — whatever their names say — and publishing them mints one artifact that the
    # second silently overwrites. Caught here, while both are in front of an author.
    by_identity: dict[str, list[str]] = {}
    for a in cards:
        by_identity.setdefault(a["measure_id"], []).append(a["name"])
    for mid, names in sorted(by_identity.items()):
        if len(names) > 1:
            problems.append(
                f"{', '.join(sorted(names))}: these cards have the same identity — the same "
                f"entity, FROM clause, formula and mandatory filters. Either they are one "
                f"metric under two names, or what makes them different is missing from the "
                f"fields that define them.")

    # ── EVERY METRIC A MODEL DECLARES, ON EXACTLY ONE CARD ──────────────────
    # The set is read from the verified layer: a metric the reader hid has no statement, and
    # one A3 or the witness refused was refused by name where it happened. So this can always
    # be met — by X1 proposing each declared statement, X2 keeping it, X3 authoring it — and
    # a metric left off is named with the gate to answer.
    claimed: dict[str, list[str]] = {}
    for mm in merged["metrics"]:
        for v in vq_ids_of(mm):
            d = _declared.of_statement(stmts.get(v))
            if d:
                claimed.setdefault(_declared.key_of(d), []).append(mm["name"])
    on_cards: dict[str, list[str]] = {}
    for nm, ks in keys_of.items():
        for k in ks:
            on_cards.setdefault(k, []).append(nm)
    for vq, o in sorted(stmts.items()):
        d = _declared.of_statement(o)
        if not d:
            continue
        k = _declared.key_of(d)
        if k not in claimed:
            problems.append(f"declared metric {d['metric']!r} of {d['model']} reaches no card. "
                            f"Every metric a model declares is published: X1 proposes it from "
                            f"{vq}, and X2 keeps it.")
        elif len(set(claimed[k])) > 1 or len(on_cards.get(k, [])) > 1:
            names = sorted(set(claimed[k]) | set(on_cards.get(k, [])))
            problems.append(f"declared metric {d['metric']!r} of {d['model']} is on "
                            f"{len(names)} metrics ({', '.join(names)}); it must be on exactly "
                            f"one.")

    unanswered = [n for n in by_name if n not in seen]

    # ── A REFUSAL AN AUTHOR CAN ACT ON, NOT JUST A TRACEBACK ────────────────
    # Every problem above is attributed to its card and written out, so the stage that
    # refused can be answered without a person in the middle. `x3_loop.py` reads this,
    # hands it to the author, and re-runs — the human was never the thing checking the
    # card, only the thing carrying the message.
    for msg in problems:
        hit = next((n for n in authored if f"({n})" in msg or msg.startswith(f"{n}:")), None)
        if hit:
            _fault(faults, hit, "", msg.split(": ", 1)[-1] if ": " in msg else msg)
    if problems or faults:
        rep = ctx.run / REPAIR
        attributed = {f["problem"] for fs in faults.values() for f in fs}
        loose = [m for m in problems
                 if m.split(": ", 1)[-1] not in attributed and m not in attributed]
        artifacts.dump(rep, _repair_bundle(faults, authored, by_name, loose,
                                           [str(x) for x in paths]),
                       "x3_repair.schema.json", indent=1)
        ctx.say(f"x3: refused — {len(faults)} card(s) named, {len(loose)} problem(s) about "
                f"the file itself. {rep} carries both.")
        lines = list(problems[:40]) + [
            f"{nm}: {f['field']}: {f['problem']}" if f["field"] else f"{nm}: {f['problem']}"
            for nm, fs in sorted(faults.items()) for f in fs
            if f["problem"] not in {x.split(": ", 1)[-1] for x in problems}]
        raise SystemExit("x3: the authored cards failed validation:\n  "
                         + "\n  ".join(lines)
                         + f"\n  ({rep} carries this in a shape an author can act on)")
    if unanswered and ctx.args.partial:
        ctx.say(f"x3: PARTIAL — {len(cards)} card(s) projected, {len(unanswered)} still "
                f"unauthored. This layer is not publishable.")
    elif unanswered:
        ctx.gate("metric_cards", "every merged metric authored?",
                 f"{len(unanswered)} still open", options=["ingest", "not yet"],
                 evidence={"missing": sorted(unanswered)[:20]})
        ctx.entry.verdict = "blocked"
        raise SystemExit(
            f"x3: {len(unanswered)} of {len(by_name)} merged metric(s) have no card — "
            f"{', '.join(sorted(unanswered)[:6])}.\n  A metric the merge kept and the card "
            f"layer drops is a number the estate measures and the layer does not publish.")

    # THE MODEL NAMES ITS METRIC. A card built from a declared metric takes the declared
    # name, in words; the author's stays as a synonym. A name another card holds already is
    # never taken, compared as the registry compares it (`metric_ref`, case-folded).
    renamed = _name_by_declaration(cards, declared_of)
    if completed or renamed:
        ctx.count(declared_completed={**completed, **({"named": len(renamed)} if renamed
                                                       else {})})
        ctx.say("x3: completed from the declared models — "
                + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(completed.items()))
                + (f"; {len(renamed)} card(s) named as declared" if renamed else "") + ".")

    # A REVISION KEEPS ITS IDENTITY. The identity above is the measure, so changing a metric's
    # rule mints a new one, and publishing it would leave the old artifact beside a second.
    # An edit of a completed run pins the metric to the measure it had before
    # (`metric_revisions.json`, metric_ref -> measure_id), so the change revises that artifact.
    pins = _revisions(ctx)
    for c in cards:
        if c["metric_ref"] in pins:
            c["measure_id"] = pins[c["metric_ref"]]
            c["source_id"] = slugs.metric_source_id(scope, c["measure_id"])

    doc = {"contract_version": "1.0",
           # The registry's own contract version, not this stage's — the card layer declares
           # which metric spec it was written against.
           "spec_version": "1.0-draft.1",
           "dialect": dialect,
           "metric_refs_by_vq": {v: [c["metric_ref"] for c in cards
                                     if v in (c.get("derived_from") or [])]
                                 for c in cards for v in (c.get("derived_from") or [])},
           "metrics": cards}
    if unanswered and ctx.args.partial:
        # THE LAYER SAYS IT IS PARTIAL, NOT ONLY THE TERMINAL. A message printed once is gone
        # by the time a later stage reads the file; the marker travels with the cards, and
        # every stage that builds something to publish (r1b, g1) refuses a layer carrying it.
        doc["partial"] = {"unauthored": sorted(by_name[n]["name"] for n in unanswered),
                          "n_authored": len(cards), "n_merged": len(by_name)}
    ctx.write(OUT, doc, "metric_spec.schema.json")
    # A repair bundle from an earlier refusal describes cards that are now accepted; left on
    # disk, a repair loop would read it as this round's refusal.
    (ctx.run / REPAIR).unlink(missing_ok=True)
    ctx.count(cards=len(cards), **({"unauthored": len(unanswered)} if "partial" in doc else {}),
              with_joins=sum(1 for c in cards if c.get("joins")),
              with_dimensions=sum(1 for c in cards if c.get("dimensions")))
    ctx.say(f"x3: {len(cards)} card(s) authored — "
            f"{sum(1 for c in cards if c.get('joins'))} declare a join, "
            f"{sum(1 for c in cards if c.get('dimensions'))} declare dimensions.")


def _revisions(ctx: stage.Ctx) -> dict[str, str]:
    """{metric_ref: measure_id} for each metric an edit revised; empty for an unedited run."""
    path = ctx.run / IN_REVISIONS
    if not path.is_file():
        return {}
    ctx.note_input_path(path)
    pins = json.loads(path.read_text()).get("metrics")
    if not isinstance(pins, dict) or not all(isinstance(v, str) and v for v in pins.values()):
        raise SystemExit(f"x3: {path} must map each revised metric_ref to its measure_id.")
    return pins


def _declared_sources(m: dict, stmts: dict) -> list[dict]:
    """The declarations the statements a merged metric was built from carry, each once."""
    out, seen = [], set()
    for s in m.get("sources") or []:
        d = _declared.of_statement(stmts.get(s.get("vq_id")))
        if d and _declared.key_of(d) not in seen:
            seen.add(_declared.key_of(d))
            out.append(d)
    return out


def _source_of(a: dict) -> dict:
    """A card's `source`, or an empty one when the author wrote something else there."""
    return a.get("source") if isinstance(a.get("source"), dict) else {}


def _derived_aliases(source_sql: str) -> set[str]:
    """Names a FROM clause gives its derived tables (`(SELECT …) name`), lower-cased."""
    flat = _without_subqueries(source_sql)
    return {m.group(1).lower()
            for m in re.finditer(r"\(…\)\s*(?:AS\s+)?([A-Za-z_]\w*)", flat, re.I)
            if m.group(1).lower() not in ("on", "where", "join", "left", "inner", "using")}


def _authored_words(card: dict, as_written: dict) -> dict:
    """The card with its PROSE as the author wrote it. A declared model's comments are data
    about the model, carried verbatim; the alias check is about what an author wrote, and must
    not refuse a card for a sentence in the model it cannot change."""
    if card is as_written:
        return card
    said = {d.get("name"): d.get("description") for d in as_written.get("dimensions") or []
            if isinstance(d, dict)}
    dims = [{**{k: v for k, v in d.items() if k not in ("description", "synonyms")},
             **({"description": said[d.get("name")]} if said.get(d.get("name")) else {})}
            for d in card.get("dimensions") or [] if isinstance(d, dict)]
    return {**card, "dimensions": dims, "notes": as_written.get("notes") or []}


def _name_by_declaration(cards: list[dict], declared_of: dict) -> dict[str, str]:
    """{author's name: declared name} for every card renamed as its model declares."""
    taken = {slugs.kebab(c["name"]) for c in cards}
    renamed: dict[str, str] = {}
    for c in cards:
        d = declared_of.get(c["name"])
        if not d:
            continue
        name = _declared.title(d["metric"])
        if name == c["name"] or slugs.kebab(name) in taken:
            continue
        taken.add(slugs.kebab(name))
        renamed[c["name"]] = name
        c["synonyms"] = sorted(set(c.get("synonyms") or []) | {c["name"]})
        c["name"] = c["display_name"] = name
        c["metric_ref"] = slugs.kebab(name)
    for c in cards:
        if c.get("depends_on"):
            c["depends_on"] = [renamed.get(x, x) if isinstance(x, str) else x
                               for x in c["depends_on"]]
    return renamed


def _dialect_and_relationships(merged: dict, stmts: dict) -> tuple[str, list[dict]]:
    """The dialect the layer was read in, and every relationship the BI model declares.

    Cardinality is the one field a SQL statement cannot answer, so it comes from the model
    that states it outright. Both halves of this stage derive them identically — the bundle
    the author reads and the projection that records what they chose have to agree.
    """
    return (_dialect.of(merged, "merged_metrics.json"),
            [r for o in stmts.values()
             for r in (((o.get("provenance") or {}).get("bi") or {}).get("relationships") or [])])


def _catalogue(ctx: stage.Ctx, stmts: dict):
    """The warehouse's own column metadata, for the datatypes and descriptions a dimension
    needs and SQL cannot supply.

    ALWAYS OPTIONAL (hard rule 3). An unreachable catalogue is missing infrastructure: it degrades
    to an empty catalogue, it never blocks, and no dimension is lost to it — the datatype is
    admitted as `unknown` instead, which is a true statement and a guess is not. WHAT WAS READ
    is recorded, because a live source can legitimately differ between two runs and that
    difference should be attributable rather than mysterious.
    """
    ri = None
    if ctx.policy.get("catalogue.enabled"):
        ri = json.loads((ctx.run / "run_inputs.json").read_text())
        ctx.entry.inputs.append(str(ctx.run / "run_inputs.json"))
    # SCOPED TO THE LAYER'S TABLES (and the run's bounded set): the lookup describes the
    # tables these statements read, not every column under the connection.
    tables = sorted({str(t) for o in stmts.values() for t in (o.get("tables") or []) if t})
    columns, note = _runtime.catalogue(
        ri, max_description_chars=ctx.policy.get("metrics.dimensions.max_description_chars"),
        tables=tables)
    ctx.say(f"x3: {note}.")
    ctx.entry.counts["catalogue"] = columns.manifest_record(note)
    return columns


def _candidates_by_vq(ctx: stage.Ctx) -> dict:
    """D1's candidates, grouped by the statement they were lifted from. Absent is empty: the
    evidence block is advisory and a missing D1 costs the author the filtered role, not the
    bundle."""
    path = ctx.run / IN_CANDS
    if not path.is_file():
        return {}
    out: dict = {}
    for c in json.loads(path.read_text()).get("candidates") or []:
        out.setdefault(c.get("vq_id"), []).append(c)
    return out


def _evidence_for(m: dict, stmts: dict, dialect: str, bi_rel: list[dict],
                  cands: dict, columns, *, max_values: int) -> dict:
    """The same parse the bundle showed the author, recomputed so the projection can attach
    WHY each dimension is one. Deterministic: same layer, same answer."""
    sources = [{"vq_id": s.get("vq_id"), "sql": (stmts.get(s.get("vq_id")) or {}).get("sql")}
               for s in (m.get("sources") or []) if s.get("vq_id") in stmts]
    return _evidence(m, sources, stmts, dialect, bi_rel, cands, columns, max_values=max_values)


def _fault(faults: dict, card: str, where: str, why: str, hint: str = "") -> None:
    """One problem, recorded against the card it belongs to.

    The stage's own `problems` list is prose for a person reading a traceback. This is the
    same fact in a shape an author can act on, which is what makes the repair loop possible
    at all — `x3_loop.py` hands these straight back to whoever wrote the card.
    """
    faults.setdefault(card, []).append(
        {"field": where, "problem": why, **({"hint": hint} if hint else {})})


def _repair_bundle(faults: dict, authored: dict, by_name: dict,
                   unattributed: list, files: list) -> dict:
    """Everything an author needs to FIX a card, and nothing about how it was made.

    Deliberately not the whole x3 prompt again: a repair is a narrow edit to a card that
    already exists, and re-sending 5.7MB of evidence invites a rewrite rather than a fix.
    The card as written, what is wrong with it, and the merged metric it answers.
    """
    return {
        "contract_version": "1.0",
        "stage": "x3_emit",
        "instruction": (
            "Each task below is a metric card that was REFUSED, with the exact field and "
            "reason. Fix the named fields and return the corrected cards in the same shape "
            "as the authored answers file: {\"metrics\": [ ...cards... ]}. Change nothing "
            "the faults do not name. See skills/author-metric-card/SKILL.md — the "
            "section 'Your card is compiled into SQL' says why each field holds SQL "
            "rather than prose."),
        "n_cards": len(faults),
        # A SCHEMA FAILURE BELONGS TO THE FILE, NOT TO A CARD — and it is checked first, so
        # on a malformed answers file there are no cards to attribute anything to. Dropping
        # those left the loop a repair bundle saying nothing is wrong, which is worse than
        # no bundle at all.
        "files": files,
        "unattributed": unattributed,
        "tasks": [{"name": nm,
                   "faults": fs,
                   "card_as_written": authored.get(nm),
                   "merged": by_name.get(nm.strip().lower())}
                  for nm, fs in sorted(faults.items())],
    }


def _project(a: dict, m: dict, stmts: dict, evidence: dict, scope: str,
             dialect: str, *, policy=None, cands: dict | None = None,
             expr_of: dict | None = None) -> dict:
    """The card: what the author wrote, plus identity and traceability.

    NOTHING ABOUT HOW IT WAS MADE. A consumer asks what the number is, how to compute it and
    which queries back it. `name_basis`, `canonical_lane`, `canonical_basis` and the merge's
    own account of itself answered none of those — they were the pipeline narrating its own
    process onto the artifact, and on a layer where every name is authored they said the same
    sentence 125 times. `derived_from` is traceability and stays; the diary does not.
    """
    vq_ids = list(dict.fromkeys(s.get("vq_id") for s in (m.get("sources") or [])
                                if s.get("vq_id") in stmts))
    # IDENTITY IS THE MEASURE, NOT ITS NAME. This used to be `digest(name, table)`, so
    # renaming a metric minted a SECOND artifact and left the first archived — and an
    # archived name stays reserved, so the old name could never be used again. It happened
    # once to this estate: every metric was renamed, 125 artifacts forked into two
    # generations, and the next push collided with 61 names nothing could see.
    #
    # ALL FOUR PARTS ARE NEEDED, and each was learned by leaving it out. `entity + formula`
    # alone gave 86 identities for 125 metrics: the eight catalogue coverage measures are
    # one MAX over one table and differ only in which JSON_KEY they count. Adding the
    # mandatory filters took it to 118; the rest differ only in their FROM clause — one
    # reads the stage history at S2 and another at S4.
    measure_id = slugs._digest(
        a["source"].get("entity") or "",
        " ".join(str(a["source"].get("sql") or "").split()),
        " ".join(str(a.get("formula") or "").split()),
        "|".join(sorted(" ".join(str(f).split())
                        for f in (a.get("mandatory_filters") or []))))
    agg = a["aggregation"]
    viewer = _viewer_binds([stmts.get(v) or {} for v in vq_ids])
    withheld = _withheld_binds([stmts.get(v) or {} for v in vq_ids])
    # HOW CLOSE THE NUMBER IS, from the statements it was merged from (never the author's), and
    # WHERE IT IS DEFINED: every BI definition behind it, with the model's own metadata.
    qual = (_quality.of_statements({v: (stmts.get(v) or {}).get("quality") for v in vq_ids},
                                   policy) if policy is not None else None)
    own: dict[str, list] = {}
    for s in m.get("sources") or []:
        if isinstance(s, dict) and s.get("vq_id"):
            own.setdefault(s["vq_id"], []).append((expr_of or {}).get(s.get("proposal_id")))
    bi_defs = _bi_tags.definitions(
        [(v, stmts.get(v) or {}) for v in vq_ids],
        {v: _bi_tags.aliases_for([m.get("expression")] + own.get(v, []),
                                 (cands or {}).get(v) or []) for v in vq_ids})
    lanes = [((stmts.get(v) or {}).get("provenance") or {}).get("lane") for v in vq_ids]
    body_lane = next((l for l in ("merged", "bi", "history") if l in set(lanes)), None)
    tier, tier_basis = measures.tier_of(body_lane, lanes)
    return {
        "metric_ref": slugs.kebab(a["name"]),
        "measure_id": measure_id,
        "source_id": slugs.metric_source_id(scope, measure_id),
        "name": a["name"],
        "display_name": a["name"],
        "description": a["description"],
        "domain": a["domain"],
        "formula": a["formula"],
        "sql": a["source"].get("sql"),
        "entity": a["source"].get("entity"),
        "alias": a["source"].get("alias"),
        "aggregation": agg if isinstance(agg, dict) else {"type": agg},
        "output": a["output"],
        "grain": a.get("grain") or "",
        "population": a["population"],
        "exclusions": a.get("exclusions") or [],
        "filters": a.get("filters") or [],
        "mandatory_filters": a.get("mandatory_filters") or [],
        "parameters": (a.get("source") or {}).get("parameters") or [],
        "timeframe_column": (a.get("source") or {}).get("timeframe_column"),
        "joins": a.get("joins") or [],
        # The dialect belongs to the run, not to the author: one warehouse produced these
        # statements and the author has no separate opinion about which.
        "dialect": dialect,
        # THE ROLE IS PROVENANCE, NOT A FIELD TO TYPE. The author decides what is a dimension;
        # the parse already knows WHY each one looked like one — grouped, filtered, projected,
        # a window partition, declared by the workbook — and r1b publishes that in extensions
        # so a consumer can tell a slicer from a column somebody grouped by once. Matched on
        # the SQL the author wrote against the SQL the parse produced.
        "dimensions": _with_roles(a.get("dimensions") or [], evidence),
        "dimension_report": evidence.get("report") or {},
        # CARRIED FROM THE VERIFIED LAYER, NOT FROM THE ANSWER. The author chose which
        # statements are examples; the statement supplies its own SQL and its own question.
        # A DECLARED example's SQL and question are the model's, carried by the completion.
        "examples": [dict(e) if str(e.get("source") or "").startswith(_declared.EXAMPLE_PREFIX)
                     else {**{k: v for k, v in e.items() if k not in ("sql", "question")},
                           "sql": (stmts.get(str(e.get("source") or "")
                                             .replace("verified_query:", "")) or {}).get("sql"),
                           "question": (stmts.get(str(e.get("source") or "")
                                                  .replace("verified_query:", "")) or {}
                                        ).get("question")}
                     for e in (a.get("examples") or [])],
        "depends_on": a.get("depends_on") or [],
        # Which statements this is computed from — checkable, not narration.
        "derived_from": vq_ids,
        "n_sources": len(vq_ids),
        # What the statements disagree about. Authored; not the merge talking about itself.
        # A metric filtered to the viewing user says so, from its statements, whatever the
        # author wrote.
        "gotchas": list(a.get("notes") or []) + ([
            "Filtered to the viewing user: computed from a statement that reads "
            + " and ".join(f"{b['placeholder']} (the viewing user's "
                           f"{b['of'].replace('_', ' ')})" for b in viewer["placeholders"])
            + "; each person who asks gets their own number. No name is stored with it."]
            if viewer else []) + ([
            "Computed from a statement that compares with a person's address or name the BI "
            "file holds, never kept here: bind "
            + " and ".join(b["placeholder"] for b in withheld["placeholders"])
            + " to the value the BI file's own formula or filter holds."]
            if withheld else []),
        **({"viewer_binds": viewer} if viewer else {}),
        **({"withheld_binds": withheld} if withheld else {}),
        **({"quality": qual} if qual else {}),
        **({"bi_definitions": bi_defs} if bi_defs else {}),
        # NAMED BY PROVENANCE, NEVER ASSERTED. An earlier version wrote "history_only" on
        # every card, which is true of an estate with no BI lane and a lie on any other. The
        # tier is the reader's first filter in the glossary, so it is computed from the lanes
        # of the statements this metric was actually mined from: the highest-precedence lane
        # among them is the body, and the rest are the bucket.
        "tier": tier,
        "tier_basis": tier_basis,
        "status": "",
        "provenance": {"source": "verified-queries", "derived_from": vq_ids},
        # The model's own other names for the metric: retrieval words, never interpreted.
        **({"synonyms": list(a["synonyms"])} if a.get("synonyms") else {}),
    }


def _viewer_binds(sources: list[dict]) -> dict | None:
    """The viewer placeholders a metric's statements read (their union), bindable only when
    every such statement was; None when none reads the viewing user."""
    marked = [s["viewer_binds"] for s in sources if s.get("viewer_binds")]
    if not marked:
        return None
    seen: dict[str, dict] = {}
    for vb in marked:
        for b in vb.get("placeholders") or []:
            seen.setdefault(b["placeholder"], {"of": b["of"], "placeholder": b["placeholder"]})
    return {"bindable": all(vb.get("bindable") for vb in marked),
            "placeholders": [seen[k] for k in sorted(seen)]}


def _withheld_binds(sources: list[dict]) -> dict | None:
    """The withheld-literal placeholders a metric's statements read (their union); None when
    none reads one."""
    seen: dict[str, dict] = {}
    for s in sources:
        for b in (s.get("withheld_binds") or {}).get("placeholders") or []:
            seen.setdefault(b["placeholder"], {"of": b["of"], "placeholder": b["placeholder"]})
    return {"placeholders": [seen[k] for k in sorted(seen)]} if seen else None


def _ambiguous_when_stripped(by_name: dict) -> set:
    """Names that two or more merged metrics would share once their leading word is dropped.

    Cached on the mapping's identity: this is asked once per card and the answer is a property
    of the layer.
    """
    key = id(by_name)
    hit = _STRIP_CACHE.get(key)
    if hit is None:
        seen, dup = {}, set()
        for n in by_name:
            w = str(n).split()
            if len(w) > 1:
                s2 = " ".join(w[1:]).lower()
                if s2 in seen:
                    dup.add(s2)
                seen[s2] = n
        _STRIP_CACHE[key] = hit = dup
    return hit


_STRIP_CACHE: dict = {}


def _with_roles(authored: list, evidence: dict) -> list:
    by_sql = {_squash_sql(d.get("sql")): d for d in (evidence.get("dimensions") or [])}
    out = []
    for d in authored:
        if not isinstance(d, dict):
            continue
        ev = by_sql.get(_squash_sql(d.get("sql"))) or {}
        roles = list(dict.fromkeys([*(d.get("_roles") or []), *(ev.get("_roles") or [])]))
        out.append({**d, **({"_roles": roles} if roles else {})})
    return out


def _squash_expr(text) -> str:
    """Alias-blind, whitespace-blind — `f.units_sold` and `v.units_sold` are one expression."""
    return re.sub(r"\b[A-Za-z_]\w*\.", "",
                  re.sub(r"\s+", " ", str(text or ""))).strip().lower()


def _squash_sql(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _extra(p) -> None:
    p.add_argument("--emit-prompt", action="store_true",
                   help="bundle the merged metrics for authoring and stop")
    p.add_argument("--name", default="",
                   help="bundle one metric by name, to settle the contract before the rest")
    # `extend` with nargs="+": `--from answers/x3/*.json` unquoted reaches the stage as one
    # word per file, as it does for m2/x1/x2/x4; a quoted glob and a repeated --from still work.
    p.add_argument("--from", dest="answers", action="extend", nargs="+", default=[],
                   help="authored card files to ingest, or globs of them")
    p.add_argument("--partial", action="store_true",
                   help="project the cards that ARE authored, for reviewing the contract on a "
                        "few before the rest are written. Never for a published layer: a run "
                        "that publishes a partial layer drops metrics the estate measures.")


def body(ctx: stage.Ctx) -> None:
    if ctx.args.emit_prompt:
        _emit(ctx)
    else:
        _ingest(ctx)


def main() -> None:
    stage.main(STAGE, "x3_emit.py", __doc__, body, extra=_extra)


if __name__ == "__main__":
    main()
