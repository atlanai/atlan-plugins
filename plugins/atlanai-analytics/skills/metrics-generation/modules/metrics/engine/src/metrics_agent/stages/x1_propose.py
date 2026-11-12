#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""X1 — read each verified query and PROPOSE the metrics in it. Judgment, not extraction.

WHAT THIS REPLACES, AND WHY. Candidate extraction was mechanical: every aliased aggregate in a
statement became a metric candidate. That rule is wrong in both directions and it cannot be
patched, because the thing it is missing is meaning.

  * `MAX(account_name)` is an aggregate and is not a metric — it is a label carried through a
    GROUP BY so the row has something to print.
  * `COUNT(*)` in a data-quality test is an aggregate and is not a metric — it is a failure
    count whose only business meaning is "the test passed".
  * `COUNT(DISTINCT id)` over an accounts table is an aggregate that is EIGHT different
    metrics depending on the statement it sits in, and one rule cannot tell them apart.
  * A statement can hold a metric its SELECT never names: a count and a share of that count
    make a rate that only the prose says out loud.

Every attempt to fix that with another rule added a rule. the old deterministic measure stage already refused 116
"label passthroughs" by a hand-written test; the layer still published `Account Accounts 2`,
one measure fused out of a success-plan sign-off count and a per-CSM account count, because
both spell `COUNT(DISTINCT id)` over `ACCOUNT`.

So the judgment moves to where judgment lives. This stage hands a person the statement, its
authored question and description, what the outer select returns, and the aggregates the
parser found — and asks: what, if anything, does this statement MEASURE? The answer is zero,
one or several, and zero is a real answer that gets recorded.

