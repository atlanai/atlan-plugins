#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A3b — the model REVIEWS the compiler's output. It never writes SQL.

THE INVERSION, stated once. A3 used to ask a model for SQL. Fluent SQL that computes the wrong
thing is indistinguishable from fluent SQL that computes the right thing, so the mistakes were
invisible and the cost was per-widget forever. Now the compiler writes the statement and the
model is pointed at two questions it is actually good at:

  1. does this statement match what the widget describes?
  2. here is a construct the compiler could not translate — what ROW would translate it?

A finding is prose plus, optionally, a RULE. Any `sql` key in the output is refused outright,
exactly as at C3: the reviewer runs after the statement exists and may not rewrite it. What it
may do is change what the compiler KNOWS, and the next compile picks that up.

WHY A ROW AND NOT A PATCH. A row is data — schema-checkable, diffable, reversible, and
promotable by a gate rather than by a person. Arbitrary Python is none of those, and hard rule
2 says a skill's output is never trusted, only checked. You cannot check arbitrary code against
a contract.

THE GATE IS THE CORPUS. Promotion re-runs every accumulated regression case against the
CANDIDATE table before anything is written, each case through the reader whose compiler it
gates (`readers.corpus`): this stage compiles nothing itself and knows no formula language. A
row that changes a previously-correct statement is rejected by the run, with nobody watching —
which is the whole point, because the loop is autonomous.

