# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""bicompile, part 1 of 4: the BI→SQL compiler, split for the plugin directory's 256 KiB file limit. See __init__.py."""
from __future__ import annotations
import pathlib
import re
from typing import Any
import yaml
from metrics_agent.lib import bi_models as _bi_models
from metrics_agent.lib import declared as _declared
from metrics_agent.lib import slugs
from metrics_agent.lib.card_sql import statement_sha  # noqa: F401 - the review's, re-exported
from metrics_agent.plugs.readers import tableau_params as _tparams
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
RULES = DATA / "bi_compiler_rules.yml"
CORPUS = DATA / "compiler_corpus"

# An expression the compiler understands is built from these and nothing else. Anything the
# tokeniser cannot classify is reported by name rather than guessed at — the whole value of a
# compiler over a model is that it knows what it does not know.
_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<col>\[[^\]]+\])                    # [Units Sold]  — Tableau and DAX both use these
    # Table[Column] — DAX. A keyword followed by a field (Tableau's `THEN [x]`, `AND [y]`;
    # DAX's `RETURN [x]`, an LOD's `{FIXED [d] : …}`) is NOT a qualified reference, so the
    # keywords are excluded.
    | (?P<qualified>(?!(?i:then|else|elseif|when|and|or|not|if|case|end|return|fixed|include|exclude)\b)[A-Za-z_][\w]*\s*\[[^\]]+\])
    # 'Date'[Date] — DAX quotes a table name containing a space or a reserved word. It MUST
    # be tried before the string literal, or `'Date'` tokenises as a string and `[Date]`
    # arrives where an operator was expected. That is not a hypothetical: it is how the
    # Power BI fixture writes time intelligence.
    | (?P<quoted_qualified>'(?:[^']|'')*'\s*\[[^\]]+\])
    | (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
    # #2025-01-01# — a Tableau date (or date and time) literal; a date parameter's value.
    | (?P<datelit>\#\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?\#)
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<func>[A-Za-z_][\w.]*)
    | (?P<op><=|>=|<>|!=|==|&&|\|\||[-+*/%<>=&^])   # `&&` / `||`: DAX's logical AND / OR
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<comma>,)
    | (?P<colon>:)                           # `{FIXED [d] : SUM([x])}`
    | (?P<brace>[{}])                        # an LOD opens with one
