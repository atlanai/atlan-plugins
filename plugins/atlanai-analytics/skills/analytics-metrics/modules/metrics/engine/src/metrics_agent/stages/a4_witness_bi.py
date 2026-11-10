#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A4 — witness the BI candidates against the warehouse.

Same contract as B6: parse by default, execute on request, and a missing substrate NEVER
aborts the run. A `fail` is reported with its error so A3 can be re-run against it; after the
policy repair budget is spent the unit is QUARANTINED and reported, never silently dropped
and never published as verified.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from collections import Counter


from metrics_agent.lib import access as _access  # noqa: E402
from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent import plug_runtime as _runtime  # noqa: E402
from metrics_agent import sockets as _sockets  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402
from metrics_agent.lib import whgate  # noqa: E402
from metrics_agent.lib import witness as _witness  # noqa: E402
from metrics_agent.lib import quality as _quality  # noqa: E402
from metrics_agent.lib import withheld as _withheld  # noqa: E402

STAGE = "a4_witness_bi"
IN = "bi_candidates.json"


def viewer_names(ri: dict, dialect: str, ctx: stage.Ctx) -> dict[str, str]:
    """The fetching identity's names, for a statement filtered to the viewing user: asked of
    the run's BI plug (`viewer_names`, an optional capability), IN MEMORY. The caller binds
    and then drops them; they are never written, logged or recorded (guardrail 8). {} when no
    plug offers them, or when several do (whose identity a statement is for is not guessed)."""
    offers = []
    for entry in _runtime.served(ri, "bi"):
        try:
            plug = _runtime.build(entry, dialect=dialect)
        except Exception:  # noqa: BLE001 - a plug that cannot be built offers nothing here
            continue
        if callable(getattr(plug, "viewer_names", None)):
            offers.append((entry["name"], plug))
    if len(offers) != 1:
        if offers:
            ctx.say(f"a4: {len(offers)} BI plugs could name a viewer; a statement filtered "
                    "to the viewing user is parsed, not executed.")
        return {}
    name, plug = offers[0]
    try:
        got = plug.viewer_names() or {}
    except Exception as exc:  # noqa: BLE001 - by type only: the message may carry a name
        ctx.say(f"a4: {name} could not name its identity ({type(exc).__name__}); a statement "
                "filtered to the viewing user is parsed, not executed.")
        return {}
    return {str(k): str(v) for k, v in got.items() if k in ("user_name", "full_name") and v}


def withheld_values(ri: dict, ctx: stage.Ctx) -> dict[str, str]:
    """{withheld_N: value} read again from the run's BI files exactly as A1 read them, IN
    MEMORY (lib/withheld.recover). {} when they cannot be read again: a statement that needs
    one is then parsed, not executed."""
    try:
        return _withheld.recover(ri, ctx.policy)
    except Exception as exc:  # noqa: BLE001 - by type only: the message may carry a value
        ctx.say(f"a4: the BI files could not be read again for their withheld literals "
                f"({type(exc).__name__}); a statement comparing with one is parsed, not "
                "executed.")
        return {}


