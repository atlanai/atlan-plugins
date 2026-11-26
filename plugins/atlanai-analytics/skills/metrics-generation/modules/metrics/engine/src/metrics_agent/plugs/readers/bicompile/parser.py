# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Formula text to tokens, and the parser that turns a DAX or Tableau calculation into a SQL expression. Part of bicompile; see __init__.py."""
from __future__ import annotations

import re
from typing import Any

from .common import (  # noqa: F401
    CompileError,
    TABLE_CALC_NAMES,
    _SortNeedsWindow,
    _TOKEN,
    _constant_count,
)

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


class _Parser:
    """Recursive descent over the subset above. Emits SQL text, not an AST.

    It is deliberately small. Everything it cannot handle raises with the construct NAMED, and
    that name is what a proposed rule has to cover — which is how the review loop knows what to
    ask for instead of guessing.
    """

    def __init__(self, tokens: list[tuple[str, str]], rules, language: str, alias: str,
                 dialect: str, colmap: dict[str, str] | None = None,
                 measures: dict[str, str] | None = None, row_filter: str | None = None,
                 inlining: tuple[str, ...] = ()):
        self.t, self.i = tokens, 0
        # Model measures by name (lower) -> DAX formula, so `[Requests Total]` inside another
        # measure is compiled from ITS formula rather than emitted as a column that does not
        # exist. `inlining` is the chain being expanded, so a cycle is refused, not looped.
        self.measures = measures or {}
        # Inside IFERROR / ISERROR: the conditions under which the expression being read
        # raises an evaluation error (a division by zero or BLANK). None elsewhere.
        self.error_conds: list[str] | None = None
        self.inlining = inlining
        # Set inside CALCULATE: the SQL condition every aggregate's argument is gated by.
        self.row_filter = row_filter
        # Set when the statement reads a NATIVE QUERY's result set: model column name
        # (lower) -> the query's own output column. None means a physical table, where the
        # model name is emitted as written (unchanged behaviour).
        self.colmap = colmap
        self.rules, self.language, self.alias, self.dialect = rules, language, alias, dialect
        self.used: list[str] = []
        self.provisional: list[str] = []
        self.untranslatable: list[dict] = []
        self.unknown: list[str] = []
        self.calc_columns: set[str] = set()
        # (model table, lower -> the warehouse table it reads; the table this statement reads).
        # Set by compile_unit. A `Table[Column]` whose table reads ANOTHER warehouse table
        # cannot be bound to this statement's alias: see _bind.
        self.home: tuple[dict[str, str], str] | None = None
        # The VAR scopes open at this point, innermost last: name (lower) -> compiled SQL.
        # A sub-parser for a CALCULATE argument shares them; an inlined measure does not, as
        # a measure cannot see the caller's variables.
        self.vars: list[dict[str, str]] = []
        # What the statement around this formula is (`_UnitContext`), set by compile_unit:
        # where a column of another table is read, the visual's grouping columns and filters,
        # each column's data type, and the date facts time intelligence needs. None for a
        # formula compiled on its own (a regression case without a model).
        self.ctx: _UnitContext | None = None
        # True while a column of another table may be read through a joined table: a
        # CALCULATE filter on a dimension's column (the relationship carries the filter to
        # the measure's rows) and RELATED inside a row iterator. Anywhere else such a column
        # aggregates a different table's rows and stays refused.
        self.foreign_ok = False
        # Inside a row iterator (SUMX and the like): the expression is evaluated per row, so
        # an aggregate or a measure in it would be a context transition, which is refused.
        self.row_level = False
        # Inside a CALCULATE that removes the visual's grouping on some columns (ALL,
        # REMOVEFILTERS, ALLEXCEPT, ALLSELECTED): the PARTITION BY each aggregate is summed
        # over, across the visual's rows. None outside one.
        self.window: list[str] | None = None
        # The visual's filters the enclosing ALL / REMOVEFILTERS removes, as (column, table):
        # the aggregates then read rows the statement's WHERE does not keep, so each is its
        # own statement over the rows without those filters (`_context_statements`).
        self.window_drop: list[tuple[str, str | None]] = []
        # Inside a time-intelligence CALCULATE: the date set the aggregates read (`_DateSet`).
        self.date_set: _DateChain | None = None
        # The aggregate slots of a statement computed per date set (`_compile_time_unit`):
        # each aggregate becomes one column of its own sub-statement, and the expression reads
        # it back. None for an ordinary statement.
        self.slots: list[dict] | None = None
        # Inside a CALCULATE beside time intelligence whose filter REPLACES one the visual
        # applies: the visual's filters each slot's sub-statement reads its rows without.
        self.slot_drops: list[tuple[str, str | None]] = []
        # Beside time intelligence, a CALCULATE that removes some of the visual's grouping
        # (ALL / ALLSELECTED of a grouped column): the grouping SQL its slots keep.
        self.slot_keep: list[str] | None = None
        # Reading a measure's value on a row with no rows of the statement's table: the
        # aggregates over a wider context (ALL of the row's columns) it reads there, each its
        # own statement over the rows that context holds (`_on_empty_rows`).
        self.empty_aggs: list[dict] | None = None
        # Under a filter on a measure (the visual keeps the rows whose measure passes): an
        # aggregate of a CALCULATE that removes grouping with ALL is its own statement over
        # the rows before that filter (`window_own`); one under ALLSELECTED keeps it
        # (`window_selected`).
        self.window_own = False
        self.window_selected = False
        self._keep_filters = False
        # Compiling the formula's value on a row of the visual with NO rows of the statement's
        # table (`_blank_on_empty`): each aggregate is BLANK there, and one summed across the
        # visual's other rows is not.
        self.empty = False
        # The unit's level-of-detail expressions (`_lod`), shared by every parser of one
        # statement; None where a formula is compiled on its own (an LOD is refused there).
        self.lods: list[dict] | None = None

    def _child(self, tokens, *, row_filter: str | None, inlining: tuple[str, ...],
               share_vars: bool) -> "_Parser":
        """A parser for part of this formula (a CALCULATE's expression, an inlined measure)
        that inherits everything about the statement around it."""
        sub = _Parser(tokens, self.rules, self.language, self.alias, self.dialect, self.colmap,
                      self.measures, row_filter, inlining)
        sub.home, sub.ctx, sub.row_level = self.home, self.ctx, self.row_level
        sub.window, sub.date_set, sub.slots = self.window, self.date_set, self.slots
        sub.window_drop = self.window_drop
        sub.slot_drops = self.slot_drops
        sub.slot_keep = self.slot_keep
        sub.empty_aggs = self.empty_aggs
        sub.window_own, sub.window_selected = self.window_own, self.window_selected
        sub.empty = self.empty
        sub.lods = self.lods
        sub.error_conds = self.error_conds
        if share_vars:
            sub.vars = self.vars
        return sub

    def _absorb(self, sub: "_Parser") -> None:
        self.used += sub.used
        self.provisional += sub.provisional
        self.untranslatable += sub.untranslatable
        self.unknown += sub.unknown

    # -- token helpers
    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, kind: str):
        k, v = self.take()
        if k != kind:
            raise CompileError(f"expected {kind}, found {v!r}")
        return v

    # -- grammar
    def parse(self) -> str:
        if self._tableau and self.i == 0 and any(
                k == "func" and v.upper() == "PREVIOUS_VALUE" for k, v in self.t):
            return self._previous_value()
        sql = self.expr()
        if self.i != len(self.t):
            raise CompileError(f"trailing tokens from {self.t[self.i][1]!r}")
        return sql

    def expr(self) -> str:
        if self._dax and self._kw("VAR"):
            # A VAR block is an expression in its own right and may open any argument.
            return self._var_block()
        if self._dax:
            return self._dax_binary(0)
        # `head` is the text up to the last operator looser than `*` and `/`; `term` the
        # multiplicative run after it, so a division wraps exactly its own operands.
        head, term = "", self.term()

        def whole() -> str:
            return head + term
        while True:
            kind, value = self.peek()
            left = whole()
            if kind == "op" and value in ("&&", "||"):
                if not self._dax:
                    raise CompileError(f"{value!r} is not an operator in {self.language}")
                self.take()
                head, term = f"{left} {'AND' if value == '&&' else 'OR'} ", self.term()
            elif kind == "op":
                op = self.take()[1]
                if op == "&" or (op == "^" and not self._tableau):
                    raise CompileError(f"{op!r} is not an operator the compiler reads in "
                                       f"{self.language}")
                op = "=" if op == "==" else op
                right = self.term()
                if op == "/" and self._tableau and not self._nonzero(right):
                    # A zero divisor gives NULL in the calc language (checked live), where
                    # SQL raises; the spelling is the rule row's.
                    rule = self._function_rule("/")
                    term = _apply(_emit_for(rule, self.dialect), [term, right], "/")
                    continue
                if op == "/" and self._tableau and str(self.dialect or "").lower() == "sqlite":
                    term = f"CAST({term} AS REAL) / {right}"      # decimals, as the calc divides
                    continue
                if op in ("*", "/", "%"):
                    term = f"{term} {op} {right}"
                    continue
                if (op == "+" and self._tableau
                        and (self._is_text(left) or self._is_text(right))):
                    # Tableau `+` on a string is CONCATENATION; SQL `+` is arithmetic.
                    head, term = f"{left} || ", right
                    continue
                head, term = f"{left} {op} ", right
            elif self._tableau and kind == "func" and value.upper() in ("AND", "OR"):
                self.take()
                head, term = f"{left} {value.upper()} ", self.term()
            elif self._tableau and kind == "func" and value.upper() == "IN":
                # `[x] IN ('a', 'b')` — membership, as in SQL.
                self.take()
                self.expect("lparen")
                items = [self.expr()]
                while self.peek()[0] == "comma":
                    self.take()
                    items.append(self.expr())
                self.expect("rparen")
                head, term = "", f"{left} IN ({', '.join(items)})"
            else:
                return left

    # -- DAX operators, by DAX's own precedence (loosest first) and its BLANK rules. A blank
    # is not SQL's NULL: in `+` and `-` it is 0 (`BLANK - 5` is -5) and the result is blank
    # only when both sides are; in `&` it is the empty string; in a comparison it is 0
    # against a number and "" against a text (`BLANK < 100` is TRUE, `BLANK = 0` is TRUE).
    # `*`, `/` and `^` with a blank are blank, as NULL is.
    # The sign sits between `*` and `^`: `^` binds tighter, so `-2^2` is -(2^2) = -4 (Power
    # BI's own answer), and a sign may open the exponent (`2^-1`).
    _DAX_LEVELS = (("||",), ("&&",), ("=", "==", "<>", "!=", "<", ">", "<=", ">="), ("&",),
                   ("+", "-"), ("*", "/"))

    def _dax_binary(self, level: int) -> str:
        if level == len(self._DAX_LEVELS):
            return self._dax_unary()
        ops = self._DAX_LEVELS[level]
        left = self._dax_binary(level + 1)
        terms = [("+", left)]
        while True:
            kind, value = self.peek()
            if kind != "op" or value not in ops:
                return left
            self.take()
            right = self._dax_binary(level + 1)
            if value in ("+", "-"):
                terms.append((value, right))
                left = self._dax_sum(terms)
            else:
                left = self._dax_op(value, left, right)
            if len(left) > self._MAX_EXPRESSION_CHARS:
                raise CompileError(f"the compiled expression is too large to run ({len(left)} "
                                   f"characters); split the formula into smaller measures")

    def _dax_unary(self) -> str:
        kind, value = self.peek()
        if kind == "op" and value in ("-", "+"):
            self.take()
            inner = self._text_as_number(self._dax_unary())
            return f"-{inner}" if value == "-" else inner
        return self._dax_power()

    def _dax_power(self) -> str:
        left = self.term()
        while self.peek() == ("op", "^"):
            self.take()
            left = self._dax_op("^", left, self._dax_unary_operand())
            if len(left) > self._MAX_EXPRESSION_CHARS:
                raise CompileError(f"the compiled expression is too large to run ({len(left)} "
                                   f"characters); split the formula into smaller measures")
        return left

    def _dax_unary_operand(self) -> str:
        """An exponent: a signed term (`2^-1`), with no `^` of its own, so `^` stays
        left-associative as it was."""
        kind, value = self.peek()
        if kind == "op" and value in ("-", "+"):
            self.take()
            inner = self._dax_unary_operand()
            return f"-{inner}" if value == "-" else inner
        return self.term()

    _NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
    _STR = re.compile(r"^'(?:[^']|'')*'$")
    # Arithmetic over number literals only: never blank.
    _CONST_ARITH = re.compile(r"^[\d\s.+\-*/()]+$")
    # Past this, a statement is refused rather than built: an operand repeated by each
    # operator's template doubles or quadruples the text at every level of nesting.
    _MAX_EXPRESSION_CHARS = 100_000

    def _dax_sum(self, terms: list[tuple[str, str]]) -> str:
        """`a + b - c` as one expression. A blank is 0 and the result is blank only when every
        operand is, as DAX applies pairwise. Written once per chain, so the statement grows
        with the number of operands rather than doubling with each one."""
        self.used.append("engine.dax_blank_arithmetic")
        terms = [(op, self._text_as_number(sql)) for op, sql in terms]
        for _op, sql in terms:
            if self._is_date(sql):
                raise CompileError(
                    "a date in + or -: DAX reads it as a datetime, a number of days from 30 "
                    "December 1899 (DATE(2024, 1, 1) + 0.5 is noon, and a date minus a date is "
                    "a datetime too), and the result's type is not carried through the "
                    "expression")
        kinds = [self._literal_kind(sql) for _, sql in terms]
        parts = [sql if kind else f"COALESCE({sql}, 0)" for (_, sql), kind in zip(terms, kinds)]
        body = parts[0] + "".join(f" {op} {part}" for (op, _), part in zip(terms[1:], parts[1:]))
        if any(kinds):
            return body
        blank = " AND ".join(f"{sql} IS NULL" for _, sql in terms)
        return f"(CASE WHEN {blank} THEN NULL ELSE {body} END)"

    def _dax_op(self, op: str, a: str, b: str) -> str:
        if op in ("&&", "||"):
            return f"{a} {'AND' if op == '&&' else 'OR'} {b}"
        if op in ("^", "*", "/"):
            a, b = self._text_as_number(a), self._text_as_number(b)
        if op == "^":
            return f"POWER({a}, {b})"
        lit_a, lit_b = self._literal_kind(a), self._literal_kind(b)
        if op == "/" and not (lit_b == "num" and self._nonzero(b)) \
                and self.error_conds is not None:
            # Inside IFERROR / ISERROR a zero or blank divisor under a non-blank dividend IS
            # an error (seen live: IFERROR(5 / 0, -1) is -1, IFERROR(BLANK() / 0, -1) BLANK).
            self.error_conds.append(f"({a} IS NOT NULL AND COALESCE({b}, 0) = 0)")
        if op == "/" and not (lit_b == "num" and self._nonzero(b)):
            # IEEE division (seen live): a zero or blank divisor gives an infinity or NaN,
            # where SQL raises. The dialect's spelling is the rule row's.
            rule = self._function_rule("/")
            return _apply(_emit_for(rule, self.dialect), [a, b], "/")
        if op in ("*", "/"):
            return f"{a} {op} {b}"
        if op == "&":
            self.used.append("engine.dax_concatenate")
            ca = a if lit_a == "str" else f"COALESCE(CAST({a} AS VARCHAR), '')"
            cb = b if lit_b == "str" else f"COALESCE(CAST({b} AS VARCHAR), '')"
            return f"{ca} || {cb}"
        # A comparison.
        sql_op = {"==": "=", "!=": "<>"}.get(op, op)
        if "NULL" in (a.strip().upper(), b.strip().upper()):
            if op == "==":
                other = b if a.strip().upper() == "NULL" else a
                return f"{other} IS NULL"
            # A comparison with BLANK() reads the blank as the other side's type's zero (seen
            # live: BLANK() > 0 FALSE, BLANK() >= 0 TRUE, -1 < BLANK() TRUE, "a" > BLANK()
            # TRUE, "" < BLANK() FALSE, FALSE() < BLANK() FALSE, BLANK() <= BLANK() TRUE).
            both = a.strip().upper() == "NULL" and b.strip().upper() == "NULL"
            kind = "num" if both else self._blank_kind(b if a.strip().upper() == "NULL" else a)
            if kind is None:
                raise CompileError(f"a comparison with BLANK() by {op!r}: a blank equals 0, "
                                   f"\"\" and FALSE as well, and the other side's type is not "
                                   f"known")
            self.used.append("engine.dax_blank_comparison")
            if both:
                return "TRUE" if sql_op in ("=", "<=", ">=") else "FALSE"
            # A blank date is the zero date, 30 December 1899 (DAX's day 0).
            fill = {"num": "0", "str": "''", "bool": "FALSE",
                    "date": "CAST('1899-12-30' AS DATE)"}[kind]
            ca = fill if a.strip().upper() == "NULL" else f"COALESCE({a}, {fill})"
            cb = fill if b.strip().upper() == "NULL" else f"COALESCE({b}, {fill})"
            return f"{ca} {sql_op} {cb}"
        if op == "==" or (lit_a and lit_b) or not (lit_a or lit_b):
            # Strict equality treats a blank as itself; with no literal side the type a blank
            # stands for is not known, so the comparison is as written.
            return f"{a} {sql_op} {b}"
        fill = "0" if (lit_a or lit_b) == "num" else "''"
        self.used.append("engine.dax_blank_comparison")
        ca = a if lit_a else f"COALESCE({a}, {fill})"
        cb = b if lit_b else f"COALESCE({b}, {fill})"
        if fill == "''":
            # DAX compares text case-insensitively (seen live: `REGION = "North"` is TRUE for
            # 'north'); SQL's comparison is the collation's, case-sensitive on most engines.
            self.used.append("engine.dax_text_comparison")
            return f"UPPER({ca}) {sql_op} UPPER({cb})"
        return f"{ca} {sql_op} {cb}"

    @classmethod
    def _nonzero(cls, sql: str) -> bool:
        """A plain number literal other than zero (arithmetic is not evaluated)."""
        t = sql.strip()
        while t.startswith("(") and t.endswith(")") and _balanced(t[1:-1]):
            t = t[1:-1].strip()
        return bool(cls._NUM.match(t)) and float(t) != 0

    _NUMERIC_AGG = re.compile(r"\b(SUM|COUNT|AVG|MEDIAN|STDDEV\w*|VAR\w*)\s*\(", re.I)

    def _blank_kind(self, sql: str) -> str | None:
        """The type a BLANK() compared with `sql` stands for: `num`, `str` or `bool`, or None
        when the SQL does not show it. A literal says; a count, sum or average is a number; a
        column of the statement's model is its declared type."""
        lit = self._literal_kind(sql)
        s = sql.strip()
        while s.startswith("(") and s.endswith(")") and _balanced(s[1:-1]):
            s = s[1:-1].strip()
        if s.upper() in ("TRUE", "FALSE"):
            return "bool"
        if lit:
            return lit
        outer = re.match(r"(\w+)\s*\(", s)
        if "||" not in s and self._NUMERIC_AGG.search(s) and not (
                outer and outer.group(1).upper() in ("MIN", "MAX", "CASE")):
            return "num"
        # A column, or the MIN / MAX of one: the column's declared type.
        m = re.fullmatch(r"(?:(?:MIN|MAX)\s*\(\s*)?(\w+)\.(\w+)\s*\)?", s, re.I)
        if m and s.count("(") != s.count(")"):
            m = None
        plan = self.ctx.plan if self.ctx is not None else None
        if m and plan is not None:
            table = plan.table_at.get(m.group(1))
            t = self.ctx.types.get((str(table or "").lower(), m.group(2).lower())) or ""
            if re.match(r"(?i)^(string|text|varchar|char)", t):
                return "str"
            if re.match(r"(?i)^(int|double|decimal|number|float|currency)", t):
                return "num"
            if re.match(r"(?i)^bool", t):
                return "bool"
            if re.match(r"(?i)^(date|time)", t):
                return "date"
        if re.match(r"(?i)^CAST\('\d{4}-\d{2}-\d{2}[^']*' AS (DATE|TIMESTAMP)\)$", s):
            return "date"
        return None

    _DIGITS = re.compile(r"^\s*([+-]?)\s*(\d+)\s*$")

    def _text_as_number(self, sql: str) -> str:
        """A text literal as an arithmetic operand, read as the number DAX converts it to
        (seen live: "3" * 2 is 6, "1" + "2" is 3, -"4" is -4). DAX reads it by the model's
        culture; whole digits read alike in every culture, so they are that number. Text with
        a decimal or group separator, or that is no number, is refused by name, as VALUE is.
        Any other operand is returned as it is."""
        if self._literal_kind(sql) != "str":
            return sql
        s = sql.strip()
        while s.startswith("(") and s.endswith(")") and _balanced(s[1:-1]):
            s = s[1:-1].strip()
        text = s[1:-1].replace("''", "'")
        m = self._DIGITS.match(text)
        if not m:
            raise CompileError(
                f"the text {s} in arithmetic: DAX converts it to a number by the model's "
                f"culture (its decimal and group separators), which the warehouse does not "
                f"reproduce; only whole digits are read")
        self.used.append("engine.dax_text_as_number")
        n = int(m.group(2))
        return f"(-{n})" if m.group(1) == "-" and n else str(n)

    def _is_date(self, sql: str) -> bool:
        """Whether compiled SQL is certainly a date: a column declared a date (or MIN / MAX
        of one), a date literal, or a cast to a date, as DATE() compiles."""
        if self._blank_kind(sql) == "date":
            return True
        s = sql.strip()
        while s.startswith("(") and s.endswith(")") and _balanced(s[1:-1]):
            s = s[1:-1].strip()
        m = re.match(r"(?is)^CAST\((.*)\s+AS\s+(DATE|TIMESTAMP\w*|DATETIME)\)$", s)
        return bool(m) and _balanced(m.group(1))

    def _literal_kind(self, sql: str) -> str | None:
        s = sql.strip()
        while s.startswith("(") and s.endswith(")") and _balanced(s[1:-1]):
            s = s[1:-1].strip()
        if self._NUM.match(s) or self._CONST_ARITH.match(s):
            return "num"
        if self._STR.match(s):
            return "str"
        if s.upper() in ("TRUE", "FALSE"):
            return "num"
        return None

    # -- Tableau statement syntax: CASE / IF, logical NOT, NULL / TRUE / FALSE literals.
    @property
    def _tableau(self) -> bool:
        return self.language in ("tableau-calc", "tableau")

    @property
    def _dax(self) -> bool:
        return self.language == "dax"

    def _var_block(self) -> str:
        """`VAR a = … VAR b = … RETURN …`, by SUBSTITUTION inside the parser.

        Each variable's value is compiled once, where it is defined, and its SQL replaces every
        later bare reference to its name: in the variables after it and in RETURN. That is
        DAX's own semantics, not an approximation of it: a variable is evaluated in the filter
        context of its definition, so one defined outside a CALCULATE is NOT gated by that
        CALCULATE's filter, and its SQL (compiled with no filter) is exactly that. Names are
        case-insensitive; a block's names are visible only inside it, an inner block shadows
        an outer one, and a bare name resolves to a variable before anything else. Tokens are
        substituted, never text, so a name inside a string literal is left alone."""
        scope: dict[str, str] = {}
        self.vars.append(scope)
        try:
            while self._kw("VAR"):
                self.take()
                kind, name = self.take()
                if kind != "func":
                    raise CompileError(f"VAR needs a name, found {name!r}")
                if self.take() != ("op", "="):
                    raise CompileError(f"VAR {name} needs `=`")
                scope[name.lower()] = f"({self.expr()})"
            self._expect_kw("RETURN")
            return self.expr()
        finally:
            self.vars.pop()

    def _variable(self, name: str) -> str | None:
        for scope in reversed(self.vars):
            if name.lower() in scope:
                return scope[name.lower()]
        return None

    @staticmethod
    def _is_text(sql: str) -> bool:
        """Whether compiled SQL is certainly a string. A literal and a `||` chain are; so is a
        string function and a COALESCE whose fallback is a string literal — the shape
        `IFNULL([x], "")` compiles to, and `IFNULL([a], "") + IFNULL([b], "")` is how a
        Tableau calc builds a label. Missed, the `+` stays arithmetic and no engine runs it."""
        s = sql.strip()
        while s.startswith("(") and s.endswith(")") and _balanced(s[1:-1]):
            s = s[1:-1].strip()
        return ((s.startswith("'") and s.endswith("'")) or " || " in s
                or bool(re.match(r"(?is)^CASE\b.*\bTHEN\s+'(?:[^']|'')*'\s+(WHEN|ELSE|END)\b", s))
                or bool(_TEXT_FN.match(s))
                or bool(re.match(r"(?is)^COALESCE\(.*,\s*'(?:[^']|'')*'\s*\)$", s))
                or bool(re.match(r"(?is)^CAST\(.*\s+AS\s+(VARCHAR|CHAR|STRING|TEXT)\b.*\)$", s)))

    def _kw(self, word: str) -> bool:
        kind, value = self.peek()
        return kind == "func" and value.upper() == word

    def _expect_kw(self, word: str) -> None:
        if not self._kw(word):
            raise CompileError(f"expected {word}, found {self.peek()[1]!r}")
        self.take()

    def _case(self) -> str:
        subject = None
        if not self._kw("WHEN"):
            subject = self.expr()
        if not self._kw("WHEN"):
            raise CompileError("CASE without WHEN")
        branches: list[tuple[str, str]] = []
        while self._kw("WHEN"):
            self.take()
            when = self.expr()
            self._expect_kw("THEN")
            branches.append((when, self.expr()))
        other = None
        if self._kw("ELSE"):
            self.take()
            other = self.expr()
        self._expect_kw("END")
        # A CASE on a CONSTANT (a parameter already fixed to its value) can take one branch
        # only. Emitting all of them is the same number but drags every other branch's
        # columns into the statement — columns the warehouse may not have.
        lit = re.compile(r"^(?:-?\d+(?:\.\d+)?|'(?:[^']|'')*')$")
        if subject is not None and lit.match(subject.strip()) \
                and all(lit.match(w.strip()) for w, _ in branches):
            norm = lambda v: v.strip().strip("'") if v.strip().startswith("'") else float(v)
            try:
                hit = next((t for w, t in branches if norm(w) == norm(subject)), None)
            except ValueError:
                hit = None
            return f"({hit})" if hit is not None else (f"({other})" if other else "NULL")
        parts = ["CASE"] + ([subject] if subject is not None else [])
        parts += [f"WHEN {w} THEN {t}" for w, t in branches]
        if other is not None:
            parts.append(f"ELSE {other}")
        return "(" + " ".join(parts + ["END"]) + ")"

    def _if(self) -> str:
        cond = self.expr()
        self._expect_kw("THEN")
        parts = [f"WHEN {cond} THEN {self.expr()}"]
        while self._kw("ELSEIF"):
            self.take()
            cond = self.expr()
            self._expect_kw("THEN")
            parts.append(f"WHEN {cond} THEN {self.expr()}")
        if self._kw("ELSE"):
            self.take()
            parts.append(f"ELSE {self.expr()}")
        self._expect_kw("END")
        return "(CASE " + " ".join(parts) + " END)"

    def term(self) -> str:
        out = self._atom()
        while self._tableau and self.peek() == ("op", "^"):
            # Tableau's power operator binds tighter than `*` and `/`.
            self.take()
            rule = self._function_rule("^")
            out = _apply(_emit_for(rule, self.dialect), [out, self._atom()], "^")
        return out

    def _atom(self) -> str:
        kind, value = self.take()
        if kind is None:
            raise CompileError("expression ended early")
        if kind == "brace" and value == "{" and self._tableau:
            return self._lod(None)
        if kind == "brace":
            raise CompileError("unexpected brace")
        if kind == "op" and value in ("-", "+") and self._tableau:
            return f"{value}{self.term()}"          # unary sign: DATEADD('month', -11, …)
        if kind == "lparen":
            inner = self.expr()
            self.expect("rparen")
            return f"({inner})"
        if kind in ("col", "qualified", "quoted_qualified"):
            on = self.alias
            if kind != "col":
                on = self._bind(value)[0]
            col = _column_ref(value)
            if kind != "col" and self._dax:
                # `Table[Column]` naming a calculated column: its formula, over the same row.
                qkey = f"{_table_ref(value)}[{col}]".lower()
                if qkey in self.measures and qkey not in self.inlining and (
                        self.colmap is None or col.lower() not in self.colmap):
                    return self._inline_measure(qkey)
            # A calc that reads the RAW field of its own name (`Year` = `INT([year])`) names
            # the column, not itself: Tableau resolves the inner reference to the data field.
            self_ref = bool(self.inlining) and col.lower() == self.inlining[-1]
            if kind == "col" and col.lower() in self.measures and not self_ref and (
                    self.colmap is None or col.lower() not in self.colmap):
                return self._inline_measure(col)
            if self.colmap is None:
                if self.ctx is not None and self.ctx.renames:
                    # A column the model shows under another name than the one its table
                    # reads (a Power Query rename, a computed column's output name).
                    phys = (self._bind(value)[1] if kind != "col"
                            else (_bare(self.home[1]) if self.home else self.ctx.source))
                    # Keyed by the bare table name (`prepare`), whatever qualification the
                    # binding carries: a three-part warehouse name missed every rename.
                    col = self.ctx.renames.get((_bare(phys), col.lower()), col)
                if self._tableau and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", col):
                    # Not a known calc and not an identifier a warehouse column can have:
                    # `t.Sales-MTD/YTD` would PARSE (as arithmetic) and answer nonsense.
                    raise CompileError(f"[{col}] is neither a source column nor a calc "
                                       f"the compiler knows")
                return f"{on}.{col}"
            src = self.colmap.get(col.lower())
            if src is None:
                # Not a column of the query the statement reads. In DAX that is most often a
                # MEASURE reference (`[Requests Total]`), which this compiler does not inline;
                # emitting it as a column produced `t.Requests Total`, which does not parse.
                raise CompileError(f"[{col}] is not a column of the native query this "
                                   f"measure's table is built from (a measure reference is "
                                   f"not inlined)")
            return f"{self.alias}.{_ident(src)}"
        if kind == "str" and (self._tableau or self._dax) and value.startswith('"'):
            # Tableau and DAX write "month"; in SQL a double-quoted token is an IDENTIFIER.
            return _sql_string(value, self.dialect)
        if kind in ("str", "num"):
            return value
        if kind == "datelit":
            # A date literal: the date, or the date and time, it names.
            text = value.strip("#").replace("T", " ")
            self.used.append("engine.date_literal")
            return (f"CAST('{text}' AS TIMESTAMP)" if " " in text
                    else f"CAST('{text}' AS DATE)")
        if kind == "func" and self._dax:
            up = value.upper()
            if up in ("TRUE", "FALSE"):
                # DAX writes the constants as `TRUE()` or bare `TRUE`.
                if self.peek()[0] == "lparen" and self.i + 1 < len(self.t) \
                        and self.t[self.i + 1][0] == "rparen":
                    self.i += 2
                return up
            if self.peek()[0] != "lparen":
                var = self._variable(value)
                if var is not None:
                    return var
        if kind == "func":
            if self._tableau:
                up = value.upper()
                if up == "NULL":
                    return "NULL"
                if up in ("TRUE", "FALSE"):
                    return up
                if up == "NOT":
                    return f"NOT {self.term()}"
                if up == "CASE":
                    return self._case()
                if up == "IF":
                    return self._if()
            if self._dax and value.upper() == "NOT":
                # DAX's NOT, as a function (`NOT ( x )`) or as an operator (`NOT x`, looser
                # than a comparison: `NOT a = b` is NOT (a = b)). A blank is FALSE, so NOT of a
                # blank is TRUE.
                if self.peek()[0] == "lparen":
                    self.take()
                    inner = self.expr()
                    self.expect("rparen")
                else:
                    inner = self._dax_binary(2)
                return f"(NOT COALESCE({inner}, FALSE))"
            return self.call(value)
        raise CompileError(f"unexpected {value!r}")

    def _inline_measure(self, name: str) -> str:
        key = name.lower()
        if key in self.inlining:
            raise CompileError(f"measure [{name}] refers to itself through "
                               f"{' -> '.join(self.inlining + (key,))}")
        if self.row_level and "[" not in key and not (
                self.ctx is not None and key in self.ctx.row_formulas) \
                and not _context_free(key, self.measures, self.language):
            raise CompileError(f"measure [{name}] inside a row iterator is evaluated once per "
                               f"row in that row's filter context (a context transition), "
                               f"which one aggregate over the rows does not express")
        sub = self._child(_tokens(self.measures[key], self.language), row_filter=self.row_filter,
                          inlining=self.inlining + (key,), share_vars=False)
        # A table calculation inlined from another calc runs by that calc's own compute-using
        # where the measure states one for it (`table_calc.nested`).
        tc = self.ctx.table_calc if self.ctx is not None else None
        own = ((tc or {}).get("nested") or {}).get(key)
        if own is not None:
            self.ctx.table_calc = {**own, "nested": tc.get("nested")}
        try:
            sql = sub.parse()
        finally:
            if own is not None:
                self.ctx.table_calc = tc
            self._absorb(sub)
        return f"({sql})"

    def _calculate(self) -> str | None:
        """`CALCULATE(expr, filter, ...)`, or None for the declared limit.

        Each filter argument is one of:

          a simple filter     a predicate over the rows' columns (`_filter_condition`); a
                              column of a dimension is read through the relationship that
                              carries the filter to the measure's rows. In DAX it REPLACES the
                              visual's own filter on that column: where the visual groups by
                              the column, the aggregate is summed across the visual's rows
                              that differ only in it (a window); where the visual FILTERS it,
                              one statement cannot, and that is refused by name.
          KEEPFILTERS(simple) the same predicate, intersected with the visual's own filter
                              (conditional aggregation exactly; no override).
          FILTER(T, pred)     T the measure's own table: a row predicate evaluated in the
                              visual's context, so conditional aggregation exactly.
          ALL / REMOVEFILTERS / ALLEXCEPT / ALLSELECTED
                              the visual's grouping on the named columns or tables is removed:
                              each aggregate is summed over the visual's rows that differ only
                              in those (`_removal`).
          USERELATIONSHIP     the join along the named inactive relationship; the statement's
                              join plan was switched before compiling (compile_unit).
          a date set          DATESYTD, SAMEPERIODLASTYEAR, DATEADD, ...: the aggregates read
                              the dates of that set (`_compile_time_unit`); also the
                              date-table lookup `FILTER(ALL(D), D[c] IN VALUES(D[p]))`
                              (`_lookup_dates`).

        Anything else (a measure predicate, a variable, another table function) returns None
        and the rule table's declared limit names CALCULATE.
        """
        start = self.i
        if self.peek()[0] != "lparen":
            return None
        end, args = _call_args(self.t, start)
        if end is not None and len(args) == 1 and not self.row_level:
            # No filter argument and no row context to turn into filters: the expression in
            # the context it is in.
            sub = self._child(args[0], row_filter=self.row_filter, inlining=self.inlining,
                              share_vars=True)
            try:
                sql = sub.parse()
            finally:
                self._absorb(sub)
                self.calc_columns |= sub.calc_columns
            self.i = end + 1
            self.used.append("dax.calculate.no_filter")
            return f"({sql})"
        if end is None or len(args) < 2:
            return None
        # What the filters record is kept only if every one of them is simple: a richer one
        # anywhere means the declared limit, and an unknown named on the way would be noise.
        kept = (len(self.used), len(self.unknown), set(self.calc_columns))
        conds, errors = [], []
        overrides: list[tuple[str, str | None]] = []       # (column, table) a filter replaces
        removal: list[tuple[str, Any]] = []                # (modifier, what it removes)
        date_sets: list[_DateSet] = []
        for toks in args[1:]:
            head, inner = _call_head(toks)
            try:
                if head == "KEEPFILTERS":
                    conds.append(self._filter_condition(inner, keep=True))
                elif head in _REMOVERS:
                    removal.append((head, self._removed(head, inner)))
                elif head in ("USERELATIONSHIP", "CROSSFILTER"):
                    self._check_userelationship(inner, head)
                elif head in _DATE_SETS:
                    date_sets.append(self._date_set(head, inner))
                elif (running := self._running_dates(toks)) is not None:
                    date_sets.append(running)
                elif (looked := self._lookup_dates(toks)) is not None:
                    date_sets.append(looked)
                elif head == "FILTER":
                    conds.append(self._filter_table(inner, overrides))
                elif head == "TREATAS":
                    # TREATAS({values}, T[c]): the values as a filter on T[c], replacing the
                    # visual's own filter on it (seen live), i.e. `T[c] IN {values}`.
                    parts = _split_top(inner)
                    if (len(parts) != 2 or not parts[0] or parts[0][0] != ("brace", "{")
                            or self._column_arg(parts[1]) is None
                            or any(k == "lparen" for k, _v in parts[0])):
                        raise _NotSimple
                    self._last_cols = []
                    conds.append(self._filter_condition(parts[1] + [("func", "IN")]
                                                        + parts[0]))
                    overrides += self._last_cols
                    self.used.append("engine.dax_treatas_values")
                else:
                    self._last_cols = []
                    conds.append(self._filter_condition(toks))
                    overrides += self._last_cols
            except _NotSimple:
                del self.used[kept[0]:]
                del self.unknown[kept[1]:]
                self.calc_columns = kept[2]
                return None
            except CompileError as exc:
                # A filter this dialect or this statement cannot express. Named, but only once
                # every filter is known to be one the compiler reads (above).
                errors.append(exc)
        if errors:
            raise errors[0]
        self._drops = []
        self._slot_drops = []
        window = self._window_for(removal, overrides)
        drops = self._drops
        slot_drops = self._slot_drops
        cond = " AND ".join(c for c in conds if c)
        if self.row_filter and cond:
            cond = f"({self.row_filter}) AND {cond}"
        elif self.row_filter:
            cond = self.row_filter
        sub = self._child(args[0], row_filter=cond or None, inlining=self.inlining,
                          share_vars=True)
        # A date set in THIS CALCULATE is read from the row's own dates, before its other
        # filters apply (seen live: DATESYTD beside MONTH <= 6 of March is January to March);
        # what they do to the date table's columns the date set replaces, so they neither
        # remove the row's date grouping nor drop its date filters here.
        here = {_bare(ds.column[1]) for ds in date_sets} if self.slots is not None else set()
        if here:
            slot_drops = [d for d in slot_drops if _bare(d[1] or "") not in here]
            if window is not None and self.ctx is not None:
                window = list(window) + [sql for _c, t, sql in self.ctx.groups
                                         if _bare(t or "") in here and sql not in window]
                if len(window) == len(self.ctx.groups) and not drops:
                    window = None
        if window is not None and self.slots is not None:
            # Beside time intelligence each aggregate is a slot of a sub-statement: the
            # slot's sub-statement is grouped by the columns the CALCULATE keeps
            # (`_compile_time_unit`), its filters taken off as `slot_drops` say.
            sub.slot_keep = (list(window) if self.slot_keep is None
                             else [k for k in window if k in self.slot_keep])
            self.used.append("engine.dax_time_intelligence_window")
        elif window is not None:
            if self.window is not None:
                # Inside another CALCULATE that removes grouping: both removals hold (the
                # columns either keeps, the filters either takes off).
                window = [k for k in window if k in self.window]
                drops = list(dict.fromkeys(list(self.window_drop or []) + list(drops)))
                self._keep_filters = self._keep_filters and not self.window_own
                self.used.append("engine.dax_context_window_nested")
            sub.window = window
            sub.window_drop = drops
            if self.ctx is not None and self.ctx.measure_filtered:
                # The visual keeps the rows whose measure passes, a filter on its grouping
                # columns (seen live: ALLSELECTED() is the sum of the rows kept; ALL(channel)
                # reads every channel's rows, the ones left out too).
                sub.window_selected = self._keep_filters
                sub.window_own = not self._keep_filters
        if slot_drops:
            sub.slot_drops = list(self.slot_drops) + slot_drops
        if date_sets:
            if self.slots is None:
                raise _TimeIntelligence()
            # Two date sets in one CALCULATE are both read from the row's dates and
            # intersected; a date set inside another's CALCULATE is read from the dates the
            # outer one leaves (seen live: CALCULATE([YTD], SAMEPERIODLASTYEAR(d)) of March
            # 2025 is January to March 2024; DATESYTD beside DATESINPERIOD(-2 months) of
            # March is February and March).
            sub.date_set = _DateChain.then(self.date_set, date_sets)
        try:
            sql = sub.parse()
        finally:
            self._absorb(sub)
            self.used.append("dax.calculate.filter")
            self.calc_columns |= sub.calc_columns
        self.i = end + 1
        return f"({sql})"

    # -- CALCULATE modifiers -------------------------------------------------------------
    def _table_arg(self, toks) -> str | None:
        """A bare or quoted table name as one argument (`Sales`, `'Sales Data'`): the
        warehouse table it reads (bare, lower), or its own name when the model does not say."""
        if len(toks) != 1 or toks[0][0] not in ("func", "str"):
            return None
        k, v = toks[0]
        if k == "str":
            if not v.startswith("'"):
                return None
            v = v[1:-1].replace("''", "'")
        if self.home is not None:
            return _bare(self.home[0].get(v.lower()) or v)
        return _bare(v)

    def _column_arg(self, toks) -> tuple[str, str | None] | None:
        """`T[c]` as one argument: (column lower, the warehouse table it reads, bare)."""
        if len(toks) != 1 or toks[0][0] not in ("qualified", "quoted_qualified", "col"):
            return None
        v = toks[0][1]
        col = _column_ref(v).lower()
        table = _table_ref(v) if toks[0][0] != "col" else None
        if table and self.home is not None:
            table = self.home[0].get(table.lower()) or table
        return col, (_bare(table) if table else None)

    def _removed(self, head: str, inner) -> dict:
        """What ALL / REMOVEFILTERS / ALLEXCEPT / ALLSELECTED take off the visual's context:
        {"all": bool, "tables": {bare}, "columns": {(col, bare)}, "except": {(col, bare)}}.
        The measure's own table stands for everything it reaches: removing the filters of a
        table removes those of its expanded table, which holds every table its many-to-one
        relationships reach."""
        out = {"all": False, "tables": set(), "columns": set(), "except": set()}
        parts = _split_top(inner) if inner else []
        source = _bare(self.home[1]) if self.home else (self.ctx.source if self.ctx else None)
        expand = self.ctx.expanded if self.ctx is not None else (lambda t: {t})
        renames = self.ctx.renames if self.ctx is not None else {}

        def read_as(col: str, table: str | None) -> str:
            # The column as its table reads it (a Power Query rename): the visual's grouping
            # and filters name it so, and `_window_for` compares the two by name.
            return str(renames.get((str(table or source or "").lower(), col), col)).lower()

        if not parts:
            out["all"] = True
            return out
        if head == "ALLEXCEPT":
            table = self._table_arg(parts[0])
            cols = [self._column_arg(p) for p in parts[1:]]
            if table is None or not cols or None in cols:
                raise CompileError("ALLEXCEPT is compiled as ALLEXCEPT(table, column, ...)")
            if table == source:
                out["all"] = True
            else:
                out["tables"] |= expand(table)
            out["except"] = {(read_as(c, t or table), t or table) for c, t in cols}
            return out
        for p in parts:
            table = self._table_arg(p)
            if table is not None:
                if table == source:
                    out["all"] = True
                else:
                    out["tables"] |= expand(table)
                continue
            col = self._column_arg(p)
            if col is None:
                raise CompileError(f"{head} is compiled over tables and columns, not "
                                   f"{' '.join(v for _k, v in p)!r}")
            out["columns"].add((read_as(*col), col[1]))
        return out

    def _window_for(self, removal, overrides) -> list[str] | None:
        """The PARTITION BY for the aggregates of a CALCULATE that removes the visual's
        grouping on some columns, or None when it removes none of them.

        Exact only over the visual's own rows: every row of the statement's table that passes
        its filters belongs to one of them, so summing an additive aggregate across the rows
        that differ only in the removed columns is the aggregate over the context the
        CALCULATE leaves. Hence the refusals: a removed column the visual also FILTERS (ALL
        drops that filter, the statement's rows have it applied), ALLSELECTED beside a Top N
        (which rows are "selected" is the Top N's), and a filter that replaces one the
        visual applies."""
        ctx = self.ctx
        if not removal and not overrides:
            return None
        if ctx is None:
            if overrides:
                # No statement around this formula to say what the visual groups by: the
                # outer check (`_compile_measure`) compares the columns by name.
                self.calc_columns |= {c for c, _t in overrides}
            if removal:
                raise CompileError("ALL / REMOVEFILTERS need the visual's grouping columns, "
                                   "which this compile was not given")
            return None
        kinds = {h for h, _r in removal}
        keep_filters = kinds == {"ALLSELECTED"}
        self._keep_filters = keep_filters

        def hit(col: str, table: str | None, only: Any = None) -> bool:
            for _h, r in removal:
                if only is not None and not only(_h):
                    continue
                if (col, table) in r["except"] or any(c == col and t is None
                                                      for c, t in r["except"]):
                    continue
                if r["all"] or (table and table in r["tables"]):
                    return True
                if any(c == col and (t is None or table is None or t == table)
                       for c, t in r["columns"]):
                    return True
            return False

        def overridden(col: str, table: str | None) -> bool:
            return any(c == col and (t is None or table is None or t == table)
                       for c, t in overrides)

        def hit_all(col: str, table: str | None) -> bool:
            # ALL / REMOVEFILTERS / ALLEXCEPT take the column's filters off too; ALLSELECTED
            # beside them only its grouping (seen live: CALCULATE(m, ALLSELECTED(status),
            # ALL(channel)) keeps the status filter and drops the channel one).
            return hit(col, table, only=lambda h: h != "ALLSELECTED")

        if "ALLSELECTED" in kinds and kinds - {"ALLSELECTED"}:
            self.used.append("engine.dax_allselected_beside_all")
            for col, table, _sql in ctx.groups:
                if hit(col, table, only=lambda h: h == "ALLSELECTED") and hit_all(col, table) \
                        and not any(r["all"] for h, r in removal if h == "ALLSELECTED"):
                    raise CompileError(f"ALLSELECTED and ALL of the same column {col!r} in one "
                                       f"CALCULATE are not compiled")
        for col, table in ctx.filters:
            if overridden(col, table):
                if self.slots is not None or self.date_set is not None:
                    # Beside time intelligence every aggregate is a slot of a sub-statement:
                    # the slot's sub-statement reads its rows without the visual's filter
                    # (`_compile_time_unit`), under the CALCULATE's own predicate.
                    self._slot_drops.append((col, table))
                    continue
                # DAX REPLACES the visual's filter on the column: the aggregate reads the rows
                # without it, under the CALCULATE's own predicate (`_context_statements`).
                self._drops.append((col, table))
                continue
            if not keep_filters and removal and hit_all(col, table):
                if self.slots is not None or self.date_set is not None:
                    # Beside time intelligence: the slot's sub-statement reads its rows
                    # without the filter (`_compile_time_unit`).
                    self._slot_drops.append((col, table))
                    continue
                # The rows without that filter: the aggregate is its own statement over them
                # (`_context_statements`), never a window over the filtered rows.
                self._drops.append((col, table))
        if "ALLSELECTED" in kinds and ctx.top_n and not ctx.top_n_filters:
            # A Top N kept by the statement's LIMIT: the rows it keeps are known only after
            # the statement is aggregated. A Top N FILTER (the members a ranking keeps, the
            # visual's own form) is a filter ALLSELECTED keeps like any other (seen live: the
            # top 3 products' ALLSELECTED total is the sum of the three).
            raise CompileError("ALLSELECTED under a Top N: which rows are selected is the "
                               "Top N's, which the statement applies after aggregating")
        keep = [sql for col, table, sql in ctx.groups
                if not (hit(col, table) or overridden(col, table))]
        if len(keep) == len(ctx.groups) and not self._drops:
            return None
        self.used.append("engine.dax_context_window")
        return keep

    def _check_userelationship(self, inner, head: str = "USERELATIONSHIP") -> None:
        """USERELATIONSHIP(a, b) / CROSSFILTER(a, b, direction): the statement's join plan was
        switched for them before compiling (`_measure_graph`); here only checked."""
        parts = _split_top(inner)
        n = 2 if head == "USERELATIONSHIP" else 3
        cols = [self._column_arg(p) for p in parts[:2]]
        if len(parts) != n or None in cols:
            raise CompileError(f"{head} is compiled with two columns"
                               + ("" if n == 2 else " and a direction"))
        if n == 3 and not (len(parts[2]) == 1 and str(parts[2][0][1]).strip("'\"").upper()
                           in ("BOTH", "NONE", "ONEWAY", "ONEWAY_LEFTFILTERSRIGHT",
                               "ONEWAY_RIGHTFILTERSLEFT")):
            raise CompileError("CROSSFILTER's direction is Both, None or OneWay")
        ctx = self.ctx
        key = frozenset(cols)
        if ctx is None or key not in ctx.relationships_used:
            raise CompileError(f"{head} names a relationship the model does not declare")
        if n == 3:
            self._check_one_way(str(parts[2][0][1]).strip("'\"").upper(), cols, key)
        self.used.append("engine.dax_userelationship" if n == 2 else "engine.dax_crossfilter")

    def _check_one_way(self, how: str, cols: list, key: frozenset) -> None:
        """CROSSFILTER's one-way directions the relationship's cardinality does not take, each
        refused by name. Microsoft's CROSSFILTER reference
        (learn.microsoft.com/dax/crossfilter-function-dax), in its own terms: OneWay is the
        one (lookup) side filtering the other, is not for a one-to-one relationship, and on a
        many-to-many one leaves unclear which side is the lookup side;
        OneWay_LeftFiltersRight and OneWay_RightFiltersLeft name the filtering side and are
        not for a one-to-one or many-to-one relationship. Seen against Power BI: a one-way
        direction on a one-to-one relationship is an error, and so is the many side alone
        filtering the one side, while the one side named as the filtering side is OneWay
        itself. The join plan read every one of them as the relationship's single direction
        and gave a number."""
        if not how.startswith("ONEWAY"):
            return
        hit = _spec_edge(self.ctx.graph, key)
        if hit is None:
            return
        edge = self.ctx.graph[hit]
        fs, ts = _card_ends(edge.get("cardinality"))
        if fs == ts == "one":
            raise CompileError("CROSSFILTER with a one-way direction on a one-to-one "
                               "relationship: Power BI rejects it (Both or None are its "
                               "directions)")
        if how == "ONEWAY":
            if fs == ts == "many":
                raise CompileError("CROSSFILTER OneWay on a many-to-many relationship: which "
                                   "side filters the other is not defined")
            return
        ends = {_bare(edge.get("from_table")): fs, _bare(edge.get("to_table")): ts}
        filtering = cols[0 if how == "ONEWAY_LEFTFILTERSRIGHT" else 1][1]
        side = ends.get(filtering) if len(ends) == 2 else None
        if side == "one" and "many" in (fs, ts):
            return                      # the one side filters the many side: OneWay itself
        if side == "many" and "one" in (fs, ts):
            raise CompileError("CROSSFILTER with the many side of a many-to-one relationship "
                               "alone filtering the one side: Power BI rejects it")
        raise CompileError(f"CROSSFILTER {how.title()} on a relationship that is not "
                           f"many-to-one: no exact rule")

    def _filter_table(self, inner, overrides) -> str:
        """`FILTER(T, predicate)` as a CALCULATE filter. Over the measure's own table it is
        evaluated in the visual's context, so it is a row condition and replaces nothing.
        Over `ALL(T[c])` with a predicate on that column it is the simple filter on it, which
        replaces the visual's filter on the column."""
        parts = _split_top(inner)
        if len(parts) != 2:
            raise _NotSimple
        table = self._table_arg(parts[0])
        source = _bare(self.home[1]) if self.home else None
        if table is not None and (source is None or table == source):
            return self._row_predicate(parts[1])
        head, cols = _call_head(parts[0])
        if head == "ALL":
            col = self._column_arg(cols)
            if col is None:
                raise _NotSimple
            self._last_cols = []
            cond = self._filter_condition(parts[1])
            if any(c != col[0] for c, _t in self._last_cols):
                raise _NotSimple
            overrides += self._last_cols
            return cond
        raise _NotSimple

    def _row_predicate(self, toks) -> str:
        """A row expression used as a condition, with DAX's blank comparisons; a NULL is
        FALSE."""
        sub = self._child(toks, row_filter=None, inlining=self.inlining, share_vars=True)
        sub.row_level = True
        try:
            sql = sub.parse()
        finally:
            self._absorb(sub)
        return f"COALESCE({sql}, FALSE)"

    def _filter_condition(self, toks: list[tuple[str, str]], keep: bool = False) -> str:
        """One CALCULATE filter argument -> a SQL row condition, or `_NotSimple`.

        The forms, each with DAX's BLANK semantics made explicit (SQL's NULL differs):

          T[c] <op> literal          a string, a number, TRUE / FALSE (with or without `()`).
                                     DAX compares a blank as 0, "" or FALSE, so a blank row
                                     passes `< 5`, `<> "x"` or `= FALSE()`: `OR c IS NULL`
                                     is added exactly when it does (`_blank_passes`).
          T[c] IN { lit, ... }       membership; DAX's IN is strict, so a blank never matches.
          ISBLANK ( T[c] )           `IS NULL`; NOT ISBLANK is `IS NOT NULL`.
          CONTAINSSTRING ( T[c], "text" )
                                     the rule table's row for the dialect, refused by name
                                     where the dialect has none.
          a && b, a || b, ( a ), NOT a
                                     AND / OR (`||` binds weaker); grouping; DAX's NOT of a
                                     condition (a SQL NULL is FALSE first, or NOT would drop
                                     the blank rows DAX keeps).

        TEXT is compared as the model holds it (`_text_key`): case-insensitively, and a
        column's value without its trailing spaces (seen live: 'Retail', 'retail' and
        'Retail ' are one value, which `= "RETAIL"` matches and `= "Retail "` does not).

        T may be another table than the measure's: the column is read on that table, joined
        along the relationship that carries its filter to the measure's rows (`foreign_ok`).
        Each column read is recorded in `_last_cols`, so the caller knows which of the
        visual's columns the filter replaces (`keep`: KEEPFILTERS, which replaces none).

        A measure, a variable, a table or any other function is not simple."""
        pos = 0
        if not keep:
            self._last_cols = []

        def peek(o: int = 0):
            return toks[pos + o] if pos + o < len(toks) else (None, None)

        def take():
            nonlocal pos
            tok = peek()
            pos += 1
            return tok

        def kw(word: str) -> bool:
            k, v = peek()
            return k == "func" and v.upper() == word

        def want(kind: str, value: str | None = None):
            k, v = take()
            if k != kind or (value is not None and v != value):
                raise _NotSimple

        def column() -> str:
            k, v = take()
            if k not in ("qualified", "quoted_qualified", "col"):
                raise _NotSimple
            col = _column_ref(v)
            if k == "col" and col.lower() in self.measures:
                raise _NotSimple                    # a measure predicate: not a row filter
            on = self.alias
            table = None
            if k != "col":
                on, table = self._bind(v, foreign=True)
            if not keep:
                self.calc_columns.add(col.lower())
                self._last_cols.append((col.lower(), table))
            if self.colmap is not None:
                if col.lower() not in self.colmap:
                    raise _NotSimple
                return f"{self.alias}.{_ident(self.colmap[col.lower()])}"
            if self.ctx is not None and self.ctx.renames:
                # The column as its table reads it (a model column shown under another name,
                # `Void Line Flag` read as `Void_Line_Flag`), as in a measure's own body.
                phys = table or (_bare(self.home[1]) if self.home else self.ctx.source)
                col = self.ctx.renames.get((_bare(phys), col.lower()), col)
            return f"{on}.{col}"

        # Text compared as the model holds it, unless the source compares it (a table read by
        # DirectQuery: the statement's own comparison, case and spaces kept).
        fold = self.ctx is None or not self.ctx.source_compared

        def literal() -> tuple[str, Any]:
            k, v = take()
            if k == "str" and v.startswith('"'):
                # Text is compared as the model holds it (`_text_key`), so the literal is
                # upper-cased here; its own spaces are kept.
                text = v[1:-1].replace('""', '"')
                return _quote(text.upper() if fold else text, self.dialect), text
            if k == "num":
                return v, float(v)
            if (k, v) == ("op", "-") and peek()[0] == "num":
                n = take()[1]
                return f"-{n}", -float(n)
            if k == "func" and v.upper() in ("TRUE", "FALSE"):
                if peek()[0] == "lparen":
                    want("lparen")
                    want("rparen")
                return v.upper(), 1.0 if v.upper() == "TRUE" else 0.0
            if k == "func" and peek()[0] != "lparen":
                # A variable holding a constant (a number, or a parameter written in as
                # its value): a scalar in every row, so the comparison stays a row filter.
                var = self._variable(v)
                text = (var or "").strip()
                while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
                    text = text[1:-1].strip()
                if var is not None and re.fullmatch(r"-?\d+(\.\d+)?", text):
                    self.used.append("engine.dax_constant_variable")
                    return text, float(text)
            raise _NotSimple

        def isblank(negate: bool) -> str:
            take()
            want("lparen")
            col = column()
            want("rparen")
            return f"{col} IS {'NOT ' if negate else ''}NULL"

        def contains() -> str:
            name = take()[1]
            want("lparen")
            col = column()
            want("comma")
            text = take()
            want("rparen")
            rule = self._function_rule(name)
            emit = rule.get("emit") or {}
            if emit.get("untranslatable"):
                raise CompileError(f"{name.upper()}: {emit['untranslatable']}")
            _check_literal_args(emit, name, self.language, [[], [text]])
            if text[0] != "str" or not text[1].startswith('"'):
                raise _NotSimple
            return _apply(_emit_for(rule, self.dialect), [col, _sql_string(text[1], self.dialect)],
                          name)

        def atom() -> str:
            if peek()[0] == "lparen":
                take()
                inner = disj()
                want("rparen")
                return f"({inner})"
            if kw("NOT"):
                take()
                if kw("ISBLANK"):
                    return isblank(negate=True)
                return f"NOT COALESCE({atom()}, FALSE)"
            if kw("ISBLANK"):
                return isblank(negate=False)
            if kw("CONTAINSSTRING"):
                return contains()
            col = column()
            if kw("IN"):
                take()
                want("brace", "{")
                items = [literal()]
                while peek()[0] == "comma":
                    take()
                    items.append(literal())
                want("brace", "}")
                if fold and all(isinstance(v, str) for _s, v in items):
                    self.used.append("engine.dax_text_key")
                    return f"{self._text_key(col)} IN ({', '.join(s for s, _v in items)})"
                return f"{col} IN ({', '.join(s for s, _v in items)})"
            op = take()
            if op[0] != "op" or op[1] not in _COMPARISONS:
                raise _NotSimple
            lit, value = literal()
            if fold and isinstance(value, str):
                self.used.append("engine.dax_text_key")
                pred = f"{self._text_key(col)} {op[1]} {lit}"
            else:
                pred = f"{col} {op[1]} {lit}"
            return f"({pred} OR {col} IS NULL)" if _blank_passes(op[1], value) else pred

        def conj() -> str:
            parts = [atom()]
            while peek() == ("op", "&&"):
                take()
                parts.append(atom())
            return " AND ".join(parts)

        def disj() -> str:
            parts = [conj()]
            while peek() == ("op", "||"):
                take()
                parts.append(conj())
            if len(parts) == 1:
                return parts[0]
            # Each side is a definite TRUE or FALSE in DAX; a SQL NULL side is FALSE.
            return " OR ".join(f"COALESCE({p}, FALSE)" for p in parts)

        out = disj()
        if pos != len(toks):
            raise _NotSimple
        return out

    @staticmethod
    def _text_key(sql: str) -> str:
        """A text column as the model compares it: case-insensitive, trailing spaces dropped
        (the model keeps one value for 'Retail', 'retail' and 'Retail '; a literal keeps its
        own spaces, seen live)."""
        return f"UPPER(RTRIM({sql}))"

    def _bind(self, token: str, foreign: bool = False) -> tuple[str, str | None]:
        """The alias a `Table[Column]` is read under, and the warehouse table it reads (bare).

        The statement's own table is its own alias. Another table's column is read on that
        table only where `foreign` (or `foreign_ok`) allows it and the statement joins that
        table along a many-to-one relationship (`ctx.place`); anywhere else it is refused:
        bound to this statement's alias it would read a different column, or none."""
        table = _table_ref(token)
        if self.home is None or not table:
            return self.alias, (_bare(self.home[1]) if self.home else None)
        table_of, source = self.home
        physical = table_of.get(table.lower())
        if not physical or physical.lower() == source.lower():
            return self.alias, _bare(source)
        col = _column_ref(token)
        if (foreign or self.foreign_ok) and self.ctx is not None and self.colmap is None:
            at = self.ctx.place(col, physical)
            if at:
                self.used.append("engine.dax_related_table_column")
                return at, _bare(physical)
            why = self.ctx.unplaced.get((col.lower(), _bare(physical)))
            raise CompileError(f"{token.strip()} reads {physical}, which this statement does "
                               f"not reach along a many-to-one relationship from {source}"
                               + (f" ({why})" if why else ""))
        raise CompileError(f"{token.strip()} reads {physical}, not this statement's table "
                           f"{source}; a measure across tables needs one aggregate per "
                           f"table and is not compiled into one FROM")

    def _function_rule(self, name: str) -> dict:
        """The rule row for a function call in this language and dialect, or a named refusal.
        A row that exists for OTHER dialects is named as such: the fix is a row for this one."""
        rule = self.rules.function(self.language, name, self.dialect)
        if rule is None:
            self.unknown.append(f"{self.language}:{name.upper()}")
            known = self.rules.function(self.language, name)
            if known is not None:
                raise CompileError(f"no rule for {self.language} function {name.upper()} in "
                                   f"the {self.dialect} dialect; "
                                   + _no_form_reason(known, self.dialect))
            raise CompileError(f"no rule for {self.language} function {name.upper()}")
        self.used.append(rule["id"])
        if rule.get("tier") == "provisional":
            self.provisional.append(rule["id"])
        return rule

    def _scalar_iterator(self, name: str, emit: dict) -> str:
        """`MINX ( { a, b, c }, [Value] )`: an iterator over a table constructor of scalars.

        DAX's iterator skips a blank and is blank only when every value is. A SQL function
        such as LEAST may return NULL when ANY argument is NULL, so each argument is first
        coalesced with the others (`_scalar_list`): a NULL is replaced by one of the other
        values, which changes neither the least nor the greatest. That is right whatever the
        dialect's function does with NULL. Over a model table the iterator reads rows, which
        one scalar call cannot, so it is refused by name."""
        self.expect("lparen")
        if self.peek() != ("brace", "{"):
            raise CompileError(f"{name.upper()} over a table iterates its rows; only a table "
                               f"constructor of scalars ({{ a, b }}) is compiled")
        self.take()
        items = [self.expr()]
        while self.peek()[0] == "comma":
            self.take()
            items.append(self.expr())
        if self.take() != ("brace", "}"):
            raise CompileError(f"{name.upper()}: a table constructor of one column of scalars "
                               f"is compiled, nothing richer")
        self.expect("comma")
        k, v = self.take()
        if k != "col" or _column_ref(v).lower() != "value":
            raise CompileError(f"{name.upper()} over a table constructor is compiled only "
                               f"with [Value], the constructor's own column, as its expression")
        self.expect("rparen")
        if len(items) == 1:
            return f"({items[0]})"
        return _apply(_emit_for({"emit": emit}, self.dialect), [_scalar_list(items)], name)

    def _switch(self) -> str:
        """DAX `SWITCH(expr, value, result, …[, else])` as a SQL CASE, exactly: with `TRUE()` as
        the expression each value is a condition (`CASE WHEN cond THEN …`), otherwise it is
        compared with the expression (`CASE expr WHEN value THEN …`). With no else the
        result is BLANK, which is SQL's NULL. Inside CALCULATE each part is gated as usual."""
        self.expect("lparen")
        at = self.i
        args: list[str] = [self.expr()]
        first = self.t[at:self.i]
        while self.peek()[0] == "comma":
            self.take()
            args.append(self.expr())
        self.expect("rparen")
        if len(args) < 3:
            raise CompileError("SWITCH needs an expression and at least one value/result pair")
        pairs, rest = args[1:], None
        if len(pairs) % 2:
            pairs, rest = pairs[:-1], pairs[-1]
        conditions = [k for k, _ in first] == ["func", "lparen", "rparen"] \
            and first[0][1].upper() == "TRUE" or [k for k, _ in first] == ["func"] \
            and first[0][1].upper() == "TRUE"
        if not conditions and any(pairs[i].strip().upper() == "NULL"
                                  for i in range(0, len(pairs), 2)):
            # `SWITCH(x, BLANK(), r, …)` matches a blank x (seen live); SQL's `CASE x WHEN
            # NULL` never matches, so the match is spelled out. A blank x matches no other
            # value (`SWITCH(BLANK(), 0, …)` is not 0's branch, seen live), as in SQL.
            pairs = [(f"{args[0]} IS NULL" if pairs[i].strip().upper() == "NULL"
                      else f"{args[0]} = {pairs[i]}") if i % 2 == 0 else pairs[i]
                     for i in range(len(pairs))]
            conditions = True
        whens = "\n".join(f"  WHEN {pairs[i]} THEN {pairs[i + 1]}"
                           for i in range(0, len(pairs), 2))
        self.used.append("engine.dax_switch")
        head = "CASE" if conditions else f"CASE {args[0]}"
        return f"({head}\n{whens}" + (f"\n  ELSE {rest}" if rest is not None else "") + "\nEND)"

    def _weekday(self) -> str:
        """Tableau's `DATEPART('weekday', d, 'start')`: 1 on the week's first day. The first
        day is the data source's (the reader adds it from `<date-options start-of-week>`);
        without it the numbering is the locale's, so it is refused rather than assumed."""
        self.expect("lparen")
        self.take()                                               # 'weekday'
        self.expect("comma")
        date = self.expr()
        start = None
        if self.peek()[0] == "comma":
            self.take()
            tok = self.take()
            if tok[0] != "str":
                raise CompileError("DATEPART('weekday') takes its start of week as a literal")
            start = tok[1][1:-1].lower()
        self.expect("rparen")
        if start not in _ISO_DAY:
            raise CompileError("DATEPART('weekday') counts from the data source's start of "
                               "week, which the file does not state; the numbering would be "
                               "guessed")
        # The ISO day of week per dialect is DATA (the DATEPART row's `weekday_iso`), never a
        # dialect named here; the start of week is arithmetic on it.
        row = self.rules.function(self.language, "DATEPART", "") or {}
        iso = ((row.get("emit") or {}).get("weekday_iso") or {}).get(
            (self.dialect or "").lower())
        if not iso:
            raise CompileError(f"DATEPART('weekday') has no form for the "
                               f"{self.dialect or '?'} dialect")
        self.used.append("engine.tableau_weekday")
        return f"({_mod(f'{iso.format(date)} - {_ISO_DAY[start]} + 7', '7', self.dialect)} + 1)"

    def _week_start(self) -> str:
        """Tableau's `DATETRUNC('week', d, 'start')`: the first day of d's week, the week
        starting on the data source's day (the reader adds it from `<date-options
        start-of-week>`; `iso-week` starts on Monday). A SQL week truncation starts on the
        warehouse's own setting, so without the day it is refused rather than assumed."""
        self.expect("lparen")
        part = self.take()[1][1:-1].lower()
        self.expect("comma")
        date = self.expr()
        start = "monday" if part == "iso-week" else None
        if self.peek()[0] == "comma":
            self.take()
            tok = self.take()
            if tok[0] != "str":
                raise CompileError("DATETRUNC('week') takes its start of week as a literal")
            start = tok[1][1:-1].lower()
        self.expect("rparen")
        if start not in _ISO_DAY:
            raise CompileError("DATETRUNC('week') starts the week on the data source's day, "
                               "which the file does not state; the week would be guessed")
        row = self.rules.function(self.language, "DATETRUNC", "") or {}
        back = ((row.get("emit") or {}).get("days_back") or {}).get((self.dialect or "").lower())
        iso = (((self.rules.function(self.language, "DATEPART", "") or {}).get("emit") or {})
               .get("weekday_iso") or {}).get((self.dialect or "").lower())
        if not back or not iso:
            raise CompileError(f"DATETRUNC('week') has no form for the {self.dialect or '?'} "
                               f"dialect")
        self.used.append("engine.tableau_week_start")
        n = _mod(f"{iso.format(date)} - {_ISO_DAY[start]} + 7", "7", self.dialect)
        return back.replace("{d}", date).replace("{n}", n)

    def _previous_value(self) -> str:
        """PREVIOUS_VALUE(init): this calculation's value on the previous row along the
        compute-using (`init` on the first). It is a recursion, compiled in the closed form of
        the shapes it takes: `PREVIOUS_VALUE(i) + x` (or `x + …`, `… - x`) is i plus the
        running sum of x, and `MAX` / `MIN(PREVIOUS_VALUE(i), x)` is the running greatest or
        least with i. A NULL x makes that row and every later one NULL, as the recursion
        does (`NULL + …` is NULL)."""
        toks = list(self.t)
        while len(toks) >= 2 and toks[0][0] == "lparen" and _call_args(toks, 0)[0] == len(toks) - 1:
            toks = toks[1:-1]

        def pv_at(i: int) -> tuple[int, list] | None:
            if i < len(toks) and toks[i][0] == "func" and toks[i][1].upper() == "PREVIOUS_VALUE" \
                    and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
                end, args = _call_args(toks, i + 1)
                if end is not None and len(args) == 1:
                    return end, args[0]
            return None
        shape, init_t, rest = None, None, None
        hit = pv_at(0)
        if hit and hit[0] + 1 < len(toks) and toks[hit[0] + 1] in (("op", "+"), ("op", "-")):
            shape = "sum" if toks[hit[0] + 1][1] == "+" else "minus"
            init_t, rest = hit[1], toks[hit[0] + 2:]
        elif len(toks) > 2 and toks[-1][0] == "rparen" and toks[0][0] == "func" \
                and toks[0][1].upper() in ("MAX", "MIN") and _call_args(toks, 1)[0] == len(toks) - 1:
            args = _call_args(toks, 1)[1]
            for a, b in ((0, 1), (1, 0)) if len(args) == 2 else ():
                inner = args[a]
                if inner and inner[0][0] == "func" and inner[0][1].upper() == "PREVIOUS_VALUE" \
                        and _call_args(inner, 1)[0] == len(inner) - 1:
                    shape = toks[0][1].upper()
                    init_t, rest = _call_args(inner, 1)[1][0], args[b]
                    break
        else:
            # `x + PREVIOUS_VALUE(i)`: the last term.
            for j in range(len(toks) - 1, 0, -1):
                if toks[j - 1] == ("op", "+"):
                    h = pv_at(j)
                    if h and h[0] == len(toks) - 1:
                        shape, init_t, rest = "sum", h[1], toks[:j - 1]
                    break
        tc = self.ctx.table_calc if self.ctx is not None else None
        if tc is None:
            raise CompileError("PREVIOUS_VALUE is a table calculation, and the view's "
                               "compute-using for it is not known here")
        if shape is None or not rest or any(
                k == "func" and v.upper() == "PREVIOUS_VALUE" for k, v in rest):
            return self._previous_value_recursive(toks, tc)
        sub = self._child(init_t, row_filter=None, inlining=self.inlining, share_vars=False)
        init = sub.parse()
        self._absorb(sub)
        sub = self._child(rest, row_filter=None, inlining=self.inlining, share_vars=False)
        x = sub.parse()
        self._absorb(sub)
        if " OVER (" in x:
            # Over another table calculation: that one is a column of the grouped rows.
            return self._previous_value_recursive(toks, tc)
        part, order = list(tc["partition"]), list(tc["order"])
        pby = f"PARTITION BY {', '.join(part)} " if part else ""
        keys = order or part[:1] or ["1"]
        over = (f"{pby}ORDER BY " + ", ".join(self._order_terms(o, tc, "FIRST") for o in keys)
                + " ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW")
        self.used.append("engine.tableau_previous_value")
        nulls = f"COUNT(CASE WHEN ({x}) IS NULL THEN 1 END) OVER ({over}) > 0"
        if shape in ("sum", "minus"):
            sign = "+" if shape == "sum" else "-"
            value = f"({init}) {sign} SUM({x}) OVER ({over})"
        else:
            fn = "MAX" if shape == "MAX" else "MIN"
            pick = ">=" if shape == "MAX" else "<="
            run = f"{fn}({x}) OVER ({over})"
            value = (f"CASE WHEN ({init}) IS NULL THEN NULL WHEN ({init}) {pick} {run} "
                     f"THEN ({init}) ELSE {run} END")
        return f"(CASE WHEN {nulls} THEN NULL ELSE {value} END)"

    # Tableau calc-language words that are not a value of their own (control flow and
    # operators written as words): never an atom of a recursive PREVIOUS_VALUE.
    _CONTROL_WORDS = {"IF", "THEN", "ELSE", "ELSEIF", "END", "CASE", "WHEN", "AND", "OR",
                      "NOT", "IN", "NULL", "TRUE", "FALSE"}

    def _previous_value_recursive(self, toks: list, tc: dict) -> str:
        """PREVIOUS_VALUE in any other shape: the recursion itself, a statement of its own
        over the statement's grouped rows (`lw<n>`, joined back on the grouping, as a table
        calculation over another one is). Every part of the formula that does not reach
        PREVIOUS_VALUE (an aggregate, a scalar function of aggregates, another table
        calculation) is a column of those rows (`a<i>`); the rows are numbered along the
        compute-using within each partition, and a recursive statement computes the formula
        row by row: on the first row each PREVIOUS_VALUE(i) is its i, on every later one the
        value on the row before (Tableau's own definition)."""
        if self.ctx is None:
            raise CompileError("PREVIOUS_VALUE outside a statement")
        rule = self.rules.operator("recursive_rows", self.dialect) or {}
        emit = rule.get("emit") or {}
        dia = str(self.dialect or "").lower()
        opener = (emit.get("dialect_templates") or {}).get(dia) or emit.get("template")
        if not opener or dia not in [str(d).lower() for d in rule.get("dialects") or []]:
            raise CompileError(f"PREVIOUS_VALUE in this shape is a recursion, which has no "
                               f"form for the {self.dialect or '?'} dialect")
        atoms: list[list] = []
        inits: list[list] = []
        step: list = []
        i = 0

        def reaches_pv(span) -> bool:
            return any(k == "func" and v.upper() == "PREVIOUS_VALUE" for k, v in span)
        while i < len(toks):
            k, v = toks[i]
            call = (k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen"
                    and v.upper() not in self._CONTROL_WORDS)
            if call and v.upper() == "PREVIOUS_VALUE":
                end, args = _call_args(toks, i + 1)
                if end is None or len(args) != 1 or reaches_pv(args[0]):
                    raise CompileError("PREVIOUS_VALUE takes one argument, its value on the "
                                       "first row")
                step.append(("num", f"__PV{len(inits)}__"))
                inits.append(args[0])
                i = end + 1
                continue
            if call:
                end, _args = _call_args(toks, i + 1)
                if end is None:
                    raise CompileError(f"{v} without its closing parenthesis")
                span = toks[i:end + 1]
                if not reaches_pv(span):
                    step.append(("num", f"__b.a{len(atoms)}"))
                    atoms.append(span)
                    i = end + 1
                    continue
            if k in ("col", "qualified", "quoted_qualified") or (k == "brace" and v == "{"):
                # A calc named by reference (an aggregate calc), or a level-of-detail
                # expression: its value on the grouped row.
                end = i
                if k == "brace":
                    depth = 0
                    for j in range(i, len(toks)):
                        if toks[j] == ("brace", "{"):
                            depth += 1
                        elif toks[j] == ("brace", "}"):
                            depth -= 1
                            if depth == 0:
                                end = j
                                break
                span = toks[i:end + 1]
                step.append(("num", f"__b.a{len(atoms)}"))
                atoms.append(span)
                i = end + 1
                continue
            step.append(toks[i])
            i += 1
        args = []
        for span in atoms:
            sub = self._child(span, row_filter=None, inlining=self.inlining, share_vars=False)
            args.append(sub.parse())
            self._absorb(sub)
        init_sql = []
        for span in inits:
            sub = self._child(span, row_filter=None, inlining=self.inlining, share_vars=False)
            init_sql.append(sub.parse())
            self._absorb(sub)
        sub = self._child(step, row_filter=None, inlining=self.inlining, share_vars=False)
        body = sub.expr()
        if sub.i != len(sub.t):
            raise CompileError(f"PREVIOUS_VALUE: trailing tokens from {sub.t[sub.i][1]!r}")
        self._absorb(sub)
        text = any(t[0] == "str" for span in inits for t in span)
        cast = (emit.get("text_type") if text else emit.get("number_type")) or {}
        typ = cast.get(dia) if isinstance(cast, dict) else cast
        if not typ:
            raise CompileError(f"PREVIOUS_VALUE: no value type for the {self.dialect} dialect")
        anchor, again = body, body
        for j, init in enumerate(init_sql):
            anchor = anchor.replace(f"__PV{j}__", f"({init})")
            again = again.replace(f"__PV{j}__", "__r.v")
        keys = [sql for _n, _t, sql in self.ctx.groups]

        def at(sql: str) -> int:
            if sql not in keys:
                raise CompileError("PREVIOUS_VALUE runs over a column the statement does not "
                                   "group by")
            return keys.index(sql)
        sorts, lifted_sorts = {}, []
        for col, spec in (tc.get("sorts") or {}).items():
            spec2 = {k: v for k, v in spec.items() if k not in ("by", "within_sql")}
            if spec.get("by"):
                name = f"s{len(lifted_sorts)}"
                lifted_sorts.append((name, self._sort_key(col, spec, tc)))
                spec2["lifted"] = f"__q.{name}"
            sorts[f"__q.k{at(col)}"] = spec2
        part = [at(c) for c in tc.get("partition") or []]
        order = [f"__q.k{at(c)}" for c in tc.get("order") or []] or \
            [f"__q.k{p}" for p in part[:1]] or ["1"]
        mapped = {"sorts": sorts}
        oby = ", ".join(self._order_terms(o, mapped, "FIRST") for o in order)
        n = len(self.ctx.lifts) + 1
        self.ctx.lifts.append({"n": n, "args": args, "sorts": lifted_sorts, "recursive": {
            "with": opener, "level": (emit.get("recursion_clause") or {}).get(dia) or "",
            "hoist": dia in [str(d).lower() for d in emit.get("hoist") or []], "n": n,
            "partition": part, "order": oby,
            "anchor": f"CAST({anchor} AS {typ})", "step": f"CAST({again} AS {typ})"}})
        self.used.append("engine.tableau_previous_value_recursive")
        return f"MIN(lw{n}.v)"

    def _top_set_member(self) -> str:
        """TOPN_SET_MEMBER([field], n, 'desc' | 'asc', aggregate): whether the row's member is
        among a Top N set's members, ranked over the rows before the dimension filters."""
        self._function_rule("TOPN_SET_MEMBER")
        self.expect("lparen")
        end = _call_args(self.t, self.i - 1)
        args = end[1]
        self.i = end[0] + 1
        count = _constant_count(args[1]) if len(args) in (4, 5) else None
        if len(args) not in (4, 5) or len(args[0]) != 1 or args[0][0][0] != "col" \
                or count is None or not args[2] or args[2][0][0] != "str":
            raise CompileError("a Top N set is a field, a whole-number count (a number or a "
                               "parameter; Tableau rejects any other), an order, an expression "
                               "and the members it ranks")
        if self.lods is None:
            raise CompileError("a Top N set outside a statement that can rank its members")
        direction = args[2][0][1][1:-1].upper()

        def compiled(toks, alias: str) -> str:
            sub = _Parser(toks, self.rules, self.language, alias, self.dialect, self.colmap,
                          self.measures, None, self.inlining)
            sub.home, sub.lods = self.home, None
            try:
                return sub.parse()
            finally:
                self._absorb(sub)
        n = len(self.lods) + 1
        rec = {"n": n, "kind": "FIXED", "outer": None, "mode": "members",
               "dims_in": [compiled(args[0], "s")], "dims_out": [compiled(args[0], self.alias)],
               "dim_names": [_column_ref(args[0][0][1]).lower()],
               "value_in": compiled(args[3], "s"), "value_out": None,
               "limit": count, "direction": "DESC" if direction == "DESC" else "ASC",
               # The members it ranks (a row-level test), when not every member.
               "among": compiled(args[4], "s") if len(args) == 5 else None}
        if not _AGGREGATE_CALL.search(rec["value_in"]):
            raise CompileError("a Top N set ranks by an aggregate")
        self.lods.append(rec)
        self.used.append("engine.tableau_top_set")
        # The Null member is a member: it is In when it ranks among the first n.
        out = rec["dims_out"][0]
        return (f"({out} IN __LODSET{n}__ OR ({out} IS NULL AND "
                f"(SELECT COUNT(*) FROM __LODSET{n}__ z WHERE z.k IS NULL) > 0))")

    def _value_kind(self, toks, depth: int = 0) -> str | None:
        """'date', 'datetime' or None: what a Tableau argument holds, read from its tokens (a
        date column, a calc whose formula is one, a function that returns one)."""
        toks = list(toks)
        while len(toks) >= 2 and toks[0][0] == "lparen" and _call_args(toks, 0)[0] == len(toks) - 1:
            toks = toks[1:-1]
        if not toks or depth > 8:
            return None
        k, v = toks[0]
        if k == "datelit":
            return "datetime" if (" " in v or "T" in v) else "date"
        if k == "func" and len(toks) > 1 and toks[1][0] == "lparen" \
                and _call_args(toks, 1)[0] == len(toks) - 1:
            up = v.upper()
            if up in ("DATETRUNC", "NOW", "DATEPARSE", "MAKEDATETIME", "DATETIME"):
                return "datetime"
            if up in ("TODAY", "MAKEDATE", "DATE"):
                return "date"
            if up in ("DATEADD", "MIN", "MAX", "ATTR", "IFNULL", "ZN"):
                first = _split_top(toks[2:-1])
                return self._value_kind(first[-1] if up == "DATEADD" else first[0], depth + 1) \
                    if first else None
            return None
        if k == "col" and len(toks) == 1:
            key = _column_ref(v).lower()
            if key in self.measures and key not in self.inlining:
                try:
                    return self._value_kind(_tokens(self.measures[key], self.language), depth + 1)
                except CompileError:
                    return None
            types = self.ctx.types if self.ctx is not None else {}
            for (_tbl, col), t in types.items():
                if col == key:
                    t = str(t or "").lower()
                    if t.startswith(("datetime", "timestamp")):
                        return "datetime"
                    if t.startswith("date"):
                        return "date"
        return None

    def _str(self) -> str:
        """Tableau's STR: the warehouse's own text of a number or a text; a date as
        'YYYY-MM-DD' and a date and time as 'YYYY-MM-DD HH:MM:SS' (checked live)."""
        rule = self._function_rule("STR")
        emit = rule.get("emit") or {}
        args, raw = self._args()
        if len(args) != 1:
            raise CompileError("STR takes one argument")
        kind = self._value_kind(raw[0])
        if kind:
            form = (emit.get(f"{kind}_template") or {}).get(str(self.dialect or "").lower())
            if not form:
                raise CompileError(f"STR of a {kind} has no form for the "
                                   f"{self.dialect or '?'} dialect")
            self.used.append("engine.tableau_str")
            return form.replace("{0}", args[0])
        return _apply(_emit_for(rule, self.dialect), args, "STR")

    def _week_number(self, start: str | None, date: str) -> str:
        """Tableau's week of the year: week 1 holds 1 January, and a week starts on the data
        source's day (checked live, 1,070 dates: FLOOR((day of year - 1 + the days 1 January
        is past the week's start) / 7) + 1)."""
        if start not in _ISO_DAY:
            raise CompileError("a week number counts from the data source's start of week, "
                               "which the file does not state")
        row = (self.rules.function(self.language, "WEEK", self.dialect) or {}).get("emit") or {}
        d = (self.dialect or "").lower()
        doy, jan1 = (row.get("day_of_year") or {}).get(d), (row.get("year_start") or {}).get(d)
        iso = (((self.rules.function(self.language, "DATEPART", "") or {}).get("emit") or {})
               .get("weekday_iso") or {}).get(d)
        if not doy or not jan1 or not iso:
            raise CompileError(f"a week number has no form for the {self.dialect or '?'} dialect")
        self.used.append("engine.tableau_week")
        off = _mod(f"{iso.format(jan1.format(date))} - {_ISO_DAY[start]} + 7", "7", self.dialect)
        return f"(CAST(FLOOR(({doy.format(date)} - 1 + {off}) / 7) AS INTEGER) + 1)"

    def _part_call(self) -> tuple[str, str, str | None]:
        """`F('part', date[, 'start'])`, the `(` at the cursor: (part, date SQL, start)."""
        self.expect("lparen")
        part = self.take()[1][1:-1].lower()
        self.expect("comma")
        date = self.expr()
        start = None
        if self.peek()[0] == "comma":
            self.take()
            tok = self.take()
            if tok[0] != "str":
                raise CompileError("a start of week is a literal")
            start = tok[1][1:-1].lower()
        self.expect("rparen")
        return part, date, start

    def _datename(self) -> str:
        """DATENAME(part, date): the part as Tableau shows it (rule data per dialect); a week
        is its number as text."""
        rule = self._function_rule("DATENAME")
        part, date, start = self._part_call()
        if part in ("week", "iso-week"):
            n = self._week_number("monday" if part == "iso-week" else start, date)
            # The number as text, as the dialect writes a cast to text (`as_text`).
            as_text = ((rule.get("emit") or {}).get("as_text") or {}).get(
                str(self.dialect or "").lower()) or "CAST({0} AS VARCHAR)"
            return as_text.replace("{0}", n)
        forms = ((rule.get("emit") or {}).get("parts") or {}).get(str(self.dialect or "").lower()) or {}
        if part not in forms:
            raise CompileError(f"DATENAME('{part}') has no form for the {self.dialect or '?'} "
                               f"dialect")
        self.used.append("engine.tableau_datename")
        return forms[part].replace("{d}", date)

    def _dateparse(self) -> str:
        """DATEPARSE(format, text): Tableau's ICU pattern rewritten into the dialect's, token
        by token (rule data); a pattern with a token the row does not list is refused."""
        rule = self._function_rule("DATEPARSE")
        emit = rule.get("emit") or {}
        args, raw = self._args()
        if len(args) != 2 or len(raw[0]) != 1 or raw[0][0][0] != "str":
            raise CompileError("DATEPARSE takes a literal format and a text")
        pattern = raw[0][0][1][1:-1]
        whole = (emit.get("whole_formats") or {}).get(str(self.dialect or "").lower())
        if whole is not None:
            # A dialect that parses by a fixed list of styles, not a pattern: the whole
            # pattern names its style, and a pattern with none is refused by name.
            if pattern not in whole:
                raise CompileError(f"DATEPARSE format {pattern!r} is none of the "
                                   f"{self.dialect} styles ({', '.join(sorted(whole))})")
            self.used.append("engine.tableau_dateparse")
            template = (emit.get("dialect_templates") or {}).get(
                str(self.dialect).lower(), emit["template"])
            return template.replace("{0}", str(whole[pattern])).replace("{1}", args[1])
        table = (emit.get("formats") or {}).get(str(self.dialect or "").lower())
        if not table:
            raise CompileError(f"DATEPARSE has no form for the {self.dialect or '?'} dialect")
        out, i = "", 0
        keys = sorted(table, key=len, reverse=True)
        while i < len(pattern):
            ch = pattern[i]
            if ch.isalpha():
                hit = next((k for k in keys if pattern.startswith(k, i)), None)
                if hit is None:
                    run = re.match(r"([A-Za-z])\1*", pattern[i:]).group(0)
                    raise CompileError(f"DATEPARSE format token {run!r} has no "
                                       f"{self.dialect} form")
                out += str(table[hit])
                i += len(hit)
            elif ch == "'":
                raise CompileError("DATEPARSE with quoted text in its format is not compiled")
            else:
                out += ch
                i += 1
        self.used.append("engine.tableau_dateparse")
        by_dialect = emit.get("dialect_templates") or {}
        template = by_dialect.get(str(self.dialect or "").lower(), emit["template"])
        return template.replace("{0}", "'" + out.replace("'", "''") + "'").replace("{1}", args[1])

    def _datediff_week(self) -> str:
        """DATEDIFF('week', a, b[, start]): the week starts between them, each week starting
        on the data source's day."""
        self._function_rule("DATEDIFF")
        self.expect("lparen")
        self.take()
        self.expect("comma")
        a = self.expr()
        self.expect("comma")
        b = self.expr()
        start = None
        if self.peek()[0] == "comma":
            self.take()
            tok = self.take()
            if tok[0] != "str":
                raise CompileError("a start of week is a literal")
            start = tok[1][1:-1].lower()
        self.expect("rparen")
        if start not in _ISO_DAY:
            raise CompileError("DATEDIFF('week') counts weeks from the data source's start of "
                               "week, which the file does not state")
        row = self.rules.function(self.language, "DATETRUNC", "") or {}
        back = ((row.get("emit") or {}).get("days_back") or {}).get((self.dialect or "").lower())
        iso = (((self.rules.function(self.language, "DATEPART", "") or {}).get("emit") or {})
               .get("weekday_iso") or {}).get((self.dialect or "").lower())
        demit = (self.rules.function(self.language, "DATEDIFF", self.dialect) or {}
                 ).get("emit") or {}
        day = [[("str", "'day'")]]
        diff = (_part_template(demit, self.dialect, day)
                or (demit.get("dialect_templates") or {}).get((self.dialect or "").lower())
                or demit.get("template"))
        if not back or not iso or not diff:
            raise CompileError(f"DATEDIFF('week') has no form for the {self.dialect or '?'} "
                               f"dialect")

        def ws(x: str) -> str:
            return back.replace("{d}", x).replace(
                "{n}", _mod(f"{iso.format(x)} - {_ISO_DAY[start]} + 7", "7", self.dialect))
        self.used.append("engine.tableau_week_start")
        # The part is 'day', which a form per part does not place.
        return "(" + _apply({"template": diff, "extra_args_ok": True},
                            ["'day'", ws(a), ws(b)], "DATEDIFF") + " / 7)"

    def call(self, name: str) -> str:
        if self.peek()[0] != "lparen":
            # A bare word that is not a call: a Tableau field written without brackets.
            return f"{self.alias}.{name}"
        up = name.upper()
        if self._dax and up == "CALCULATE":
            out = self._calculate()
            if out is not None:
                return out
        if self._dax and up == "SWITCH":
            return self._switch()
        if self._dax and up == "COUNTROWS":
            return self._countrows()
        if self._dax and up == "SELECTEDVALUE":
            out = self._selected_dimension()
            if out is not None:
                return out
        if self._dax and up in _PERIOD_TOTALS:
            return self._period_total(up)
        if self._tableau and up in _LOD_OUTER and self.peek()[0] == "lparen":
            lod = self._lod_argument()
            if lod is not None:
                # `SUM({FIXED [d] : …})`, or `SUM([calc])` of a calc that is one: the
                # aggregate over a level of detail, read at its grain.
                end = _call_args(self.t, self.i)[0]
                self.t = self.t[:self.i + 1] + lod + self.t[end:]
                self.take()
                self.take()
                out = self._lod(up)
                self.expect("rparen")
                return out
        if (self._tableau and up == "DATEPART" and self.i + 1 < len(self.t)
                and self.t[self.i + 1][0] == "str"
                and self.t[self.i + 1][1][1:-1].lower() == "weekday"):
            return self._weekday()
        nxt = (self.t[self.i + 1][1][1:-1].lower() if self.i + 1 < len(self.t)
               and self.t[self.i + 1][0] == "str" else None)
        if self._tableau and up == "DATEPART" and nxt in ("week", "iso-week"):
            part, date, start = self._part_call()
            self._function_rule("DATEPART")
            return self._week_number("monday" if part == "iso-week" else start, date)
        if self._tableau and up == "DATEDIFF" and nxt == "week":
            return self._datediff_week()
        if self._tableau and up == "DATENAME":
            return self._datename()
        if self._tableau and up == "STR":
            return self._str()
        if self._tableau and up == "TOPN_SET_MEMBER":
            return self._top_set_member()
        if self._tableau and up == "DATEPARSE":
            return self._dateparse()
        if (self._tableau and up == "DATETRUNC" and self.i + 1 < len(self.t)
                and self.t[self.i + 1][0] == "str"
                and self.t[self.i + 1][1][1:-1].lower() in ("week", "iso-week")):
            return self._week_start()
        # THE RULE IS RESOLVED BEFORE THE ARGUMENTS. An untranslatable construct's arguments
        # are irrelevant — and worse, parsing them first turns a clean "CALCULATE has no SQL
        # form" into whatever syntax error its arguments happen to raise, which reports a
        # compiler bug where there is a declared limit.
        rule = self._function_rule(name)
        emit = rule.get("emit") or {}
        if emit.get("untranslatable"):
            self.untranslatable.append({"construct": up, "why": emit["untranslatable"]})
            raise CompileError(f"{up}: {emit['untranslatable']}")
        if emit.get("table_calc"):
            return self._table_calc(up, emit)
        if emit.get("visual_calc"):
            # A function of the visual's matrix (readers/visualcalc.py), which a model measure
            # has no matrix to read: DAX itself rejects it outside a visual calculation.
            why = "a visual calculation function, valid only in a visual's own calculation"
            self.untranslatable.append({"construct": up, "why": why})
            raise CompileError(f"{up} is {why}")
        if emit.get("context_test") and self._dax:
            return self._context_test(up, emit["context_test"])
        if self.i + 1 < len(self.t) and self.t[self.i + 1] != ("brace", "{") and (
                emit.get("row_iterator") or emit.get("row_template")):
            return self._row_iterator(up, emit)
        gated = up in _DAX_AGGREGATES and self._dax
        if (self.row_filter and not gated
                and _AGGREGATE_CALL.search(emit.get("template") or "")):
            # The CALCULATE filter gates an aggregate by wrapping its argument; a rule whose
            # own template aggregates (COUNT(*) included) has no argument that covers it.
            raise CompileError(f"{up} aggregates inside its own rule, where the "
                               f"CALCULATE filter cannot gate it")
        if emit.get("scalar_list"):
            return self._scalar_iterator(name, emit)
        if gated and self.row_level:
            raise CompileError(f"{up} inside a row iterator aggregates in each row's filter "
                               f"context (a context transition), which one aggregate over "
                               f"the rows does not express")
        if (self.window is not None or self.slots is not None) and not gated \
                and _AGGREGATE_CALL.search(emit.get("template") or ""):
            raise CompileError(f"{up} aggregates inside its own rule, which is not summed "
                               f"across the visual's rows")

        self.expect("lparen")
        args: list[str] = []
        raw: list[list[tuple[str, str]]] = []
        related = bool(emit.get("reads_related"))
        if related and not self.row_level:
            raise CompileError(f"{up} reads the related row of a row iterator's current row; "
                               f"outside one there is no current row")
        was = self.foreign_ok
        self.foreign_ok = was or related
        catching = emit.get("error_free") is not None
        outer_conds = self.error_conds
        conds: list[str] = []
        # A branch the function evaluates only under a condition on its arguments (IF's then
        # and else): inside IFERROR / ISERROR, an error raised there is one only where the
        # branch is taken (seen live: IFERROR(IF(1 < 0, ERROR("e"), 5), -1) is 5).
        guards = emit.get("branch_guards") or {} if outer_conds is not None else {}
        try:
            if self.peek()[0] != "rparen":
                while True:
                    at = self.i
                    if catching and len(args) == int(emit["error_free"]):
                        self.error_conds = conds
                    guard = guards.get(str(len(args)))
                    branch: list[str] = []
                    words = (emit.get("keyword_values") or {}).get(str(len(args)))
                    if words is not None:
                        # A bare keyword argument (DAX's DATEDIFF interval `DAY`): one of the
                        # words the row names, written as the word; anything else refused.
                        k, v = self.take()
                        if k != "func" or str(v).lower() not in {str(w).lower() for w in words} \
                                or self.peek()[0] == "lparen":
                            raise CompileError(
                                f"{up} argument {len(args) + 1} is {v!r}; it compiles only as "
                                f"one of {', '.join(map(str, words))}")
                        args.append(str(v).upper())
                        raw.append([(k, v)])
                        if self.peek()[0] == "comma":
                            self.take()
                            continue
                        break
                    if guard and args:
                        self.error_conds = branch
                    args.append(self.expr())
                    self.error_conds = outer_conds
                    if guard and args[:-1]:
                        test = _apply({"template": guard, "extra_args_ok": True}, args[:1],
                                      name)
                        outer_conds.extend(f"(({test}) AND {c})" for c in branch)
                    raw.append(self.t[at:self.i])
                    if self.peek()[0] == "comma":
                        self.take()
                        continue
                    break
            self.expect("rparen")
        finally:
            self.foreign_ok = was
        if emit.get("variadic"):
            args = [", ".join(args)]
        _check_literal_args(emit, name, self.language, raw)
        _check_literal_values(emit, name, self.language, raw)
        if str(self.dialect or "").lower() in (emit.get("literal_backslash_escapes") or []):
            # A dialect that reads `\` in a string literal as an escape: a pattern's
            # backslash (`\w`) is written doubled so the function receives it.
            args = [a.replace("\\", "\\\\") if len(r) == 1 and r[0][0] == "str" else a
                    for a, r in zip(args, raw)] + args[len(raw):]
        if emit.get("only_caught") and self.error_conds is None:
            # ERROR(): outside IFERROR / ISERROR the visual itself shows the error wherever
            # the call is reached; no value stands for that.
            raise CompileError(f"{up} outside IFERROR / ISERROR: the visual shows an error "
                               f"where it is reached, which no value stands for")
        if emit.get("raises_when") and self.error_conds is not None:
            # Read inside IFERROR / ISERROR: the condition under which this call raises an
            # evaluation error in the formula language (SQRT of a negative, LN of zero), as
            # the row states it over the call's own arguments.
            self.error_conds.append("(" + _apply({"template": emit["raises_when"],
                                                  "defaults": emit.get("defaults") or {},
                                                  "extra_args_ok": True}, args, name) + ")")
        if emit.get("error_free") is not None:
            # IFERROR / ISERROR catch an EVALUATION ERROR. What this compiler emits raises
            # none where the formula language raises none (division is IEEE: x / 0 is
            # infinity, not an error; BLANK is no error), so the value is the argument's own.
            # A function the row names as raising (a text conversion) is refused by name.
            k = int(emit["error_free"])
            text = " ".join(v for _t, v in raw[k]) if k < len(raw) else ""
            called = {v.upper() for t, v in _expanded_tokens(text, self.measures)
                      if t == "func"}
            hit = sorted(called & {str(x).upper() for x in emit.get("raises") or []})
            if hit:
                raise CompileError(f"{up} around {', '.join(hit)}: the formula language "
                                   f"raises an error there that the warehouse may not")
            raised = " OR ".join(dict.fromkeys(conds)) or "FALSE"
            self.used.append("engine.dax_error_conditions")
            emit = {**emit, "template": emit["template"].replace("{raised}", raised)}
        if gated:
            return self._aggregate(up, _emit_for({"emit": emit}, self.dialect), args, raw)
        if emit.get("viewer_bind"):
            # The viewing user's name or full name: the statement reads a bind placeholder,
            # never the value (guardrail 8), so it is the statement for whoever runs it.
            ph = self.rules.viewer_placeholder(emit["viewer_bind"], str(self.dialect or ""))
            if not ph:
                raise CompileError(f"{up}: no bind placeholder for the viewing user in the "
                                   f"{self.dialect} dialect (an op.viewer_bind rule row)")
            # A name it compares with that was a person's name or address in the workbook is
            # a numbered marker (A1's scrub, guardrail 8): A3 reads it as a bind placeholder of
            # its own (lib/withheld), never the value.
            self.used.append("op.viewer_bind")
            emit = {**emit, "template": emit["template"].replace("{viewer}", ph)}
        for k in emit.get("date_args") or ():
            # A text where the row reads a date: DAX converts it by the model's culture.
            if self._dax and int(k) < len(args) and self._literal_kind(args[int(k)]) == "str":
                raise CompileError(
                    f"{up} of the text {args[int(k)].strip()}: DAX reads text as a date by the "
                    f"model's culture (\"3/5/2024\" is 5 March in en-US, 3 May in en-GB), which "
                    f"the warehouse does not reproduce")
        if self._dax and up == "DATE" and args:
            # A two-digit year is windowed as DAX's DATE windows it (`dax_year`).
            args = [dax_year_sql(args[0]), *args[1:]]
        by_dialect = emit.get("dialect_templates") or {}
        if by_dialect and str(self.dialect or "").lower() in by_dialect:
            # The same function spelled per dialect (date arithmetic is not portable); a
            # dialect the row does not list is refused by the row's `dialects`.
            emit = {**emit, "template": by_dialect[str(self.dialect).lower()]}
        part_form = _part_template(emit, self.dialect, raw)
        if part_form:
            # The part is read to choose the form, so a form need not place it; every other
            # argument it must place.
            placed = {int(i) for i in re.findall(r"\{(\d+)(?:!word)?\}", part_form)}
            rest = set(range(len(args))) - {int(emit["part_arg"])}
            if not rest <= placed:
                raise CompileError(f"the {self.dialect} form of {up} for this part places "
                                   f"{len(placed)} of its {len(args)} arguments")
            emit = {**emit, "template": part_form, "extra_args_ok": True}
        return _apply(emit, args, name)

    # -- levels of detail -----------------------------------------------------------------
    def _lod(self, outer: str | None) -> str:
        """`{FIXED [d], … : agg}`, `{INCLUDE …}`, `{EXCLUDE …}` or `{agg}` (FIXED on no
        dimension), the `{` just taken. `outer` is the aggregate around it (`SUM({…})`), or
        None where it is read per row. Registered on the statement (`self.lods`) and replaced
        by a placeholder the statement resolves (`_resolve_lods`):

        * FIXED is computed BEFORE the view's dimension filters (its own statement over the
          rows the data source and context filters keep) at its dimensions. Per row (a
          grouping column, a row calc) it is joined to each row on those dimensions; under an
          aggregate the view's rows are taken at its dimensions and the view's together, and
          aggregated from there (one value per combination, never one per row).
        * INCLUDE is computed after them, at the view's dimensions and its own, and aggregated
          to the view's.
        * EXCLUDE is computed after them at the view's dimensions without its own: a window
          over the view's rows partitioned by the dimensions it keeps.
        """
        kind = "FIXED"
        k, v = self.peek()
        if k == "func" and str(v).upper() in ("FIXED", "INCLUDE", "EXCLUDE"):
            kind = self.take()[1].upper()
            scoped = True
        else:
            scoped = False
        close = _brace_end(self.t, self.i - (2 if scoped else 1))
        if close is None:
            raise CompileError("a level-of-detail expression without its closing brace")
        body = self.t[self.i:close]
        self.i = close + 1
        dims: list[list[tuple[str, str]]] = []
        if scoped:
            colon = _top_colon(body)
            if colon is None:
                raise _lod_refusal(kind, "no ':' after its dimensions")
            dims = [d for d in _split_top(body[:colon]) if d]
            body = body[colon + 1:]
        if getattr(self, "nested_in", None) is not None:
            return self._lod_inner(kind, dims, body, outer)
        if self.lods is None or (self.ctx is None and outer is not None):
            raise _lod_refusal(kind)
        lod_dims: dict[int, dict] = {}
        for i, d in enumerate(dims):
            inner_d = self._lod_tokens(d)
            if inner_d is None:
                continue
            # A level of detail among its own dimensions (a cohort: `{FIXED {FIXED [c] :
            # MIN([d])} : …}`, or a calc that is one): a FIXED value per row, so inside this
            # one's statement a window over the rows it reads, partitioned by its dimensions,
            # and on the view's rows the row-level value it joins on.
            lod_dims[i] = self._lod_dimension(inner_d, kind)
        if self.row_filter or self.row_level or self.window is not None \
                or self.slots is not None:
            raise _lod_refusal(kind, "inside a filtered or row-by-row expression")
        if kind == "FIXED" and not dims and outer not in (None, "SUM", "AVG", "MIN", "MAX"):
            raise _lod_refusal(kind, f"{outer} of a value over the whole table")
        nested = any(tok == ("brace", "{") for tok in body)
        inner_recs: list[dict] = []

        def compiled(toks, alias: str, inner: bool = False) -> str:
            sub = _Parser(toks, self.rules, self.language, alias, self.dialect, self.colmap,
                          self.measures, None, self.inlining)
            sub.home, sub.lods = self.home, (inner_recs if inner else None)
            if inner:
                # A level of detail inside this one: computed at its own dimensions (an
                # INCLUDE or EXCLUDE relative to this one's) and read here per combination
                # of this one's dimensions and its own.
                sub.nested_in = {"kind": kind, "dims": [compiled(d, "s") for d in dims],
                                 "names": [_column_ref(d[0][1]).lower() if len(d) == 1
                                           and d[0][0] == "col" else None for d in dims]}
            try:
                return sub.parse()
            finally:
                self._absorb(sub)
        if nested and kind != "FIXED":
            # A level of detail inside an INCLUDE or EXCLUDE: that one is computed after the
            # view's dimension filters at the view's dimensions with its own added (INCLUDE)
            # or taken away (EXCLUDE), so it is the FIXED level of detail at those dimensions
            # over the rows those filters keep, the inner ones nested in it as in a FIXED.
            return self._lod_relative_nested(kind, dims, body, outer)
        value_in = compiled(body, "s", inner=nested)
        if not _AGGREGATE_CALL.search(value_in):
            raise _lod_refusal(kind, "its expression aggregates nothing")
        if " OVER (" in value_in:
            raise _lod_refusal(kind, "a table calculation inside it")
        n = len(self.lods) + 1
        rec = {"n": n, "kind": kind, "outer": outer,
               "dims_in": [lod_dims[i]["in"] if i in lod_dims else compiled(d, "s")
                           for i, d in enumerate(dims)],
               "dims_out": [lod_dims[i]["out"] if i in lod_dims else compiled(d, self.alias)
                            for i, d in enumerate(dims)],
               **({"pre": [(x["col"], x["window"]) for x in lod_dims.values()]}
                  if lod_dims else {}),
               "dim_names": [_column_ref(d[0][1]).lower() if len(d) == 1 and d[0][0] == "col"
                             else None for d in dims],
               "value_in": value_in,
               "value_out": None if nested else compiled(body, self.alias)}
        self.used.append("engine.tableau_lod")
        if nested:
            self._nest(rec, inner_recs)
        if kind != "FIXED" and outer is None:
            # INCLUDE / EXCLUDE read per row: computed after the view's dimension filters at
            # the view's dimensions with its own added (INCLUDE) or taken away (EXCLUDE), and
            # joined to each row on them.
            return self._lod_per_row(rec)
        if kind == "EXCLUDE":
            return self._lod_exclude(rec)
        rec["mode"] = ("scalar" if not dims else "row" if outer is None else "grain")
        self.lods.append(rec)
        return _LOD_MARK[rec["mode"]].format(n=n)

    def _lod_tokens(self, toks: list) -> list | None:
        """The tokens of the level-of-detail expression `toks` is (a brace group, in redundant
        parentheses or not, or a calc whose formula is one, followed through calcs); None for
        anything else."""
        arg, seen = list(toks), set(self.inlining)
        for _hop in range(16):
            while (len(arg) >= 2 and arg[0][0] == "lparen"
                   and _call_args(arg, 0)[0] == len(arg) - 1):
                arg = arg[1:-1]
            if arg and arg[0] == ("brace", "{") and _brace_end(arg, 0) == len(arg) - 1:
                return arg
            if len(arg) != 1 or arg[0][0] != "col":
                return None
            key = _column_ref(arg[0][1]).lower()
            if key not in self.measures or key in seen:
                return None
            seen.add(key)
            try:
                arg = _tokens(self.measures[key], self.language)
            except CompileError:
                return None
        return None

    def _lod_dimension(self, toks: list, outer_kind: str) -> dict:
        """A FIXED level of detail used as a dimension of another one: {in: its column on the
        rows the outer one reads, col, window: that column's SQL (a window over those rows by
        its dimensions), out: its row-level value on the view's rows}."""
        body = toks[1:-1]
        k, v = body[0] if body else (None, None)
        if not (k == "func" and str(v).upper() == "FIXED"):
            # Tableau rejects the formula itself (checked live, VizQL Data Service): "You
            # cannot use an INCLUDE or EXCLUDE expression as a dimension, because it is a
            # measure." The workbook cannot render it.
            raise CompileError(f"an {str(v).upper()} level of detail used as a dimension of "
                               f"another: the calc language rejects it (an INCLUDE or EXCLUDE "
                               f"is a measure), so the view renders nothing")
        colon = _top_colon(body)
        if colon is None:
            raise _lod_refusal("FIXED", "no ':' after its dimensions")
        dims = [d for d in _split_top(body[1:colon]) if d]
        expr = body[colon + 1:]
        if any(tok == ("brace", "{") for tok in expr) or any(
                tok == ("brace", "{") for d in dims for tok in d):
            raise _lod_refusal(outer_kind, "a dimension that is a level of detail over another")

        def compiled(t, alias: str) -> str:
            sub = _Parser(t, self.rules, self.language, alias, self.dialect, self.colmap,
                          self.measures, None, self.inlining)
            sub.home, sub.lods = self.home, None
            try:
                return sub.parse()
            finally:
                self._absorb(sub)
        value = compiled(expr, "s")
        if not _AGGREGATE_CALL.search(value):
            raise _lod_refusal(outer_kind, "a dimension that is a level of detail over "
                                           "nothing aggregated")
        part = [compiled(d, "s") for d in dims]
        over = f" OVER (PARTITION BY {', '.join(part)})" if part else " OVER ()"
        # Any expression of aggregates (a rounded ratio, a distinct count): each aggregate a
        # window over the rows by the dimensions, the expression over those (checked live: a
        # FIXED [customer] : ROUND(SUM(amount) / 1000) band as a FIXED's dimension).
        window = _map_aggregates(value, lambda call: call + over)
        sub = self._child(toks, row_filter=None, inlining=self.inlining, share_vars=False)
        out = sub.parse()
        self._absorb(sub)
        self._lod_dims = getattr(self, "_lod_dims", 0) + 1
        col = f"__lodd{self._lod_dims}"
        self.used.append("engine.tableau_lod_dimension")
        return {"in": f"s.{col}", "col": col, "window": window, "out": out}

    def _lod_relative_nested(self, kind: str, dims, body, outer: str | None) -> str:
        """An INCLUDE / EXCLUDE whose expression holds another level of detail: the FIXED
        level of detail at its effective dimensions (the view's, with its own added or taken
        away), after the view's dimension filters (`after_filters`)."""
        if self.ctx is None:
            raise _lod_refusal(kind)
        view = [(nm, sql) for nm, _t, sql in self.ctx.groups]
        if any(re.search(r"\b(\w+)\.", sql) and not re.fullmatch(
                rf"(?s)[^.]*\b{re.escape(self.alias)}\.\w+[^.]*", sql) for _nm, sql in view):
            raise _lod_refusal(kind, "nested, on a view grouped by another table's column")

        def compiled(toks, alias: str) -> str:
            sub = _Parser(toks, self.rules, self.language, alias, self.dialect, self.colmap,
                          self.measures, None, self.inlining)
            sub.home, sub.lods = self.home, None
            try:
                return sub.parse()
            finally:
                self._absorb(sub)
        own_out = [compiled(d, self.alias) for d in dims]
        own_names = [_column_ref(d[0][1]).lower() if len(d) == 1 and d[0][0] == "col"
                     else None for d in dims]
        if kind == "INCLUDE":
            pairs = list(dict.fromkeys(view + list(zip(own_names, own_out))))
        else:
            if None in own_names:
                raise _lod_refusal(kind, "a dimension that is not a field")
            pairs = [(nm, sql) for nm, sql in view if nm not in set(own_names)]
        seen_sql: set[str] = set()
        pairs = [(nm, sql) for nm, sql in pairs if not (sql in seen_sql or seen_sql.add(sql))]
        dims_out = [sql for _nm, sql in pairs]
        dims_in = [re.sub(rf"\b{re.escape(self.alias)}\.", "s.", d) for d in dims_out]
        names = [nm for nm, _sql in pairs]
        inner_recs: list[dict] = []
        sub = _Parser(body, self.rules, self.language, "s", self.dialect, self.colmap,
                      self.measures, None, self.inlining)
        sub.home, sub.lods = self.home, inner_recs
        sub.nested_in = {"kind": "FIXED", "dims": dims_in, "names": names}
        try:
            value_in = sub.parse()
        finally:
            self._absorb(sub)
        if not _AGGREGATE_CALL.search(value_in):
            raise _lod_refusal(kind, "its expression aggregates nothing")
        n = len(self.lods) + 1
        rec = {"n": n, "kind": "FIXED", "outer": outer, "dims_in": dims_in,
               "dims_out": dims_out, "dim_names": names, "value_in": value_in,
               "value_out": None, "after_filters": True}
        self.used.append("engine.tableau_lod")
        self._nest(rec, inner_recs)
        rec["mode"] = ("scalar" if not dims_out else "row" if outer is None else "grain")
        self.lods.append(rec)
        return _LOD_MARK[rec["mode"]].format(n=n)

    def _lod_inner(self, kind: str, dims, body, outer: str | None) -> str:
        """A level of detail inside another one's expression (`self.nested_in`): registered
        with its effective dimensions and read as a column of the outer one's rows, each a
        combination of the outer dimensions and its own."""
        here = self.nested_in

        def compiled(toks) -> str:
            sub = _Parser(toks, self.rules, self.language, "s", self.dialect, self.colmap,
                          self.measures, None, self.inlining)
            sub.home, sub.lods = self.home, None
            try:
                return sub.parse()
            finally:
                self._absorb(sub)
        own = [compiled(d) for d in dims]
        names = [_column_ref(d[0][1]).lower() if len(d) == 1 and d[0][0] == "col" else None
                 for d in dims]
        if kind == "FIXED":
            eff = own
        elif kind == "INCLUDE":
            eff = list(dict.fromkeys(here["dims"] + own))
        else:
            if None in names:
                raise _lod_refusal(kind, "a dimension that is not a field")
            eff = [d for d, nm in zip(here["dims"], here["names"]) if nm not in names]
        deeper: list[dict] = []
        if any(tok == ("brace", "{") for tok in body):
            # Levels of detail inside this one (three or more deep): each at its effective
            # dimensions relative to this one's, read as a column of this one's own rows (the
            # combinations of its dimensions and theirs), as this one is read by its outer.
            sub = _Parser(body, self.rules, self.language, "s", self.dialect, self.colmap,
                          self.measures, None, self.inlining)
            sub.home, sub.lods = self.home, deeper
            sub.nested_in = {"kind": kind, "dims": eff,
                             "names": [next((nm for d2, nm in zip(here["dims"] + own,
                                                                  here["names"] + names)
                                             if d2 == d), None) for d in eff]}
            try:
                value = sub.parse()
            finally:
                self._absorb(sub)
        else:
            value = compiled(body)
        if not _AGGREGATE_CALL.search(value):
            raise _lod_refusal(kind, "its expression aggregates nothing")
        n = len(self.lods) + 1
        rec = {"n": n, "dims": eff, "value": value, "kind": kind}
        if deeper:
            rec["from"], rec["dims_from"], rec["value"] = self._combos(
                eff, deeper, "{src_all}" if kind == "FIXED" else "{src}", kind, value)
        self.lods.append(rec)
        self.used.append("engine.tableau_lod_nested")
        col = f"s.__lodin{n}"
        if outer is None:
            return col
        return _apply(_emit_for(self._function_rule(outer), self.dialect), [col], outer)

    def _nest(self, rec: dict, inner: list[dict]) -> None:
        """An outer FIXED over inner levels of detail: its rows are the combinations of its
        dimensions and each inner one's (`from`, a statement over the source rows `{src}`),
        each carrying the inner values; its own dimensions are columns of those rows."""
        rec["from"], rec["dims_in"], rec["value_in"] = self._combos(
            rec["dims_in"], inner, "{src}", rec["kind"], rec["value_in"])

    def _combos(self, dims: list[str], inner: list[dict], rows: str, kind: str,
                value: str) -> tuple[str, list[str], str]:
        """The rows a level of detail with inner ones is computed over: the combinations of
        its dimensions and each inner one's, read from `rows` (`{src}` the rows after the
        view's dimension filters where this one reads them so, `{src_all}` before), each
        carrying the inner values (`__lodin<n>`; an inner one with inner ones of its own reads
        its own such rows, `from`). Returns (that statement, its dimensions as its columns)."""
        exprs = list(dict.fromkeys(dims + [d for r in inner for d in r["dims"]]))
        name = {e: f"e{i}" for i, e in enumerate(exprs)}
        nse_row = self.rules.operator("null_safe_eq", self.dialect) or {}
        emit = nse_row.get("emit") or {}
        nse = ((emit.get("dialect_templates") or {}).get(str(self.dialect or "").lower())
               or emit.get("template"))
        if not nse:
            raise _lod_refusal(kind, "nested, it joins NULL to NULL, which has no form for "
                                     "this dialect")
        combos = ("SELECT DISTINCT " + ", ".join(f"{e} AS {name[e]}" for e in exprs)
                  + f" FROM {rows} s") if exprs else (
            # One row where there are rows: DISTINCT is that in every dialect.
            f"SELECT DISTINCT 1 AS e FROM {rows} s" if str(self.dialect or "").lower()
            in _LIMIT else f"SELECT 1 AS e FROM {rows} s LIMIT 1")
        sql = f"SELECT c.*, " + ", ".join(f"i{r['n']}.v AS __lodin{r['n']}" for r in inner) \
            + f"\n      FROM ({combos}) c"
        subs: list[tuple[dict, str]] = []
        for r in inner:
            gdims = r.get("dims_from") or r["dims"]
            keys = ", ".join(f"{d} AS k{i}" for i, d in enumerate(gdims))
            # A FIXED one inside reads the rows before the view's dimension filters, even
            # inside an INCLUDE or EXCLUDE that reads them after (`{src_all}`).
            src = (f"({r['from']})" if r.get("from") else
                   "{src_all}" if r.get("kind") == "FIXED" else "{src}")
            sub = (f"SELECT {keys + ', ' if keys else ''}{r['value']} AS v FROM {src} s"
                   + (f" GROUP BY {', '.join(gdims)}" if gdims else ""))
            subs.append((r, sub))
            on = " AND ".join(nse.replace("{0}", f"i{r['n']}.k{i}").replace(
                "{1}", f"c.{name[d]}") for i, d in enumerate(r["dims"])) or "1 = 1"
            sql += f"\n      LEFT JOIN ({sub}) i{r['n']} ON {on}"
        # An aggregate of the rows' own fields beside the inner values (`SUM([x]) / MAX({…})`)
        # reads the ROWS, grouped by this one's dimensions, and the inner values their
        # combinations (seen live: SUM([amount]) - SUM({FIXED [a], [b] : …}) is the rows' sum
        # less the sum over the (a, b) combinations): each such aggregate is a column of its
        # own statement over the rows, the same on every combination of one group.
        atoms: list[str] = []

        def own(m) -> str:
            atoms.append(m)
            return f"MAX(s.__lodr{len(atoms) - 1})"
        value = _map_aggregates(value, lambda a: own(a) if _reads_rows(a) else a)
        if atoms:
            keys = ", ".join(f"{d} AS k{i}" for i, d in enumerate(dims))
            cols = ", ".join(f"{a} AS r{i}" for i, a in enumerate(atoms))
            read = rows
            if any("__lodin" in a for a in atoms):
                # One aggregate over a row-level expression of the rows' fields and an inner
                # level of detail (`SUM([amount] * {FIXED [a], [b] : AVG([d])})`) reads it row
                # by row: each row carries the inner value of its own dimensions (checked
                # live: the rows' sum of amount times their (a, b) average).
                read = "(SELECT s.*, " + ", ".join(
                    f"i{r['n']}.v AS __lodin{r['n']}" for r, _sub in subs) + f" FROM {rows} s"
                for r, sub in subs:
                    on = " AND ".join(nse.replace("{0}", f"i{r['n']}.k{i}").replace("{1}", d)
                                      for i, d in enumerate(r["dims"])) or "1 = 1"
                    read += f"\n      LEFT JOIN ({sub}) i{r['n']} ON {on}"
                read += ")"
            sub = (f"SELECT {keys + ', ' if keys else ''}{cols} FROM {read} s"
                   + (f" GROUP BY {', '.join(dims)}" if dims else ""))
            on = " AND ".join(nse.replace("{0}", f"rr.k{i}").replace("{1}", f"c.{name[d]}")
                              for i, d in enumerate(dims)) or "1 = 1"
            sql = sql.replace("SELECT c.*, ", "SELECT c.*, " + "".join(
                f"rr.r{i} AS __lodr{i}, " for i in range(len(atoms))), 1)
            sql += f"\n      LEFT JOIN ({sub}) rr ON {on}"
        left = re.sub(r"\bs\.(__lodin\d+|__lodr\d+)\b", "", value)
        if re.search(r"\bs\.\w", left):
            raise _lod_refusal(kind, "a level of detail inside it beside the rows' own fields "
                                     "outside an aggregate")
        return sql, [f"s.{name[e]}" for e in dims], value

    def _lod_per_row(self, rec: dict) -> str:
        """An INCLUDE / EXCLUDE level of detail read per row (no aggregate around it)."""
        if self.ctx is None:
            raise _lod_refusal(rec["kind"])
        view = [(nm, sql) for nm, _t, sql in self.ctx.groups]
        if any(re.search(r"\b(\w+)\.", sql) and not re.fullmatch(
                rf"(?s)[^.]*\b{re.escape(self.alias)}\.\w+[^.]*", sql) for _nm, sql in view):
            raise _lod_refusal(rec["kind"], "per row on a view grouped by another table's "
                                            "column")
        if None in rec["dim_names"]:
            raise _lod_refusal(rec["kind"], "a dimension that is not a field")
        if rec["kind"] == "INCLUDE":
            out = list(dict.fromkeys([sql for _nm, sql in view] + rec["dims_out"]))
        else:
            gone = set(rec["dim_names"])
            out = [sql for nm, sql in view if nm not in gone]
        rec["dims_out"] = out
        rec["dims_in"] = [re.sub(rf"\b{re.escape(self.alias)}\.", "s.", d) for d in out]
        rec["after_filters"] = True
        rec["mode"] = "row" if out else "scalar"
        self.lods.append(rec)
        return _LOD_MARK[rec["mode"]].format(n=rec["n"])

    def _lod_argument(self) -> list | None:
        """The tokens of the level-of-detail expression a call's one argument is (the `(` at
        the cursor): a brace group, in redundant parentheses or not, or a calc whose formula
        is one (followed through calcs naming calcs). None for any other argument."""
        end = _call_args(self.t, self.i)[0]
        if end is None:
            return None
        arg, seen = self.t[self.i + 1:end], set(self.inlining)
        for _hop in range(16):
            while (len(arg) >= 2 and arg[0][0] == "lparen"
                   and _call_args(arg, 0)[0] == len(arg) - 1):
                arg = arg[1:-1]
            if arg and arg[0] == ("brace", "{") and _brace_end(arg, 0) == len(arg) - 1:
                return list(arg)
            if len(arg) != 1 or arg[0][0] != "col":
                return None
            key = _column_ref(arg[0][1]).lower()
            if key not in self.measures or key in seen:
                return None
            seen.add(key)
            try:
                arg = _tokens(self.measures[key], self.language)
            except CompileError:
                return None
        return None

    def _lod_exclude(self, rec: dict) -> str:
        """EXCLUDE under an aggregate: its value at the view's dimensions without its own, a
        window over the statement's rows; the aggregate around it reads one value per view
        row, which is that value."""
        if self.ctx is None:
            raise _lod_refusal("EXCLUDE")
        if None in rec["dim_names"]:
            raise _lod_refusal("EXCLUDE", "a dimension that is not a field")
        gone = set(rec["dim_names"])
        kept = [sql for name, _t, sql in self.ctx.groups if name not in gone]
        if len(kept) == len(self.ctx.groups):
            return rec["value_out"]
        row = self.rules.function(self.language, "TOTAL", self.dialect) or {}
        combine = (row.get("emit") or {}).get("combine") or {}
        m = re.match(r"(?is)^\s*(SUM|COUNT|MIN|MAX|AVG)\s*\((.*)\)\s*$", rec["value_out"])
        if not m or not _balanced(m.group(2)) or m.group(1).upper() not in combine:
            raise _lod_refusal("EXCLUDE", f"over {rec['value_out']!r}: only one "
                                          f"{'/'.join(sorted(combine))} is compiled")
        pby = f"PARTITION BY {', '.join(kept)}" if kept else ""
        return "(" + combine[m.group(1).upper()].replace("{agg}", rec["value_out"]).replace(
            "{x}", m.group(2)).replace("{partition}", pby) + ")"

    # -- calculations over the view's aggregated rows -------------------------------------
    def _args(self) -> tuple[list[str], list[list[tuple[str, str]]]]:
        """A call's arguments, compiled, with each one's tokens."""
        self.expect("lparen")
        args: list[str] = []
        raw: list[list[tuple[str, str]]] = []
        if self.peek()[0] != "rparen":
            while True:
                at = self.i
                args.append(self.expr())
                raw.append(self.t[at:self.i])
                if self.peek()[0] == "comma":
                    self.take()
                    continue
                break
        self.expect("rparen")
        return args, raw

    @staticmethod
    def _offset(toks: list[tuple[str, str]], what: str) -> int | str:
        """A window offset: a whole number (negative is before the current row), or FIRST() /
        LAST() for the partition's first or last row."""
        text = "".join(v for _k, v in toks).replace(" ", "").upper()
        if text in ("FIRST()", "LAST()"):
            return text[:-2]
        if re.fullmatch(r"[-+]?\d+", text):
            return int(text)
        raise CompileError(f"{what} is {text!r}; only a whole number, FIRST() or LAST() is "
                           f"compiled as a window offset")

    def _table_calc(self, up: str, emit: dict) -> str:
        """A table-calculation function: computed over the statement's GROUPED rows, as a
        window over the columns the measure's compute-using runs along (ORDER BY, empty value
        first as the view lays them out) and restarts on (PARTITION BY). The template places
        `{over}` (partition + order), `{partition}` (partition only) and `{frame}`."""
        tc = self.ctx.table_calc if self.ctx is not None else None
        if tc is None:
            why = "a table calculation, and the view's compute-using for it is not known here"
            self.untranslatable.append({"construct": up, "why": why})
            raise CompileError(f"{up} is {why}")
        if self.row_filter or self.row_level or self.window is not None \
                or self.slots is not None:
            raise CompileError(f"{up} inside a row-level or filtered expression")
        args, raw = self._args()
        if any(" OVER (" in a for a in (args[:1] if emit["table_calc"] in ("lookup", "window")
                                        else args)) and emit["table_calc"] != "total":
            # A table calculation over another one: the inner one's result is a column of the
            # statement's grouped rows, and the outer one runs over those rows.
            return self._lifted_window(up, emit, tc, args, raw)
        try:
            return self._window_sql(up, emit, tc, args, raw)
        except _SortNeedsWindow:
            # The view orders the rows by a member's aggregate over finer rows: that order is
            # itself a column of the grouped rows.
            return self._lifted_window(up, emit, tc, args, raw)

    def _lifted_window(self, up: str, emit: dict, tc: dict, args: list[str], raw) -> str:
        """A table calculation computed over the statement's grouped rows as a statement of
        their own (`lw<n>`): each argument a column of those rows (`a<i>`, a window of its
        own allowed), the window run over them by the grouped columns (`k<i>`), joined back to
        each row of the statement on its grouping. The measure reads it as `MIN(lw<n>.v)`
        (one value per group)."""
        if self.ctx is None:
            raise CompileError(f"{up} over another table calculation outside a statement")
        keys = [sql for _n, _t, sql in self.ctx.groups]
        n = len(self.ctx.lifts) + 1

        def at(sql: str) -> str:
            if sql not in keys:
                raise CompileError(f"{up} runs over a column the statement does not group by")
            return f"__q.k{keys.index(sql)}"
        lifted_sorts: list[tuple[str, str]] = []
        sorts = {}
        for col, spec in (tc.get("sorts") or {}).items():
            spec2 = {k: v for k, v in spec.items() if k not in ("by", "within_sql")}
            if spec.get("by"):
                name = f"s{len(lifted_sorts)}"
                lifted_sorts.append((name, self._sort_key(col, spec, tc)))
                spec2["lifted"] = f"__q.{name}"
            sorts[at(col)] = spec2
        mapped = {"partition": [at(c) for c in tc.get("partition") or []],
                  "order": [at(c) for c in tc.get("order") or []], "sorts": sorts}
        outer = self._window_sql(up, emit, mapped, [f"__q.a{i}" for i in range(len(args))],
                                 raw)
        self.ctx.lifts.append({"n": n, "args": list(args), "sorts": lifted_sorts,
                               "window": outer})
        self.used.append("engine.table_calc_nested")
        return f"MIN(lw{n}.v)"

    def _window_sql(self, up: str, emit: dict, tc: dict, args: list[str], raw) -> str:
        """The window of one table-calculation function over `tc` (its partition and order
        SQL), its arguments compiled."""
        kind = emit["table_calc"]
        nulls = str(emit.get("nulls") or "first").upper()
        part, order = list(tc["partition"]), list(tc["order"])
        pby = f"PARTITION BY {', '.join(part)}" if part else ""
        keys = order or part[:1] or ["1"]
        oby = "ORDER BY " + ", ".join(self._order_terms(o, tc, nulls) for o in keys)
        over = f"{pby} {oby}".strip()
        frame = "ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING"
        self.used.append("engine.table_calc")
        if kind == "window" and len(args) in (2, 3):
            lo = self._offset(raw[1], f"{up}'s start")
            hi = self._offset(raw[2], f"{up}'s end") if len(args) == 3 else "LAST"

            def bound(v) -> str:
                if v == "FIRST":
                    return "UNBOUNDED PRECEDING"
                if v == "LAST":
                    return "UNBOUNDED FOLLOWING"
                return ("CURRENT ROW" if v == 0 else f"{-v} PRECEDING" if v < 0
                        else f"{v} FOLLOWING")
            frame = f"ROWS BETWEEN {bound(lo)} AND {bound(hi)}"
            args = args[:1]
        elif kind == "total":
            return self._total(up, emit, args, raw, pby)
        elif kind == "lookup":
            if len(args) != 2:
                raise CompileError("LOOKUP takes an expression and an offset")
            n = self._offset(raw[1], "LOOKUP's offset")
            if not isinstance(n, int):
                # The partition's first or last row's value, along the compute-using.
                fn = "FIRST_VALUE" if n == "FIRST" else "LAST_VALUE"
                return (f"{fn}({args[0]}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND "
                        f"UNBOUNDED FOLLOWING)")
            if n == 0:
                return args[0]
            fn = "LAG" if n < 0 else "LEAD"
            return f"{fn}({args[0]}, {abs(n)}) OVER ({over})"
        elif kind == "rank":
            default = str(emit.get("direction") or "desc")
            if len(args) == 2:
                d = "".join(v for _k, v in raw[1]).strip().strip("'\"").lower()
                if d not in ("asc", "desc"):
                    raise CompileError(f"{up}'s order is {d!r}; 'asc' or 'desc' is compiled")
                default = d
            elif len(args) != 1:
                raise CompileError(f"{up} takes an expression and an optional order")
            x = args[0]
            # A NULL value is left out of the ranking and gets no rank.
            ranked = (f"PARTITION BY {', '.join(part + [f'CASE WHEN {x} IS NULL THEN 1 ELSE 0 END'])}"
                      f" ORDER BY {x} {default.upper()}")
            if emit.get("tie_break_by_view"):
                # Ties are numbered in the view's order along the compute-using (checked
                # live: twelve tied rows, down two dimensions in either order).
                ranked += "".join(f", {self._order_terms(o, tc, nulls)}" for o in order)
            body = emit["template"].replace("{ranked}", ranked).replace("{0}", x)
            return f"(CASE WHEN {x} IS NULL THEN NULL ELSE {body} END)"
        elif kind == "running" and len(args) != 1:
            raise CompileError(f"{up} takes one expression")
        elif kind == "position" and args:
            raise CompileError(f"{up} takes no argument")
        elif kind == "window" and len(args) != 1:
            raise CompileError(f"{up} takes an expression and an optional start and end")
        template = (emit["template"].replace("{over}", over).replace("{partition}", pby)
                    .replace("{frame}", frame))
        return _apply({**emit, "template": template}, args, up)

    def _order_terms(self, o: str, tc: dict, nulls: str) -> str:
        """One addressed column's ORDER BY term: ascending with the empty member first, or in
        the order the view sorts it (a manual order, a direction, or a computed sort by an
        aggregate over the column's members, within its outer columns when nested)."""
        spec = (tc.get("sorts") or {}).get(o)
        if not spec:
            return _sort_term(o, "ASC", nulls, self.dialect)
        d = str(spec.get("direction") or "asc").upper()
        if spec.get("members") is not None:
            members = list(spec["members"])
            whens = [f"WHEN {o} IS NULL THEN {members.index(None) + 1}"] if None in members \
                else []
            whens += [f"WHEN {o} = {_literal(m, self.dialect)} THEN {i + 1}"
                      for i, m in enumerate(members) if m is not None]
            self.used.append("engine.table_calc_sort")
            return (f"CASE {' '.join(whens)} ELSE {len(members) + 1} END {d}, "
                    + _sort_term(o, "ASC", nulls, self.dialect))
        if spec.get("lifted"):
            # The member's aggregate over finer rows, a column of the grouped rows.
            self.used.append("engine.table_calc_sort")
            return (_sort_term(spec["lifted"], d, "LAST", self.dialect) + ", "
                    + _sort_term(o, "ASC", nulls, self.dialect))
        if spec.get("by"):
            # The member's aggregate: the row's own where the statement groups by nothing
            # finer than the sorted column and the columns it is nested within; otherwise a
            # window over the member's rows, which orders the calculation as a column of the
            # grouped rows (`_lifted_window`).
            grain = {sql for _n, _t, sql in self.ctx.groups}
            if not grain <= set(spec.get("within_sql") or []) | {o}:
                raise _SortNeedsWindow()
            key = self._sort_key(o, spec, tc)
            self.used.append("engine.table_calc_sort")
            return (_sort_term(key, d, "LAST", self.dialect) + ", "
                    + _sort_term(o, "ASC", nulls, self.dialect))
        return _sort_term(o, d, "LAST" if d == "DESC" else "FIRST", self.dialect)

    def _sort_key(self, o: str, spec: dict, tc: dict) -> str:
        """The aggregate a computed sort orders the member `o` by, over the member's rows (and
        within the columns its sort is nested in): the row's own aggregate when the statement
        groups by nothing finer, else that aggregate's total over the rows sharing the member
        (a TOTAL over them: checked live, the members sorted by their whole sum)."""
        grain = {sql for _n, _t, sql in self.ctx.groups}
        within = list(spec.get("within_sql") or [])
        text = spec["by"]
        if not grain <= set(within) | {o}:
            text = f"TOTAL({text})"
        sub = _Parser(_tokens(text, self.language), self.rules,
                      self.language, self.alias, self.dialect, self.colmap, self.measures,
                      None, self.inlining)
        sub.home, sub.ctx, sub.lods = self.home, self.ctx, self.lods
        saved = self.ctx.table_calc
        self.ctx.table_calc = {"partition": within + [o], "order": []}
        try:
            return sub.parse()
        finally:
            self.ctx.table_calc = saved
            self._absorb(sub)

    def _total(self, up: str, emit: dict, args: list[str], raw, pby: str) -> str:
        """TOTAL(agg(x)): the aggregate over every row of the partition, of the underlying
        rows (TOTAL(AVG(x)) is the average of all of them, checked live), not of the view's
        values. Over an aggregate whose total is a sum, minimum or maximum of the view's
        values; anything else is refused by name."""
        if len(args) != 1:
            raise CompileError("TOTAL takes one aggregate")
        toks = raw[0]
        head = toks[0][1].upper() if toks and toks[0][0] == "func" else ""
        whole = (len(toks) >= 3 and toks[1][0] == "lparen" and toks[-1][0] == "rparen"
                 and _call_args(toks, 1)[0] == len(toks) - 1)
        combine = (emit.get("combine") or {}).get(head)
        if not whole or not combine:
            return self._total_of_expression(up, emit, toks)
        inner = re.match(r"(?is)^\s*\w+\s*\((.*)\)\s*$", args[0])
        x = inner.group(1) if inner else args[0]
        return "(" + combine.replace("{agg}", args[0]).replace("{x}", x).replace(
            "{partition}", pby) + ")"

    def _total_of_expression(self, up: str, emit: dict, toks: list) -> str:
        """TOTAL(expression of aggregates): the expression of each aggregate's total over the
        partition (checked live: TOTAL(SUM(a) / SUM(b)) is the ratio of the two totals, and
        TOTAL(AVG(a) * 2 + MAX(b)) the same expression of the whole partition's average and
        maximum). An aggregate whose total the rule does not combine is refused by name."""
        combine = emit.get("combine") or {}
        out, i, found, toks, depth = [], 0, 0, list(toks), 0
        while i < len(toks):
            k, v = toks[i]
            key = _column_ref(v).lower() if k == "col" else None
            if key and key in self.measures and key not in self.inlining and depth < 16:
                # An aggregate calc it names: its aggregates are totalled too.
                toks[i:i + 1] = [("lparen", "(")] + list(_tokens(self.measures[key],
                                                                 self.language)) + [
                    ("rparen", ")")]
                depth += 1
                continue
            if k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
                end = _call_args(toks, i + 1)[0]
                if end is None:
                    raise CompileError(f"{up}: an unclosed call")
                if v.upper() not in combine:
                    if v.upper() in ("COUNTD", "MEDIAN", "PERCENTILE", "STDEV", "STDEVP",
                                     "VAR", "VARP", "ATTR") or TABLE_CALC_NAMES.match(v):
                        raise CompileError(
                            f"TOTAL over {''.join(x for _k, x in toks)!r}: {v.upper()} has "
                            f"no total over the partition here (only "
                            f"{'/'.join(sorted(combine))} are combined)")
                    out.append(toks[i])
                    i += 1
                    continue
                out += [("func", up), ("lparen", "(")] + list(toks[i:end + 1]) + [
                    ("rparen", ")")]
                found += 1
                i = end + 1
                continue
            out.append(toks[i])
            i += 1
        if not found:
            raise CompileError(f"TOTAL over {''.join(x for _k, x in toks)!r}: no aggregate")
        sub = self._child(out, row_filter=self.row_filter, inlining=self.inlining,
                          share_vars=True)
        try:
            sql = sub.parse()
        finally:
            self._absorb(sub)
        self.used.append("engine.tableau_total_expression")
        return f"({sql})"

    # -- aggregates ----------------------------------------------------------------------
    def _aggregate(self, up: str, emit: dict, args: list[str], raw) -> str:
        """One DAX aggregate over the statement's rows: gated by the CALCULATE filter around
        it, its text argument compared as the model compares text, summed across the visual's
        rows inside an ALL-modified CALCULATE, or one slot of a date-set statement."""
        gate = self.row_filter
        if emit.get("text_key") and args and raw and self._is_text_column(raw[0]):
            args = [self._text_key(args[0])] + args[1:]
            self.used.append("engine.dax_text_key")
        template = emit.get("template") or ""
        if "{gate}" in template or "{g0}" in template:
            # The row's own form: `{0}` is the argument as is, `{g0}` the argument where the
            # filter admits the row (NULL elsewhere), `{gate}` the filter as a leading
            # condition. A template that tests the argument for blank needs the raw value,
            # or every row the filter refuses would read as blank.
            g0 = f"CASE WHEN {gate} THEN {args[0]} END" if gate else args[0]
            emit = {**emit, "template": template.replace("{gate}", f"({gate}) AND " if gate
                                                         else "").replace("{g0}", g0)}
            if gate:
                self.used.append("engine.dax_count_blank")
            sql = _apply(emit, args, up)
            parts_args = [g0] + args[1:]
        elif emit.get("ignore_args"):
            # COUNTROWS of a table the filter leaves empty is BLANK, not 0 (seen live).
            sql = (f"NULLIF(COUNT(CASE WHEN {gate} THEN 1 END), 0)" if gate else template)
            if gate:
                self.used.append("engine.dax_count_blank")
            parts_args = ["1"] if not gate else [f"CASE WHEN {gate} THEN 1 END"]
        else:
            if gate:
                args = [f"CASE WHEN {gate} THEN {args[0]} END"] + args[1:]
            sql = _apply(emit, args, up)
            parts_args = args
        return self._combine(up, emit, sql, parts_args)

    def _combine(self, up: str, emit: dict, sql: str, args: list[str]) -> str:
        """An aggregate's SQL as the statement around it reads it."""
        if self.empty:
            if self.window is not None and self.empty_aggs is not None:
                # Over a wider context the row's own rows are not all of it: the value there
                # is that context's, read from its own statement.
                n = len(self.empty_aggs) + 1
                self.empty_aggs.append({"n": n, "sql": sql, "keep": list(self.window),
                                        "drop": list(self.window_drop or [])})
                return f"__EMPTYAGG{n}__"
            return "1" if self.window is not None else "NULL"
        if self.window is not None and (self.window_drop or self.window_own) \
                and self.ctx is not None:
            # Rows the statement's WHERE (or its filter on a measure) does not keep: its own
            # statement, additive or not.
            n = len(self.ctx.context_aggs) + 1
            self.ctx.context_aggs.append({"n": n, "sql": sql, "keep": list(self.window),
                                          "drop": list(self.window_drop)})
            self.used.append("engine.dax_context_statement")
            return _CTX_MARK.format(n=n)
        if self.window is not None:
            win = emit.get("window") or {}
            over = (f"OVER (PARTITION BY {', '.join(self.window)})" if self.window
                    else "OVER ()")
            if win.get("combine"):
                return f"{win['combine']}({sql}) {over}"
            if win.get("ratio"):
                num, den = (_apply({"template": t}, args, up) for t in win["ratio"])
                return f"(SUM({num}) {over} / NULLIF(SUM({den}) {over}, 0))"
            if self.ctx is None:
                raise CompileError(f"{up} is not additive across the visual's rows, so an "
                                   f"ALL / REMOVEFILTERS over it needs a statement of its "
                                   f"own, which this compile has no statement for")
            # NOT ADDITIVE (a distinct count, a median): the rows' values cannot be combined,
            # so the aggregate is computed again over the same rows, grouped by only the
            # columns the context keeps (`_context_statements`).
            n = len(self.ctx.context_aggs) + 1
            self.ctx.context_aggs.append({"n": n, "sql": sql, "keep": list(self.window),
                                          "selected": self.window_selected})
            self.used.append("engine.dax_context_statement")
            return _CTX_MARK.format(n=n)
        if self.slots is not None:
            self.slots.append({"sql": sql, "date_set": self.date_set,
                               "drops": sorted(set(self.slot_drops), key=str),
                               "keep": None if self.slot_keep is None
                               else tuple(self.slot_keep)})
            return f"MAX(u.s{len(self.slots)})"
        return sql

    def _is_text_column(self, toks) -> bool:
        if self.ctx is None or len(toks) != 1 or toks[0][0] not in (
                "qualified", "quoted_qualified"):
            return False
        return self.ctx.is_text(_table_ref(toks[0][1]) or "", _column_ref(toks[0][1]))

    def _context_test(self, up: str, kind: str) -> str:
        """ISINSCOPE / ISFILTERED / HASONEVALUE of a column, read from the statement's
        context (seen live on a matrix with subtotals): ISINSCOPE is TRUE where the row groups
        by the column (a subtotal or total over it is its own statement, which does not);
        ISFILTERED where the visual groups or filters by it; HASONEVALUE where the row groups
        by it or a filter keeps one value of it, else the count of its values: over the
        statement's rows for a column of the statement's own table, over the column's table
        for a dimension's (a filter on the fact does not narrow a dimension's values)."""
        start = self.i
        end, args = _call_args(self.t, start)
        if end is None or len(args) != 1:
            raise CompileError(f"{up} takes one column")
        colarg = self._column_arg(args[0])
        if colarg is None and kind == "crossfiltered" and self.ctx is not None:
            # ISCROSSFILTERED(T) of a whole table: the same test, over every column of T.
            name = self._table_arg(args[0])
            if name is not None:
                colarg = ("", name)
        if colarg is None or self.ctx is None:
            raise CompileError(f"{up} is compiled over a column of the visual's model, in a "
                               f"statement whose grouping and filters are known")
        if self.window is not None or self.slots is not None or self.row_level:
            raise CompileError(f"{up} inside a CALCULATE that changes the visual's context, "
                               f"beside time intelligence or in a row iterator is not compiled")
        col, table = colarg
        if table is None and self.home is not None:
            table = _bare(self.home[1])

        def same(c: str, t: str | None) -> bool:
            return c == col and (t is None or table is None or _bare(t) == _bare(table))
        grouped = any(same(c, t) for c, t, _s in self.ctx.groups)
        self.i = end + 1
        self.used.append(f"engine.dax_{kind}")
        if kind == "inscope":
            return "TRUE" if grouped else "FALSE"
        if kind == "crossfiltered":
            # TRUE where the visual groups or filters by any column of the column's table, or
            # of a table whose filters reach it (the one side of a many-to-one relationship,
            # either side of a both-ways one).
            if self.row_filter:
                raise CompileError(f"{up} inside a CALCULATE filter is not compiled")
            reach = self.ctx.filtered_by(table or self.ctx.source)
            hit = any(_bare(t or self.ctx.source) in reach
                      for c, t, *_rest in list(self.ctx.groups) + list(self.ctx.filters))
            return "TRUE" if hit else "FALSE"
        filtered = any(same(c, t) for c, t in self.ctx.filters)
        if kind == "filtered":
            if self.row_filter:
                raise CompileError(f"{up} inside a CALCULATE filter is not compiled")
            return "TRUE" if grouped or filtered else "FALSE"
        # HASONEVALUE
        if grouped:
            return "TRUE"
        for c, t, op, value in self.ctx.filter_values:
            if same(c, t) and op in ("in", "eq", "=") and (
                    not isinstance(value, list) or len(value) == 1):
                return "TRUE"
        x = (f"{self.alias}.{col}" if table is None or _bare(table) == self.ctx.source
             else None)
        if x is not None and self.empty:
            # A row of the visual with no rows of the statement's table: no value of the column.
            return "FALSE"
        if x is not None:
            if self.row_filter:
                x = f"CASE WHEN {self.row_filter} THEN {x} END"
                blank = f"CASE WHEN ({self.row_filter}) AND {self.alias}.{col} IS NULL"
            else:
                blank = f"CASE WHEN {x} IS NULL"
            return (f"(COUNT(DISTINCT {x}) + MAX({blank} THEN 1 ELSE 0 END) = 1)")
        # A filter on another column of the column's own table narrows its values (a
        # membership test, read on the table's rows; text as the model compares it).
        where = self._dimension_filters(up, table)
        if where:
            self.used.append("engine.dax_onevalue_table_filters")
        phys = next((v for k, v in (self.home[0] if self.home else {}).items()
                     if _bare(v) == _bare(table)), table)
        cond = ("\n WHERE " + " AND ".join(where)) if where else ""
        return (f"((SELECT COUNT(DISTINCT h.{col}) + MAX(CASE WHEN h.{col} IS NULL THEN 1 "
                f"ELSE 0 END) FROM {phys} h{cond}) = 1)")

    def _dimension_filters(self, up: str, table: str) -> list[str]:
        """The visual's filters on `table`'s columns as predicates over its rows (alias `h`):
        what narrows a dimension's values in the row's context."""
        where: list[str] = []
        for c, t, op, value in self.ctx.filter_values:
            if _bare(t or "") != _bare(table):
                continue
            vals = value if isinstance(value, list) else [value]
            if op not in ("in", "eq", "=", "not_in") or any(isinstance(v, (dict, list))
                                                            for v in vals):
                raise CompileError(f"{up} of {table}[{col}] under a {op} filter on another "
                                   f"column of its table is not compiled")
            rest = [v for v in vals if v is not None]
            fold = self.ctx.text_key and rest and all(_foldable(v) for v in rest)
            x = f"UPPER(RTRIM(h.{c}))" if fold else f"h.{c}"
            lits = _literal_list([v.upper() for v in rest] if fold else rest, self.dialect)
            if op == "not_in":
                pred = (f"(h.{c} IS NULL OR {x} NOT IN ({lits}))" if rest and None not in vals
                        else f"({x} NOT IN ({lits}))" if rest else f"h.{c} IS NOT NULL")
            else:
                member = f"{x} IN ({lits})" if rest else "FALSE"
                pred = f"(h.{c} IS NULL OR {member})" if None in vals else member
            where.append(pred)
        return where

    def _selected_dimension(self) -> str | None:
        """`SELECTEDVALUE(D[c][, alternate])` of a column of ANOTHER table than the statement's:
        the dimension's own values in the row's context, as for HASONEVALUE (a filter on the
        fact does not narrow a dimension's values). The group's value where the visual groups
        by the column; the value a filter keeps where it keeps one; otherwise the one value of
        the column over the dimension's rows the visual's filters on it leave, or the
        alternate. Budgets at a coarser grain than the article are commonly blanked this way
        (`SWITCH(TRUE(), SELECTEDVALUE(Product[Is House Brand]) = "Y", BLANK(), ...)`) and
        the measure was refused as reading another table. None for a column of the
        statement's own table (the rule row reads it)."""
        start = self.i
        end, args = _call_args(self.t, start)
        if end is None or len(args) not in (1, 2) or self.ctx is None:
            return None
        colarg = self._column_arg(args[0])
        if colarg is None or colarg[1] is None or _bare(colarg[1]) == self.ctx.source:
            return None
        if self.home is None or not any(_bare(v) == _bare(colarg[1])
                                        for v in self.home[0].values()):
            # The model does not say which warehouse table the name reads: the rule row.
            return None
        if self.window is not None or self.slots is not None or self.row_level:
            raise CompileError("SELECTEDVALUE of another table's column inside a CALCULATE "
                               "that changes the visual's context, beside time intelligence "
                               "or in a row iterator is not compiled")
        col, table = colarg
        alt = "NULL"
        if len(args) == 2:
            sub = self._child(args[1], row_filter=None, inlining=self.inlining, share_vars=True)
            try:
                alt = sub.parse()
            finally:
                self._absorb(sub)
        self.i = end + 1
        self.used.append("engine.dax_selectedvalue_dimension")
        for c, t, sql in self.ctx.groups:
            if c == col and (t is None or _bare(t) == _bare(table)):
                return sql
        for c, t, op, value in self.ctx.filter_values:
            if c == col and _bare(t or "") == _bare(table) and op in ("in", "eq", "=") and (
                    not isinstance(value, list) or len(value) == 1):
                v = value[0] if isinstance(value, list) else value
                if v is None:
                    return "NULL"
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return str(v)
                return _quote(str(v), self.dialect)
        where = self._dimension_filters("SELECTEDVALUE", table)
        phys = next((v for k, v in (self.home[0] if self.home else {}).items()
                     if _bare(v) == _bare(table)), table)
        wc = self.ctx.renames.get((_bare(table), col), col)
        cond = ("\n WHERE " + " AND ".join(where)) if where else ""
        return (f"(SELECT CASE WHEN COUNT(DISTINCT h.{wc}) + MAX(CASE WHEN h.{wc} IS NULL "
                f"THEN 1 ELSE 0 END) = 1 THEN MIN(h.{wc}) ELSE {alt} END FROM {phys} h{cond})")

    def _countrows(self) -> str:
        """`COUNTROWS(T)`: the rows of the measure's own table (BLANK when there are none);
        `COUNTROWS(FILTER(T, pred))` those where the row predicate holds;
        `COUNTROWS(VALUES(T[c]))` / `DISTINCT` the column's distinct values, a blank one
        included. COUNTROWS of ANOTHER table counts that table's rows, which a statement
        over this table's rows does not, and is refused by name."""
        start = self.i
        end, args = _call_args(self.t, start)
        if end is None or len(args) != 1:
            raise CompileError("COUNTROWS takes one table")
        toks = args[0]
        rule = self._function_rule("COUNTROWS")
        emit = rule.get("emit") or {}
        table = self._table_arg(toks)
        source = _bare(self.home[1]) if self.home else None
        if table is not None or toks and toks[0][0] == "str":
            if table is not None and source is not None and table != source:
                raise CompileError(f"COUNTROWS({' '.join(v for _k, v in toks)}) counts the "
                                   f"rows of {table}, not of this statement's table {source}; "
                                   f"a statement over {source} does not count another table")
            self.i = end + 1
            return self._aggregate("COUNTROWS", emit, [], [])
        head, inner = _call_head(toks)
        if head == "FILTER":
            parts = _split_top(inner)
            t2 = self._table_arg(parts[0]) if len(parts) == 2 else None
            if t2 is not None and (source is None or t2 == source):
                pred = self._row_predicate(parts[1])
                self.i = end + 1
                gate = f"({self.row_filter}) AND {pred}" if self.row_filter else pred
                self.used.append("engine.dax_count_blank")
                return self._combine("COUNTROWS", emit,
                                     f"NULLIF(COUNT(CASE WHEN {gate} THEN 1 END), 0)",
                                     [f"CASE WHEN {gate} THEN 1 END"])
        if head in ("VALUES", "DISTINCT") and len(_split_top(inner)) == 1:
            sub = _Parser([("func", "DISTINCTCOUNT"), ("lparen", "(")] + inner
                          + [("rparen", ")")], self.rules, self.language, self.alias,
                          self.dialect, self.colmap, self.measures, self.row_filter,
                          self.inlining)
            sub.home, sub.ctx, sub.window, sub.slots = self.home, self.ctx, self.window, \
                self.slots
            sub.date_set, sub.vars, sub.empty = self.date_set, self.vars, self.empty
            try:
                sql = sub.parse()
            finally:
                self._absorb(sub)
            self.i = end + 1
            return sql
        raise CompileError(f"COUNTROWS of {head or 'that table'}: only the measure's own "
                           f"table, FILTER over it, or the VALUES of a column is compiled")

    def _row_iterator(self, up: str, emit: dict) -> str:
        """`SUMX(T, expr)` and the like over the measure's own table: the expression is
        evaluated on each row, with RELATED reading the row's many-to-one dimension, and the
        rows' values aggregated (the row's own template). `FILTER(T, pred)` as the table keeps
        the rows where the predicate holds. Over another table, the distinct values of a
        column, or RELATEDTABLE, the iterator computes at another grain and is refused by
        name."""
        start = self.i
        end, args = _call_args(self.t, start)
        if end is None or len(args) != 2:
            raise CompileError(f"{up} is compiled with a table and one expression")
        source = _bare(self.home[1]) if self.home else None
        pred = None
        table = self._table_arg(args[0])
        if table is None:
            head, inner = _call_head(args[0])
            parts = _split_top(inner) if inner else []
            t2 = self._table_arg(parts[0]) if head == "FILTER" and len(parts) == 2 else None
            if t2 is not None:
                table = t2
                pred = self._row_predicate(parts[1])
            else:
                raise CompileError(f"{up} over {head or 'that table'}: an iterator over the "
                                   f"distinct values of a column or another table computes "
                                   f"at a grain this statement does not have")
        if source is not None and table != source:
            raise CompileError(f"{up} iterates the rows of {table}, not of this statement's "
                               f"table {source}")
        sub = self._child(args[1], row_filter=None, inlining=self.inlining, share_vars=True)
        sub.row_level = True
        try:
            expr = sub.parse()
        finally:
            self._absorb(sub)
        if pred is not None:
            expr = f"CASE WHEN {pred} THEN {expr} END"
        self.i = end + 1
        self.used.append("engine.dax_row_iterator")
        row = {**emit, "template": emit.get("row_template") or emit.get("template")}
        return self._aggregate(up, row, [expr], [])

    # -- time intelligence ---------------------------------------------------------------
    def _date_column(self, toks, head: str) -> tuple[str, str]:
        self.untranslatable.append({"construct": head, "why": "time intelligence"})
        try:
            col = self._date_column_of(toks, head)
        except CompileError:
            raise
        self.untranslatable.pop()
        return col

    def _date_column_of(self, toks, head: str) -> tuple[str, str]:
        """The date table's date column a time-intelligence function names: (warehouse table,
        column), checked against the relationship the statement's dates come through."""
        col = self._column_arg(toks)
        if col is None or col[1] is None:
            raise CompileError(f"{head} is compiled over a date table's date column, written "
                               f"Table[Column]")
        ctx = self.ctx
        info = ctx.date_keys.get(col) if ctx is not None else None
        if info is None and ctx is not None:
            info = _keyed_date(ctx, col, self.home[0] if self.home else None)
        if info is None:
            raise CompileError(
                f"{head} over {col[1]}[{col[0]}]: time intelligence is compiled over the date "
                f"column of a date table that an active many-to-one relationship from the "
                f"measure's table reaches ON THAT COLUMN (the case in which the tool removes "
                f"the date table's other filters too); this column is not one")
        return col

    def _number_arg(self, toks) -> int:
        vals = [v for _k, v in toks]
        if len(toks) == 1 and toks[0][0] == "num" and "." not in vals[0]:
            return int(vals[0])
        if len(toks) == 2 and toks[0] == ("op", "-") and toks[1][0] == "num" \
                and "." not in vals[1]:
            return -int(vals[1])
        raise CompileError("a time-intelligence interval count is compiled as a whole-number "
                           "literal")

    def _date_set(self, head: str, inner, label: str | None = None) -> "_DateSet":
        """A time-intelligence date set as data (`_DateSet`); its SQL is the date-set
        statement's (`_compile_time_unit`). Only the forms whose dates follow from the dates
        of the visual's row are read; the rest are refused by name."""
        parts = _split_top(inner) if inner else []
        if not parts:
            raise CompileError(f"{head} needs a date column")
        col = self._date_column(parts[0], label or head)
        units = {"DAY": ("day", 1), "MONTH": ("month", 1), "QUARTER": ("month", 3),
                 "YEAR": ("month", 12)}

        def unit(toks) -> tuple[str, int]:
            word = toks[0][1].upper() if len(toks) == 1 and toks[0][0] == "func" else None
            if word not in units:
                raise CompileError(f"{head}: the interval is DAY, MONTH, QUARTER or YEAR")
            return units[word]

        self.used.append(f"engine.dax_time_{head.lower()}")
        if head in ("DATESYTD", "DATESQTD", "DATESMTD"):
            period = {"DATESYTD": "year", "DATESQTD": "quarter", "DATESMTD": "month"}[head]
            year_end = None
            if len(parts) == 2 and head == "DATESYTD":
                year_end = _year_end(parts[1])
            elif len(parts) != 1:
                raise CompileError(f"{head} is compiled with its date column"
                                   + (" and a year-end date" if head == "DATESYTD" else ""))
            return _DateSet("to_date", col, period=period, year_end=year_end)
        if head == "SAMEPERIODLASTYEAR":
            if len(parts) != 1:
                raise CompileError("SAMEPERIODLASTYEAR takes its date column only")
            return _DateSet("shift", col, part="month", n=-12)
        if head == "DATEADD":
            if len(parts) != 3:
                raise CompileError("DATEADD is compiled as DATEADD(dates, n, interval)")
            part, k = unit(parts[2])
            return _DateSet("shift", col, part=part, n=self._number_arg(parts[1]) * k)
        if head == "PARALLELPERIOD":
            if len(parts) != 3:
                raise CompileError("PARALLELPERIOD is compiled as PARALLELPERIOD(dates, n, "
                                   "interval)")
            part, k = unit(parts[2])
            if part == "day":
                raise CompileError("PARALLELPERIOD shifts by MONTH, QUARTER or YEAR")
            return _DateSet("parallel", col, n=self._number_arg(parts[1]), months=k)
        if head in ("PREVIOUSMONTH", "PREVIOUSQUARTER", "PREVIOUSYEAR"):
            if len(parts) != 1:
                raise CompileError(f"{head} takes its date column only")
            months = {"PREVIOUSMONTH": 1, "PREVIOUSQUARTER": 3, "PREVIOUSYEAR": 12}[head]
            return _DateSet("parallel", col, n=-1, months=months, previous=True)
        if head == "DATESINPERIOD":
            if len(parts) != 4:
                raise CompileError("DATESINPERIOD is compiled as DATESINPERIOD(dates, end, n, "
                                   "interval)")
            h2, a2 = _call_head(parts[1])
            anchor = anchor_fact = None
            today = self._today(parts[1])
            source = _bare(self.home[1]) if self.home else None
            other = self._column_arg(a2) if h2 == "MAX" else None
            if today is not None:
                # TODAY() / UTCTODAY(): the service's date, in UTC (seen live: TODAY() is
                # UTCTODAY(), and DATESINPERIOD(d, TODAY(), -12, MONTH) is the year back from
                # it), the same dates on every row.
                anchor = today
            elif h2 == "DATE":
                # A literal end date: the same dates on every row (seen live:
                # DATESINPERIOD(d, DATE(2025, 3, 31), -3, MONTH) is January to March 2025
                # whatever the row's dates).
                anchor = _date_literal(parts[1])
            elif other is not None and other != col and source and other[1] == source:
                # From the last date of a column of the measure's own table in the row's
                # context (seen live: MAX(Sales[OrderDate]) back one month is the month up to
                # the row's last sale, the dates outside the row's own included).
                anchor_fact = other[0]
            elif h2 not in ("MAX", "LASTDATE") or self._column_arg(a2) != col:
                raise CompileError("DATESINPERIOD is compiled from the last date of the "
                                   "visual's row (MAX or LASTDATE of the same date column), "
                                   "from the MAX of a date column of the measure's own table, "
                                   "or from a DATE(year, month, day) literal")
            part, k = unit(parts[3])
            return _DateSet("in_period", col, part=part, n=self._number_arg(parts[2]) * k,
                            anchor=anchor, anchor_fact=anchor_fact)
        if head == "DATESBETWEEN":
            if len(parts) != 3:
                raise CompileError("DATESBETWEEN is compiled as DATESBETWEEN(dates, start, end)")
            if self._is_blank(parts[1]) and self._last_date_of(parts[2], col):
                # From the first date to the row's last: a running total. Seen live: on the
                # date table's blank row (facts whose date it does not hold) the end is blank
                # too, which DATESBETWEEN reads as the column's last date, so that row shows
                # the grand running total (`open_end`: every date).
                self.used.append("engine.dax_time_running_total")
                return _DateSet("to_date", col, period=None, open_end=True)
            return _DateSet("between", col,
                            lo=self._today(parts[1]) or _date_literal(parts[1]),
                            hi=self._today(parts[2]) or _date_literal(parts[2]))
        raise CompileError(f"{head} is not compiled")

    @staticmethod
    def _is_blank(toks) -> bool:
        head, inner = _call_head(toks)
        return head == "BLANK" and not inner

    def _last_date_of(self, toks, col) -> bool:
        """`MAX(dates)` / `LASTDATE(dates)` of the same date column: the row's last date."""
        head, inner = _call_head(toks)
        return head in ("MAX", "LASTDATE") and self._column_arg(inner) == col

    def _running_dates(self, toks) -> "_DateSet | None":
        """The running-total date set as pattern libraries write it:
        `FILTER(ALL(dates), dates <= MAX(dates))`, or its shorthand `dates <= MAX(dates)`,
        over the date column a date table is related on. Every date up to the row's last
        (the tool removes the date table's other filters there, as for DATESYTD): the same
        dates as `DATESBETWEEN(dates, BLANK(), MAX(dates))`. None for any other filter."""
        head, inner = _call_head(toks)
        pred = toks
        if head == "FILTER":
            parts = _split_top(inner)
            if len(parts) != 2:
                return None
            th, tin = _call_head(parts[0])
            if th not in ("ALL", "REMOVEFILTERS") or self._column_arg(tin) is None:
                return None
            pred, over = parts[1], self._column_arg(tin)
        else:
            over = None
        ops = [i for i, (k, v) in enumerate(pred) if k == "op" and v == "<="]
        if len(ops) != 1:
            return None
        left, right = pred[:ops[0]], pred[ops[0] + 1:]
        col = self._column_arg(left)
        if col is None or (over is not None and over != col) \
                or not self._last_date_of(right, col):
            return None
        try:
            col = self._date_column(left, "a running total")
        except CompileError:
            return None
        self.used.append("engine.dax_time_running_total")
        return _DateSet("to_date", col, period=None)

    def _lookup_dates(self, toks) -> "_DateSet | None":
        """The prior-period date set retail models write with a date table's own mapping
        column instead of a time-intelligence function:

            FILTER(ALL('Date'), 'Date'[Calendar Date] IN VALUES('Date'[Previous Year Same Date]))

        For the visual's row, the dates whose `Calendar Date` is one of the row's dates'
        `Previous Year Same Date` values: last year's same days, by whatever rule the date
        table encodes (a 364-day retail year, a fiscal calendar), which no fixed shift
        reproduces. ALL / REMOVEFILTERS of the date table takes every other filter on it off,
        as for any date set. Read through the relationship the measure's table reaches the
        date table on, whatever its key (a `Date Sk` integer as often as the date itself), so
        the set is mapped onto that key (`_compile_time_unit`). None for any other filter."""
        head, inner = _call_head(toks)
        if head != "FILTER" or self.ctx is None:
            return None
        parts = _split_top(inner)
        if len(parts) != 2:
            return None
        th, tin = _call_head(parts[0])
        if th not in ("ALL", "REMOVEFILTERS"):
            return None
        table = self._table_arg(tin)
        pred = parts[1]
        at = [i for i, (k, v) in enumerate(pred) if k == "func" and v.upper() == "IN"]
        if table is None or len(at) != 1:
            return None
        left, right = pred[:at[0]], pred[at[0] + 1:]
        vh, vin = _call_head(right)
        match, via = self._column_arg(left), self._column_arg(vin) if vh in (
            "VALUES", "DISTINCT") else None
        if match is None or via is None or match[1] != table or via[1] != table:
            return None
        key = _lookup_key(self.ctx, table)
        if key is None:
            return None
        self.used.append("engine.dax_time_lookup")
        return _DateSet("lookup", key, match=match[0], via=via[0])

    def _today(self, toks) -> "_SqlDate | None":
        """`TODAY()` / `UTCTODAY()` as one argument: the current date in UTC as the dialect
        spells the current moment (the `op.relative_range` row's `now`), else None."""
        head, inner = _call_head(toks)
        if head not in ("TODAY", "UTCTODAY") or inner:
            return None
        row = self.rules.operator("relative_range", self.dialect)
        now = (((row or {}).get("emit") or {}).get("now") or {}).get(
            str(self.dialect or "").lower())
        if not now:
            raise CompileError(f"{head}() has no current moment for the {self.dialect} "
                               f"dialect (the op.relative_range row's `now`)")
        self.used.append("engine.dax_today")
        return _SqlDate(f"CAST({now} AS DATE)")

    def _period_total(self, up: str) -> str:
        """`TOTALYTD(expr, dates[, year_end])` (and QTD / MTD): `CALCULATE(expr,
        DATESYTD(dates[, year_end]))`. With a filter argument (`TOTALYTD(expr, dates, filter[,
        year_end])`) it is `CALCULATE(expr, DATESYTD(dates[, year_end]), filter)`, the filter
        read as any CALCULATE filter is."""
        start = self.i
        end, args = _call_args(self.t, start)
        if end is None or len(args) < 2:
            raise CompileError(f"{up} needs an expression and a date column")
        head = {"TOTALYTD": "DATESYTD", "TOTALQTD": "DATESQTD", "TOTALMTD": "DATESMTD"}[up]
        rest = args[1:]

        def is_str(toks) -> bool:
            return len(toks) == 1 and toks[0][0] == "str"
        if len(rest) > 1 and not (up == "TOTALYTD" and len(rest) == 2 and is_str(rest[1])):
            # A filter argument, then (TOTALYTD only) the year end.
            filt, year_end = rest[1], rest[2:]
            if is_str(filt) or len(year_end) > 1 or (year_end and (
                    up != "TOTALYTD" or not is_str(year_end[0]))):
                raise CompileError(f"{up} is compiled as {up}(expression, dates[, filter]"
                                   + ("[, year_end]" if up == "TOTALYTD" else "") + ")")
            dates = [("func", head), ("lparen", "(")] + rest[0]
            for ye in year_end:
                dates += [("comma", ",")] + ye
            toks = ([("func", "CALCULATE"), ("lparen", "(")] + args[0] + [("comma", ",")]
                    + dates + [("rparen", ")"), ("comma", ",")] + filt + [("rparen", ")")])
            sub = self._child(toks, row_filter=self.row_filter, inlining=self.inlining,
                              share_vars=True)
            try:
                sql = sub.parse()
            finally:
                self._absorb(sub)
                self.calc_columns |= sub.calc_columns
            self.i = end + 1
            self.used.append("engine.dax_period_total_filter")
            return sql
        inner: list = []
        for i, a in enumerate(rest):
            inner += ([("comma", ",")] if i else []) + a
        ds = self._date_set(head, inner, label=up)
        if self.slots is None:
            raise _TimeIntelligence()
        if self.window is not None:
            raise CompileError(f"{up} inside an ALL is not compiled")
        sub = self._child(args[0], row_filter=self.row_filter, inlining=self.inlining,
                          share_vars=True)
        sub.date_set = _DateChain.then(self.date_set, [ds])
        try:
            sql = sub.parse()
        finally:
            self._absorb(sub)
        self.i = end + 1
        return f"({sql})"