""", re.X)


# The table-calculation functions of the calc language, by name.
TABLE_CALC_NAMES = re.compile(r"^(RUNNING_\w+|WINDOW_\w+|TOTAL|INDEX|FIRST|LAST|SIZE|LOOKUP|"
                              r"PREVIOUS_VALUE|RANK\w*)$", re.I)


def _constant_count(toks: list) -> int | None:
    """A Top N set's count: one whole number (a parameter stands at its value by now), in
    parentheses or not; None for anything else. Tableau itself rejects any other count
    (checked live: count='2 + 3' fails with "The filter limit expression is invalid")."""
    while len(toks) >= 2 and toks[0][0] == "lparen" and toks[-1][0] == "rparen":
        toks = toks[1:-1]
    if len(toks) == 1 and toks[0][0] == "num" and re.fullmatch(r"\d+", str(toks[0][1])):
        return int(toks[0][1])
    return None


def _calls_table_calc(formula: str) -> bool:
    """Whether a calc-language formula calls a table-calculation function."""
    return any(TABLE_CALC_NAMES.match(name)
               for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", formula))


class _SortNeedsWindow(Exception):
    """A table calculation's order is a window of its own (a member's aggregate over finer
    rows): it is computed over the grouped rows as a statement of their own."""


class CompileError(ValueError):
    """The compiler cannot produce a statement it believes in. Always carries the reason."""


Result = Compiled          # the socket's result type; this name is the engine's own


# --------------------------------------------------------------------------- expressions
def _strip_line_comments(text: str, dax: bool = True) -> str:
    """Drop comments outside string literals. `// …` to end of line in every language (Tableau
    and DAX both use it). DAX also comments with `-- …` to end of line and `/* … */` blocks
    (`dax`), and both are everywhere in real models: a measure kept with its old version
    commented out above it, a note after a SWITCH branch. Read as code, `-- DISTINCTCOUNT(x)`
    became two minus signs and an aggregate, and the refusal that came out
    (`trailing tokens from 'COUNTROWS'`, `unexpected '/'`, `unreadable character '$'`) named
    text the author had switched off. In DAX a `[…]` name may hold any of these characters,
    so a bracketed name is copied as it is. Tableau has neither form (`--x` there is a double
    negation), so its formulas keep `//` only."""
    out, i, n, q = [], 0, len(text), None
    while i < n:
        ch = text[i]
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            out.append(ch)
        elif dax and ch == "[":
            j = text.find("]", i)
            j = n - 1 if j < 0 else j
            out.append(text[i:j + 1])
            i = j + 1
            continue
        elif text[i:i + 2] == "//" or (dax and text[i:i + 2] == "--"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif dax and text[i:i + 2] == "/*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def tokenize(text: str, dax: bool = True) -> list[tuple[str, str]]:
    """The tokens of a formula, comments dropped (`_strip_line_comments`). `dax` is False only
    for a Tableau calculation (`_tokens`): every other caller reads DAX."""
    out: list[tuple[str, str]] = []
    text = _strip_line_comments(text or "", dax)
    i = 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if m is None:
            raise CompileError(f"unreadable character {text[i]!r} at offset {i}")
        i = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group()))
    return out


_TEXT_FN = re.compile(r"(?i)^(UPPER|LOWER|TRIM|LTRIM|RTRIM|REPLACE|SUBSTR|SUBSTRING|LEFT|RIGHT"
                      r"|CONCAT|CHR|LPAD|RPAD|TO_VARCHAR|TO_CHAR|STRFTIME|SPLIT_PART"
                      r"|REGEXP_SUBSTR|REGEXP_REPLACE|REGEXP_EXTRACT|DAYNAME|INITCAP|REPEAT)\s*\(")


def _balanced(s: str) -> bool:
    """`s` has no parenthesis that closes before it opens — so `(a) + (b)` is not `(…)`."""
    depth, q = 0, False
    for ch in s:
        if ch == "'":
            q = not q
        elif not q and ch == "(":
            depth += 1
        elif not q and ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _quote(text: str, dialect: str | None) -> str:
    """`text` as a SQL string literal in `dialect`. Doubling the quote is not enough where a
    backslash is an escape character (Snowflake, Databricks, BigQuery, Redshift): there
    `'C:\\'` ends in an escaped quote and the statement no longer parses. sqlglot knows each
    dialect's rule; with no dialect the standard form is used."""
    if dialect:
        import sqlglot.expressions as _exp  # noqa: PLC0415 - sqlglot is already loaded by A3
        return _exp.Literal.string(text).sql(dialect=dialect)
    return "'" + text.replace("'", "''") + "'"


def _sql_string(token: str, dialect: str | None = None) -> str:
    """A double-quoted source string literal (`"North"`) as a SQL string (`'North'`)."""
    return _quote(token[1:-1].replace('""', '"'), dialect)


def _column_ref(token: str) -> str:
    """`[Units Sold]` and `Sales[Units Sold]` both name a column. The TABLE part is dropped on
    purpose: the compiler binds columns to the statement's own alias, and a model-side table
    name is not a warehouse table name."""
    inner = token[token.index("[") + 1:token.rindex("]")]
    return inner.strip()


def _table_ref(token: str) -> str | None:
    """`Sales[Units Sold]` -> `Sales`; `'Sales Data'[Units]` -> `Sales Data`; `[Units]` -> None."""
    head = token[:token.index("[")].strip()
    if len(head) >= 2 and head[0] == head[-1] == "'":
        head = head[1:-1].replace("''", "'")
    return head or None


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

