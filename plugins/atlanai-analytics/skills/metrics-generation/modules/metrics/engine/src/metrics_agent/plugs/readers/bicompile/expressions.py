# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The context an expression compiles in: DAX date sets, the unit's groups and filters, rule templates, and compile_expression. Part of bicompile; see __init__.py."""
from __future__ import annotations

import re
from typing import Any

from .common import (  # noqa: F401
    CompileError,
)

class _NotSimple(Exception):
    """A CALCULATE filter argument that is not a plain row predicate. Internal: it means "the
    declared CALCULATE limit applies", never an error of its own."""


class _TimeIntelligence(Exception):
    """Raised by a formula that reads a date set, outside the date-set statement: the unit is
    compiled again as one (`_compile_time_unit`). Internal, never a refusal of its own."""


_REMOVERS = {"ALL", "REMOVEFILTERS", "ALLEXCEPT", "ALLSELECTED"}
_DATE_SETS = {"DATESYTD", "DATESQTD", "DATESMTD", "SAMEPERIODLASTYEAR", "DATEADD",
              "PARALLELPERIOD", "DATESINPERIOD", "DATESBETWEEN", "PREVIOUSMONTH",
              "PREVIOUSQUARTER", "PREVIOUSYEAR"}
_PERIOD_TOTALS = {"TOTALYTD", "TOTALQTD", "TOTALMTD"}


class _DateSet:
    """The dates one time-intelligence filter reads, for one row of the visual, as a function
    of the row's own dates. `kind`:

      to_date    from the start of the period (year, quarter, month; a fiscal year from the
                 day after `year_end`) holding the row's last date, to that date;
      shift      each of the row's dates moved by `n` days or months; a month-end date whose
                 day before is also the row's extends to the end of the month it lands in
                 (seen live: February 2023 a year on is all 29 days of February 2024; one
                 day, 28 February 2023, is 28 February 2024 alone);
      parallel   the whole periods (`months` long) from the row's first to its last date,
                 `n` periods on; PREVIOUS* is the one period before the first date;
      in_period  `n` days or months back from (or on from) the row's last date;
      between    two literal dates;
      lookup     the dates whose `match` column is one of the row's dates' `via` values
                 (`FILTER(ALL(D), D[match] IN VALUES(D[via]))`); `column` is then the key
                 the measure's table reaches the date table on, not a date column."""

    def __init__(self, kind: str, column: tuple[str, str], **kw: Any) -> None:
        self.kind, self.column = kind, column
        self.period = kw.get("period")
        self.year_end = kw.get("year_end")
        self.part = kw.get("part")
        self.n = kw.get("n")
        self.months = kw.get("months")
        self.previous = kw.get("previous", False)
        self.lo, self.hi = kw.get("lo"), kw.get("hi")
        self.anchor = kw.get("anchor")
        self.anchor_fact = kw.get("anchor_fact")
        self.open_end = kw.get("open_end", False)
        self.match, self.via = kw.get("match"), kw.get("via")

    @property
    def key(self) -> tuple:
        return (self.kind, self.column, self.period, self.year_end, self.part, self.n,
                self.months, self.previous, self.lo, self.hi, self.anchor, self.anchor_fact,
                self.open_end, self.match, self.via)


class _SqlDate(str):
    """A date bound that is SQL (the current date), not an ISO literal."""


class _DateChain:
    """The dates a slot reads, as steps applied to the row's own dates in order: each step
    is one date set, or several in one CALCULATE (each read from the same dates, then
    intersected). A date set inside another's CALCULATE is the next step: it reads the dates
    the step before it left."""

    def __init__(self, steps) -> None:
        self.steps = tuple(tuple(step) for step in steps)
        cols = {ds.column for step in self.steps for ds in step}
        if len(cols) != 1:
            raise CompileError("time intelligence over two date columns in one CALCULATE "
                               "is not compiled")
        self.column = next(iter(cols))

    @classmethod
    def then(cls, chain: "_DateChain | None", sets: list["_DateSet"]) -> "_DateChain":
        return cls((chain.steps if chain is not None else ()) + (tuple(sets),))

    @property
    def key(self) -> tuple:
        return tuple(tuple(ds.key for ds in step) for step in self.steps)


