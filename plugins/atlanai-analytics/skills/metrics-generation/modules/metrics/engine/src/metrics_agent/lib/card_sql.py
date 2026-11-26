# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a metric card's SQL is and reads, derived from the card alone.

Core, not any destination's: the metric stages compose a card's statement for their own
checks, and an output plug that needs the statement or the tables it reads gets them here, so
two destinations never derive them two ways.
"""
from __future__ import annotations

import hashlib
import re

# A date written into a filter. `>= '2026-04-22'` was right the day it was written and narrows
# every day after: on a card it is a parameter's default, never a definitional restriction.
# One rule, read by X1 (a proposal's mandatory filters) and X3 (a card's).
DATE_LITERAL = re.compile(r"(?i)(?:\bDATE\s*)?'\d{4}-\d{2}-\d{2}(?:[ T][0-9:.]+)?'")


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


# Where a double-quoted token is a STRING, not a name, so an alias has to be written in backticks.
# T-SQL is left with double quotes: it reads them as identifiers by default, so its statements
# (and the evidence recorded against them) keep their bytes.
_BACKTICK_DIALECTS = frozenset({"databricks", "spark", "spark2", "hive", "bigquery", "mysql",
                                "doris", "starrocks", "singlestore", "drill"})


def quoted_identifier(name: str, dialect: str | None) -> str:
    """`name` as a quoted identifier in `dialect`: backticks where a double-quoted token is a
    string (Databricks, Spark, Hive, BigQuery, the MySQL family), the standard double-quoted
    form everywhere else, and for no dialect or one not listed.

    PLAIN STRING WORK, NO PARSER. This module is loaded by file path from the project helper,
    which runs under the person's own Python with nothing installed, so it imports nothing
    beyond the standard library. Both it and the engine therefore build the same statement
    for the same card.

    The quote character inside a name is doubled, which each of these dialects and the
    engine's own parser read back as one character. BigQuery is the exception: it reads a
    quoted name with a string literal's backslash escapes, the parser reads a doubled
    backtick, and no spelling of a backtick or a backslash is read the same by both. A name
    must never be able to close its own quotes for one reader and not the other, so on
    BigQuery those two characters are written as underscores. ClickHouse reads a backslash
    escape inside a double-quoted name, so a doubled quote does not hold there either
    (`a\\"` would end the name): its backslash and double quote are written as underscores too."""
    text = str(name)
    key = dialect.strip().lower() if isinstance(dialect, str) else ""
    if key == "bigquery":
        return "`" + text.replace("\\", "_").replace("`", "_") + "`"
    if key == "clickhouse":
        return '"' + text.replace("\\", "_").replace('"', "_") + '"'
    if key in _BACKTICK_DIALECTS:
        return "`" + text.replace("`", "``") + "`"
    return '"' + text.replace('"', '""') + '"'


def canonical_statement(card: dict, dialect: str | None = None) -> str:
    """The metric's definition as one SELECT, composed from the fields that define it.

    `source.sql` is the FROM clause, not a statement — Atlan Trident's `Source` is "a table
    or SQL source" — so nothing on the card is a runnable query any more. A reader is still
    owed one, and it is four pieces concatenated: the measure, the source, the restrictions
    that are part of what the metric IS.

    Deliberately not the compiler. This is the human-facing surface and it needs a sentence
    of SQL, not a compilation — and the agent does not depend on the compiler to publish.

    The output alias and a derived source's alias are quoted for `dialect`, or the card's own
    `dialect` when none is passed: the statement is run on the card's warehouse.
    """
    # A blank dialect argument says nothing, so the card's own dialect still decides.
    dialect = (dialect.strip() if isinstance(dialect, str) else dialect) or card.get("dialect")
    formula = str(card.get("formula") or "").strip()
    alias = str(((card.get("output") or {}).get("alias")) or card.get("name") or "").strip()
    frm = str(card.get("sql") or (card.get("source") or {}).get("sql") or "").strip() \
        or str(card.get("entity") or (card.get("source") or {}).get("table") or (card.get("source") or {}).get("entity") or "").strip()
    if frm.startswith('(') and frm.endswith(')') and (card.get('source') or {}).get('alias'):
        frm += f" AS {quoted_identifier(card['source']['alias'], dialect)}"
    if not formula or not frm:
        return ""
    out = [f"SELECT {formula}" + (f" AS {quoted_identifier(alias, dialect)}" if alias else ""),
           f"FROM   {frm}"]
    mand = [str(f).strip() for f in (card.get("mandatory_filters") or []) if str(f).strip()]
    if mand:
        out.append("WHERE  " + "\n  AND  ".join(mand))
    return "\n".join(out)


_MARKER = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}")
_QUOTED = re.compile(r"('(?:''|[^'])*')")


def bound(sql: str, parameters: list | None) -> str:
    """`sql` with each `{{name}}` outside a text value replaced by its parameter's default,
    written as a literal. A card's statement cannot run with a marker in it, and the default
    is the producer's own value, so this is the statement a reader gets by changing nothing.
    A marker with no declared default is left as it is."""
    defaults = {str(p.get("name")): p["default"] for p in parameters or []
                if isinstance(p, dict) and p.get("default") is not None}
    if not defaults or "{{" not in sql:
        return sql

    def literal(match: re.Match) -> str:
        if match.group(1) not in defaults:
            return match.group(0)
        value = defaults[match.group(1)]
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    return "".join(part if i % 2 else _MARKER.sub(literal, part)
                   for i, part in enumerate(_QUOTED.split(sql)))


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