# Languages that HAVE level-of-detail syntax. DAX does not: a brace there is a table
# constructor or, far more often, a character inside a string.
_LOD_LANGUAGES = ("tableau-calc", "tableau")


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


# --------------------------------------------------------------------------- statements
def _literal(value: Any, dialect: str | None = None) -> str:
    """One scalar literal. A LIST is not a scalar and is never stringified here.

    `str(['AW25', 'SS25'])` yields `"['AW25', 'SS25']"`, which quotes into the single garbage
    literal `'[''AW25'', ''SS25'']'`. That SQL runs, matches nothing, and reports a number —
    the precise failure this layer exists to prevent, and one no syntax check would catch.
    """
    if value is None:
        return "NULL"
    if isinstance(value, (list, tuple, set)):
        raise CompileError("a multi-value filter is not a scalar literal")
    text = str(value)
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    return _quote(text, dialect)


def _literal_list(values: Any, dialect: str | None = None) -> str:
    """The comma-separated body of an `IN (...)`. Order preserved, duplicates kept: the
    widget's own list is the evidence, not a set we tidied."""
    items = list(values) if isinstance(values, (list, tuple, set)) else [values]
    return ", ".join(_literal(v, dialect) for v in items)


# DAX functions that aggregate rows — the ones a CALCULATE filter gates. Language semantics,
# not a tunable: which functions aggregate is a fact about DAX.
_DAX_AGGREGATES = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "COUNTROWS",
                   "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "MEDIAN", "STDEV.P", "VAR.P",
                   "COUNTBLANK"}

_SIMPLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _ident(name: str) -> str:
    """A column name as SQL text: bare when it is a plain LOWERCASE identifier, double-quoted
    (and case-preserved) otherwise. `Account ID` and `Continuity %` are not legal bare
    identifiers; `Pod` is, but a bare name folds to POD, and a query that aliased the column
    as the quoted `"Pod"` has no column POD — so any uppercase letter means quote."""
    if _SIMPLE_IDENT.match(name) and name == name.lower():
        return name
    return '"' + name.replace('"', '""') + '"'


def _filtered_by(graph: list[dict], table: str) -> set[str]:
    """The tables whose filters reach `table` (it included): along active relationships from
    the one side to the many side, both ways where the relationship filters both ways, and
    along a many-to-many one (or one whose multiplicity the model does not state) from its
    `to` table to its `from` table, the direction the join plan walks it (`_plan_joins`)."""
    out, frontier = {_bare(table)}, [_bare(table)]
    while frontier:
        cur = frontier.pop()
        for e in graph:
            if e.get("is_active") is False \
                    or str(e.get("kind") or "relationship") in _NOT_WALKED:
                continue
            fs, ts = _card_ends(e.get("cardinality"))
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
            for near, far, near_end, far_end, forward in ((a, b, fs, ts, True),
                                                          (b, a, ts, fs, False)):
                if near != cur or far in out:
                    continue
                unstated = far_end is None or (far_end == "many" and near_end == "many")
                if far_end == "one" or both or (forward and unstated):
                    out.add(far)
                    frontier.append(far)
    return out


# Functions whose value does not depend on the filter context (no table, no column).
_CONTEXT_FREE_FUNCS = {"BLANK", "TRUE", "FALSE", "IF", "DIVIDE", "COALESCE", "ABS", "ROUND",
                       "ROUNDUP", "ROUNDDOWN", "INT", "POWER", "SQRT", "VAR", "RETURN", "NOT",
                       "AND", "OR", "SWITCH", "DATE", "MOD", "SIGN", "EXP", "LN", "LOG"}


