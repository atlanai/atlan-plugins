#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A3 — translate each question unit to warehouse SQL in the DECLARED dialect.

THE READER COMPILES; THIS STAGE ASKS IT TO (AGENT_GUARDRAILS rule 22). A unit's formulas are
written in its BI tool's language, and only the reader that read them knows that language. So
each model's units are compiled by the reader that wrote the model — its `prepare` and
`compile_unit` (`sockets.BIReader`), reached through `metrics_agent.readers` by the `vendor` the
model carries — against the rule table the readers declare (or the operator's, beside the
policy). This stage knows no formula language: it turns each compiled statement into a
candidate in the common format, and a reader that compiles nothing has its units refused by
name.

THIS STAGE IS DETERMINISTIC. It used to be an LLM stage: the model read the facts and wrote
the SQL. That is expensive, irreproducible, and its mistakes are invisible — fluent SQL that
computes the wrong thing looks exactly like fluent SQL that computes the right thing.

Now a COMPILER writes the SQL from a rule table and the model REVIEWS it at A3b. What the
compiler cannot do it says so, by name, and that name is what a proposed rule has to cover.
Nothing is guessed and nothing is skipped quietly.

THREE RULES that make the output safe, plus the dialect:
  * CLOSED WORLD — every filter and literal cites evidence. Also the injection control.
  * NO INVENTED COLUMNS — enforced afterwards by C4, not by trust.
  * UNTRANSLATABLE CONSTRUCTS ARE DECLARED, not approximated.
  * THE DIALECT IS DECLARED, never inferred.