def _year_end(toks) -> tuple[int, int]:
    """A fiscal year-end literal, `"6-30"` or `"30/6"` (the order is the model's locale): the
    day of a month end is 28 or more, so it is never read as the month."""
    if len(toks) != 1 or toks[0][0] != "str":
        raise CompileError("a year-end date is compiled as a string literal such as \"6-30\"")
    m = re.fullmatch(r"\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*", toks[0][1][1:-1])
    if not m:
        raise CompileError(f"year-end date {toks[0][1]} is not month and day")
    a, b = int(m.group(1)), int(m.group(2))
    month, day = (a, b) if b > 12 else (b, a) if a > 12 else (None, None)
    last = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30,
            12: 31}
    if month is None or not 1 <= month <= 12 or day not in (last[month], 29 if month == 2
                                                            else -1):
        raise CompileError(f"year-end date {toks[0][1]}: only a month's last day is compiled")
    return month, day


def dax_year(year: int) -> int:
    """The year DAX's DATE reads from its first argument: a two-digit year is windowed (0 to
    49 is 2000 to 2049, 50 to 99 is 1950 to 1999); 100 and up is the year itself (Power BI's
    own answers: DATE(99, 1, 1) is 1999-01-01, DATE(100, 1, 1) is 0100-01-01)."""
    if 0 <= year <= 49:
        return year + 2000
    if 50 <= year <= 99:
        return year + 1900
    return year


def dax_year_sql(year: str) -> str:
    """`dax_year` over a SQL expression; a number literal is folded."""
    if re.fullmatch(r"\d+", year.strip()):
        return str(dax_year(int(year.strip())))
    if re.fullmatch(r"\(?\s*-\s*\d+\s*\)?", year.strip()):
        return year
    return (f"(CASE WHEN {year} BETWEEN 0 AND 49 THEN {year} + 2000 "
            f"WHEN {year} BETWEEN 50 AND 99 THEN {year} + 1900 ELSE {year} END)")


def _date_literal(toks) -> str:
    """`DATE(2025, 1, 31)` -> '2025-01-31'."""
    head, inner = _call_head(toks)
    parts = _split_top(inner) if head == "DATE" else []
    if len(parts) != 3 or any(len(x) != 1 or x[0][0] != "num" for x in parts):
        raise CompileError("a date bound is compiled as DATE(year, month, day) of literals")
    import datetime as _dt  # noqa: PLC0415 - only time intelligence needs it
    try:
        y, m, d = (int(x[0][1]) for x in parts)
        return _dt.date(dax_year(y), m, d).isoformat()
    except ValueError as exc:
        raise CompileError(f"DATE literal: {exc}") from None


def _semi_join(pred: str, joins: list[str], alias: str) -> str:
    """`pred` (on joined tables' columns, `j<n>.c`) as a test of the statement's own row
    (`alias`): EXISTS a related row, along the joins that reach the columns it reads, that
    passes it. Each join is `<kind> JOIN <table> j<n> ON <condition>`."""
    parsed = {}
    for j in joins:
        m = re.match(r"(?is)^\s*(?:(?:LEFT|RIGHT|FULL)(?:\s+OUTER)?|INNER)?\s*JOIN\s+(.+?)\s+"
                     r"(j\d+)\s+ON\s+(.+)$", j)
        if m:
            parsed[m.group(2)] = (m.group(1), m.group(3).strip())
    need: list[str] = []

    def reach(a: str) -> None:
        if a in need:
            return
        if a not in parsed:
            raise CompileError(f"it reads {a}, which no join of the statement reaches")
        for b in re.findall(r"\b(j\d+)\.", parsed[a][1]):
            if b != a:
                reach(b)
        need.append(a)
    for a in dict.fromkeys(re.findall(r"\b(j\d+)\.", pred)):
        reach(a)
    first, rest = need[0], need[1:]
    table, on = parsed[first]
    if re.search(r"\bj\d+\.", re.sub(rf"\b{first}\.", "", on)):
        raise CompileError("its first join reads another joined table")
    body = f"SELECT 1\n        FROM   {table} {first}" + "".join(
        f"\n        JOIN   {parsed[a][0]} {a} ON {parsed[a][1]}" for a in rest)
    return f"EXISTS (\n        {body}\n        WHERE  {on}\n          AND  {pred})"


