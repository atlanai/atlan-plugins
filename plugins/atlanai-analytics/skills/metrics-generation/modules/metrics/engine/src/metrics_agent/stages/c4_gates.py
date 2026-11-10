#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""C4 — the grounding gates. A dirty report stops the pipeline. There is no force flag.

  1. SHAPE            the candidate validates against the contract
  2. COLUMN GROUNDING every column named in the SQL exists on the table it is used against
  3. LITERAL GROUNDING every WHERE literal is parameterized, present in that column's observed
                      vocabulary, or cited to BI evidence
  4. VARIANT ACCOUNTING every variant declared at C2 is still present and still scoped
  5. FAITHFULNESS     the WORDS match the SQL — every claim the description makes is actually
                      in the statement
  6. COMPILER REVIEW  A3b's reviewer did not call the statement `wrong`

Gates 1, 2 and 4 BLOCK: a failure exits non-zero and the object never reaches C5. Gates 3, 5
and 6 FLAG: the object is marked `needs_review` rather than silently published. The design's rule
is that nothing ungrounded is quietly kept, not that everything kills the run.

WHY GATE 5 IS NOT BLOCKING. A claim missing from the SQL means the DESCRIPTION is wrong, not
the statement — the number is still the number. Blocking would throw away a correct query over
a bad sentence. Flagging withholds the `verified` label, which is exactly the point: a reader
sees `needs_review` and the gotcha naming the unsupported claim. The one thing that DOES stop
the run is the band: if most objects fail, the pipeline is broken rather than the estate.

WHAT GATE 2 CANNOT CATCH: its authority is per-object. It will not catch a column used against
the wrong one of an object's own declared tables, nor a type error. Only execution catches
those — measured at roughly five per run AFTER a clean column gate. That is the honest
argument for turning the witness on, and the honest cost of leaving it off.