"""
from __future__ import annotations

import hashlib
import json
import re
import pathlib
import sys


from metrics_agent import readers as _readers  # noqa: E402
from metrics_agent import sockets as _sockets  # noqa: E402
from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import withheld as _withheld  # noqa: E402
from metrics_agent.lib import evidence as _ev  # noqa: E402
from metrics_agent.lib import observed as _observed  # noqa: E402
from metrics_agent.lib import pii as _pii  # noqa: E402
from metrics_agent.lib import quality as _quality  # noqa: E402
from metrics_agent.lib import rules as _rules  # noqa: E402
from metrics_agent.lib import slugs  # noqa: E402
from metrics_agent.lib import sqlshape as S  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402
from metrics_agent.lib import bi_models as _models  # noqa: E402

STAGE = "a3_translate"
OUT = "bi_candidates.json"

def _extra(p) -> None:
    p.add_argument("--rules", default=None,
                   help="compiler rule table. Defaults to bi_compiler_rules.yml beside the "
                        "policy, else the tables the installed BI readers declare. The A3b loop "
                        "passes a CANDIDATE table here so a rejected row never touches the file "
                        "on disk.")
    p.add_argument("--recompile", action="store_true",
                   help="after the A3b review: recompile every unit with the run's learned "
                        "table (compiler_rules_learned.yml) when the review accepted a rule; "
                        "otherwise leave the compile as it is and say so")


def viewer_gotcha(binds: list[dict], bindable: bool) -> str:
    """The sentence every object filtered to the viewing user carries."""
    what = " and ".join(f"{b['placeholder']} (the viewing user's {b['of'].replace('_', ' ')})"
                        for b in binds)
    return ("Filtered to the viewing user: the statement reads " + what + " as a bind "
            "parameter, so each person who runs it gets their own rows. Bind it to the name "
            "of whoever asks; no name is stored with it. "
            + ("Verification binds it to the fetching identity's name, held in memory only."
               if bindable else
               "This run has no identity to bind it to: it is parsed, never executed."))


def withheld_gotcha(binds: list[dict]) -> str:
    """The sentence a statement that compares with a withheld literal carries."""
    what = " and ".join(b["placeholder"] for b in binds)
    return ("Compares with a person's address or name the BI file holds, which is never "
            "kept (guardrail 8): the statement reads it as " + what + ", a bind parameter. "
            "Bind it to the value the BI file's own formula or filter holds. Verification "
            "binds it in memory from the BI file and drops it.")


def _bind_withheld(res, rules, dialect: str) -> list[dict]:
    """The statement and its totals with each withheld marker read as its bind placeholder
    (lib/withheld.bind_markers), in place; the {of, placeholder} of each one they read.
    Raises ValueError when the dialect has no placeholder for one."""
    def ph(n: int) -> str | None:
        return rules.withheld_placeholder(n, dialect)
    seen: list[str] = []
    res.sql, ofs = _withheld.bind_markers(res.sql, ph)
    seen += ofs
    for t in getattr(res, "totals", None) or []:
        if t.get("sql"):
            t["sql"], ofs = _withheld.bind_markers(t["sql"], ph)
            seen += ofs
    out: dict[str, dict] = {}
    for of in seen:
        n = int(of.rsplit("_", 1)[1])
        out.setdefault(of, {"of": of, "placeholder": ph(n)})
    if out:
        res.rules_used.append(_withheld.RULE)
    return [out[k] for k in sorted(out, key=lambda k: int(k.rsplit("_", 1)[1]))]


def _scrubbed_literal(tree, totals: list[str], dialect: str) -> bool:
    """Whether the statement or one of its totals holds a string literal A1's scrub
    replaced (the PII marker)."""
    trees = [tree]
    for sql in totals:
        if sql:
            try:
                trees.append(S.parse(sql, dialect))
            except S.ParseError:
                continue
    return any(_pii.PLACEHOLDER in v for t in trees for v in S.string_literals(t))


def body(ctx: stage.Ctx) -> None:
    if ctx.args.recompile:
        # THE REVIEW'S RULES ARE USED, NOT ONLY RECORDED. A3b gates each proposed row and
        # writes the run's learned table; until this pass the statements downstream were
        # still compiled without them.
        review_path = ctx.run / "a3b_review.json"
        review = json.loads(review_path.read_text()) if review_path.is_file() else {}
        learned = ctx.run / "compiler_rules_learned.yml"
        if not (review.get("needs_recompile") and learned.is_file()):
            ctx.say("a3: the review accepted no rule; the compile stands as it is.")
            ctx.entry.verdict = "skipped"
            ctx.entry.notes = "no learned rule to recompile with"
            return
        ctx.args.rules = str(learned)
        ctx.say(f"a3: recompiling every unit with the {len(review.get('accepted_rules') or [])} "
                "rule(s) the review accepted.")
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    qu = ctx.read("question_units.json", "question_units.schema.json")
    facts = ctx.read("bi_facts.json", "bi_facts.schema.json")
    dialect = ri["dialect"]

    # One compiler per BI model (a run may read several, lib/bi_models): the reader that
    # wrote the model, prepared with that model alone, since names are looked up only within
    # the model that declares them. None when the reader compiles nothing.
    contexts = {}
    for model, part in _models.partitions(facts):
        reader = _readers.compiler(part["vendor"])
        contexts[model] = (reader, reader.prepare(part) if reader else None, part["vendor"])
    model_of = _models.model_of_units(facts)
    # Whether verification may bind the viewing user's name, per model: the fetch that wrote
    # the model's viewer file can supply its identity's name, in memory (never written).
    bindable_of = {model: bool((part.get("viewer") or {}).get("user_name_bindable"))
                   for model, part in _models.partitions(facts)}

    def _of(u: dict):
        return contexts.get(model_of.get(u["unit_id"])) or next(iter(contexts.values()))

    units = [u for u in qu["units"] if u["status"] == "resolved"]
    shaper = _observed.Shaper(ctx.policy)

    rules_paths = _readers.rule_table_paths(ctx.policy.path, ctx.args.rules)
    rules = _rules.load_many(rules_paths)
    for rules_path in rules_paths:
        ctx.note_input_path(pathlib.Path(rules_path))
    ctx.say(f"a3: compiling with {len(rules.rows)} rule(s), {rules.sha()} "
            f"({len(rules.by_tier(_rules.PROVISIONAL))} provisional).")

    cands = []
    n_partial = 0
    refused: list[dict] = []
    partials: list[dict] = []
    gaps: list[dict] = []
    n_provisional = 0
    seen_ids: set[str] = set()
    graded_units: list[dict] = []
    graded_measures: dict[tuple, dict] = {}

    def _grade(u: dict, cid: str | None, q: dict, untrans: list[dict],
               own_gotchas: list[str]) -> None:
        """One widget's compile grade, and each of its measures': a construct left out counts
        against the measure it is in only, when the compiler says which."""
        names = [str(m.get("name") if isinstance(m, dict) else m)
                 for m in u.get("measures") or []]
        graded_units.append({"unit_id": u["unit_id"], "candidate_id": cid,
                             "grade": q["grade"], "reason": q["reason"],
                             "measures": names})
        named = {str(g.get("in_measure")) for g in untrans or [] if g.get("in_measure")}
        for m in names:
            mq = q
            if q["grade"] == _quality.APPROXIMATE and named and m not in named:
                mq = _quality.compile_grade(coverage=_sockets.TRANSLATED, reason=None,
                                            untranslatable=[], gotchas=own_gotchas,
                                            provisional_rules=[], policy=ctx.policy)
            key = (model_of.get(u["unit_id"]), m)
            have = graded_measures.get(key)
            if have is None or _quality.worst([have["grade"], mq["grade"]],
                                               ctx.policy) != have["grade"]:
                graded_measures[key] = {"measure": m, "model": key[0], "grade": mq["grade"],
                                        "reason": mq["reason"],
                                        "units": (have or {}).get("units", [])}
            graded_measures[key]["units"].append(u["unit_id"])

    def _grade_refused(u: dict, reason: str) -> None:
        _grade(u, None, _quality.compile_grade(
            coverage=_sockets.REFUSED, reason=reason, untranslatable=[], gotchas=[],
            provisional_rules=[], policy=ctx.policy), [], [])

    for u in units:
        reader, model, vendor = _of(u)
        if reader is None:
            res = _sockets.Compiled(reason=f"the {vendor!r} BI reader compiles no formulas "
                                           "(it defines no compile_unit; see sockets.BIReader)")
        else:
            res = reader.compile_unit(u, rules, dialect, model=model)
        if res.coverage == _sockets.REFUSED or not res.sql:
            # DECLARED, not dropped. A refusal names what the compiler could not do, and that
            # name is exactly what A3b asks the reviewer to propose a rule for.
            refused.append({"unit_id": u["unit_id"], "reason": res.reason,
                            "unknown": sorted(set(res.unknown)),
                            "untranslatable": res.untranslatable})
            if res.unknown:
                gaps.append({"unit_id": u["unit_id"], "unknown": sorted(set(res.unknown)),
                             "reason": res.reason})
            _grade_refused(u, res.reason or "the compiler gave no statement")
            continue

        translation = "partial" if res.coverage == _sockets.PARTIAL else "full"
        untrans = res.untranslatable
        partial_reason = None
        if translation == "partial":
            n_partial += 1
            # A PARTIAL STATEMENT SAYS WHAT IT LEAVES OUT. The reason used to stop here: the
            # candidate carried only `untranslatable`, which is empty for a gap that is not a
            # declared construct, so a reviewer saw "partial" and nothing else. A reader that
            # names nothing is said to have named nothing, rather than left blank.
            partial_reason = (res.reason or "; ".join(
                f"{g.get('construct')}" + (f" in {g['in_measure']!r}" if g.get("in_measure")
                                           else "") for g in untrans)
                or "the reader declared the unit partial without naming what it left out")

        # A LITERAL THE SCRUB WITHHELD IS A BIND PLACEHOLDER. A1 numbered each person's
        # address or name the BI file compares with (guardrail 8); the statement reads it as
        # `:withheld_N`, which whoever runs it binds to the value in the BI file, and which
        # verification binds in memory (A4, lib/withheld). The value is never written.
        try:
            withheld = _bind_withheld(res, rules, dialect)
        except ValueError as exc:
            refused.append({"unit_id": u["unit_id"], "reason": f"{exc} (an op.withheld_bind "
                            f"rule row for the {dialect} dialect)",
                            "unknown": [], "untranslatable": []})
            _grade_refused(u, refused[-1]["reason"])
            continue
        try:
            tree = S.parse(res.sql, dialect)
            feat = S.extract_feat(tree, [])
            tables = S.extract_tables(tree) or u["warehouse_tables"]
        except S.ParseError as exc:
            # The compiler emitted text that does not parse. That is a COMPILER defect, not a
            # workbook problem, and it is reported as one so the review pass sees it.
            refused.append({"unit_id": u["unit_id"],
                            "reason": f"compiler emitted unparseable SQL: {exc}",
                            "unknown": [], "untranslatable": []})
            _grade_refused(u, refused[-1]["reason"])
            continue

        # A CREDENTIAL THE SCRUB REMOVED IS NOT A VALUE. A secret in the BI file (a password
        # in a connection string) is replaced by the plain marker and never kept, so a
        # statement comparing with it would run and match no row: silently wrong. It is never
        # bound either (a statement is not a place for a secret); refused by name.
        scrubbed = _scrubbed_literal(tree, [t.get("sql") or ""
                                            for t in getattr(res, "totals", None) or []],
                                     dialect)
        if scrubbed:
            refused.append({"unit_id": u["unit_id"],
                            "reason": "the statement compares with a literal the scrub removed "
                                      "as a secret (a credential in the BI file is never kept "
                                      "or bound), so it would match no row",
                            "unknown": [], "untranslatable": []})
            _grade_refused(u, refused[-1]["reason"])
            continue

        gotchas = []
        if res.provisional_rules:
            n_provisional += 1
            gotchas.append(
                "Compiled using PROVISIONAL rule(s) "
                + ", ".join(sorted(set(res.provisional_rules)))
                + " — learned by a run and not yet confirmed across estates. Review the SQL "
                  "before relying on this object.")
        # What the reader says every object compiled from this unit must carry.
        gotchas.extend(res.gotchas)
        # THE STATEMENT IS FILTERED TO WHOEVER RUNS IT. It reads the viewing user's name as a
        # bind placeholder, never the value; only verification binds it (A4), in memory.
        binds = rules.viewer_binds(
            res.rules_used, dialect,
            [res.sql] + [t.get("sql") or "" for t in getattr(res, "totals", None) or []])
        bindable = bindable_of.get(model_of.get(u["unit_id"]), False)
        if binds:
            gotchas.append(viewer_gotcha(binds, bindable))
        if withheld:
            gotchas.append(withheld_gotcha(withheld))

        # The id is the unit's, cut to a readable length. Two long unit ids can share the
        # cut, so a collision takes a short digest of the full id rather than overwriting.
        cid = f"bi-{slugs.kebab(u['unit_id'], max_len=60)}"
        if cid in seen_ids:
            cid += "-" + hashlib.sha256(str(u["unit_id"]).encode()).hexdigest()[:8]
        seen_ids.add(cid)
        # THE COMPILE'S GRADE, from the compiler's own account of what it translated (what it
        # left out, the conditions it names, the rules it used); A4 adds the witness.
        own = (list(res.gotchas) + ([viewer_gotcha(binds, bindable)] if binds else [])
               + ([withheld_gotcha(withheld)] if withheld else []))
        cq = _quality.compile_grade(coverage=res.coverage, reason=partial_reason,
                                    untranslatable=untrans, gotchas=own,
                                    provisional_rules=res.provisional_rules,
                                    policy=ctx.policy)
        _grade(u, cid, cq, untrans, own)
        cands.append({
            "candidate_id": cid,
            "lane": "bi",
            "sql": res.sql,
            "tables": tables,
            "joins": feat["joins"],
            "filters": feat["filters"],
            "group_by": feat["group_by"],
            "aggregations": feat["aggregations"],
            "grain": S.derive_grain(feat),
            "klass": S.classify(feat, S.compute_constructs(tree, feat)),
            "domain": u["domain"],
            # BI filter values, for the same reason as the history lane: a person searching for
            # the season code must find the record. Evidenced by VISUAL USAGE rather than
            # adoption, so the distinct-user floor is met by construction.
            "observed_values": _observed.from_filters(
                u["filters"] + u["container"]["scoped_filters"], shaper),
            "semantic_shape_key": slugs.semantic_shape_key(
                tables, [(x["fn"], x["expr"]) for x in feat["aggregations"]],
                feat["group_by"], S.normalised_predicates(feat)),
            "title": u["title"],
            "question_draft": None,
            "translation": translation,
            "untranslatable": untrans,
            "variants": [],
            # The total rows the widget shows beside `sql`'s rows, each its own statement.
            **({"totals": list(getattr(res, "totals", None) or [])}
               if getattr(res, "totals", None) else {}),
            "gotchas": gotchas,
            **({"viewer_binds": {"bindable": bindable, "placeholders": binds}}
               if binds else {}),
            **({"withheld_binds": {"placeholders": withheld}} if withheld else {}),
            "compiler": {
                "rules_sha": rules.sha(),
                "rules_used": sorted(set(res.rules_used)),
                "provisional_rules": sorted(set(res.provisional_rules)),
                "coverage": res.coverage,
                **({"reason": partial_reason} if partial_reason else {}),
            },
            "provenance": {
                "lane": "bi",
                "bi": {
                    "workbook": u["container"]["path"][0] or None,
                    "model": None,
                    "report": u["container"]["path"][1] or None,
                    "page": u["container"]["path"][1] or None,
                    "visual": u["container"]["path"][2] or None,
                    "unit_id": u["unit_id"],
                    "container_path": [p for p in u["container"]["path"] if p],
                    "source_fragments": u["container"]["source_fragments"],
                    "n_dashboards": u["container"]["n_dashboards"],
                    # The assets by IDENTITY — the tool's own ids and, where a BI plug resolved
                    # them, catalogue ids — so a published object can name its source by id.
                    "assets": u["container"].get("assets") or [],
                    # The bounded block C3 reads. Carried on the candidate so C3 needs no
                    # second artifact and no file read.
                    "bi_context": u.get("bi_context"),
                    # WHAT THE MODEL DECLARES about the metric this statement computes, when it
                    # declares anything: it travels with the statement to X1-X3, which hold a
                    # card built from it to it. Absent for a model that declares nothing.
                    **({"declared": u["declared"]} if u.get("declared") else {}),
                },
                "history": None,
                "merged_from": [],
                "evidence_citations": [
                    _ev.cite(f"{f['column']} {f.get('op')}", "visual-usage",
                             f.get("evidence") or "a filter the BI file applies to the widget")
                    for f in u["filters"]] + [
                    # Literals the COMPILED statement carries from the widget's calculated
                    # fields (a CASE comparison, a parameter at its declared default, a
                    # scaling divisor) are evidenced by those calcs, not by a widget filter.
                    _ev.cite(f"{f.get('column')} {f.get('op')}", u["expression_language"],
                             "compiled from the widget's calculated fields (parameters at "
                             "their declared defaults)")
                    for f in (feat.get("filters") or [])
                    if u.get("expression_language") and f.get("column")],
                "ranking_basis": (f"bi dashboard placement "
                                  f"(n_dashboards={u['container']['n_dashboards']})"),
            },
            "witness": None,
            # The grade so far: no warehouse has checked it yet; A4 rewrites it.
            "quality": _quality.after_witness(
                {"compile_grade": cq["grade"], "reason": cq["reason"],
                 "differences": cq["differences"]}, None, ctx.policy),
        })
        if partial_reason:
            partials.append({"unit_id": u["unit_id"], "candidate_id": cid,
                             "reason": partial_reason, "unknown": sorted(set(res.unknown)),
                             "untranslatable": untrans})
            if res.unknown:
                # What a new rule would have to cover, whether the unit compiled in part or
                # not at all: the gap is the same either way.
                gaps.append({"unit_id": u["unit_id"], "unknown": sorted(set(res.unknown)),
                             "reason": partial_reason})

    cands.sort(key=lambda c: c["candidate_id"])
    ctx.write(OUT, {"contract_version": "1.0", "dialect": dialect, "candidates": cands},
              "candidates.schema.json")

    coverage = round(100 * len(cands) / max(len(units), 1))
    report = {
        "contract_version": "1.0",
        "rules_sha": rules.sha(),
        "rules": len(rules.rows),
        "provisional_rules": [r["id"] for r in rules.by_tier(_rules.PROVISIONAL)],
        "units": len(units), "compiled": len(cands), "partial": n_partial,
        "refused": refused,
        "partial_units": partials,
        # What a NEW RULE would have to cover. This list shrinking run over run is the
        # measure of the loop working; it is the only honest one.
        "gaps": gaps,
        "coverage_pct": coverage,
        # Each widget's and each measure's grade from the compile; the candidate carries the
        # final one (A4 adds the witness).
        "quality": {
            "counts": _quality.counts(g["grade"] for g in graded_units),
            "widgets": sorted(graded_units, key=lambda g: g["unit_id"]),
            "measures": [dict(graded_measures[k],
                              units=sorted(set(graded_measures[k]["units"])))
                         for k in sorted(graded_measures, key=lambda k: (str(k[0]), k[1]))],
        },
    }
    artifacts.validate(report, "a3_compile_report.schema.json", what="a3_compile_report.json")
    (ctx.run / "a3_compile_report.json").write_text(json.dumps(report, indent=2) + "\n")
    ctx.entry.outputs.append(str(ctx.run / "a3_compile_report.json"))
    ctx.count(units=len(units), candidates=len(cands), partial=n_partial,
              refused=len(refused), gaps=len(gaps), coverage_pct=coverage,
              rules_sha=rules.sha(), compiled_with_provisional=n_provisional)
    ctx.say(f"a3: compiled {len(cands)}/{len(units)} units ({coverage}%); {n_partial} partial "
            f"(construct declared, never approximated); {len(refused)} refused.")
    ctx.say("a3: conversion quality of the compile: " + (", ".join(
        f"{g.replace('_', ' ')} {n}" for g, n in report["quality"]["counts"].items() if n)
        or "nothing compiled") + " (A4 adds the witness).")
    if n_provisional:
        ctx.say(f"a3: {n_provisional} object(s) used a PROVISIONAL rule and carry a gotcha "
                f"saying so.")
    for r in refused[:4]:
        ctx.say(f"      refused {r['unit_id']}: {r['reason']}")


def main() -> None:
    stage.main(STAGE, "a3_translate.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
