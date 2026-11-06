# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The conversion-quality grade of an object compiled from a BI widget.

One word a reader can filter on, and the reason behind it:

  exact                    a complete translation that names no difference from the BI
                           tool's formula, and the warehouse accepted the statement
  equivalent_with_gotcha   the same number under a condition the compiler names (a value fixed
                           at its default, a blank member listed, a learned rule)
  approximate              part of the formula has no SQL form and is named: the number can
                           differ
  unverified               no warehouse checked the statement (parse only, no substrate, an
                           access limit, a failed or empty run): why, by name
  refused                  no statement: the compiler's refusal, by name

THE GRADE NAMES ITS EVIDENCE AND NEVER MAKES A CLAIM TRUE. `exact` rests on the compiler's own
account of what it translated plus the witness; neither compares a value with the BI tool, and
the reason says which checks ran. The order, the witness results that count and every
sentence live in policy (`quality.*`), not here.

Vendor-neutral: it reads the compiler's coverage, gotchas and rules and the witness verdict,
never a formula.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

EXACT, GOTCHA, APPROXIMATE, UNVERIFIED, REFUSED = (
    "exact", "equivalent_with_gotcha", "approximate", "unverified", "refused")
GRADES = (EXACT, GOTCHA, APPROXIMATE, UNVERIFIED, REFUSED)


def _order(policy) -> list[str]:
    order = list(policy.get("quality.order"))
    if sorted(order) != sorted(GRADES):
        raise ValueError(f"quality.order must list each of {', '.join(GRADES)} once")
    return order


def worst(grades: Iterable[str], policy) -> str | None:
    """The worst of `grades` in policy order; None for none."""
    order = _order(policy)
    seen = [g for g in grades if g in order]
    return max(seen, key=order.index) if seen else None


def _cut(text: str, policy) -> str:
    n = int(policy.get("quality.max_reason_chars"))
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _say(grade: str, detail: str, policy) -> str:
    head = str((policy.get("quality.wording") or {}).get(grade) or grade)
    return _cut(f"{head} {detail}".strip() if detail else head, policy)


def compile_grade(*, coverage: str, reason: str | None, untranslatable: list[dict],
                  gotchas: list[str], provisional_rules: list[str], policy) -> dict[str, Any]:
    """The grade the compile alone earns (A3): refused, approximate, equivalent_with_gotcha or
    exact. Verification is added by `after_witness`."""
    if coverage == "refused":
        return {"grade": REFUSED, "reason": _say(REFUSED, reason or "", policy),
                "differences": []}
    if coverage == "partial":
        diffs = [f"{g.get('construct')}" + (f" in {g['in_measure']!r}" if g.get("in_measure")
                                             else "") for g in untranslatable or []]
        return {"grade": APPROXIMATE,
                "reason": _say(APPROXIMATE, reason or "; ".join(diffs), policy),
                "differences": [d for d in diffs if d] or ([reason] if reason else [])}
    notes = [str(g) for g in gotchas or [] if str(g).strip()]
    if provisional_rules:
        capped = str(policy.get("quality.provisional_rule_grade"))
        if capped not in GRADES:
            raise ValueError("quality.provisional_rule_grade must be a grade")
        notes = notes or [f"learned rule(s) {', '.join(sorted(set(provisional_rules)))}"]
        grade = worst([GOTCHA, capped], policy)
        return {"grade": grade, "reason": _say(grade, "; ".join(notes), policy),
                "differences": notes}
    if notes:
        return {"grade": GOTCHA, "reason": _say(GOTCHA, "; ".join(notes), policy),
                "differences": notes}
    return {"grade": EXACT, "reason": _say("compiled_exact", "", policy), "differences": []}


def _verified(witness: dict | None, policy) -> tuple[bool, str]:
    """Whether the warehouse accepted the statement, and the words for why not."""
    w = witness or {}
    result, mode = w.get("result"), w.get("mode")
    if result == "empty" and policy.get("quality.empty_is_verified") \
            and mode in policy.get("quality.verified_modes"):
        return True, ""
    if result in policy.get("quality.verified_results") \
            and mode in policy.get("quality.verified_modes"):
        return True, ""
    if not w:
        return False, "No witness ran."
    access = (w.get("access") or {}).get("code")
    if access:
        return False, f"The warehouse refused this run's identity ({access}): parsed only."
    if result == "parse_ok":
        return False, "The run checks statements by parsing only."
    if result == "disabled":
        return False, "The run turned the witness off."
    if result == "skipped_no_substrate":
        return False, "No warehouse was reachable: parsed only." + (
            f" {w['error']}" if w.get("error") else "")
    if result == "empty":
        return False, "It ran and every value was blank, which usually means a filter is wrong."
    return False, f"The warehouse answered {result}" + (f": {w['error']}" if w.get("error")
                                                         else ".")


def after_witness(q: dict | None, witness: dict | None, policy) -> dict[str, Any]:
    """The final grade (A4 and after): the compile's grade, or `unverified` when no warehouse
    accepted the statement, whichever is worse. `compile_grade` keeps the compile's own."""
    q = dict(q or {})
    base = q.get("compile_grade") or q.get("grade") or UNVERIFIED
    out = {"compile_grade": base, "differences": list(q.get("differences") or [])}
    if base == REFUSED:
        return {**out, "grade": REFUSED, "reason": q.get("reason") or _say(REFUSED, "", policy),
                "verified_by": None}
    ok, why = _verified(witness, policy)
    if not ok:
        grade = worst([base, UNVERIFIED], policy)
        detail = why + (f" Compiled as {base.replace('_', ' ')}." if base != UNVERIFIED else "")
        return {**out, "grade": grade, "reason": _say(grade, detail, policy),
                "verified_by": None}
    by = f"witness:{(witness or {}).get('mode')}"
    reason = q.get("reason") or _say(base, "", policy)
    if base == EXACT:
        reason = _say(EXACT, f"Checked: {by}.", policy)
    return {**out, "grade": base, "reason": reason, "verified_by": by}


def of_statements(by_statement: dict[str, dict], policy) -> dict[str, Any] | None:
    """A metric's grade from the BI statements it was merged from: the worst of theirs, the
    reason that statement gives, and each statement's grade. None when no statement carries
    one (a metric of the query-history lane only)."""
    graded = {k: v for k, v in by_statement.items() if v and v.get("grade") in GRADES}
    if not graded:
        return None
    grade = worst((v["grade"] for v in graded.values()), policy)
    first = next(k for k in sorted(graded) if graded[k]["grade"] == grade)
    return {"grade": grade, "reason": graded[first].get("reason") or _say(grade, "", policy),
            "statements": {k: graded[k]["grade"] for k in sorted(graded)},
            "counts": dict(sorted(Counter(v["grade"] for v in graded.values()).items()))}


def counts(grades: Iterable[str]) -> dict[str, int]:
    """How many of each grade, every grade listed (0 included), in the canonical order."""
    c = Counter(grades)
    return {g: c.get(g, 0) for g in GRADES}