# The sibling modules' names, imported after this module's own definitions: the modules
# call each other, and a name is looked up only when a function runs.
from .expressions import (  # noqa: E402
    _AGGREGATE_CALL,
    _COMPARISONS,
    _CTX_MARK,
    _DATE_SETS,
    _DateChain,
    _DateSet,
    _ISO_DAY,
    _LOD_MARK,
    _LOD_OUTER,
    _NotSimple,
    _PERIOD_TOTALS,
    _REMOVERS,
    _SqlDate,
    _TimeIntelligence,
    _UnitContext,
    _apply,
    _blank_passes,
    _brace_end,
    _call_args,
    _call_head,
    _check_literal_args,
    _check_literal_values,
    _date_literal,
    _emit_for,
    _lod_refusal,
    _map_aggregates,
    _no_form_reason,
    _part_template,
    _reads_rows,
    _scalar_list,
    _split_top,
    _tokens,
    _top_colon,
    _year_end,
    dax_year_sql,
)
from .statements import (  # noqa: E402
    _DAX_AGGREGATES,
    _context_free,
    _ident,
    _literal,
    _literal_list,
)
from .joins import (  # noqa: E402
    _LIMIT,
    _bare,
    _card_ends,
    _expanded_tokens,
    _foldable,
    _keyed_date,
    _lookup_key,
    _mod,
    _sort_term,
    _spec_edge,
)