def _map_aggregates(sql: str, fn) -> str:
    """`sql` with each outermost aggregate call (SUM, COUNT, AVG, MIN, MAX, MEDIAN and its
    balanced argument) replaced by `fn(call)`."""
    out, i = [], 0
    while True:
        m = _AGGREGATE_CALL.search(sql, i)
        if not m:
            out.append(sql[i:])
            return "".join(out)
        depth, j = 0, m.end() - 1
        while j < len(sql):
            if sql[j] == "(":
                depth += 1
            elif sql[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(sql[i:m.start()])
        out.append(fn(sql[m.start():j + 1]))
        i = j + 1


def _reads_rows(call: str) -> bool:
    """Whether an aggregate call reads a field of the rows (`s.<column>`), not only inner
    levels of detail (`s.__lodin<n>`)."""
    return bool(re.search(r"\bs\.(?!__lodin\d|__lodr\d|e\d+\b)\w", call))


def _top_colon(body: list) -> int | None:
    """The index of the `:` that ends a level of detail's dimensions: the first one outside
    any brace (a dimension may itself be a level of detail)."""
    depth = 0
    for j, tok in enumerate(body):
        if tok == ("brace", "{"):
            depth += 1
        elif tok == ("brace", "}"):
            depth -= 1
        elif tok[0] == "colon" and depth == 0:
            return j
    return None


def _call_args(t: list, start: int) -> tuple[int | None, list[list]]:
    """The arguments of the call whose `(` is at `start`: (index of its `)`, each argument's
    tokens). A brace opens a table constructor, whose commas are not the call's."""
    depth, j, cur, args = 0, start, [], []
    while j < len(t):
        k, v = t[j]
        if k == "lparen" or (k == "brace" and v == "{"):
            depth += 1
            if depth == 1:
                j += 1
                continue
        elif k == "rparen" or (k == "brace" and v == "}"):
            depth -= 1
            if depth == 0:
                if cur or args:
                    args.append(cur)
                return j, args
        elif k == "comma" and depth == 1:
            args.append(cur)
            cur = []
            j += 1
            continue
        cur.append(t[j])
        j += 1
    return None, []


# The aggregates a shelf or a calc may put around a level-of-detail expression, and how the
# view's rows (one per combination of its dimensions and the LOD's) are aggregated to it.
# The placeholder each kind of level of detail stands in the SQL as until the statement is
# assembled (`_lod_parts`, `_grain_statement`).
_LOD_MARK = {"row": "__LODROW{n}__", "scalar": "__LODONE{n}__", "grain": "__LODAGG{n}__"}
# A non-additive aggregate over a wider context than the visual's row (`_combine`): its own
# statement over the same rows, resolved by `_context_statements`.
_CTX_MARK = "__CTXAGG{n}__"
_LOD_OUTER = {"SUM": "SUM", "AVG": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT",
              "COUNTD": "COUNT(DISTINCT", "MEDIAN": "MEDIAN"}


def _brace_end(t: list, start: int) -> int | None:
    """The index of the `}` closing the `{` at `start`."""
    depth = 0
    for j in range(start, len(t)):
        if t[j] == ("brace", "{"):
            depth += 1
        elif t[j] == ("brace", "}"):
            depth -= 1
            if depth == 0:
                return j
    return None


def _call_head(toks: list) -> tuple[str | None, list]:
    """`NAME ( … )` spanning all of `toks` -> (NAME upper, the tokens inside), else (None, [])."""
    if len(toks) >= 3 and toks[0][0] == "func" and toks[1][0] == "lparen":
        end, _args = _call_args(toks, 1)
        if end == len(toks) - 1:
            return toks[0][1].upper(), toks[2:-1]
    return None, []


def _split_top(toks: list) -> list[list]:
    """`toks` split on its top-level commas."""
    out, cur, depth = [], [], 0
    for k, v in toks:
        if k == "lparen" or (k == "brace" and v == "{"):
            depth += 1
        elif k == "rparen" or (k == "brace" and v == "}"):
            depth -= 1
        if k == "comma" and depth == 0:
            out.append(cur)
            cur = []
            continue
        cur.append((k, v))
    if cur or out:
        out.append(cur)
    return out


class _UnitContext:
    """What a formula needs to know about the statement it is compiled into."""

    def __init__(self, source: str) -> None:
        self.source = _bare(source)
        self.groups: list[tuple[str, str | None, str]] = []     # (column, table, its SQL)
        self.filters: list[tuple[str, str | None]] = []
        # Each filter of the visual as (column, table, op, value), for a test of the context.
        self.filter_values: list[tuple[str, str | None, str, Any]] = []
        # The statement's own table as the warehouse names it.
        self.source_table = source
        self.top_n = None
        # The visual's Top N filters that keep the members a ranking keeps (row predicates of
        # the statement), as (column, table).
        self.top_n_filters: list[tuple[str, str | None]] = []
        self.plan: "_JoinPlan | None" = None
        self.unplaced: dict[tuple[str, str], str] = {}
        self.relationships_used: set = set()
        self.types: dict[tuple[str, str], str] = {}
        self.row_formulas: set[str] = set()
        # (date table, its date column) -> how the measure's table reaches it.
        self.date_keys: dict[tuple[str, str], dict] = {}
        # (key column, date table) -> how the statement's table reaches the date table, for a
        # lookup date set (`_lookup_key`): any key, a date or not.
        self.lookup_keys: dict[tuple[str, str], dict] = {}
        self.graph: list[dict] = []
        self.text_key = False
        self.source_compared = False
        # (warehouse table, bare, lower; model column, lower) -> the column the table reads it
        # as, where the two differ: a formula names a column by its model name.
        self.renames: dict[tuple[str, str], str] = {}
        # The measure being compiled runs over the statement's grouped rows (a table
        # calculation): {partition: [SQL], order: [SQL]}. None otherwise.
        self.table_calc: dict | None = None
        # The statement's level-of-detail expressions, as the parsers register them.
        self.lods: list[dict] = []
        # Non-additive aggregates over a wider context (ALL / ALLSELECTED of grouped
        # columns): {n, sql, keep}, each its own statement over the same rows.
        self.context_aggs: list[dict] = []
        # The visual keeps only the rows whose measure passes a test (a measure filter).
        self.measure_filtered = False
        # Table calculations over another one's result, each a statement over the grouped
        # rows joined back to them: {n, args, sorts, window} (`_Parser._lifted_window`).
        self.lifts: list[dict] = []

    def expanded(self, table: str) -> set[str]:
        """A table and every table its active many-to-one relationships reach: what removing
        the table's filters removes (a filter on a table those reach filters it)."""
        out, frontier = {_bare(table)}, [_bare(table)]
        while frontier:
            cur = frontier.pop()
            for e in self.graph:
                if e.get("is_active") is False or str(e.get("kind") or "") in _NOT_WALKED:
                    continue
                fs, ts = _card_ends(e.get("cardinality"))
                a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
                for near, far, far_end in ((a, b, ts), (b, a, fs)):
                    if near == cur and far_end == "one" and far not in out:
                        out.add(far)
                        frontier.append(far)
        return out

    def filtered_by(self, table: str) -> set[str]:
        """The tables whose filters reach `table` (it included): along active relationships
        from the one side to the many side, and both ways where the relationship filters
        both ways."""
        return _filtered_by(self.graph, table)

    def place(self, column: str, table: str) -> str | None:
        if self.plan is None:
            return None
        return self.plan.measure_at.get((str(column).lower(), _bare(table)))

    def is_text(self, table: str, column: str) -> bool:
        t = self.types.get((str(table).lower(), str(column).lower())) or ""
        return bool(re.match(r"(?i)^(string|text|varchar|char|nvarchar|nchar)\b", t))


_COMPARISONS = ("=", "<>", ">", "<", ">=", "<=")

# An aggregate call inside a rule's template.
_AGGREGATE_CALL = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN)\s*\(", re.I)


def _blank_passes(op: str, value: Any) -> bool:
    """Whether a BLANK passes `blank <op> value` in DAX. A blank compares as the empty string
    against a string and as 0 against a number or a boolean (FALSE is 0, TRUE is 1); SQL's
    NULL passes no comparison, so where DAX's blank does, `OR c IS NULL` says so."""
    blank: Any = "" if isinstance(value, str) else 0.0
    return {"=": blank == value, "<>": blank != value, "<": blank < value,
            ">": blank > value, "<=": blank <= value, ">=": blank >= value}[op]


def _check_literal_args(emit: dict, name: str, language: str,
                        raw: list[list[tuple[str, str]]]) -> None:
    """A row's `literal_args` ({argument index: characters}) says the argument must be a
    string literal free of those characters: they are wildcards in the source language, and a
    match that read them literally would answer a different question."""
    for idx, chars in (emit.get("literal_args") or {}).items():
        i = int(idx)
        toks = raw[i] if i < len(raw) else []
        if len(toks) != 1 or toks[0][0] != "str":
            if not chars:
                raise CompileError(f"{name.upper()} argument {i + 1} must be a string literal "
                                   f"in {language}")
            raise CompileError(f"{name.upper()} argument {i + 1} is compiled only as a string "
                               f"literal: {chars!r} are wildcards in {language}, and an "
                               f"expression could hold them")
        hit = sorted({c for c in toks[0][1][1:-1] if c in chars})
        if hit:
            raise CompileError(f"{name.upper()} argument {i + 1} holds {''.join(hit)!r}, a "
                               f"wildcard in {language}; a SQL substring match would read it "
                               f"literally")


def _check_literal_values(emit: dict, name: str, language: str,
                          raw: list[list[tuple[str, str]]]) -> None:
    """A row's `literal_values` ({argument index: [values]}) says the argument must be a string
    literal naming one of those values (case-insensitive). A date part is the case: Tableau's
    `weekday` counts from Sunday = 1 and its `week` has its own start, so only the parts whose
    meaning SQL shares compile; the rest are refused by name rather than answered off by one."""
    for idx, allowed in (emit.get("literal_values") or {}).items():
        i = int(idx)
        toks = raw[i] if i < len(raw) else []
        value = toks[0][1][1:-1].lower() if len(toks) == 1 and toks[0][0] == "str" else None
        if value not in {str(a).lower() for a in allowed}:
            shown = toks[0][1] if len(toks) == 1 else "an expression"
            raise CompileError(f"{name.upper()} argument {i + 1} is {shown}; it compiles only "
                               f"as one of {', '.join(map(str, allowed))}, whose meaning SQL "
                               f"shares in {language}")


def _no_form_reason(rule: dict, dialect: str | None) -> str:
    """Why a row has no form for the dialect: what the row's `no_form` says the dialect
    lacks, else that its SQL differs by dialect and is not guessed."""
    why = {str(k).lower(): v for k, v in (rule.get("no_form") or {}).items()}.get(
        str(dialect or "").lower())
    return why or "its SQL differs by dialect and is not guessed"


def _emit_for(rule: dict, dialect: str | None) -> dict:
    """A row's emit with its `dialect_templates` spelling for the dialect, if it has one."""
    emit = rule.get("emit") or {}
    form = (emit.get("dialect_templates") or {}).get(str(dialect or "").lower())
    return {**emit, "template": form} if form else emit


def _agg_emit(rule: dict, agg: str, dialect: str | None) -> dict:
    """An aggregation row's emit for the dialect: its `dialect_templates` spelling, and a
    dialect outside its `dialects` refused by name (a median is not MEDIAN everywhere)."""
    d = str(dialect or "").lower()
    allowed = [str(x).lower() for x in rule.get("dialects") or []]
    if allowed and d and d not in allowed:
        raise CompileError(f"no rule for aggregation {agg!r} in the {dialect} dialect; "
                           + _no_form_reason(rule, dialect))
    emit = rule.get("emit") or {}
    form = (emit.get("dialect_templates") or {}).get(d)
    return {**emit, "template": form} if form else emit


def _part_literal(raw: list[list[tuple[str, str]]], index: int) -> str | None:
    """The date part a call names at `index` (a string literal or a bare keyword), lower."""
    toks = raw[index] if index < len(raw) else []
    if len(toks) != 1:
        return None
    kind, value = toks[0]
    return str(value)[1:-1].lower() if kind == "str" else str(value).lower()


def _part_template(emit: dict, dialect: str | None, raw: list[list[tuple[str, str]]]
                   ) -> str | None:
    """The template a dialect spells per date part (`part_templates`: {dialect: {part:
    template}}, the part being argument `part_arg`): a dialect whose function takes no part
    argument writes each part its own way (Postgres has no DATEDIFF). None where the row
    names no form per part for the dialect, or none for this part."""
    forms = (emit.get("part_templates") or {}).get(str(dialect or "").lower())
    if not forms or emit.get("part_arg") is None:
        return None
    return forms.get(_part_literal(raw, int(emit["part_arg"])) or "")


def _scalar_list(items: list[str]) -> str:
    """The arguments of a NULL-propagating LEAST / GREATEST that skip a NULL as DAX skips a
    blank: each item coalesced with every other, so it is NULL only when all of them are."""
    return ", ".join(
        "COALESCE(" + ", ".join([x] + [y for j, y in enumerate(items) if j != i]) + ")"
        for i, x in enumerate(items))


# A start of week by its ISO day number (Monday = 1 … Sunday = 7).
_ISO_DAY = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5,
            "saturday": 6, "sunday": 7}