MODES: --emit-prompt | --from <file> | --skip (no reviewer configured this run).
"""
from __future__ import annotations

import json
import pathlib
import sys


from metrics_agent import readers as _readers  # noqa: E402
from metrics_agent._paths import CORPUS_NAME  # noqa: E402
from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import rules as _rules  # noqa: E402
from metrics_agent.lib.card_sql import statement_sha  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "a3b_review"
IN = "bi_candidates.json"
OUT = "a3b_review.json"
PROMPT = "a3b_prompt.json"

# The reviewer emits PROSE and RULES. Anything that would let it author the statement is
# refused — the same list C3 refuses, for the same reason.
FORBIDDEN_KEYS = {"sql", "statement", "query", "filters", "tables", "group_by",
                  "aggregations"}


def _extra(p) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit-prompt", action="store_true",
                   help="write the bundle the review-compiler skill consumes, then stop")
    g.add_argument("--from", dest="from_file", default=None,
                   help="ingest findings, validate them, promote what survives the gate")
    g.add_argument("--skip", action="store_true",
                   help="no reviewer this run. An explicit choice, recorded on the manifest — "
                        "never a silent fallback.")
    p.add_argument("--rules", default=None)
    p.add_argument("--model", default=None)


def _dump(ctx: stage.Ctx, name: str, schema: str, doc: dict) -> None:
    artifacts.dump(ctx.run / name, doc, schema)


def _rule_table(ctx: stage.Ctx) -> _rules.Rules:
    """The table A3 compiled with (`readers.rule_table_paths`)."""
    return _readers.rule_table(ctx.policy.path, ctx.args.rules)


def _emit_prompt(ctx: stage.Ctx, report: dict, cands: list[dict]) -> None:
    """Two populations, two questions. Mixing them produces a reviewer that does neither.

    An earlier version carried a third — `compiler_suspects`, measures where the warehouse
    refused our statement and ran an analyst's — read from D2b's `measures.json`. The metric
    lane is authored now and writes no such file, so the list could only ever be empty.
    """
    compiled = [{"candidate_id": c["candidate_id"], "title": c.get("title"),
                 "sql": c["sql"], "coverage": c["compiler"]["coverage"],
                 "rules_used": c["compiler"]["rules_used"],
                 # What a partial statement leaves out: the reviewer's to explain or cover.
                 **({"reason": c["compiler"]["reason"]} if c["compiler"].get("reason")
                    else {}),
                 "widget": ((c["provenance"].get("bi") or {}).get("bi_context") or {})}
                for c in cands]
    _dump(ctx, PROMPT, "a3b_prompt.schema.json", {
        "contract_version": "1.0",
        "instruction": "See skills/review-compiler/SKILL.md. Emit FINDINGS and "
                       "optionally RULE rows. You may NOT write SQL: any sql/query/filters "
                       "key is refused outright. For each refusal, propose the row that "
                       "would translate the named construct, or say plainly that the "
                       "construct has no SQL form — `untranslatable` is a correct answer and "
                       "a row claiming otherwise would emit a statement that runs and answers "
                       "a different question.",
        "rules_sha": report["rules_sha"],
        "compiled": compiled,
        # What the compiler could not do, BY NAME. This is the reviewer's actual work.
        "refused": report["refused"],
        "gaps": report["gaps"],
    })
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.count(prompted=len(compiled), refused=len(report["refused"]))
    ctx.say(f"a3b: wrote {PROMPT} — {len(compiled)} statement(s) to review, "
            f"{len(report['refused'])} refusal(s) to explain.")


def _skip_empty(ctx: stage.Ctx, rules: _rules.Rules, report: dict) -> None:
    """NOTHING TO REVIEW IS A SKIP, NOT A REVIEW OF NOTHING.

    No statement compiled and none was refused — every unit stopped before the compiler, most
    often because the BI model resolved to no warehouse target. Emitting a prompt with empty
    lists hands a reviewer a session's worth of nothing and reads, afterwards, as a review that
    found no fault. So every mode records the same thing: skipped, with the reason, and a
    review artifact that says so. A later `--from` for the same run is accepted on the same
    terms — there is nothing its findings could be about.
    """
    why = (f"nothing to review: {report.get('units', 0)} unit(s) reached the compiler, "
           f"0 compiled and 0 refused")
    _skip(ctx, rules, why)
    ctx.say(f"a3b: SKIPPED — {why}. No prompt was written. Check a1/a2: a BI model whose "
            f"units resolve to no warehouse table compiles nothing, and that is where the "
            f"loss is.")


def _skip(ctx: stage.Ctx, rules: _rules.Rules, why: str) -> None:
    """Record a review that did not happen: skipped, with the reason, on the artifact, the
    manifest and the trace."""
    stale = ctx.run / PROMPT
    if stale.is_file():
        stale.unlink()      # a previous round's bundle must not be answered as this one's
    _dump(ctx, OUT, "a3b_review.schema.json", {
        "contract_version": "1.0",
        "rules_sha_before": rules.sha(),
        "reviewed": 0,
        "skipped": True,
        "skipped_reason": why,
        "findings": [],
        "accepted_rules": [],
        "rejected_rules": [],
        "needs_recompile": False,
    })
    ctx.entry.outputs.append(str(ctx.run / OUT))
    if ctx.args.from_file:
        ctx.note_input_path(ctx.args.from_file)
    ctx.entry.verdict = "skipped"
    ctx.entry.notes = why
    ctx.count(prompted=0, refused=0, skipped=True)
    ctx.decide("review", STAGE, "skipped", because=why)


def _validate(rows: list[dict]) -> list[dict]:
    out = []
    for i, r in enumerate(rows):
        stray = FORBIDDEN_KEYS & set(r)
        if stray:
            raise SystemExit(
                f"a3b finding {i} carries {sorted(stray)}. The reviewer does not write SQL — "
                f"it reviews it and proposes RULES. Refused.")
        if not str(r.get("finding", "")).strip():
            raise SystemExit(f"a3b finding {i}: empty finding")
        sev = r.get("severity")
        if sev not in ("wrong", "risky", "note"):
            raise SystemExit(f"a3b finding {i}: severity must be wrong|risky|note, got {sev!r}")
        out.append(r)
    return out


def _bind(findings: list[dict], cands: list[dict]) -> list[dict]:
    """Every finding names a statement this run compiled, and records which SQL it read.

    A finding keyed to an id the run does not hold was written against another compile (a
    `.pbix` read into a random temp directory gave new ids every run), and ingesting it
    silently reviewed nothing. A `wrong` finding with no id cannot be acted on, so it is
    refused rather than recorded as prose nobody applies. C4 holds a `wrong` statement back
    from `verified`, but only while the SQL is the one that was read: `sql_sha` says which."""
    sql_of = {c["candidate_id"]: c.get("sql") or "" for c in cands}
    out = []
    for i, r in enumerate(findings):
        cid = r.get("candidate_id")
        if cid is None:
            if r["severity"] == "wrong":
                raise SystemExit(f"a3b finding {i}: a `wrong` finding names no candidate_id, so "
                                 f"nothing can be held back. Name the statement it is about.")
            out.append(r)
            continue
        if cid not in sql_of:
            raise SystemExit(f"a3b finding {i}: unknown candidate_id {cid!r}. This run's "
                             f"compile holds no such statement: the answer was written against "
                             f"another compile. Re-emit the prompt and review again.")
        out.append(dict(r, sql_sha=statement_sha(sql_of[cid])))
    return out


def body(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "candidates.schema.json")
    report = json.loads((ctx.run / "a3_compile_report.json").read_text())
    ctx.entry.inputs.append(str(ctx.run / "a3_compile_report.json"))
    rules = _rule_table(ctx)

    if not doc["candidates"] and not report.get("refused") and not report.get("gaps"):
        _skip_empty(ctx, rules, report)
        return

    if ctx.args.emit_prompt:
        _emit_prompt(ctx, report, doc["candidates"])
        return
    if ctx.args.skip:
        # NO REVIEWER IS A SKIP, NOT A REVIEW THAT FOUND NOTHING. Written as one, the artifact
        # said `reviewed: N, findings: []` — exactly what a clean review says — and the
        # operator's choice survived only in a line printed to the terminal.
        _skip(ctx, rules, f"--skip: no reviewer this run, by the operator's choice; "
                          f"{len(doc['candidates'])} compiled statement(s) and "
                          f"{len(report.get('refused') or [])} refusal(s) were not reviewed")
        ctx.say("a3b: --skip — no reviewer this run. The compiler's own refusals stand and no "
                "rule is learned. An explicit choice, recorded here.")
        return

    findings: list[dict] = []
    proposals: list[dict] = []
    if ctx.args.from_file:
        ctx.authored(ctx.args.from_file)
        path = pathlib.Path(ctx.args.from_file).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"--from file not found: {path}")
        ctx.note_input_path(path)
        raw = json.loads(path.read_text())
        # THE WHOLE ANSWER, NOT ONLY ITS FINDINGS. A `sql` key beside `findings`, or riding on
        # a rule row, is the reviewer writing SQL all the same — and a rule row is written into
        # the compiler's table, so a key smuggled there outlives this run.
        if isinstance(raw, dict):
            stray = FORBIDDEN_KEYS & set(raw)
            if stray:
                raise SystemExit(
                    f"a3b answer carries {sorted(stray)} at the top level. The reviewer does "
                    f"not write SQL — it reviews it and proposes RULES. Refused.")
            for i, row in enumerate(raw.get("rules") or []):
                stray = FORBIDDEN_KEYS & set(row if isinstance(row, dict) else {})
                if stray:
                    raise SystemExit(
                        f"a3b rule {i} ({row.get('id', '?')}) carries {sorted(stray)}. A rule "
                        f"row is a match and an emit template, never a statement. Refused.")
        findings = _bind(_validate(raw.get("findings", []) if isinstance(raw, dict) else raw),
                         doc["candidates"])
        proposals = (raw.get("rules") or []) if isinstance(raw, dict) else []
        ctx.entry.model = ctx.args.model or "unrecorded"

    accepted, rejected = _gate(ctx, rules, proposals)

    _dump(ctx, OUT, "a3b_review.schema.json", {
        "contract_version": "1.0",
        "rules_sha_before": rules.sha(),
        "reviewed": len(doc["candidates"]),
        "findings": findings,
        "accepted_rules": accepted,
        "rejected_rules": rejected,
        # The loop driver reads this. A new rule means the compiler now knows something it did
        # not when it compiled unit #1, so EVERY unit is compiled again — never some of them.
        "needs_recompile": bool(accepted),
    })
    ctx.entry.outputs.append(str(ctx.run / OUT))

    if accepted:
        _write_rules(ctx, rules, accepted)

    wrong = [f for f in findings if f.get("severity") == "wrong"]
    ctx.count(findings=len(findings), wrong=len(wrong),
              accepted=len(accepted), rejected=len(rejected),
              needs_recompile=bool(accepted))
    ctx.say(f"a3b: {len(findings)} finding(s) ({len(wrong)} 'wrong'); "
            f"{len(accepted)} rule(s) accepted, {len(rejected)} rejected by the gate.")
    for r in rejected[:4]:
        ctx.say(f"      rejected {r['id']}: {r['why']}")


def _gate(ctx: stage.Ctx, rules: _rules.Rules, proposals: list[dict]):
    """The corpus decides. Nothing is written until it has.

    Each proposal is tried against a CANDIDATE table — the current rows plus this one — and
    the whole accumulated corpus is re-run. One at a time and cumulatively, so a row that only
    breaks things in combination with another is still caught.
    """
    corpus_dirs = _readers.corpus_dirs(ctx.policy.path)
    corpus_dir = ", ".join(str(d) for d in corpus_dirs) or "(no installed reader declares one)"
    corpus = _readers.corpus(ctx.policy.path)
    ungated = [case.get("_file") for case, who in corpus if not who]
    corpus = [(case, who) for case, who in corpus if who]
    accepted: list[dict] = []
    rejected: list[dict] = []

    if not corpus:
        # AN EMPTY CORPUS GATES NOTHING, SO IT ADMITS NOTHING. Accepting a row "on validation
        # alone" would let the loop adopt — and recompile every widget against — a rule no
        # regression case has ever checked, with nobody watching. Every proposal is refused
        # by name; the compiler's own refusals stand, exactly as with no reviewer.
        why = (f"the regression corpus ({CORPUS_NAME}/) holds no case, so nothing can show "
               f"this row leaves a correct statement unchanged. A learned rule is never "
               f"accepted on validation alone: add regression cases to the corpus first.")
        for raw in proposals:
            rid = str(raw.get("id", "?")) if isinstance(raw, dict) else "?"
            rejected.append({"id": rid, "why": why})
        if proposals:
            ctx.say(f"a3b: REFUSED {len(proposals)} proposed rule(s): the regression corpus "
                    f"at {corpus_dir} is EMPTY, and a rule no case has checked is never "
                    f"adopted.")
        return accepted, rejected

    for raw in proposals:
        try:
            row = _rules.validate_proposal(raw, rules.with_rows(accepted))
        except _rules.RuleError as exc:
            rejected.append({"id": str(raw.get("id", "?")), "why": str(exc)})
            continue
        candidate = rules.with_rows(accepted + [row])
        # A case fails once per way it fails, however many compilers it gates.
        failures = list({(f["case"], f["why"]): f for case, who in corpus for reader in who
                         for f in _rules.run_corpus([case], candidate, reader.compile_unit)
                         }.values())
        if failures:
            rejected.append({
                "id": row["id"],
                "why": (f"the regression corpus failed on {len(failures)} case(s): "
                        f"{[f['case'] for f in failures][:3]}. A row that changes a statement "
                        f"that was already correct is rejected, whatever else it fixes."),
            })
            continue
        accepted.append(row)
    ctx.say(f"a3b: the regression corpus holds {len(corpus)} case(s) and gated every "
            f"proposal.")
    if ungated:
        ctx.say(f"a3b: {len(ungated)} corpus case(s) are in a formula language no installed "
                f"reader compiles, so they gated nothing: {ungated[:4]}")
    return accepted, rejected


def _write_rules(ctx: stage.Ctx, rules: _rules.Rules, accepted: list[dict]) -> None:
    """Rules land in the RUN directory, not in the repo's table.

    The run adopts them immediately — that is what makes the loop autonomous. Promotion into
    the checked-in table is a separate, explicit step, because a row learned from one estate's
    workbook would otherwise silently govern every future estate the moment it was written.
    """
    out = ctx.run / "compiler_rules_learned.yml"
    merged = rules.with_rows(accepted)
    out.write_text(merged.dump())
    ctx.entry.outputs.append(str(out))
    ctx.say(f"a3b: {len(accepted)} rule(s) written to {out.name} (tier=provisional). "
            f"The run recompiles against it; the checked-in table is untouched until "
            f"promoted.")


def main() -> None:
    stage.main(STAGE, "a3b_review.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