GATE 2 BLOCKS ONLY WHAT IT CAN SEE. A column it cannot find on a table it COULD read is a
hallucination and blocks. A column it cannot find because a table could not be read at all is
UNVERIFIED, and flags. The distinction matters under `querylog.scope.match: any`, where a
query may reach past the declared bound by design: blocking there would kill the run for
something we admitted on purpose, and passing it would claim evidence nobody has.
"""
from __future__ import annotations

import json
import pathlib
import sys


from metrics_agent import plug_runtime as _runtime  # noqa: E402
from metrics_agent import socket_gates as _gates  # noqa: E402
from metrics_agent.lib import access as _access  # noqa: E402
from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import evidence as _ev  # noqa: E402
from metrics_agent.lib.card_sql import statement_sha  # noqa: E402
from metrics_agent.lib import faithfulness as _faith  # noqa: E402
from metrics_agent.lib import sqlshape as S  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402
from metrics_agent.lib import whgate  # noqa: E402

STAGE = "c4_gates"
IN = "questioned_candidates.json"
OUT = "verify_report.json"
BLOCKING = {"shape", "column_grounding", "variant_accounting"}


def _database_of(table: str) -> str:
    """The database part of a table name, as `columns_of_many` groups by it ('' if none)."""
    parts = table.split(".")
    return parts[-3].lower() if len(parts) >= 3 else ""


def _access_limit(ctx: stage.Ctx, exc: BaseException) -> None:
    """An access limit met reading columns is a named blocker in the run's access report (the
    same limit A4 met is the same blocker); any other error is only said, as before."""
    found = _access.from_exception(exc)
    if found:
        _access.record(ctx.run, [dict(b, stage=STAGE) for b in found])


def _warehouse_columns(ctx: stage.Ctx, dialect: str, tables: set[str]) -> dict[str, set[str]]:
    """Real columns per table, or {} when the run has no warehouse.

    An empty map is ABSENCE OF EVIDENCE, and the caller records it as such. It is never
    treated as "every column is fine".
    """
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    mode = ri["witness_mode"]
    disclaimer = whgate.degrade(mode, STAGE, "real column lists")
    if disclaimer:
        ctx.say("c4: " + disclaimer)
        return {}
    out: dict[str, set[str]] = {}
    try:
        with _runtime.sql_runner(ri, dialect) as eng:
            # ONE ROUND TRIP PER DATABASE. Asking per table is 215 serial catalogue reads on
            # this estate; the catalogue is one table and answers them all at once. A table
            # absent from the reply stays absent evidence, exactly as a failed read was.
            try:
                batched = eng.columns_of_many(sorted(tables))
            except Exception as exc:
                batched = {}
                ctx.say(f"c4: batched column read failed ({exc}); falling back to per-table.")
            # A DATABASE THAT ANSWERED HAS ANSWERED FOR EVERY TABLE IN IT. A table missing from
            # a database that returned other tables is absent from its catalogue, and asking
            # again one table at a time returns the same nothing — on an estate that reaches
            # past its bound that was one serial round trip per outside table. Only a database
            # with no answer at all (its read failed) is retried per table.
            answered = {_database_of(t) for t in batched}
            for t in sorted(tables):
                if t in batched:
                    out[t] = set(batched[t])
                    continue
                # ONLY A NAME THE CATALOGUE COULD HAVE ANSWERED FOR is known absent. A name that
                # is not a plain identifier (quoted, or carrying SQL) is never put into a
                # catalogue query, so its absence from the reply says nothing about the
                # warehouse: it stays absent evidence, as it was before the batched read.
                if _database_of(t) in answered and _gates.plain_table_name(t):
                    out[t] = set()           # absent from a catalogue that was read
                    continue
                try:
                    out[t] = set(eng.columns_of(t))
                except Exception as exc:
                    _access_limit(ctx, exc)
                    ctx.say(f"c4: could not read columns for {t} ({exc}); recorded as absent "
                            f"evidence, not as a pass.")
    except Exception as exc:
        _access_limit(ctx, exc)
        ctx.say(f"c4: warehouse unreachable ({exc}); column grounding is UNVERIFIED for this "
                f"run, which is absence of evidence rather than a passing check.")
        return {}
    return out


def body(ctx: stage.Ctx) -> None:
    # What this stage met on an earlier pass is replaced by what it meets now.
    _access.clear_stage(ctx.run, STAGE)
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    doc = ctx.read(IN, "candidates.schema.json")
    cands = doc["candidates"]
    dialect = ri["dialect"]

    vocab: set[str] = set()
    vpath = ctx.run / "literal_vocabulary.json"
    if vpath.is_file():
        vocab = {str(v) for v in json.loads(vpath.read_text())["vocabulary"]}
        ctx.entry.inputs.append(str(vpath))

    all_tables = {t for c in cands for t in c["tables"]}
    wh_cols = _warehouse_columns(ctx, dialect, all_tables)

    # The prose C3 authored, and the checkable claims it stated beside it.
    authored: dict[str, dict] = {}
    apath = ctx.run / "c3_authored.json"
    if apath.is_file():
        authored = json.loads(apath.read_text())["authored"]
        ctx.entry.inputs.append(str(apath))

    # A3b's `wrong` findings, and the BI statements as compiled now. WHY THIS GATE: a reviewer
    # who says a statement computes something the widget does not was recorded and then
    # ignored, so the statement still shipped as `verified`. It is held to `needs_review` with
    # the finding as its gotcha, but only while it is the SQL that was reviewed.
    review_wrong: dict[str, list[dict]] = {}
    bi_sql: dict[str, str] = {}
    rpath = ctx.run / "a3b_review.json"
    if rpath.is_file():
        ctx.entry.inputs.append(str(rpath))
        for f in json.loads(rpath.read_text()).get("findings") or []:
            if f.get("severity") == "wrong" and f.get("candidate_id"):
                review_wrong.setdefault(f["candidate_id"], []).append(f)
    bpath = ctx.run / "bi_candidates.json"
    if review_wrong and bpath.is_file():
        bi_sql = {b["candidate_id"]: b.get("sql") or ""
                  for b in json.loads(bpath.read_text())["candidates"]}
    stale_reviews = 0

    checks: list[dict] = []
    flagged: set[str] = set()
    repaired: list[dict] = []
    faith_verdicts: list[dict] = []
    # C5 composes the emitted object's `gotchas` from C3's sidecar, so a note written onto the
    # candidate here would go nowhere. C4 publishes its own and C5 merges them — the stage that
    # found it owns it, and no stage writes into another's artifact.
    faith_gotchas: dict[str, list[str]] = {}

    for c in cands:
        cid = c["candidate_id"]

        # -- 1. shape -------------------------------------------------------
        ok_shape = bool(c.get("sql")) and bool(c.get("tables"))
        checks.append({"gate": "shape", "object": cid,
                       "status": "pass" if ok_shape else "fail",
                       "rule": "candidate carries a statement and at least one table",
                       "path": "sql/tables", "detail": ""})

        # -- 2. column grounding --------------------------------------------
        if not wh_cols:
            checks.append({"gate": "column_grounding", "object": cid, "status": "warn",
                           "rule": "columns unverified — no warehouse this run",
                           "path": "sql",
                           "detail": "absence of evidence, not a passing check"})
        else:
            parsed = True
            try:
                _tree = S.parse(c["sql"], dialect)
                # A NAME THE STATEMENT DEFINES FOR ITSELF IS NOT AN INVENTED COLUMN — but
                # only where it is actually READ from the statement. `columns_to_ground`
                # decides that by the qualifier: a name reached through a base-table handle is
                # grounded whatever the statement aliases, and only an unqualified name, or one
                # reached through a CTE or table function, is excused. Subtracting
                # `projected_names` wholesale instead let `SUM(f.x) AS x` hide an invented `x`.
                used = S.columns_to_ground(_tree)
            except S.ParseError as exc:
                used, parsed = set(), False
                # ONE verdict per candidate and gate: a statement that does not parse has
                # nothing more to ground, so no second entry follows this one.
                checks.append({"gate": "column_grounding", "object": cid, "status": "fail",
                               "rule": "statement must parse to be grounded",
                               "path": "sql", "detail": str(exc)})
            known = set().union(*(wh_cols.get(t, set()) for t in c["tables"])) if c["tables"] else set()
            unknown = sorted(col for col in used if known and col not in known)
            # A table whose column list we could not read is UNVERIFIABLE, not wrong. Under
            # `querylog.scope.match: any` a query may legitimately reach past the declared
            # bound, and its outside tables are exactly the ones the warehouse read misses.
            # Blocking there would kill a run for a reach-outside we admitted on purpose;
            # passing it would claim evidence nobody has. It flags instead.
            # An EMPTY column list means the same thing as a failed lookup: we learned
            # nothing about that table. `columns_of` returns [] for a table that is not
            # there, so keying on presence alone would call it resolved and block the query
            # for columns nobody could have verified.
            unresolved = sorted(t for t in c["tables"] if not wh_cols.get(t))
            if not parsed:
                pass
            elif used and not known:
                # NONE of this candidate's own tables could be read, though others could: the
                # same absence of evidence as an unreachable warehouse, for this candidate.
                # Calling it a pass would claim a check nobody made.
                flagged.add(cid)
                checks.append({
                    "gate": "column_grounding", "object": cid, "status": "warn",
                    "rule": "every column exists on a declared table",
                    "path": "sql",
                    "detail": (f"columns unverified: none of {', '.join(unresolved)} could be "
                               "read. Unverified is not the same as wrong — flagged "
                               "needs_review rather than blocked."),
                })
            elif unknown and unresolved:
                flagged.add(cid)
                checks.append({
                    "gate": "column_grounding", "object": cid, "status": "warn",
                    "rule": "every column exists on a declared table",
                    "path": "sql",
                    "detail": (f"{len(unknown)} column(s) unverified because "
                               f"{', '.join(unresolved)} could not be read: "
                               f"{', '.join(unknown[:6])}. Unverified is not the same as "
                               f"wrong — flagged needs_review rather than blocked."),
                })
            else:
                checks.append({
                    "gate": "column_grounding", "object": cid,
                    "status": "fail" if unknown else "pass",
                    "rule": "every column exists on a declared table",
                    "path": "sql",
                    "detail": (f"unknown columns on tables we CAN read: {', '.join(unknown)}"
                               if unknown else ""),
                })

        # -- 3. literal grounding (REPAIRS, does not block) -------------------
        cites = c["provenance"].get("evidence_citations", [])
        ungrounded = []
        for f in c.get("filters", []):
            val = f.get("value")
            if val is None:
                continue  # parameterized: nothing to ground
            if str(f.get("op") or "").lower() in ("is", "is not", "is_not") \
                    and str(val).strip("'\"").lower() == "null":
                continue  # IS [NOT] NULL carries no literal to ground
            claim = f"{f['column']} {f['op']}"
            if str(val) in vocab or _ev.is_grounded(claim, cites):
                continue
            ungrounded.append(f)
        if ungrounded:
            flagged.add(cid)
            repaired.append({"candidate_id": cid,
                             "stripped": [f"{f['column']} {f['op']} {f.get('value')!r}"
                                          for f in ungrounded]})
            c["filters"] = [f for f in c["filters"] if f not in ungrounded]
            checks.append({
                "gate": "literal_grounding", "object": cid, "status": "warn",
                "rule": "a literal must be parameterized, in the column's observed "
                        "vocabulary, or cited",
                "path": "filters",
                "detail": f"stripped {len(ungrounded)} ungrounded clause(s); object flagged "
                          f"needs_review rather than silently keeping them",
            })
        else:
            checks.append({"gate": "literal_grounding", "object": cid, "status": "pass",
                           "rule": "every literal grounded", "path": "filters", "detail": ""})

        # -- 5. faithfulness (FLAGS, does not block) --------------------------
        a = authored.get(cid) or {}
        v = _faith.check(a.get("claims", []), c.get("sql", ""), dialect,
                         a.get("description", ""))
        faith_verdicts.append(v)
        if not v["passed"]:
            flagged.add(cid)
            note = _faith.gotcha(v)
            if note:
                faith_gotchas.setdefault(cid, []).append(note)
            checks.append({
                "gate": "faithfulness", "object": cid, "status": "warn",
                "rule": "every claim the description makes is present in the statement",
                "path": "description/claims",
                "detail": (f"absent: {v['absent']}" if v["absent"]
                           else f"undeclared restriction language: {v['undeclared']}"),
            })
        else:
            checks.append({
                "gate": "faithfulness", "object": cid,
                "status": "warn" if v["unresolved"] else "pass",
                "rule": "every claim the description makes is present in the statement",
                "path": "description/claims",
                "detail": (f"{len(v['unresolved'])} claim(s) behind a CTE or subquery — "
                           f"unverified, which is not the same as wrong"
                           if v["unresolved"] else ""),
            })

        # -- 4. variant accounting -------------------------------------------
        bad_variants = [v for v in c.get("variants", [])
                        if not str(v.get("label", "")).strip()
                        or not str(v.get("scope_when", "")).strip()
                        or not str(v.get("evidence", "")).strip()]
        checks.append({"gate": "variant_accounting", "object": cid,
                       "status": "fail" if bad_variants else "pass",
                       "rule": "every variant is labelled, scoped and evidenced",
                       "path": "variants",
                       "detail": f"{len(bad_variants)} unscoped variant(s)"
                                 if bad_variants else ""})

        # -- 6. compiler review (FLAGS, does not block) ------------------------
        held = [f for i in [cid] + list(c["provenance"].get("merged_from") or [])
                for f in review_wrong.get(i, [])]
        live = [f for f in held if not f.get("sql_sha")
                or f["sql_sha"] == statement_sha(bi_sql.get(f["candidate_id"], ""))]
        stale_reviews += len(held) - len(live)
        if live:
            flagged.add(cid)
            faith_gotchas.setdefault(cid, []).extend(
                f"COMPILER REVIEW: the reviewer called this statement wrong: {f['finding']}"
                for f in live)
            checks.append({
                "gate": "compiler_review", "object": cid, "status": "warn",
                "rule": "the compiler's reviewer did not call the statement wrong",
                "path": "sql",
                "detail": "; ".join(str(f["finding"])[:300] for f in live)})

    if stale_reviews:
        ctx.say(f"c4: {stale_reviews} `wrong` finding(s) were about SQL a later compile "
                f"replaced, so they hold nothing back. Review the new compile to check it.")

    # The band. A mass-flagging event is a pipeline defect, not a shippable low score.
    band = _faith.band_violation(
        faith_verdicts,
        ctx.policy.get("faithfulness.min_pass_rate"),
        ctx.policy.get("faithfulness.min_sample"))
    if band:
        ctx.entry.verdict = "blocked"
        raise SystemExit("c4: " + band)

    failed = [c for c in checks if c["status"] == "fail" and c["gate"] in BLOCKING]
    clean = not failed
    n_unfaithful = sum(1 for v in faith_verdicts if not v["passed"])
    counts = {"objects": len(cands), "checks": len(checks),
              "failed": len(failed), "unfaithful": n_unfaithful,
              "faith_unresolved": sum(1 for v in faith_verdicts if v["unresolved"]),
              "warned": len([c for c in checks if c["status"] == "warn"]),
              "flagged": len(flagged), "repaired": len(repaired)}

    ctx.write(OUT, {"contract_version": "1.0", "stage": STAGE, "clean": clean,
                    "checks": checks, "counts": counts}, "verify_report.schema.json")
    artifacts.dump(ctx.run / "c4_flagged.json",
                   {"contract_version": "1.0", "flagged": sorted(flagged), "repairs": repaired,
                    "gotchas": faith_gotchas}, "c4_flagged.schema.json")
    artifacts.dump(ctx.run / "c4_candidates.json",
                   {"contract_version": "1.0", "dialect": dialect, "candidates": cands},
                   "candidates.schema.json")
    ctx.entry.outputs.append(str(ctx.run / "c4_flagged.json"))
    ctx.entry.outputs.append(str(ctx.run / "c4_candidates.json"))
    ctx.count(**counts)

    if not clean:
        ctx.entry.verdict = "blocked"
        for f in failed[:10]:
            ctx.say(f"c4 BLOCKED {f['object']}: [{f['gate']}] {f['rule']} — {f['detail']}")
        raise SystemExit(
            f"c4: {len(failed)} blocking failure(s). Fix them; there is no force flag. "
            f"The full report is {OUT}."
        )
    ctx.say(f"c4: clean. {counts['objects']} objects, {counts['warned']} warnings, "
            f"{counts['flagged']} flagged needs_review.")
    if n_unfaithful:
        ctx.say(f"c4: {n_unfaithful} object(s) say something the SQL does not do. Flagged "
                f"needs_review with the unsupported claim named — the words are wrong, not "
                f"the statement.")


def main() -> None:
    stage.main(STAGE, "c4_gates.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