def _context_free(key: str, measures: dict[str, str], language: str,
                  seen: tuple = ()) -> bool:
    """Whether measure `key` is the same in every filter context: no column, table or
    aggregate in it or in a measure it names (a constant, such as a parameter written in as
    its value). Inside a row iterator such a measure needs no context transition."""
    formula = measures.get(key)
    if formula is None or key in seen:
        return False
    try:
        toks = _tokens(formula, language)
    except CompileError:
        return False
    for i, (k, v) in enumerate(toks):
        if k in ("qualified", "quoted_qualified"):
            return False
        if k == "col":
            if not _context_free(_column_ref(v).lower(), measures, language, seen + (key,)):
                return False
        elif k == "func":
            nxt = toks[i + 1][0] if i + 1 < len(toks) else None
            if v.upper() not in _CONTEXT_FREE_FUNCS and nxt == "lparen":
                return False
        elif k == "str" and v.startswith("'"):
            return False
    return True


# ------------------------------------------------------------------------------ the joins
# A relationship end is `one` or `many`; the vocabulary each reader writes is read here once.
_CARD_ENDS = {
    "m:1": ("many", "one"), "*:1": ("many", "one"), "many_to_one": ("many", "one"),
    "1:1": ("one", "one"), "one_to_one": ("one", "one"),
    "1:m": ("one", "many"), "1:*": ("one", "many"), "one_to_many": ("one", "many"),
    "m:m": ("many", "many"), "*:*": ("many", "many"), "many_to_many": ("many", "many"),
}
# Relationships a statement never joins along: a condition rather than equal columns, and a
# blend, which is a left join AFTER aggregation at the primary's grain, not a SQL join.
_NOT_WALKED = {"range": "a range relationship (a condition, not equal columns)",
               "asof": "an as-of relationship (a condition, not equal columns)",
               "blend": "a data blend (a left join after aggregation at the primary's grain, "
                        "not a SQL join)"}


def _card_ends(card: Any) -> tuple[str | None, str | None]:
    key = re.sub(r"[\s-]+", "_", str(card or "").strip().lower())
    return _CARD_ENDS.get(key, (None, None))


