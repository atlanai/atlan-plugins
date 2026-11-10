# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a metric card's SQL is and reads, derived from the card alone.

Core, not any destination's: the metric stages compose a card's statement for their own
checks, and an output plug that needs the statement or the tables it reads gets them here, so
two destinations never derive them two ways.
"""
from __future__ import annotations

import hashlib


def statement_sha(sql: str) -> str:
    """Which statement a review read. A3b stamps it on each finding and C4 compares it with the
    statement now in the run: a finding about SQL a later recompile replaced is about nothing."""
    return "sha256:" + hashlib.sha256(str(sql or "").strip().encode()).hexdigest()[:16]


def tables_of(sql: str, entity, joins: list, dialect: str | None) -> list:
    """Every dotted table the canonical statement reads — the metric's link targets.

    THIS BELONGS ON THE CARD AND IS NOT THERE. The card's shape is frozen, so an output plug
    derives it per run instead of reading it off the spec, through this function, so every
    destination derives it the same way.

    The linker cannot work from SQL alone: it needs a DECLARED list to complete a partially
    qualified reference (`sales` -> `retail.merch.sales`, and only when exactly one declared
    table ends with it), and to link anything at all when a statement will not parse. So the
    parsed names are unioned with `entity` and every join target.

    CASE IS PRESERVED EXACTLY AS THE SOURCE SPELLS IT. These names are resolved against the
    warehouse; Snowflake quoted identifiers, Databricks and Postgres are case-sensitive, so a
    folded name is a different object. Duplicates collapse case-INSENSITIVELY and the
    statement's own spelling wins over the declared one, because the statement is what ran.
    """
    import sqlglot  # noqa: PLC0415 — only this one function needs a parser

    from metrics_agent.lib import sqlshape as S  # noqa: PLC0415

    out: dict[str, str] = {}
    try:
        tree = sqlglot.parse_one(sql or "", read=dialect or None)
    except Exception:                       # noqa: BLE001 — declared names still carry it
        tree = None
    if tree is not None:
        for name in S.extract_tables(tree, preserve_case=True):
            out.setdefault(name.lower(), name)
    for declared in [entity] + [j.get("to") for j in (joins or [])]:
        if declared:
            out.setdefault(str(declared).lower(), str(declared))
    return [out[k] for k in sorted(out)]


def canonical_statement(card: dict) -> str:
    """The metric's definition as one SELECT, composed from the fields that define it.

    `source.sql` is the FROM clause, not a statement — the registry's `Source` is "a table
    or SQL source" — so nothing on the card is a runnable query any more. A reader is still
    owed one, and it is four pieces concatenated: the measure, the source, the restrictions
    that are part of what the metric IS.

    Deliberately not the compiler. This is the human-facing surface and it needs a sentence
    of SQL, not a compilation — and the agent does not depend on the compiler to publish.
    """
    formula = str(card.get("formula") or "").strip()
    alias = str(((card.get("output") or {}).get("alias")) or card.get("name") or "").strip()
    frm = str(card.get("sql") or (card.get("source") or {}).get("sql") or "").strip() \
        or str(card.get("entity") or (card.get("source") or {}).get("table") or (card.get("source") or {}).get("entity") or "").strip()
    if frm.startswith('(') and frm.endswith(')') and (card.get('source') or {}).get('alias'):
        name = str(card['source']['alias']).replace('"', '""')
        frm += f' AS "{name}"'
    if not formula or not frm:
        return ""
    out = [f'SELECT {formula}' + (f' AS "{alias}"' if alias else ""), f"FROM   {frm}"]
    mand = [str(f).strip() for f in (card.get("mandatory_filters") or []) if str(f).strip()]
    if mand:
        out.append("WHERE  " + "\n  AND  ".join(mand))
    return "\n".join(out)


def aggregation_problems(sql: str, dialect: str | None) -> list[str]:
    """Reject invalid aggregation within a query scope without executing SQL.

    A subquery starts a new scope. An aggregate used as a window function may consume a
    grouped aggregate, but a regular aggregate cannot consume another aggregate or window.
    """
    from sqlglot import exp
    from metrics_agent.lib import sqlshape

    try:
        tree = sqlshape.parse(sql, dialect)
    except Exception:
        return ["the canonical statement cannot be parsed for aggregation checks"]
    problems = set()
    for node in tree.walk():
        if not isinstance(node, (exp.AggFunc, exp.Window)):
            continue
        parent = node.parent
        while parent is not None and not isinstance(parent, exp.Query):
            if isinstance(parent, (exp.Where, exp.Join, exp.Group)):
                problems.add("an aggregate or window appears in a row filter, join condition or grouping key")
            if isinstance(parent, exp.AggFunc):
                windowed = isinstance(parent.parent, exp.Window) and parent.parent.this is parent
                if not windowed:
                    problems.add("nested aggregates or windows in a regular aggregate require a separate query scope")
            if isinstance(node, exp.Window) and isinstance(parent, exp.Window):
                problems.add("nested window functions require a separate query scope")
            parent = parent.parent
    return sorted(problems)