Rule 3: the agent holds no model key. `--emit-prompt` bundles, `--from` ingests and checks.

    x1_propose.py --run R --emit-prompt          # -> x1_prompt.json, batched by domain
    x1_propose.py --run R --from answers/*.json  # validated, then written
"""
from __future__ import annotations

import glob
import json
import re
import pathlib
import sys


from metrics_agent.lib import filter_text as _filter_text  # noqa: E402
from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import declared as _declared  # noqa: E402
from metrics_agent.lib import bi_tags as _bi_tags  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "x1_propose"
IN_LAYER = "verified_queries.json"
IN_CANDS = "metric_candidates.json"
OUT = "metric_proposals.json"
PROMPT = "x1_prompt.json"

REQUIRED = ("vq_id", "verdict")
# `verdict` is "metrics" or "none" — a statement that measures nothing says so rather than
# going silent, because a silent statement and an unanswered one look identical downstream.
PROPOSAL_REQUIRED = ("name", "expression", "entity", "why_a_metric")
PROPOSAL_OPTIONAL = ("definition", "grain", "mandatory_filters", "optional_filters",
                     "dimensions", "aggregation", "unit")


def _extra(p) -> None:
    p.add_argument("--emit-prompt", action="store_true",
                   help="write the authoring bundle and stop")
    p.add_argument("--from", dest="answers", nargs="*", default=None,
                   help="reviewed answer file(s); globs allowed")
    p.add_argument("--vq-id", dest="vq_ids", nargs="*", default=None,
                   help="bundle only these statement(s) — for reviewing a few before "
                        "committing to the whole layer")


def _emit(ctx: stage.Ctx) -> None:
    layer = ctx.read(IN_LAYER, "verified_queries.schema.json")
    cands = json.loads((ctx.run / IN_CANDS).read_text())["candidates"]
    ctx.note_input_path(ctx.run / IN_CANDS)

    by_vq: dict[str, list] = {}
    for c in cands:
        by_vq.setdefault(c["vq_id"], []).append(c)

    objs = layer["objects"]
    if ctx.args.vq_ids:
        want = set(ctx.args.vq_ids)
        missing = want - {o["vq_id"] for o in objs}
        if missing:
            raise SystemExit(f"x1: the layer holds no statement(s) {', '.join(sorted(missing))}.")
        objs = [o for o in objs if o["vq_id"] in want]

    by_domain: dict[str, list] = {}
    for o in objs:
        by_domain.setdefault(o.get("domain") or "unknown", []).append(o)

    tasks = []
    for dom, group in sorted(by_domain.items()):
        rows = []
        for o in group:
            cs = by_vq.get(o["vq_id"], [])
            rows.append({
                "vq_id": o["vq_id"],
                # THE PROSE LEADS. An aggregate tells you what is computed; the question tells
                # you what it is FOR, and that is the difference between a metric and a label.
                "name": o.get("name"),
                "question": o.get("question"),
                "description": o.get("description"),
                "sql": o["sql"],
                "tables": o.get("tables") or [],
                "group_by": o.get("group_by") or [],
                # WHAT THE PARSER FOUND, OFFERED AS EVIDENCE AND NOT AS AN ANSWER. Each entry
                # is an aggregate expression the statement computes. Some are metrics, some
                # are labels, some are test scaffolding, and some metrics the statement holds
                # appear in none of them. Read them, do not obey them.
                "aggregates_found": [{
                    "alias": c.get("alias"),
                    "expression": c.get("expr"),
                    "function": c.get("fn"),
                    "over_table": c.get("source_table"),
                    "grain": c.get("grain"),
                } for c in cs],
                "filters": [
                    _filter_text.restriction(f, mark_stage=False)
                    for f in (o.get("filters") or [])][:30],
                # THE ROWS THE TABLE ITSELF KEEPS. A predicate inside the derived table the
                # statement reads from (a BI tool's own query steps, compiled as the table)
                # is not a choice of the question: every metric over this statement keeps it,
                # so it belongs in `mandatory_filters`.
                **({"mandatory_source_filters": [
                    _filter_text.restriction(f, mark_stage=False)
                    for f in (o.get("filters") or []) if f.get("in_source")]}
                   if any(f.get("in_source") for f in (o.get("filters") or [])) else {}),
                "adoption": (o.get("provenance") or {}).get("history") or {},
                # A STATEMENT COMPILED FROM A METRIC ITS BI MODEL DECLARES. It measures that
                # metric, and only that: the model's name, formula and comment say which.
                **({"declared": _declared.brief(d)}
                   if (d := _declared.of_statement(o)) else {}),
                # HOW THE BI TOOL DEFINES WHAT THIS STATEMENT SHOWS: each measure's style, its
                # formula and the model's own metadata (description, folder, annotations,
                # perspectives, endorsement, KPI), as context for naming and describing it.
                # It never changes what the SQL computes.
                **({"bi_definitions": defs}
                   if (defs := _bi_tags.definitions([(o["vq_id"], o)])) else {}),
                **({"conversion_quality": {k: o["quality"].get(k) for k in ("grade", "reason")}}
                   if o.get("quality") else {}),
            })
        tasks.append({"domain": dom, "n": len(rows), "queries": rows})

    artifacts.dump(ctx.run / PROMPT, {
        "contract_version": "1.0", "stage": STAGE,
        "instruction": "See skills/propose-metrics/SKILL.md. For EVERY statement "
                       "return one object keyed by its vq_id, with `verdict` of 'metrics' or "
                       "'none'. A statement may yield zero, one or several metrics — judge "
                       "each one, and say why it is a metric. `aggregates_found` is evidence, "
                       "not an answer: some are labels or test scaffolding, and a statement "
                       "may hold a metric none of them names. Each of "
                       "`mandatory_source_filters` defines the rows of the table the statement "
                       "reads: put it in `mandatory_filters` of every metric over it.",
        "fields": {"required": list(REQUIRED),
                   "per_metric_required": list(PROPOSAL_REQUIRED),
                   "per_metric_optional": list(PROPOSAL_OPTIONAL)},
        "tasks": tasks}, "x1_prompt.schema.json", indent=1)
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.count(queries=len(objs), domains=len(tasks),
              aggregates_offered=sum(len(by_vq.get(o["vq_id"], [])) for o in objs))
    ctx.say(f"x1: wrote {PROMPT} — {len(objs)} statement(s) across {len(tasks)} domain(s), "
            f"offering {sum(len(by_vq.get(o['vq_id'], [])) for o in objs)} aggregate(s) as "
            f"evidence. Answer with the `propose-metrics` skill, then re-run with --from. "
            f"THE AGENT HOLDS NO MODEL KEY.")


def _ingest(ctx: stage.Ctx) -> None:
    layer = ctx.read(IN_LAYER, "verified_queries.schema.json")
    known = {o["vq_id"]: o for o in layer["objects"]}

    # WHAT THE BUNDLE HANDED OVER COUNTS AS THE STATEMENT'S OWN. Each aggregate is printed by
    # the parser, not copied from the source, so it can differ in spacing, aliasing and
    # quoting from the text it came from. Quoting the evidence back must not be a refusal.
    offered: dict[str, set[str]] = {}
    try:
        bundle = json.loads((ctx.run / PROMPT).read_text())
    except (OSError, ValueError):
        bundle = {}
    for task in (bundle.get("tasks") or []):
        for q in (task.get("queries") or []):
            offered[q.get("vq_id")] = {
                _squash(a.get("expression")) for a in (q.get("aggregates_found") or [])
                if a.get("expression")}

    paths: list[pathlib.Path] = []
    for pat in (ctx.args.answers or []):
        paths.extend(pathlib.Path(p) for p in sorted(glob.glob(pat)))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        ctx.gate("metric_proposals", "have the statements been read for metrics?", "not yet",
                 options=["ingest", "not yet"],
                 evidence={"prompt": str(ctx.run / PROMPT)})
        ctx.entry.verdict = "blocked"
        raise SystemExit(
            f"x1: nothing to ingest. This run is WAITING on a person, not broken — "
            f"{ctx.run / PROMPT}\n  holds the statements; answer it with the "
            f"`propose-metrics` skill and re-run.")

    verdicts: dict[str, str] = {}
    proposals: list[dict] = []
    problems: list[str] = []
    seen_names: dict[str, str] = {}

    for path in paths:
        ctx.authored(path, kind="authored_metric_proposals")
        ctx.note_input_path(path)
        payload = json.loads(path.read_text())
        rows = payload.get("statements") if isinstance(payload, dict) else payload
        for row in (rows or []):
            vq = row.get("vq_id")
            if vq not in known:
                problems.append(f"{path.name}: unknown vq_id {vq!r}")
                continue
            if vq in verdicts:
                problems.append(f"{path.name}: {vq} answered twice")
                continue
            verdict = str(row.get("verdict") or "").strip().lower()
            if verdict not in ("metrics", "none"):
                problems.append(f"{path.name}: {vq} verdict must be 'metrics' or 'none'")
                continue
            ms = row.get("metrics") or []
            if verdict == "none" and ms:
                problems.append(f"{path.name}: {vq} says 'none' but carries {len(ms)} metric(s)")
                continue
            if verdict == "metrics" and not ms:
                problems.append(f"{path.name}: {vq} says 'metrics' but carries none")
                continue
            # A DECLARED METRIC IS THE MODEL'S, NOT A JUDGEMENT. The statement was compiled from
            # it, so it measures that metric and nothing else; `none` would drop a metric the
            # model governs, and two would split it.
            decl = _declared.of_statement(known[vq])
            if decl and (verdict != "metrics" or len(ms) != 1):
                problems.append(
                    f"{path.name}: {vq} was compiled from {decl['model']}'s declared metric "
                    f"{decl['metric']!r}. It measures exactly that: answer `metrics` with one "
                    f"metric, quoting the statement's own expression.")
                continue
            verdicts[vq] = verdict
            for i, m in enumerate(ms):
                for f in PROPOSAL_REQUIRED:
                    if not str(m.get(f) or "").strip():
                        problems.append(f"{path.name}: {vq} metric {i} has no `{f}`")
                # THE EXPRESSION MUST BE THE STATEMENT'S OWN. A proposal is a reading of what
                # runs, never a rewrite of it — an invented expression would publish a number
                # the estate has never computed.
                expr = str(m.get("expression") or "")
                if expr:
                    # A quotation is matched on its TOKENS, not on its spacing. The bundle
                    # offers each aggregate as sqlglot prints it, while the statement may pad
                    # it out to line up a column of aliases — refusing over that difference
                    # would refuse the evidence the bundle itself handed over.
                    if (_squash(expr) not in _squash(known[vq]["sql"])
                            and _squash(expr) not in offered.get(vq, set())):
                        problems.append(
                            f"{path.name}: {vq} metric {i} expression {expr[:60]!r} does not "
                            f"appear in the statement. Quote what runs; do not rewrite it.")
                    elif expr.lstrip().startswith("--"):
                        # A COMMENT IS NOT A FORMULA. The line above a projection explains it;
                        # quoting it publishes the explanation as the thing that runs.
                        problems.append(
                            f"{path.name}: {vq} metric {i} expression {expr[:60]!r} opens with "
                            f"a comment. Quote the expression, not the note above it.")
                    elif expr.count("(") != expr.count(")"):
                        # A TRUNCATION IS STILL A SUBSTRING. Half an expression passes the
                        # containment test and then becomes the published formula, so the
                        # parentheses have to close for the quotation to be a whole one.
                        problems.append(
                            f"{path.name}: {vq} metric {i} expression {expr[:60]!r} is cut "
                            f"off — its parentheses do not close. Quote the whole expression.")
                # THE ENTITY MUST BE A TABLE THE STATEMENT READS. A proposal that names a
                # table its own SQL never touches is a claim about where the number comes
                # from that the query contradicts. Caught here it is one answer to correct;
                # caught at the consistency check it is three stages of rework, and caught
                # nowhere it publishes a card citing a query that computes something else.
                ent = str(m.get("entity") or "").strip()
                tabs = {str(t).rsplit(".", 1)[-1].lower()
                        for t in (known[vq].get("tables") or [])}
                if ent and tabs and ent.rsplit(".", 1)[-1].lower() not in tabs:
                    problems.append(
                        f"{path.name}: {vq} metric {i} says the entity is {ent!r}, which is "
                        f"not a table the statement reads. Name the table the expression "
                        f"runs over.")
                # THE AGGREGATION MUST BE WHAT THE FORMULA DOES. `max` declared over a bare
                # column read is the card describing an aggregate that is not there; the
                # value was aggregated upstream and the honest word is `none`.
                agg = str(m.get("aggregation") or "").strip().lower()
                if agg in ("sum", "count", "count_distinct", "min", "max", "avg"):
                    head = "count" if agg.startswith("count") else agg
                    bare = re.sub(r"\b[A-Za-z_]\w*\.", "", expr).lower()
                    if not re.search(rf"\w*{head}\w*\s*\(", bare):
                        problems.append(
                            f"{path.name}: {vq} metric {i} declares {agg!r} but the "
                            f"expression calls no {head}(): {expr[:50]!r}. If the value was "
                            f"aggregated upstream, the aggregation is 'none'.")
                # THE ROWS THE TABLE ITSELF KEEPS ARE MANDATORY. A predicate inside the derived
                # table the statement reads from defines that table; a metric without it
                # describes rows the statement never reads. Each such filter's column must be
                # named in `mandatory_filters`.
                held = " ".join(str(x) for x in (m.get("mandatory_filters") or [])).lower()
                for f in (known[vq].get("filters") or []):
                    col = str(f.get("column") or "")
                    if f.get("in_source") and col and not re.search(
                            rf"(?<![\w]){re.escape(col.lower())}(?![\w])", held):
                        problems.append(
                            f"{path.name}: {vq} metric {i} leaves out the source filter "
                            f"{col} {f.get('op')} {f.get('value')}: it defines the rows of the "
                            f"table the statement reads. Put it in `mandatory_filters`.")
                nm = str(m.get("name") or "").strip()
                # A NAME IS SPOKEN, NEVER AN IDENTIFIER (anything with an underscore in it).
                # `signoff_count` names a column; the merge and the card inherit whatever is
                # proposed here.
                if re.search(r"[A-Za-z0-9]_+[A-Za-z0-9]", nm):
                    problems.append(
                        f"{path.name}: {vq} metric {i} name {nm!r} is an identifier. Name "
                        f"what it measures the way a person says it.")
                proposals.append({
                    "proposal_id": f"{vq}#{i}",
                    "vq_id": vq,
                    **{k: m.get(k) for k in PROPOSAL_REQUIRED + PROPOSAL_OPTIONAL
                       if m.get(k) is not None},
                })
                seen_names.setdefault(nm.lower(), vq)

    missing = [v for v in known if v not in verdicts]
    if problems:
        raise SystemExit("x1: the authored answer failed validation:\n  "
                         + "\n  ".join(problems[:40]))
    if missing:
        # A HOLE STOPS THE RUN. X1 is often answered in several files (one per sitting or per
        # author), and the run resumes as soon as ANY file matches the glob. Carrying on with
        # part of the layer read would build every later gate on a partial set of metrics and
        # say nothing until the end. Refuse, name the statements, and resume when they are in.
        shown = sorted(missing)
        more = f" (+{len(shown) - 40} more)" if len(shown) > 40 else ""
        raise SystemExit(
            f"x1: {len(missing)} of {len(known)} statement(s) have no verdict in the answer "
            f"files read ({len(paths)} file(s)). A statement nobody read is not a statement "
            f"with no metric. Add a verdict for each (`none` is a verdict), or wait for the "
            f"remaining answer files, then re-run: {', '.join(shown[:40])}{more}.")

    none_ct = sum(1 for v in verdicts.values() if v == "none")
    ctx.write(OUT, {"contract_version": "1.0",
                    "dialect": layer.get("dialect"),
                    "layer_sha": layer.get("layer_sha"),
                    "n_statements": len(known),
                    "n_answered": len(verdicts),
                    "n_no_metric": none_ct,
                    "verdicts": verdicts,
                    "proposals": proposals}, "metric_proposals.schema.json")
    ctx.count(answered=len(verdicts), unanswered=len(missing),
              proposals=len(proposals), no_metric=none_ct)
    ctx.say(f"x1: {len(verdicts)}/{len(known)} statement(s) read — {len(proposals)} metric(s) "
            f"proposed, {none_ct} statement(s) judged to measure nothing.")
    if missing:
        # NAMED, NOT COUNTED. "3 statements were not answered" sends a reader back through the
        # whole layer to find which; the ids are what they act on.
        shown = sorted(missing)
        more = f" (+{len(shown) - 20} more)" if len(shown) > 20 else ""
        ctx.say(f"x1: {len(missing)} statement(s) were not answered at all. That is a HOLE, "
                f"not a 'none' — a statement nobody read is not a statement with no metric: "
                f"{', '.join(shown[:20])}{more}.")


def _squash(text: str) -> str:
    """The same expression with its spacing flattened — what it computes, not how it is laid out."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def body(ctx: stage.Ctx) -> None:
    if ctx.args.emit_prompt:
        _emit(ctx)
    elif ctx.args.answers:
        _ingest(ctx)
    else:
        raise SystemExit("x1: pass --emit-prompt to author, or --from to ingest")


def main() -> None:
    stage.main(STAGE, "x1_propose.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