def _apply(emit: dict, args: list[str], name: str) -> str:
    template = emit.get("template")
    if not template:
        raise CompileError(f"rule for {name.upper()} has no template")
    if emit.get("ignore_args"):
        return template
    # A function whose meaning changes with its number of arguments (`MID(s, n)` is the rest
    # of the text, `MIN(a, b)` the lesser of two values) names a template per count.
    template = (emit.get("by_arity") or {}).get(str(len(args)), template)
    need = len(set(re.findall(r"\{(\d+)(?:!word)?\}", template)))
    # An optional argument the call left out takes the row's declared default (`defaults`:
    # {argument index: SQL}), e.g. SELECTEDVALUE's alternate, which is BLANK when omitted.
    defaults = emit.get("defaults") or {}
    while len(args) < need and str(len(args)) in defaults:
        args = args + [str(defaults[str(len(args))])]
    if len(args) < need:
        raise CompileError(f"{name.upper()} needs {need} argument(s), found {len(args)}")
    if len(args) > need and not emit.get("extra_args_ok"):
        # A template that does not place an argument would DROP it, and a dropped argument is
        # a different answer that still runs (Tableau's IIF takes an optional fourth, the
        # value when the test is unknown). Refused by name; a row whose extra arguments are
        # truly inert says so with `extra_args_ok`.
        raise CompileError(f"{name.upper()} given {len(args)} argument(s); the rule places "
                           f"{need}, and the rest would be silently dropped")
    out = template
    for idx, a in enumerate(args):
        word = "{" + str(idx) + "!word}"
        if word in out:
            # A dialect that names a date part as a keyword (`DATEPART(year, d)`), not a
            # string: the argument's literal (`'year'`, checked by `literal_values`) unquoted.
            bare = re.fullmatch(r"'([A-Za-z_]+)'", a.strip())
            if not bare:
                raise CompileError(f"{name.upper()} argument {idx + 1} is placed as a keyword "
                                   f"and must be a literal word, not {a}")
            out = out.replace(word, bare.group(1))
        out = out.replace("{" + str(idx) + "}", a)
    return out