def body(ctx: stage.Ctx) -> None:
    # What this stage met on an earlier pass is replaced by what it meets now.
    _access.clear_stage(ctx.run, STAGE)
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    try:
        doc = ctx.read(IN, "candidates.schema.json")
    except artifacts.ArtifactError:
        ctx.entry.verdict = "stub"
        ctx.entry.notes = "bi-absent: no BI candidates to witness."
        ctx.say("a4: no bi_candidates.json (bi-absent) — nothing to witness.")
        ctx.count(witnessed=0)
        return

    dialect, mode = ri["dialect"], ri["witness_mode"]
    limit = ctx.policy.get("witness.limit_rows")
    sample = ctx.policy.get("witness.sample_rows")
    budget = ctx.policy.get("witness.repair_max_attempts")

    eng = None
    meter = _access.Meter(STAGE)
    if whgate.allows_warehouse(mode):
        try:
            eng = _runtime.sql_runner(ri, dialect)
            eng.connect()
        except Exception as exc:
            if meter.connect_failed(exc):
                # A NAMED ACCESS LIMIT (a refused sign-in, a suspended warehouse): the same
                # parse-only verdicts, each carrying the limit, and one blocker in the run's
                # access report saying what to ask for.
                ctx.say(f"a4: the warehouse refused this identity "
                        f"({meter.run_wide['code']}); every candidate is checked by parsing "
                        "only and the run continues.")
            # NO SUBSTRATE IS NOT A FAILED CHECK (hard rule 4). The runner was built but never
            # connected; left in place, every candidate was "witnessed" against it and came
            # back `missing_object` — quarantined and labelled needs_review for a warehouse
            # that was simply not there. And the type only, as h5 says it: a driver's message
            # can carry the connection string.
            eng = None
            if meter.run_wide is None:
                ctx.say(f"a4: warehouse unreachable ({type(exc).__name__}); every candidate "
                        f"records skipped_no_substrate and the run continues.")
    else:
        ctx.say("a4: " + (whgate.degrade(mode, STAGE, "live execution") or ""))

    quarantined = []
    engine_kind = getattr(eng, "kind", None)
    # Asked once, only when a statement needs them and the fetch said they can be bound;
    # held in this frame and dropped below.
    names: dict[str, str] = {}
    if eng is not None and any((c.get("viewer_binds") or {}).get("bindable")
                               for c in doc["candidates"]):
        names = viewer_names(ri, dialect, ctx)
    # The literals A1 withheld that a statement compares with: read again from the run's own
    # BI files, IN MEMORY, bound, masked out of every verdict, dropped (lib/withheld).
    withheld: dict[str, str] = {}
    if eng is not None and any(c.get("withheld_binds") for c in doc["candidates"]):
        withheld = withheld_values(ri, ctx)
    try:
        for c in doc["candidates"]:
            t0 = time.monotonic()
            vb = c.get("viewer_binds") or {}
            if meter.run_wide is not None and whgate.allows_warehouse(mode):
                # The warehouse already refused this identity for everything: not asked again.
                v = _witness.witness(c["sql"], dialect, "parse")
                if v["result"] == "parse_ok":
                    v = _witness.blocked(_sockets.AccessBlocked([meter.run_wide]))
                    v["mode"] = mode
                meter.skipped(c["candidate_id"])
            else:
                wb = (c.get("withheld_binds") or {}).get("placeholders") or []
                v = _witness.witness(c["sql"], dialect, mode, eng=eng, limit=limit,
                                     sample_rows=sample,
                                     viewer_binds=(vb.get("placeholders") or []) + wb or None,
                                     viewer_names={**(names if vb.get("bindable") else {}),
                                                   **({b["of"]: withheld.get(b["of"], "")
                                                       for b in wb})},
                                     blockers=meter.sink)
                meter.seen(c["candidate_id"])
            c["witness"] = v
            # THE FINAL GRADE: the compile's, or unverified when no warehouse accepted it.
            c["quality"] = _quality.after_witness(c.get("quality"), v, ctx.policy)
            bad = v["result"] in ("fail", "missing_object", "parse_failed")
            if bad:
                quarantined.append({"candidate_id": c["candidate_id"],
                                    "result": v["result"], "error": v.get("error")})
            # ONE DECISION PER STATEMENT. The verdict otherwise lives only inside the
            # candidates artifact, whose span is digest-only once the file is large — so
            # "was this statement ever checked against the warehouse?" had no answer a
            # reader of the trace could reach.
            ctx.decide("witness", c["candidate_id"], v["result"],
                       because=("quarantined: " + str(v.get("error") or "")[:200]) if bad
                       else f"witness mode {mode}",
                       policy_key="witness.repair_max_attempts", policy_value=budget,
                       evidence={"mode": mode, "engine": engine_kind,
                                 "error": v.get("error"), "rows": v.get("rows"),
                                 "hash": v.get("hash"),
                                 "duration_ms": round(1000 * (time.monotonic() - t0), 1),
                                 "unit_id": ((c.get("provenance") or {}).get("bi")
                                             or {}).get("unit_id")})
    finally:
        names.clear()
        del names
        if eng is not None:
            eng.close()

    ctx.write(IN, doc, "candidates.schema.json")
    found = meter.blockers()
    if found:
        _access.record(ctx.run, found)
        ctx.entry.outputs.append(str(ctx.run / _access.REPORT))
        for b in found:
            n = len((b.get("affects") or {}).get("statements") or [])
            ctx.say(f"a4: WARNING access [{b['code']}] {b['not_read']}: {n} statement(s) "
                    f"unverified; ask: {b['ask']['what']}")
    q = {"contract_version": "1.0", "repair_budget": budget,
         "note": "Reported, never silently dropped. Re-run a3 --from with the error text, or "
                 "accept that these units are not published as verified.",
         "quarantined": quarantined}
    artifacts.validate(q, "a4_quarantine.schema.json", what="a4_quarantine.json")
    (ctx.run / "a4_quarantine.json").write_text(json.dumps(q, indent=2) + "\n")
    ctx.entry.outputs.append(str(ctx.run / "a4_quarantine.json"))
    tally = Counter(c["witness"]["result"] for c in doc["candidates"])
    grades = _quality.counts(c["quality"]["grade"] for c in doc["candidates"]
                             if c.get("quality"))
    ctx.count(witnessed=len(doc["candidates"]), quarantined=len(quarantined), mode=mode,
              **{f"verdict_{k}": v for k, v in tally.items()},
              **{f"grade_{k}": v for k, v in grades.items() if v})
    ctx.say(f"a4: mode={mode}; "
            f"{', '.join(f'{k}={v}' for k, v in sorted(tally.items())) or 'nothing'}"
            f"{f'; {len(quarantined)} quarantined' if quarantined else ''}")
    if any(grades.values()):
        ctx.say("a4: conversion quality: " + ", ".join(
            f"{g.replace('_', ' ')} {n}" for g, n in grades.items() if n))


def main() -> None:
    stage.main(STAGE, "a4_witness_bi.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
