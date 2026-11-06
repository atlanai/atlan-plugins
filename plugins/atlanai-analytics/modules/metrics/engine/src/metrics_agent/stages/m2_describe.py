# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""M2 — write the natural-language question for each verified SQL. THE ONE AUTHORED STAGE.

This runs AFTER the merge, on the whole layer, and that placement is the point. Description
used to happen twice and too early: once per BI candidate in lane C, once per derived measure
in lane H, each on a different unit and each before the two arms met. A statement that both
lanes hold was therefore described twice, in two vocabularies, and neither description was
written with the other in view.

One statement, one question, written once over the merged layer.

WHAT IS AUTHORED AND WHAT IS NOT. The SET of verified queries is already fixed — by the BI
compiler and by the parser, before this stage sees anything. This adds the words a person
searches with: the question the statement answers, and the sentence explaining it. It cannot
add a query or merge two; the validator refuses an answer that tries. One it leaves out is
reported as undescribed, never filled in, so an answer can arrive in slices.

That refusal is what makes the layer re-runnable. Asked to *extract objects*, two authors
reading the same statements produced 41 and 11 — both defensible, and the layer changed size
on every rebuild. Fixing the set upstream and leaving only the prose here means a re-run
produces the same layer with, at most, different wording.

Rule 3: this agent holds no model key. The stage emits a bundle, the operator's own session
answers it via the `describe-verified-sql` skill, and `--from` ingests and checks it. The
result is an OVERLAY — `m1_merge` applies it — so the layer never waits on prose to exist.

  m2_describe.py --run R --emit-prompt          # -> m2_prompt.json, batched by domain
  m2_describe.py --run R --from answers/*.json  # validated, then written
  m1_merge.py    --run R                        # applies the overlay to the layer
"""
from __future__ import annotations

import glob
import json
import pathlib
import re
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import sqlshape as S  # noqa: E402
from metrics_agent.lib import dialect as _dialect
from metrics_agent.lib import stage  # noqa: E402

STAGE = "m2_describe"
IN = "verified_queries.json"
OUT = "sql_descriptions.json"
PROMPT = "m2_prompt.json"

# ONE FIELD, AND IT CARRIES EVERY QUESTION THE STATEMENT ANSWERS. A statement is reached from
# several angles and each phrasing is a separate way in — but `display_name` is ONE string, and
# it is the highest human-readable weight the registry ranks (B, above `description` at C). A
# list would have to pick one of them to put there and drop the rest to a weaker tier. So the
# questions are concatenated into this single field instead: "How many X? What share of Y?"
# All of them, at the weight that matters.
REQUIRED = ("question",)
# NAME AND DESCRIPTION, AND NOTHING ELSE. `grain` and `gotchas` were here and are gone: the
# registry ranks four fields and neither is one of them, so a `gotchas` list was prose nobody
# could search for and nobody was shown. The statement's grain is derivable from the statement.
OPTIONAL = ("name", "description")

# AN IDENTIFIER IS ANYTHING WITH AN UNDERSCORE IN IT — the skill's own test. Nobody says
# `eligible_count` out loud; `S1s`, `Q1` and `p3` are said, so a digit alone is not a signal.
_IDENT = re.compile(r"[A-Za-z0-9]+(?:_+[A-Za-z0-9]+)+")


def _extra(p) -> None:
    p.add_argument("--emit-prompt", action="store_true",
                   help="write the authoring bundle and stop")
    p.add_argument("--from", dest="answers", nargs="*", default=None,
                   help="reviewed answer file(s); globs allowed")
    p.add_argument("--vq-id", dest="vq_ids", nargs="*", default=None,
                   help="bundle only these statement(s) — for reviewing the prose on a few "
                        "before committing to the whole layer")
    p.add_argument("--only-provisional", action="store_true",
                   help="bundle only objects with no description yet — the usual case when "
                        "topping a layer up rather than rewriting it")


def _outer_select(sql: str, dialect: str) -> dict:
    """What the OUTERMOST select returns and groups by — the answer, not the workings.

    THE MISTAKE THIS EXISTS TO PREVENT. A CTE is the first thing an author reads and usually
    the most legible part of the statement, so a query whose CTE is per-user and whose outer
    select aggregates it to `COUNT(*), AVG(requests)` gets described as "how heavily does each
    user engage" — which is the workings. Handing the author the outer select costs one parse
    and removes the guess.

    THE OUTERMOST SELECT IS THE ROOT, NOT THE LAST ONE. An earlier version took the last
    top-level select that had no CTE ancestor, which is wrong twice over: a derived table in
    FROM is not a CTE, so the walk descended into it and reported the subquery's columns as
    the answer. Measured: a statement whose outer select is `account, convos, stage,
    last_title` reported `CRM_ID, CALL_TITLE, rn` — the inner window helper, complete
    with its `rn`. The root is the only select nothing else encloses.
    """
    try:
        tree = S.parse(sql, dialect)
    except Exception:
        return {}
    # A UNION IS NAMED BY ITS FIRST BRANCH. Unions nest to the left, so a breadth-first walk
    # meets the LAST branch first and reported its columns (a literal label and all) as the
    # statement's outputs. Follow the set operation down its left side instead, and say how
    # many branches it combines.
    branches = 1
    node = tree
    while isinstance(node, (S.exp.SetOperation, S.exp.Subquery, S.exp.Paren)):
        if isinstance(node, S.exp.SetOperation):
            branches += 1
        node = node.this
    root = node if isinstance(node, S.exp.Select) else None
    if root is None:
        for node in tree.walk():
            if isinstance(node, S.exp.Select) and node.find_ancestor(S.exp.Select) is None:
                root = node
                break
    if root is None:
        return {}

    def _cols(sel):
        # An unnamed output (`SELECT count(*)`) is still an output: it is named by its own SQL.
        # Dropping it made a bare count look like an empty wrapper, and the walk below then
        # reported the columns of a subquery inside a join as the statement's answer.
        return [str(p.alias if p.alias else p.name if isinstance(p, (S.exp.Column, S.exp.Star))
                    else p.sql(dialect=dialect)) or p.sql(dialect=dialect)
                for p in (sel.expressions or [])]

    def _is_wrapper(sel):
        exprs = sel.expressions or []
        return len(exprs) == 1 and isinstance(exprs[0], S.exp.Star)

    # A `SELECT * FROM (…)` WRAPPER ANSWERS NOTHING. dbt renders models this way, so the
    # outermost select is a passthrough and the statement it actually runs is one level in.
    hops = 0
    while hops < 4 and _is_wrapper(root):
        # Walk the whole subtree, not just `args["from"]`: sqlglot nests a subquery under
        # different keys depending on the shape, and a `from` lookup returns None for the very
        # wrapper this exists to see through. The CTEs a wrapper declares are skipped — those
        # are its workings, and descending into one reports a helper as the answer.
        inner = next((n for n in root.walk()
                      if isinstance(n, S.exp.Select) and n is not root
                      and n.find_ancestor(S.exp.CTE) is None), None)
        if inner is None:
            break
        root, hops = inner, hops + 1

    grouped = []
    g = root.args.get("group")
    if g is not None:
        for e in (g.expressions or []):
            grouped.append(e.sql(dialect=dialect))
        # `GROUP BY ALL` groups by every non-aggregate output and carries no expressions; read
        # as ungrouped it made a row-per-key listing look like a one-row population summary.
        if not grouped and g.args.get("all"):
            grouped.append("ALL")

    # AGGREGATE-OF-ITS-OWN-WORKINGS, JUDGED ON THE PROJECTIONS. Not "has outputs and no GROUP
    # BY", which the previous version used and which is true of every plain row-listing query.
    # It is an aggregate when the projections CALL an aggregate, and a summary of the
    # population when it does so without grouping.
    # Any aggregate function (MEDIAN and COUNT_IF were missed by a fixed list), but not one
    # inside a window: `MAX(x) OVER ()` keeps every row and summarises nothing.
    agg = any(isinstance(n, S.exp.AggFunc) and n.find_ancestor(S.exp.Window) is None
              and not isinstance(n.parent, S.exp.Window)
              for proj in (root.expressions or []) for n in proj.walk())
    out = {"returns": _cols(root), "group_by": grouped,
           "is_aggregate": bool(agg),
           "is_population_summary": bool(agg and not grouped)}
    if branches > 1:
        out["union_branches"] = branches
    return out


def _emit(ctx: stage.Ctx) -> None:
    layer = ctx.read(IN, "verified_queries.schema.json")
    objs = layer["objects"]
    if ctx.args.vq_ids:
        want = set(ctx.args.vq_ids)
        missing = want - {o["vq_id"] for o in objs}
        if missing:
            raise SystemExit(
                f"m2: the layer holds no statement(s) {', '.join(sorted(missing))}. Nothing "
                f"was written —\n  a bundle that silently skipped the one you named would "
                f"read as though it had nothing to describe.")
        objs = [o for o in objs if o["vq_id"] in want]
    if ctx.args.only_provisional:
        objs = [o for o in objs if o.get("name_provisional")]

    by_domain: dict[str, list] = {}
    for o in objs:
        by_domain.setdefault(o.get("domain") or "unknown", []).append(o)

    dialect = _dialect.of(layer, "verified_queries.json")
    tasks = []
    for dom, group in sorted(by_domain.items()):
        tasks.append({
            "domain": dom,
            "n": len(group),
            "queries": [{
                "vq_id": o["vq_id"],
                "sql": o["sql"],
                # THE ANSWER, SEPARATED FROM THE WORKINGS. See `_outer_select`.
                "outer_select": _outer_select(o["sql"], dialect),
                # Every restriction the statement applies, at any level — the ones inside a
                # CTE restrict the answer just as hard and are the ones most often missed.
                "restrictions": _restrictions_of(o, dialect)[:25],
                "tables": o.get("tables") or [],
                # The estate's own words for this statement's outputs. The best question is
                # usually already phrased in them, and a coinage is a word nobody will search.
                "aliases_seen": o.get("aliases") or [],
                "lane": (o.get("provenance") or {}).get("lane"),
                "adoption": ((o.get("provenance") or {}).get("history") or {}),
            } for o in group],
        })
    artifacts.dump(
        ctx.run / PROMPT,
        {"contract_version": "1.0", "stage": STAGE,
         "instruction": "See skills/describe-verified-sql/SKILL.md. For EVERY query, "
                        "return one object keyed by its vq_id. Do not add, merge or drop "
                        "queries — the set is fixed. DESCRIBE THE OUTER SELECT, not the CTEs: "
                        "`outer_select` says what the OUTERMOST select returns and groups by, "
                        "and `restrictions` lists every filter it applies at any level. "
                        "Assert only what the statement shows.",
         "fields": {"required": list(REQUIRED), "optional": list(OPTIONAL)},
         "tasks": tasks}, "m2_prompt.schema.json", indent=1)
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.count(queries=len(objs), domains=len(tasks),
              only_provisional=bool(ctx.args.only_provisional))
    ctx.say(f"m2: wrote {PROMPT} — {len(objs)} statement(s) across {len(tasks)} domain(s). "
            f"Answer it with the `describe-verified-sql` skill, then re-run with --from and "
            f"re-run m1_merge to apply. THE AGENT HOLDS NO MODEL KEY.")


def _ingest(ctx: stage.Ctx) -> None:
    layer = ctx.read(IN, "verified_queries.schema.json")
    known = {o["vq_id"] for o in layer["objects"]}
    aliases_of = {o["vq_id"]: (o.get("aliases") or []) for o in layer["objects"]}

    paths: list[pathlib.Path] = []
    for pat in (ctx.args.answers or []):
        paths.extend(pathlib.Path(p) for p in sorted(glob.glob(pat)))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        # THIS IS THE GATE, AND IT READS LIKE ONE. A run reaching here with nothing to ingest
        # has not failed — it is waiting on a person, and the message says what the person is
        # being asked for rather than reporting a missing file.
        ctx.gate("descriptions", "have the statements been described?", "not yet",
                 options=["ingest", "not yet"],
                 evidence={"patterns": list(ctx.args.answers or []),
                           "prompt": str(ctx.run / PROMPT)})
        ctx.entry.verdict = "blocked"
        raise SystemExit(
            f"m2: nothing to ingest — {' '.join(ctx.args.answers or []) or '--from'} matched "
            f"no file.\n\n"
            f"  This run is WAITING, not broken. {ctx.run / PROMPT} holds the statements; "
            f"answer it\n  with the `describe-verified-sql` skill, write the answers where "
            f"--from is looking, and\n  re-run this stage. The agent holds no model key, so "
            f"the words come from your session.")
    for _p in paths:
        ctx.authored(_p, kind="authored_descriptions")

    described: dict[str, dict] = {}
    problems: list[str] = []
    for p in paths:
        ctx.note_input_path(p)
        payload = json.loads(pathlib.Path(p).read_text())
        items = payload.get("queries") if isinstance(payload, dict) else payload
        for d in (items or []):
            vq = d.get("vq_id")
            if vq not in known:
                problems.append(f"{p.name}: unknown vq_id {vq!r} — the set is the layer's and "
                                f"this stage may not add to it")
                continue
            if vq in described:
                problems.append(f"{p.name}: {vq} described twice")
                continue
            row = {k: v for k, v in d.items() if k in REQUIRED + OPTIONAL}
            # A LIST IS ACCEPTED AND JOINED. An answer written when this field was plural is
            # not wrong, it is the same content in another shape — joining it is lossless,
            # and refusing it would throw away prose a person wrote.
            if isinstance(d.get("question"), list):
                row["question"] = " ".join(str(x).strip() for x in d["question"] if str(x).strip())
            elif d.get("questions"):
                row["question"] = " ".join(str(x).strip() for x in d["questions"] if str(x).strip())
            qs = [str(row.get("question") or "").strip()]
            if not qs[0]:
                problems.append(f"{p.name}: {vq} has no `question`")
                continue
            # THE DEFECT THIS EXISTS TO CATCH. A question carrying a SQL identifier is the
            # generated one wearing a person's voice, and it is exactly as unsearchable: it
            # was `What is eligible_count?` that sent us here. Only identifiers — an alias is
            # often an ordinary business word (`accounts`, `revenue`) and refusing those
            # would push the author out of the vocabulary a reader searches in.
            # AN UNDERSCORE, AND ONLY AN UNDERSCORE. A digit is not an identifier signal in
            # this estate: `s1s`, `s2plus`, `q1`, `p3` are the funnel stages and personas
            # people say out loud, and refusing "How many S1s did the campaign generate?"
            # pushes the author out of the business's own vocabulary — the exact opposite of
            # the point. `eligible_count` and `avg_grammar_score` carry an underscore, which
            # nobody has ever spoken.
            ident = [a for a in (d.get("_aliases") or aliases_of.get(vq) or [])
                     if "_" in str(a)]
            hit = [(a, q) for a in ident for q in qs if str(a).lower() in q.lower()]
            if hit:
                problems.append(f"{p.name}: {vq} names the SQL identifier "
                                f"{hit[0][0]!r} in a question — that is a column, not a word "
                                f"anyone searches with")
                continue
            # AND ANY OTHER IDENTIFIER. The aliases are only the output columns; a table, a
            # CTE or a column the statement filters on is just as unsearchable, and the skill's
            # rule is the token's shape — anything with an underscore in it — not where in the
            # statement it came from.
            other = _IDENT.findall(qs[0])
            if other:
                problems.append(f"{p.name}: {vq} names the identifier {other[0]!r} in a "
                                f"question — say what it is in words a person would type")
                continue
            # A QUESTION IS ASKED. Each sentence in the field ends in a question mark; a
            # label or a column list in its place is the restatement the skill refuses.
            if not qs[0].rstrip(" \"')»”’").endswith("?"):
                problems.append(f"{p.name}: {vq} `question` does not end in a question mark "
                                f"— write the business question, not a label for it")
                continue
            # A NAME IS PROSE. `plan_signoff_rate_q1_2026` is a filename, and it would land
            # at the highest search weight there is.
            nm_ident = _IDENT.findall(str(row.get("name") or ""))
            if nm_ident:
                problems.append(f"{p.name}: {vq} `name` {row.get('name')!r} is an identifier "
                                f"({nm_ident[0]!r}) — name it the way a person says it")
                continue
            described[vq] = row

    # A NAME COLLISION IS NOT COSMETIC, AND IT IS CAUGHT HERE RATHER THAN SURVIVED DOWNSTREAM.
    # The registry requires `name` unique per (workspace, kind), so the envelope disambiguates
    # a repeat by appending part of the vq_id — publishing `Account Name Value Check-90a1a485`
    # into WEIGHT A, the highest-ranked field there is. That is the machine artefact this whole
    # stage exists to remove, reintroduced at the last moment. Two statements that genuinely
    # differ deserve names that differ; usually the table or the subject tells them apart.
    by_name: dict[str, list[str]] = {}
    for _vq, _row in described.items():
        if (_nm := str(_row.get("name") or "").strip()):
            by_name.setdefault(_nm.lower(), []).append(_vq)
    for _nm, _vqs in sorted(by_name.items()):
        if len(_vqs) > 1:
            problems.append(
                f"the name {described[_vqs[0]]['name']!r} is used by {len(_vqs)} statements "
                f"({', '.join(sorted(_vqs)[:4])}). Names must be unique — a destination that keys "
                f"on the name would suffix the repeats at the highest search weight.")

    if problems:
        raise SystemExit("m2: the authored answer failed validation:\n  "
                         + "\n  ".join(problems[:40]))

    ctx.write(OUT, {"contract_version": "1.0", "descriptions": described},
              "sql_descriptions.schema.json")
    missing = len(known) - len(described)
    ctx.count(described=len(described), layer=len(known), undescribed=missing)
    ctx.say(f"m2: {len(described)}/{len(known)} statement(s) described.")
    if missing:
        ctx.say(f"m2: {missing} statement(s) have no description and keep the estate's own "
                f"alias, flagged `name_provisional`. A hole is reported, never filled in.")


def _restrictions_of(o: dict, dialect: str) -> list[str]:
    """Every condition the statement applies, as its own SQL, with where it sits.

    Read from the statement rather than from the shape's `filters`, which are a column index
    for other stages: a condition there is cut down to one column and an operator, so an
    expression, a membership test or a polarity did not survive the trip, and its `stage` is
    the clause type, which said nothing about whether the condition sat in a CTE. One entry
    per top-level conjunct of each WHERE, HAVING and QUALIFY, marked `[in a CTE]` or
    `[in a subquery]` when that is where it applies.
    """
    try:
        tree = S.parse(o["sql"], dialect)
    except Exception:  # noqa: BLE001 - an unparseable statement falls back to the index
        return [_restriction(f) for f in (o.get("filters") or [])]
    out: list[str] = []
    for clause in tree.find_all(S.exp.Where, S.exp.Having, S.exp.Qualify):
        where = ""
        node = clause.parent
        while node is not None:
            if isinstance(node, S.exp.CTE):
                where = " [in a CTE]"
                break
            if isinstance(node, S.exp.Subquery):
                where = where or " [in a subquery]"
            node = node.parent
        kind = {"Having": "having ", "Qualify": "qualify "}.get(type(clause).__name__, "")
        for part in clause.this.flatten() if isinstance(clause.this, S.exp.And) else [clause.this]:
            text = " ".join(part.sql(dialect=dialect).split())
            out.append(f"{kind}{text}{where}")
    return list(dict.fromkeys(out))


def _restriction(f: dict) -> str:
    """One filter as the author reads it, with its polarity. Without `negated` an exclusion
    reads as an inclusion: `x IS NOT NULL` was shown as `x is null`, the opposite."""
    col, op, value = f.get("column"), str(f.get("op") or ""), f.get("value")
    if f.get("negated"):
        text = (f"{col} is not {value}" if op == "is"
                else f"{col} not {op} {value}" if op in ("in", "between", "like", "ilike")
                else f"not ({col} {op}" + (f" {value}" if value is not None else "") + ")")
    else:
        text = f"{col} {op}" + (f" {value}" if value is not None else "")
    return text + (" [in a CTE]" if (f.get("stage") or "") not in ("where", "") else "")


def body(ctx: stage.Ctx) -> None:
    if ctx.args.emit_prompt:
        _emit(ctx)
    elif ctx.args.answers:
        _ingest(ctx)
    else:
        raise SystemExit("m2: pass --emit-prompt to author, or --from to ingest")


def main() -> None:
    stage.main(STAGE, "m2_describe.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
