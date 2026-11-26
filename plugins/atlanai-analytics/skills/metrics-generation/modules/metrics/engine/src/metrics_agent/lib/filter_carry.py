# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A CARD CARRIES EVERY FILTER ITS MERGE DECIDED.

The merge settles which filters a metric always applies and which a reader may set. The card
rewrites each one in qualified SQL, often as a parameter, and a filter can fall out on the way:
a date window dropped while the date literal became a parameter, a fixed campaign turned into
a free choice. Nothing downstream can tell, because the card is the only definition left.

Filters are compared by what they constrain, not by their text: each predicate is reduced to
(column, kind), kind being `eq` (= and IN), `not` (<>, NOT IN), `range` (<, >, BETWEEN),
`like` or `null`. A merge filter written in words that do not parse is not checked.
"""
from __future__ import annotations

import json
import re

_PARAM = re.compile(r"\{\{\s*\w+\s*\}\}")


def _filter_text(f) -> str:
    return str(f.get("filter") if isinstance(f, dict) else f or "")


def pairs(text: str, dialect: str = "", from_clause: bool = False,
          select: bool = False) -> set | None:
    """{(column, kind)} the text constrains, or None when it does not parse. A WHERE condition
    by default; a FROM clause (`source.sql`) with `from_clause`, or a formula with `select`
    (a `CASE WHEN` inside it filters too), every level counted."""
    import sqlglot
    from sqlglot import exp
    t = _PARAM.sub("'p'", str(text or "")).strip()
    if not t:
        return set()
    try:
        tree = sqlglot.parse_one(f"SELECT 1 FROM {t}" if from_clause else
                                 f"SELECT {t}" if select else
                                 f"SELECT 1 WHERE {t}", read=dialect or None)
    except Exception:
        return None
    if tree is None:
        return None
    kinds = ((exp.EQ, "eq"), (exp.In, "eq"), (exp.NEQ, "not"), (exp.GT, "range"),
             (exp.GTE, "range"), (exp.LT, "range"), (exp.LTE, "range"),
             (exp.Between, "range"), (exp.Like, "like"), (exp.ILike, "like"),
             (exp.Is, "null"))
    out = set()
    for node in tree.walk():
        kind = next((k for cls, k in kinds if isinstance(node, cls)), None)
        if kind is None:
            continue
        # `x IS NULL` only: words that happen to parse ("account is AI-ready") are not a filter.
        if kind == "null" and not isinstance(node.expression, exp.Null):
            continue
        # The merge filter's own predicates, not those inside a subquery it holds.
        if not (from_clause or select) and node.find_ancestor(exp.Select) is not tree:
            continue
        if kind == "eq" and isinstance(node.parent, exp.Not):
            kind = "not"
        col = node.this if isinstance(node.this, exp.Column) else node.this.find(exp.Column) \
            if node.this is not None else None
        if col is None and isinstance(node, (exp.EQ, exp.NEQ)):
            col = node.expression.find(exp.Column) if node.expression is not None else None
        if col is not None and col.name:
            out.add((col.name.lower(), kind))
    return out


def _columns(text: str, dialect: str) -> set:
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one("SELECT " + _PARAM.sub("'p'", text), read=dialect or None)
    except Exception:
        return set()
    return {c.name.lower() for c in tree.find_all(exp.Column) if c.name} if tree else set()


def lost(card: dict, merged: dict, dialect: str = "") -> list[str]:
    """What the card dropped of its merged metric's filters, one sentence each."""
    fixed, chosen = set(), set()
    for f in card.get("mandatory_filters") or []:
        fixed |= pairs(f, dialect) or set()
    for f in list(card.get("filters") or []) + list(card.get("exclusions") or []):
        chosen |= pairs(f, dialect) or set()
    q = str((card.get("source") or {}).get("sql") or card.get("sql") or "")
    if q:
        fixed |= pairs(q, dialect, from_clause=True) or set()
    formula = str(card.get("formula") or "")
    fixed |= pairs(formula, dialect, select=True) or set()
    # An aggregate over a column already skips its nulls: `COUNT(DISTINCT x)` needs no
    # `x IS NOT NULL` beside it.
    fixed |= {(c, "null") for c in _columns(formula, dialect)}
    notes = json.dumps(card.get("notes") or "").lower()
    out = []
    for field in ("mandatory_filters", "optional_filters"):
        for f in merged.get(field) or []:
            text = _filter_text(f)
            want = pairs(text, dialect)
            if not want:
                continue
            gone = {p for p in want if p not in fixed | chosen and p[0] not in notes}
            if gone:
                out.append(
                    f"the merge's {field.split('_')[0]} filter `{text[:120]}` is not on the "
                    f"card ({', '.join(f'{c} {k}' for c, k in sorted(gone))}). Carry it into "
                    f"mandatory_filters, or into filters as a parameter, or say in `notes` why "
                    f"the card leaves it out.")
                continue
            loose = {p for p in want if p[1] == "eq" and p not in fixed and p[0] not in notes}
            if field == "mandatory_filters" and loose:
                out.append(
                    f"the merge fixed `{text[:120]}`, but the card only offers it as an "
                    f"optional filter. Keep it in mandatory_filters, or say in `notes` why a "
                    f"reader may change it.")
    return out