# A Tableau level-of-detail expression: `{ FIXED [dim] : SUM([x]) }`, and the INCLUDE and
# EXCLUDE forms. Matched loosely on purpose — the point is to RECOGNISE it, not to parse it.
_LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.I)

# Languages that HAVE level-of-detail syntax. DAX does not: a brace there is a table
# constructor or, far more often, a character inside a string.
_LOD_LANGUAGES = ("tableau-calc", "tableau")


def _without_literals(formula: str) -> str:
    """Blank string literals and comments, preserving length. Used only for RECOGNISING
    constructs, never for compiling."""
    out = list(formula)
    i, n = 0, len(formula)
    while i < n:
        two = formula[i:i + 2]
        if two in ("//", "--"):
            while i < n and formula[i] != "\n":
                out[i] = " "
                i += 1
        elif two == "/*":
            while i < n and formula[i:i + 2] != "*/":
                if formula[i] != "\n":
                    out[i] = " "
                i += 1
            for k in range(i, min(i + 2, n)):
                out[k] = " "
            i += 2
        elif formula[i] in "'\"":
            q = formula[i]
            out[i] = " "
            i += 1
            while i < n and formula[i] != q:
                if formula[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def compile_expression(formula: str, language: str, rules, alias: str,
                       dialect: str) -> tuple[str, _Parser]:
    # LOD IS REFUSED BY NAME, IN THE ENGINE RATHER THAN THE RULE TABLE. An LOD computes at a
    # grain the statement does not have, so no rule row makes it SQL — the table's own header
    # says so. But a row is matched on a FUNCTION name and an LOD is brace syntax, so the
    # tokenizer reaches it first and the refusal that came out was `unreadable character ':'`.
    # That is a true statement about a lexer and tells the reader nothing about their model,
    # while the sibling trap (a table calculation) refuses with a real reason. This says why.
    #
    # It is SHAPE, not vocabulary, which is why it belongs here: the rule table cannot express
    # a construct the tokenizer cannot lex.
    #
    # NARROWED TWICE, because the first cut refused working formulas. It ran for EVERY
    # language including DAX, which has no LOD syntax at all, and it searched the raw text —
    # so `IF([Type]="{ fixed cost }", 1, 0)` and `SUM([x]) // { include everything }` were
    # both refused, telling the author their DAX "computes at a grain this statement does not
    # have", which is false. With no force flag by design, a false refusal is permanent.
    p = _Parser(_tokens(formula, language), rules, language, alias, dialect)
    return p.parse(), p


def _tokens(formula: str, language: str) -> list:
    """The tokens of one formula. EVERY formula the compiler reads comes through here — a
    measure, a grouping calc, a measure inlined into another. A level-of-detail expression
    is read by the parser (`_lod`), which refuses it by name where it has no statement."""
    return tokenize(formula, dax=language not in _LOD_LANGUAGES)


def _lod_refusal(kind: str, why: str = "") -> CompileError:
    return CompileError(
        f"{kind.upper()} level-of-detail expression: it computes at a grain this statement "
        f"does not have" + (f" ({why})" if why else
                            "; it is compiled only inside a unit's statement"))


# The sibling modules' names, imported after this module's own definitions: the modules
# call each other, and a name is looked up only when a function runs.
from .parser import (  # noqa: E402
    _Parser,
    tokenize,
)
from .statements import (  # noqa: E402
    _filtered_by,
)
from .joins import (  # noqa: E402
    _NOT_WALKED,
    _bare,
    _card_ends,
)