def _expanded_tokens(formula: str, measures: dict[str, str] | None,
                     seen: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """The tokens of a DAX formula with every measure it references expanded in place: what
    the formula reads, all the way down. Only for LOOKING (which tables, which
    relationships); compiling inlines measures itself."""
    try:
        toks = tokenize(formula or "")
    except CompileError:
        return []
    out: list[tuple[str, str]] = []
    for k, v in toks:
        key = _column_ref(v).lower() if k == "col" else None
        if key and measures and key in measures and key not in seen:
            out += _expanded_tokens(measures[key], measures, seen + (key,))
        else:
            out.append((k, v))
    return out


def _spec_edge(graph: list[dict], ends: frozenset) -> int | None:
    return next((i for i, e in enumerate(graph)
                 if frozenset({(str(e.get("from_column") or "").lower(), _bare(e.get("from_table"))),
                               (str(e.get("to_column") or "").lower(), _bare(e.get("to_table")))})
                 == ends), None)


_DATE_TYPES = re.compile(r"(?i)^(date|datetime|timestamp)")


def _keyed_date(ctx: "_UnitContext", col: tuple[str, str],
                table_of: dict[str, str] | None = None) -> dict | None:
    """A date column of a date table the statement's table reaches on ANOTHER key (an integer
    `Date Sk` beside the table's `Calendar Date`), registered as a date column of its own
    (`ctx.date_keys`): a date set is then computed over the date column's values and read
    through the key (`key_column`), each date to its key row. None where the column is not
    a date, or no single relationship reaches its table."""
    column, table = col
    # The model names its tables one way, the warehouse another: a type is looked up by both.
    names = {table} | {k.lower() for k, v in (table_of or {}).items() if _bare(v) == table}
    dtype = next((ty for (t, c), ty in ctx.types.items()
                  if (t in names or _bare(t) in names) and c == column), None)
    if not dtype or not _DATE_TYPES.match(dtype):
        return None
    key = _lookup_key(ctx, table)
    if key is None:
        return None
    via = ctx.lookup_keys[key]
    info = {"table": via["table"], "column": ctx.renames.get((_bare(table), column), column),
            "fact_column": via["fact_column"], "key_column": via["column"]}
    ctx.date_keys[col] = info
    return info


def _lookup_key(ctx: "_UnitContext", table: str) -> tuple[str, str] | None:
    """The key a lookup date set (`_Parser._lookup_dates`) reaches `table` (bare) on: the one
    active relationship from the statement's table whose ONE end is on `table`, any data type.
    Registered in `ctx.lookup_keys`, so the date-set statement joins on it. None when there is
    no such relationship, or more than one (which key the tool filters through is then the
    model's choice, not one this reads)."""
    src = ctx.source
    hits = {}
    for e in ctx.graph:
        if e.get("is_active") is False or str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        fs, ts = _card_ends(e.get("cardinality"))
        for near, far, ncol, fcol, far_end in (
                (e.get("from_table"), e.get("to_table"), e.get("from_column"),
                 e.get("to_column"), ts),
                (e.get("to_table"), e.get("from_table"), e.get("to_column"),
                 e.get("from_column"), fs)):
            if _bare(near) == src and far_end == "one" and _bare(far) == table:
                hits[(str(fcol).lower(), _bare(far))] = {
                    "table": far, "column": str(fcol), "fact_column": str(ncol)}
    if len(hits) != 1:
        return None
    key, info = next(iter(hits.items()))
    ctx.lookup_keys[key] = info
    return key


def _bare(table: Any) -> str:
    return str(table or "").split(".")[-1].lower()
_DATE_LIT = re.compile(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?")
_NUM_LIT = re.compile(r"-?\d+(\.\d+)?")


def _foldable(v) -> bool:
    """A filter value compared as text the model folds (case, trailing spaces): a string that
    is not a number or a date literal. A key column's numbers (`ORDER_ID IN (1, 2)`) and a
    date are compared as they are; folding them would compare a number as text."""
    return isinstance(v, str) and not _NUM_LIT.fullmatch(v) and not _DATE_LIT.fullmatch(v)
_READER_DIALECTS = (yaml.safe_load((pathlib.Path(__file__).parent.parent / "data"
                                     / "reader_dialects.yml").read_text()) or {})
# Dialects with no `NULLS LAST` in an ORDER BY (`data/reader_dialects.yml`).
_NO_NULLS_LAST = frozenset(_READER_DIALECTS.get("no_nulls_last") or ())
# The remainder of a whole-number division, where a dialect does not spell it MOD(a, b).
_MODULO = {str(k).lower(): str(v) for k, v in (_READER_DIALECTS.get("modulo") or {}).items()}


def _sort_term(expr: str, direction: str, nulls: str, dialect: str | None) -> str:
    """One ORDER BY term with its empty values FIRST or LAST. A dialect with no NULLS FIRST /
    LAST (`no_nulls_last` in data/reader_dialects.yml) sorts on a key that is 0 for the end
    the empty values go to, then on the value; a constant (no column to order by) is
    `(SELECT NULL)`, the only constant such a dialect's window takes."""
    if str(dialect or "").lower() not in _NO_NULLS_LAST:
        return f"{expr} {direction} NULLS {nulls}"
    if re.fullmatch(r"\d+", expr.strip()):
        return "(SELECT NULL)"
    first = nulls.upper() == "FIRST"
    return (f"CASE WHEN {expr} IS NULL THEN {0 if first else 1} ELSE {1 if first else 0} END, "
            f"{expr} {direction}")


# The first n rows of an ordered statement, where a dialect does not spell it LIMIT n.
_LIMIT = {str(k).lower(): str(v) for k, v in (_READER_DIALECTS.get("limit") or {}).items()}


def _mod(x: str, m: str, dialect: str | None) -> str:
    """`x` modulo `m` as the dialect spells it (`modulo` in data/reader_dialects.yml), else
    MOD(x, m). The sign is the dividend's in every dialect listed, as MOD's is."""
    return _MODULO.get(str(dialect or "").lower(), "MOD({0}, {1})").format(x, m)
