# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Holds already certain before the prose gates, so they describe a held statement briefly.

Publication decides holds at export, from the completed run. Two of them are settled long
before: a unit that needs one member selected and was captured with none (a drill-through
page opened on no member), and a statement the compiler's reviewer called `wrong` while it is
still the SQL that was reviewed. Each prediction is exactly the condition export (and C4)
applies, never a looser one: a statement this names is held at export whatever its prose
says, so its prose is never published. Nothing here holds anything; it only says so early.
"""
from __future__ import annotations

import json
import pathlib

from metrics_agent.lib.card_sql import statement_sha


def _selected(unit: dict, column: str) -> bool:
    """A captured member selection for `column`: the same test export applies."""
    for f in unit.get("filters") or []:
        if str(f.get("column", "")).casefold() != column.casefold() or f.get("value_kind") == "absent":
            continue
        op, value = str(f.get("op", "")).lower(), f.get("value")
        if (op in ("eq", "=") and value is not None or op == "in" and isinstance(value, list)
                and value or op == "is" and value is None):
            return True
    return False


def member_scope_missing(unit: dict) -> list[str]:
    """The required members a unit names and never captured a selection for, when it is not
    a declared per-member variant. Export holds such a unit's statement whatever its SQL."""
    if unit.get("variant_of"):
        return []
    required = [p for p in unit.get("parameters") or [] if p.get("kind") == "member"]
    return [p["column"] for p in required if not _selected(unit, str(p.get("column") or ""))]


def predicted(run: pathlib.Path) -> dict[str, dict]:
    """{candidate_id: {"reason", "detail"}} for the BI statements already certain to be held."""
    run = pathlib.Path(run)
    out: dict[str, dict] = {}
    cpath = run / "bi_candidates.json"
    if not cpath.is_file():
        return out
    cands = json.loads(cpath.read_text()).get("candidates") or []
    upath = run / "question_units.json"
    units = ({u["unit_id"]: u for u in json.loads(upath.read_text()).get("units") or []}
             if upath.is_file() else {})
    for c in cands:
        unit = units.get(((c.get("provenance") or {}).get("bi") or {}).get("unit_id") or "")
        missing = member_scope_missing(unit) if unit else []
        if missing:
            out[c["candidate_id"]] = {
                "reason": "required_filter_missing",
                "detail": "the view needs one member selected and none was captured: "
                          + ", ".join(missing)}
    rpath = run / "a3b_review.json"
    if rpath.is_file():
        sql = {c["candidate_id"]: c.get("sql") or "" for c in cands}
        for f in json.loads(rpath.read_text()).get("findings") or []:
            cid = f.get("candidate_id")
            if (f.get("severity") == "wrong" and cid in sql
                    and f.get("sql_sha") == statement_sha(sql[cid])):
                out.setdefault(cid, {"reason": "compiler_review_wrong",
                                     "detail": str(f.get("finding") or "")[:300]})
    return out
