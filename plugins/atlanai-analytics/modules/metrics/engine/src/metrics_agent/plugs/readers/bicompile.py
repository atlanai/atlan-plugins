# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The BI→SQL compiler the built-in readers share. Deterministic, rule-driven, and the primary
path at A3.

IT IS THE READERS', NOT THE CORE'S (AGENT_GUARDRAILS rule 22). A formula is written in a BI
tool's language, so turning it into SQL is the reader's job. The `tableau` and `powerbi`
readers both compile through this module (`FormulaCompiler`, at the bottom): A3 reaches it only
as the reader's `prepare` and `compile_unit` (`sockets.BIReader`), and a new reader brings its
own compiler or none. The core keeps only what every compiler shares: the rule-table format and
its gate (`lib/rules.py`), and the `Compiled` result (`sockets.Compiled`).

A3 used to be an LLM stage: the model read the widget's facts and wrote the SQL. That is
expensive, irreproducible, and — worst — its mistakes are invisible, because fluent SQL that
computes the wrong thing looks exactly like fluent SQL that computes the right thing.

THE INVERSION. The script writes the SQL; the model REVIEWS it. What the script knows lives in
`bi_compiler_rules.yml` as DATA, so a review pass can propose rows and the run can promote them
without anybody editing Python. A row is schema-checkable, diffable and reversible; a code patch
is none of those.

WHAT THIS FILE OWNS, and what it never will:

  owns    the SHAPE of the statement — which table is the source, which joins are needed to
          reach a grouping column, where each filter goes, what order things evaluate in.
  rules   the VOCABULARY — what `DIVIDE` becomes, what `COUNTD` becomes, which constructs have
          no SQL at all.

Shape is judgement and belongs in reviewed code. Vocabulary is a lookup table and belongs in
data. Confusing the two is how a compiler becomes a pile of string replacement.

TABLEAU'S ORDER OF OPERATIONS IS A CORRECTNESS TRAP, NOT A SYNTAX ONE. A context filter narrows
the population BEFORE a Top-N or an LOD is computed. Flattening it into the same WHERE produces
SQL that runs, passes the witness, and returns a different number — so context filters get their
own scope and the boundary is structural, not stylistic.

UNTRANSLATABLE IS A REAL ANSWER. A table calculation runs after the query on the result set; a
FIXED LOD computes at a grain the statement does not have. The compiler NAMES the construct and
declares the unit partial. A rule that pretended otherwise would be the worst kind of defect:
one that runs.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

from metrics_agent.lib import declared as _declared
from metrics_agent.lib import slugs
from metrics_agent.lib.card_sql import statement_sha  # noqa: F401 - the review's, re-exported
from metrics_agent.plugs.readers import tableau_params as _tparams
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

DATA = pathlib.Path(__file__).resolve().parent / "data"
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
def _strip_line_comments(text: str) -> str:
    """Drop `// …` to end of line outside string literals (Tableau and DAX both use it)."""
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
        elif text[i:i + 2] == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def tokenize(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    text = _strip_line_comments(text or "")
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
        # The columns of a Tableau calc's own table its data source declares dates (lower),
        # for a calc compiled without the statement's context (a calculated dimension).
        self.declared_dates: frozenset[str] = frozenset()
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
        sub.declared_dates = self.declared_dates
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
    _DAX_LEVELS = (("||",), ("&&",), ("=", "==", "<>", "!=", "<", ">", "<=", ">="), ("&",),
                   ("+", "-"), ("*", "/"), ("^",))

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
            inner = self._dax_unary()
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
                    col = self.ctx.renames.get((str(phys or "").lower(), col.lower()), col)
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
                              the dates of that set (`_compile_time_unit`).

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
            out["except"] = {(c, t or table) for c, t in cols}
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
            out["columns"].add(col)
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
        self.used.append("engine.dax_userelationship" if n == 2 else "engine.dax_crossfilter")

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
        if self._tableau and up == "DATETRUNC" and len(raw) > 1 and self._is_date_field(raw[1]) \
                and str(self.dialect or "").lower() != "sqlite":
            # Tableau reads a field as the type its data source declares. A custom SQL column
            # declared a date may hold the date as text (`substr(ts, 1, 10)`), which the
            # warehouse's own DATE_TRUNC does not take; on a true DATE the cast changes nothing.
            # SQLite has no DATE type: its date functions read the ISO text as it is.
            args = [args[0], f"CAST({args[1]} AS DATE)"] + args[2:]
            self.used.append("engine.tableau_declared_date")
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

    def _is_date_field(self, toks) -> bool:
        """A bare Tableau field its data source declares a date (not a date and time)."""
        if len(toks) != 1 or toks[0][0] != "col":
            return False
        col = _column_ref(toks[0][1])
        if self.ctx is None:
            return col.lower() in self.declared_dates
        table = str((self.home[1] if self.home else self.ctx.source) or "")
        return self.ctx.is_date(table, col) or self.ctx.is_date(_bare(table), col)

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
        where = []
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
        if where:
            self.used.append("engine.dax_onevalue_table_filters")
        phys = next((v for k, v in (self.home[0] if self.home else {}).items()
                     if _bare(v) == _bare(table)), table)
        cond = ("\n WHERE " + " AND ".join(where)) if where else ""
        return (f"((SELECT COUNT(DISTINCT h.{col}) + MAX(CASE WHEN h.{col} IS NULL THEN 1 "
                f"ELSE 0 END) FROM {phys} h{cond}) = 1)")

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
      between    two literal dates."""

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

    @property
    def key(self) -> tuple:
        return (self.kind, self.column, self.period, self.year_end, self.part, self.n,
                self.months, self.previous, self.lo, self.hi, self.anchor, self.anchor_fact,
                self.open_end)


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


def _date_literal(toks) -> str:
    """`DATE(2025, 1, 31)` -> '2025-01-31'."""
    head, inner = _call_head(toks)
    parts = _split_top(inner) if head == "DATE" else []
    if len(parts) != 3 or any(len(x) != 1 or x[0][0] != "num" for x in parts):
        raise CompileError("a date bound is compiled as DATE(year, month, day) of literals")
    import datetime as _dt  # noqa: PLC0415 - only time intelligence needs it
    try:
        return _dt.date(*(int(x[0][1]) for x in parts)).isoformat()
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
        out, frontier = {_bare(table)}, [_bare(table)]
        while frontier:
            cur = frontier.pop()
            for e in self.graph:
                if e.get("is_active") is False or str(e.get("kind") or "") in _NOT_WALKED:
                    continue
                fs, ts = _card_ends(e.get("cardinality"))
                a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
                both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
                for near, far, far_end in ((a, b, ts), (b, a, fs)):
                    if near == cur and (far_end == "one" or both) and far not in out:
                        out.add(far)
                        frontier.append(far)
        return out

    def place(self, column: str, table: str) -> str | None:
        if self.plan is None:
            return None
        return self.plan.measure_at.get((str(column).lower(), _bare(table)))

    def is_text(self, table: str, column: str) -> bool:
        t = self.types.get((str(table).lower(), str(column).lower())) or ""
        return bool(re.match(r"(?i)^(string|text|varchar|char|nvarchar|nchar)\b", t))

    def is_date(self, table: str, column: str) -> bool:
        t = self.types.get((str(table).lower(), str(column).lower())) or ""
        return t.strip().lower() == "date"


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
    return tokenize(formula)


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


def _measure_alias(name: str) -> str:
    """A measure's column alias: `slugs.column_alias` (a symbol kept as a word)."""
    return slugs.column_alias(name)


def _native_colmap(columns: dict) -> dict[str, str]:
    """A native-query table's columns, by lower-cased name: each model caption, and each of the
    query's own output columns under its own name.

    A2 names a column by its warehouse name once a Power Query rename is reversed (`tool_type`
    for the caption `Tool Type`). For a native-query table that name is the query's output
    column, so it must resolve as surely as the caption does; matching captions alone refused
    22 of 53 statements on a live model. Where the two spellings collide, the caption wins."""
    colmap = {k.lower(): v for k, v in columns.items()}
    for v in columns.values():
        colmap.setdefault(str(v).lower(), v)
    return colmap


def _compile_unit(unit: dict, rules, dialect: str, *, alias: str = "t",
                 columns: dict[str, set[str]] | None = None,
                 measures: dict[str, str] | None = None,
                 table_of: dict[str, str] | None = None,
                 logical: dict[str, str] | None = None,
                 model: "_declared.Model | None" = None,
                 model_graph: list[dict] | None = None,
                 types: dict[tuple[str, str], str] | None = None,
                 row_formulas: set[str] | None = None, text_key: bool | str = False,
                 renames: dict[tuple[str, str], str] | None = None) -> Result:
    """One question unit -> one statement, or a named reason why not.

    `table_sql` is each table the reader gave a statement of its own (`source_lineage.
    statement`: the rows the BI tool's query steps leave), by its name: the statement reads it
    as that statement wherever it reads the table, joins included. `renames` maps (warehouse
    table, bare, lower; model column, lower) to the column the table reads, where a formula
    names a column by another name than the table's.

    `model_graph` is every relationship of the model, on warehouse tables: a measure may read
    a column of a table the unit itself does not (a CALCULATE filter on a dimension), and the
    unit's own graph holds only the edges its grouping and filters need. `types` is each
    column's data type by (model table, column), lower; `row_formulas` the keys of `measures`
    that are calculated columns (row expressions) rather than measures. `text_key`: the model
    compares text without case and without a column's trailing spaces, so a filter on text
    values does too.

    `columns` is the resolved warehouse schema per table, which the join planner needs to know
    WHERE a grouping column lives. Passed in rather than read off the unit because a
    regression case must be able to pin it: a case whose join plan depends on a schema it does
    not carry would pass or fail for reasons invisible in the case file.

    `table_of` maps a model table name (lower) to the warehouse table it reads. With it, a
    measure that references a column of a DIFFERENT warehouse table is refused rather than
    bound to this statement's alias; without it, behaviour is unchanged.

    `model` is the BI model a `formula_language: sql` measure is placed in (lib/declared): its
    roles, the names its formulas use and its relationships. A regression case states the
    same as `logical` (role -> warehouse table) beside the unit's join graph. No other
    language reads either.
    """
    columns = {k: {c.lower() for c in v} for k, v in (columns or {}).items()}
    # `text_key="source"`: the unit's rows are read from the source on every query
    # (DirectQuery), which compares and groups text itself; nothing is folded, not even a
    # DAX filter's literal.
    text_key_in = text_key
    source_compared = text_key == "source"
    text_key = bool(text_key) and not source_compared
    res = Result()
    tables = unit.get("warehouse_tables") or []
    if not tables:
        res.reason = "no warehouse table resolved for this unit"
        return res
    # A2 orders warehouse_tables measure-home first, so [0] is the fact table the aggregates
    # actually read. Sorting alphabetically here would pick a dimension table and compute the
    # measure against the wrong grain.
    source = tables[0]

    # -- a NATIVE-QUERY table is the query's RESULT SET, not the physical table in its FROM.
    # Its columns are the query's own output (often derived: `period`, `weeks`, a CASE tier),
    # and several logical tables can read the same physical object through different
    # queries. Compiling against the physical table produced `invalid identifier` on 8 of 9
    # witnessed statements on a live model, and refused most of the rest as "not on" the
    # table. So the statement reads the query itself, as a derived table, and every column is
    # the query's output column rather than the model caption.
    native = unit.get("native_source") or None
    # The language a calculated field this unit names as a column, a filter or its sort is
    # written in, when the reader says the unit may name one. Decided from the data the
    # reader wrote, never from which tool the unit came from.
    calc_language = unit.get("expression_language") or None
    colmap = None
    if native:
        colmap = _native_colmap(native.get("columns") or {})
        # A calc used as a grouping column is compiled from its formula over the query's
        # columns, so it is not "missing" from the query.
        missing = [g for g in unit.get("group_by", []) if g.lower() not in colmap
                   and not (calc_language and g.lower() in (measures or {}))]
        if missing:
            res.reason = (f"grouping column(s) {sorted(missing)} are not columns of the native "
                          f"query behind {native.get('logical')!r}, and a join across two "
                          f"native queries is not planned")
            return res
        source = "(\n" + native["sql"].strip().rstrip(";") + "\n)"
        joins, plan = [], _JoinPlan(alias)
        graph, userel = [], set()
    else:
        # -- a BLEND: measures or filters of a table only a `blend` edge reaches are a
        # statement of their own, aggregated over the links the view groups by and joined to
        # the rows of the rest of the unit after aggregation.
        blended = _blend_parts(unit, source, columns)
        if blended is not None:
            def again(u: dict) -> Result:
                return _compile_unit(u, rules, dialect, alias=alias, columns=columns,
                                     measures=measures, table_of=table_of, logical=logical,
                                     model=model, model_graph=model_graph, types=types,
                                     row_formulas=row_formulas, text_key=text_key_in,
                                     renames=renames)
            return _blend_statement(unit, blended, again, rules, dialect)
        # -- joins: a grouping column that does not live on the source needs one, and the
        # unit's join_graph is the evidence for it. Refusing outright (as the old draft did)
        # was correct only while that evidence did not reach here.
        def again(u: dict) -> Result:
            return _compile_unit(u, rules, dialect, alias=alias, columns=columns,
                                 measures=measures, table_of=table_of, logical=logical,
                                 model=model, model_graph=model_graph, types=types,
                                 row_formulas=row_formulas, text_key=text_key_in,
                                 renames=renames)
        # MEASURES THAT ITERATE MEMBERS (a table's rows, a column's values) and evaluate an
        # expression over other rows per member: each is a statement at the members' grain,
        # aggregated again, joined to the rest on the visual's rows.
        grains, ranks, treatas = {}, {}, {}
        if measures and table_of and not unit.get("top_n"):
            # A measure that is a scalar expression OVER such forms (IF(ISBLANK([S]),
            # BLANK(), RANKX(..)), DIVIDE(COUNTROWS(FILTER(VALUES(k), ..)), ..)): each part is
            # a measure of its own, and the expression is computed over their values per row.
            done = _composite_statement(unit, measures, table_of, list(model_graph or [])
                                        + list(unit.get("join_graph") or []),
                                        set(row_formulas or ()), again, rules, dialect)
            if done is not None:
                return done
            for m in unit.get("measures") or []:
                if m.get("formula_language") == "dax" and m.get("formula"):
                    rk = (_rank_form(m["formula"], measures, table_of, set(row_formulas or ()),
                                     graph=list(model_graph or [])
                                     + list(unit.get("join_graph") or []))
                          or _window_form(m["formula"], measures, table_of,
                                          set(row_formulas or ())))
                    if rk:
                        ranks[m["name"]] = rk
                        continue
                    g = _member_grain(m["formula"], measures, table_of,
                                      list(model_graph or []) + list(unit.get("join_graph")
                                                                     or []),
                                      set(row_formulas or ()), columns=columns)
                    if g:
                        grains[m["name"]] = g
                        continue
                    tr = _treatas_form(m["formula"], measures, table_of,
                                       list(model_graph or [])
                                       + list(unit.get("join_graph") or []))
                    if tr:
                        treatas[m["name"]] = tr
        if grains or ranks or treatas:
            rest = [m for m in unit.get("measures") or [] if m["name"] not in grains
                    and m["name"] not in ranks and m["name"] not in treatas]
            parts = []
            if rest:
                sub = dict(unit, measures=rest)
                parts.append((sub, again(sub)))
            for m in unit.get("measures") or []:
                if m["name"] in grains:
                    parts.append((dict(unit, measures=[m]), _member_grain_statement(
                        unit, m, grains[m["name"]], rules, dialect, again, table_of,
                        measures)))
                elif m["name"] in ranks and ranks[m["name"]].get("fn"):
                    parts.append((dict(unit, measures=[m]), _window_statement(
                        unit, m, ranks[m["name"]], rules, dialect, again, measures)))
                elif m["name"] in ranks:
                    parts.append((dict(unit, measures=[m]), _rank_statement(
                        unit, m, ranks[m["name"]], rules, dialect, again)))
                elif m["name"] in treatas:
                    parts.append((dict(unit, measures=[m]), _treatas_statement(
                        unit, m, treatas[m["name"]], again)))
            return _joined_statements(unit, parts, rules, dialect)
        homes = _measure_homes(unit, source, measures, table_of)
        if homes and not unit.get("top_n"):
            # Measures of different tables (two facts beside one dimension): each table's
            # measures are a statement of their own over that table, joined on the rows.
            by_home: dict[str, list[dict]] = {}
            for m in unit.get("measures") or []:
                by_home.setdefault(homes.get(m["name"], source), []).append(m)
            parts = []
            for home, ms in by_home.items():
                sub = dict(unit, measures=ms, warehouse_tables=[home] + [
                    t for t in tables if t != home])
                parts.append((sub, _compile_unit(
                    sub, rules, dialect, alias=alias, columns=columns, measures=measures,
                    table_of=table_of, logical=logical, model=model, model_graph=model_graph,
                    types=types, row_formulas=row_formulas, text_key=text_key_in,
                    renames=renames)))
            return _joined_statements(unit, parts, rules, dialect)
        graph, extra, userel, split = _measure_graph(unit, source, measures, table_of,
                                                     model_graph)
        if calc_language and measures and table_of:
            # A CALCULATED COLUMN the visual groups or filters by is a row expression; one that
            # reads a related table's column (RELATED) reads it through the relationship, so
            # that column is planned like a measure's.
            tm = {k.lower(): v for k, v in table_of.items()}
            gbt0 = list(unit.get("group_by_tables") or [])
            named = [(g, gbt0[i] if i < len(gbt0) else None)
                     for i, g in enumerate(unit.get("group_by") or [])]
            named += [(f["column"], f.get("table")) for f in unit.get("filters") or []
                      if f.get("column")]
            for col, hint in named:
                key = (f"{_bare(hint)}[{str(col).lower()}]" if hint else str(col).lower())
                formula = measures.get(key) or measures.get(str(col).lower())
                if not formula:
                    continue
                for k, v in _expanded_tokens(formula, measures):
                    t = _table_ref(v) if k in ("qualified", "quoted_qualified") else None
                    phys = tm.get(str(t or "").lower())
                    if phys and _bare(phys) not in (_bare(source), _bare(hint)):
                        req = (_column_ref(v).lower(), phys)
                        if req not in extra:
                            extra.append(req)
        if split:
            # The measures join the tables along different relationships: one statement per
            # set of measures that agree, joined on the visual's rows.
            groups: dict = {}
            tm = {k.lower(): v for k, v in (table_of or {}).items()}
            for m in unit.get("measures") or []:
                key = _relationship_spec(m, measures, tm, _bare(source), []) or frozenset()
                groups.setdefault(key, []).append(m)
            if unit.get("top_n"):
                res.coverage = REFUSED
                res.reason = ("a Top N over measures that join the tables along different "
                              "relationships: the ranking reads one statement's rows")
                return res
            parts = []
            for ms in groups.values():
                sub = dict(unit, measures=ms)
                parts.append((sub, _compile_unit(
                    sub, rules, dialect, alias=alias, columns=columns, measures=measures,
                    table_of=table_of, logical=logical, model=model, model_graph=model_graph,
                    types=types, row_formulas=row_formulas, text_key=text_key_in,
                    renames=renames)))
            return _joined_statements(unit, parts, rules, dialect)
        # A level of detail over a related table's field: its own statement joins that table
        # through the same relationships, so the plan places the field as it places one the
        # view filters by (a member of a related table, never a measure's repeated rows).
        lod_fields = _lod_related_columns(unit, source, columns, measures, graph)
        plan = _plan_joins(unit, source, alias, columns, extra=extra, graph=graph,
                           fields=lod_fields, whole_model=model_graph is not None)
        if plan.detached and not plan.unreachable:
            return _detached_statement(unit, plan, graph, lambda u: _compile_unit(
                u, rules, dialect, alias=alias, columns=columns, measures=measures,
                table_of=table_of, logical=logical, model=model, model_graph=model_graph,
                types=types, row_formulas=row_formulas, text_key=text_key_in, renames=renames))
        if plan.unreachable:
            gb = {str(g).lower() for g in unit.get("group_by") or []}
            kind = ("grouping column(s)" if plan.unreachable <= gb else "column(s)")
            res.reason = (f"{kind} {sorted(plan.unreachable)} are not on {source} and "
                          f"no declared relationship reaches them"
                          + "".join(f"; {w}" for w in plan.reasons))
            return res
        joins = list(plan.clauses)

    # A SQL MEASURE IS PLACED, NEVER TRANSLATED. It is already SQL in the declared dialect, so
    # no rule applies; the engine still owns the statement's SHAPE. Each table qualifier in it
    # is a ROLE of the model: the home role is this statement's alias, and every other role
    # gets its own alias and its own LEFT JOIN along declared relationships that never fan out,
    # on every key column. Names the formula uses (facts, other metrics) are expanded first.
    placed: dict[str, str] = {}
    sql_measures = [m for m in unit.get("measures", [])
                    if m.get("formula_language") == "sql" and m.get("formula")]
    if sql_measures and not native:
        if model is None and logical:
            model = _declared.Model.from_graph(logical, unit.get("join_graph") or [])
        named = sorted({q for m in sql_measures for q in _declared.qualifiers(m["formula"])})
        if model is None and named:
            res.reason = (f"SQL measure reads table qualifier(s) {named} but the model's "
                          f"logical tables were not supplied")
            return res
        if model is not None:
            homes = {str(m.get("home_table") or "").lower() or model.role_of(source)
                     for m in sql_measures}
            if len(homes) != 1 or None in homes:
                res.reason = (f"SQL measures over {sorted(str(h) for h in homes)}: one "
                              f"statement is computed over one table of the model")
                return res
            try:
                exprs, extra, _names = _declared.place_in_statement(
                    model, homes.pop(), [m["formula"] for m in sql_measures], alias,
                    len(joins))
            except _declared.NotPlaced as exc:
                res.reason = f"SQL measure {exc}"
                return res
            placed = {m["name"]: e for m, e in zip(sql_measures, exprs)}
            joins = joins + extra

    # A CALCULATED FIELD used as a grouping column or a filter is an EXPRESSION, not a
    # column: compiled from its formula like a measure, never emitted as `t.<caption>`. Only
    # for a unit whose reader declared its `expression_language`; over a native statement the
    # calc reads the statement's output columns.
    calc_sql: dict[str, str] = {}
    calc_names: set[str] = set()
    # The level-of-detail expressions any formula of this statement registers (`_lod`).
    lod_reg: list[dict] = []

    def _calc(name: str, table: str | None = None) -> str | None:
        key = str(name).lower()
        if not calc_language:
            return None
        formula_key = key
        if table and f"{_bare(table)}[{key}]" in (measures or {}):
            formula_key = f"{_bare(table)}[{key}]"
        elif table:
            formula_key = next((k for k in (measures or {})
                                if k == f"{str(table).lower()}[{key}]"), key)
        if formula_key not in (measures or {}):
            return None
        if colmap is not None and key in colmap:
            return None
        # Read on the table the column is on: the statement's own, or a joined one.
        on = plan.at(name, table) if colmap is None else alias
        if (formula_key, on) not in calc_sql:
            p = _Parser(_tokens(measures[formula_key], calc_language), rules, calc_language,
                        on, dialect, colmap, measures, None, (key, formula_key))
            p.lods = lod_reg
            if calc_language == "tableau-calc":
                home = {str(table or tables[0]).lower(), _bare(str(table or tables[0]))}
                p.declared_dates = frozenset(c for (t, c), ty in (types or {}).items()
                                             if t in home and str(ty).strip().lower() == "date")
            if calc_language == "dax" and table_of and colmap is None:
                # A calculated column is evaluated on each row of its table (a row context):
                # RELATED reads the related row, through the statement's join.
                cctx = _UnitContext(source)
                cctx.plan, cctx.unplaced = plan, plan.unplaced
                cctx.types = dict(types or {})
                p.ctx, p.row_level = cctx, True
                p.home = ({k.lower(): v for k, v in table_of.items()},
                          str(table) if table and on != alias else source)
            sql = p.parse()
            if on != alias and plan.keys.get(on):
                # A calculated column of a JOINED table is evaluated on that table's rows; a
                # fact row the join does not match has no such row, and its value is the
                # blank member's: NULL, not the formula over NULL columns.
                sql = f"CASE WHEN {on}.{plan.keys[on][0]} IS NULL THEN NULL ELSE ({sql}) END"
            calc_sql[(formula_key, on)] = f"({sql})"
            res.rules_used += p.used
            res.provisional_rules += p.provisional
            res.unknown += p.unknown
        calc_names.add(key)
        return calc_sql[(formula_key, on)]

    gb_tables = list(unit.get("group_by_tables") or [])

    def _gcol(g: str, i: int) -> str:
        hint = gb_tables[i] if i < len(gb_tables) else None
        if colmap is not None:
            if g.lower() not in colmap:
                return _calc(g) or f"{alias}.{_ident(g)}"
            return f"{alias}.{_ident(colmap[g.lower()])}"
        return _calc(g, hint) or f"{plan.at(g, hint)}.{g}"

    # TEXT GROUPED AS THE MODEL HOLDS IT. A model that compares text without case and
    # without trailing spaces (`text_key`) keeps ONE value for 'Retail', 'retail' and 'Retail '
    # (seen live): such a column is grouped by its folded key and shows one spelling of it.
    # Which spelling the model keeps is the first it loaded, which no statement can know; the
    # statement shows the least (MIN), and a reader compares the members by their key.
    inv_table = {_bare(v): str(k).lower() for k, v in (table_of or {}).items()}
    folded: set[int] = set()
    # The source groups the text (DirectQuery) and keeps 'Retail' and 'retail' apart, but the
    # model shows ONE spelling per value that differs only in case (seen live, DirectQuery
    # and Dual): the least of the spellings equal without case.
    cased: set[int] = set()
    if (text_key or source_compared) and colmap is None:
        in_branches = {al for br in plan.branches.values() for al in br["aliases"]}
        for i, g in enumerate(unit.get("group_by", [])):
            hint = gb_tables[i] if i < len(gb_tables) else None
            at = plan.at(g, hint)
            if at in in_branches:
                continue
            wt = plan.table_at.get(at) or _bare(source)
            ty = (types or {}).get((inv_table.get(wt, wt), str(g).lower())) or ""
            if re.match(r"(?i)^(string|text|varchar|char|nvarchar|nchar)\b", ty):
                (cased if source_compared else folded).add(i)

    def _gkey(g: str, i: int) -> str:
        """The grouping expression: the column, or its folded key (`folded`)."""
        return _Parser._text_key(_gcol(g, i)) if i in folded else _gcol(g, i)

    select: list[str] = []
    for i, g in enumerate(unit.get("group_by", [])):
        try:
            expr = _gcol(g, i)
        except CompileError as exc:
            res.reason = f"grouping calc {g!r}: {exc}"
            return res
        if i in folded:
            select.append(f"MIN({expr}) AS "
                          f"{_measure_alias(g) if g.lower() in calc_names else g}")
            res.rules_used.append("engine.text_key_group")
            continue
        if i in cased:
            select.append(f"MIN({expr}) OVER (PARTITION BY UPPER({expr})) AS "
                          f"{_measure_alias(g) if g.lower() in calc_names else g}")
            res.rules_used.append("engine.text_case_label")
            continue
        labels = (unit.get("member_labels") or {}).get(str(g).lower())
        if labels:
            # The view SHOWS a label for some values (aliases): the row is still the stored
            # value's, under its label.
            select.append(_labelled(expr, labels, dialect) + " AS "
                          + (_measure_alias(g) if g.lower() in calc_names else g))
            res.rules_used.append("engine.member_labels")
            continue
        select.append(f"{expr} AS {_measure_alias(g)}" if g.lower() in calc_names else expr)

    ctx = _UnitContext(tables[0])
    ctx.lods = lod_reg
    ctx.plan = plan if not native else None
    ctx.unplaced = plan.unplaced
    ctx.top_n = unit.get("top_n")
    ctx.measure_filtered = any(str(f.get("stage") or "") == "measure" and f.get("measure")
                               for f in unit.get("filters") or [])
    ctx.types = dict(types or {})
    ctx.row_formulas = set(row_formulas or ())
    ctx.relationships_used = userel
    ctx.text_key = text_key
    ctx.source_compared = source_compared
    ctx.renames = dict(renames or {})
    for i, g in enumerate(unit.get("group_by", [])):
        hint = gb_tables[i] if i < len(gb_tables) else None
        at = plan.at(g, hint) if not native else alias
        ctx.groups.append((str(g).lower(), plan.table_at.get(at), _gkey(g, i)))
    for f in unit.get("filters") or []:
        if f.get("column") and _filter_reads_column(f):
            at = plan.at(f["column"], f.get("table")) if not native else alias
            ctx.filters.append((str(f["column"]).lower(), plan.table_at.get(at)))
            ctx.filter_values.append((str(f["column"]).lower(), plan.table_at.get(at),
                                      str(f.get("op") or ""), f.get("value")))
            if str(f.get("op") or "").lower() == "top_n" and isinstance(f.get("value"), dict) \
                    and str(f["value"].get("population") or "") == "filtered":
                ctx.top_n_filters.append((str(f["column"]).lower(), plan.table_at.get(at)))
    if not native:
        ctx.date_keys = _date_keys(graph, tables[0], ctx.types)
        ctx.graph = graph

    failed: list[str] = []
    ranked_by = None        # the first measure that compiled: what a Top-N ranks by
    alias_of: dict[str, str] = {}   # compiled expression -> its select alias
    for m in unit.get("measures", []):
        if m["name"] in placed:
            select.append(f"{placed[m['name']]} AS {_measure_alias(m['name'])}")
            alias_of.setdefault(placed[m["name"]], _measure_alias(m["name"]))
            ranked_by = ranked_by or _measure_alias(m["name"])
            continue
        seen_gaps, seen_unknown = len(res.untranslatable), len(res.unknown)
        elsewhere = _measure_elsewhere(m, unit, source, columns) if not native else None
        if elsewhere:
            # A shelf aggregation of a column of ANOTHER table: read off this statement's
            # alias it aggregates nothing, or a same-named column of the wrong table.
            failed.append(f"{m.get('name')!r}: {elsewhere}")
            res.untranslatable.append({"construct": "measure of another table",
                                       "in_measure": m.get("name") or "", "why": elsewhere})
            continue
        try:
            expr = _compile_measure(m, rules, alias, dialect, res, colmap, measures,
                                    unit, home=(table_of, source) if table_of and not native
                                    else None, ctx=ctx)
        except _TimeIntelligence:
            if plan.branches:
                res.coverage = REFUSED
                res.reason = ("time intelligence over a many-to-many (or both-ways) path: "
                              "one statement per date set does not carry its distinct members")
                return res
            return _compile_time_unit(unit, rules, dialect, alias=alias, columns=columns,
                                      measures=measures, table_of=table_of, source=source,
                                      plan=plan, joins=joins, ctx=ctx, gcol=_gcol,
                                      calc=_calc, native=native)
        except CompileError as exc:
            # One measure the compiler cannot do must not cost the ones it can. A unit with
            # four measures and one table calculation is three answers plus a declared gap,
            # not zero answers.
            failed.append(f"{m.get('name')!r}: {exc}")
            # THE GAP IS NAMED ON THE RESULT, whatever stopped the measure. Only a declared
            # construct used to leave an `untranslatable` entry; an unknown function or a
            # field that is not a column left none, and a partial unit reached its candidate
            # with nothing saying what it lacks.
            new = res.untranslatable[seen_gaps:]
            for gap in new:
                gap.setdefault("in_measure", m.get("name") or "")
            if not new:
                res.untranslatable.append({
                    "construct": ", ".join(dict.fromkeys(res.unknown[seen_unknown:]))
                                 or "measure",
                    "in_measure": m.get("name") or "", "why": str(exc)})
            continue
        select.append(f"{expr} AS {_measure_alias(m['name'])}")
        alias_of.setdefault(expr, _measure_alias(m["name"]))
        ranked_by = ranked_by or _measure_alias(m["name"])

    # EVERY value the visual shows ignores the same visual filters (each measure is wholly a
    # CALCULATE that replaces or removes them, keeping the visual's grouping): those filters
    # then choose no row of any value, and the statement is the unit without them, every
    # measure compiled over its rows as it stands.
    drops = {tuple(sorted(map(str, a["drop"]))) for a in ctx.context_aggs if a.get("drop")}
    if (len(drops) == 1 and not failed and all(a.get("drop") for a in ctx.context_aggs)
            and all(len(a["keep"]) == len(ctx.groups) for a in ctx.context_aggs)
            and all(not re.search(rf"\b{re.escape(alias)}\.|\bj\d+\.|\bOVER\b",
                                  re.sub(r"__CTXAGG\d+__", "", x.rsplit(" AS ", 1)[0]))
                    for x in select[len(unit.get("group_by") or []):])):
        gone = {(str(c).lower(), t) for c, t in ctx.context_aggs[0]["drop"]}
        grouped = {c for c, _t, _s in ctx.groups}

        def _gone(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table"))) if not native else None
            return col not in grouped and ((col, at) in gone or (col, None) in gone)
        kept = [f for f in unit.get("filters") or [] if not _gone(f)]
        if len(kept) < len(unit.get("filters") or []):
            return _compile_unit(dict(unit, filters=kept), rules, dialect, alias=alias,
                                 columns=columns, measures=measures, table_of=table_of,
                                 logical=logical, model=model, model_graph=model_graph,
                                 types=types, row_formulas=row_formulas, text_key=text_key_in,
                                 renames=renames)

    shown = _rows_not_on_source(ctx)
    # A measure NOT BLANK on a row with no rows of the statement's table (a + 0, COALESCE, an
    # IF with an else): the visual then lists every member of the columns it groups by, also
    # those with no rows, with the measure's value on an empty row there.
    on_empty: dict[str, str] = {}
    on_empty_ctx: dict[str, tuple[str, list[dict]]] = {}
    if shown and not native:
        for m in unit.get("measures", []):
            if m.get("formula_language") != "dax" or not m.get("formula") \
                    or m["name"] in placed or any(f.startswith(f"{m.get('name')!r}:")
                                                  for f in failed):
                continue
            verdict = _blank_on_empty(m, rules, alias, dialect, measures, unit, table_of,
                                      source, ctx)
            empty = (_empty_value(m, rules, alias, dialect, measures, unit, table_of, source,
                                  ctx) if verdict is not True else None)
            mine = next((x for x in select if x.endswith(f" AS {_measure_alias(m['name'])}")), "")
            # A Top N FILTER (the members a ranking keeps) narrows the members listed, as
            # any filter on the column's table does; one kept by the statement's LIMIT is not.
            limit_top = bool(unit.get("top_n")) and not ctx.top_n_filters
            if verdict is not True and empty is not None and unit.get("group_by") \
                    and not limit_top and not re.search(r"\bOVER\b|__CTXAGG", mine):
                on_empty[_measure_alias(m["name"])] = empty
                continue
            wider = (_empty_context_value(m, rules, alias, dialect, measures, unit, table_of,
                                          source, ctx)
                     if verdict is not True and unit.get("group_by")
                     and not limit_top else None)
            if wider is not None:
                # Not blank on an empty row because it reads a wider context (a total over
                # ALL, an ALLEXCEPT that removes a dimension's filter): seen live, the
                # visual lists every member, with that context's value there.
                on_empty_ctx[_measure_alias(m["name"])] = wider
                continue
            if verdict is not True:
                res.coverage = REFUSED
                res.untranslatable.append({
                    "construct": "measure not blank on an empty row", "in_measure": m["name"],
                    "why": "the visual then shows rows the statement does not have"})
                res.reason = (
                    f"measure {m['name']!r} is "
                    + ("not BLANK" if verdict is False else "not shown to be BLANK")
                    + f" on a row of the visual with no rows of {source} (a + 0, COALESCE, an "
                    f"IF with an else, a total over ALL), so the visual shows every member of "
                    f"{', '.join(shown)} it groups or filters by, also those with no rows; "
                    f"a statement over {source}'s rows does not have them")
                return res

    if failed and not any(" AS " in x for x in select):
        # Nothing was translated. PARTIAL means "a statement, with a named gap"; with no
        # statement there is nothing partial about it, and calling it partial would let a
        # unit with no SQL look like one that half-worked.
        res.coverage = REFUSED
        res.reason = "; ".join(f"measure {f}" for f in failed)
        return res
    if failed:
        res.reason = "; ".join(f"measure {f}" for f in failed)

    if not select:
        res.reason = "nothing to select: the unit has neither a measure nor a grouping column"
        return res

    special: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                                "display": []}
    # The language a Top N's ranking measure and a measure filter are read in: the widget's,
    # else its measures'.
    measure_language = calc_language or next(
        (x.get("formula_language") for x in unit.get("measures") or []
         if x.get("formula_language")), None)
    try:
        ctx_preds, dim_preds, deferred = _plan_filters(unit, rules, alias, res, colmap,
                                                       resolve=_calc, dialect=dialect,
                                                       place=plan.at, text_key=text_key,
                                                       special=special)
    except CompileError as exc:
        res.reason = f"filter calc: {exc}"
        return res
    if deferred:
        # A filter the compiler cannot express must NOT be silently omitted. The widget
        # applies it, so a statement without it computes a different number — and unlike a
        # syntax error, that failure ships. The reason distinguishes the two causes: "no rule
        # for this operator" and "this operator cannot take a list" need different fixes, and
        # a message that conflates them sends the reviewer after the wrong one.
        named = ("multi-value", "not-a-column", "no-value", "measure-stage", "member-filter",
                 "relative-date")
        no_rule = sorted({str(f.get("op")) for f in deferred if f.get("_why") not in named})
        multi = sorted({str(f.get("op")) for f in deferred if f.get("_why") == "multi-value"})
        not_col = sorted({str(f.get("column")) for f in deferred
                          if f.get("_why") == "not-a-column"})
        no_value = sorted({str(f.get("column")) for f in deferred
                           if f.get("_why") == "no-value"})
        after_agg = sorted({str(f.get("column")) for f in deferred
                            if f.get("_why") == "measure-stage"})
        member = sorted({str(f.get("column")) for f in deferred
                         if f.get("_why") == "member-filter"})
        parts = [f"{f.get('_detail')} (on {f.get('column')!r})" for f in deferred
                 if f.get("_why") == "relative-date"]
        if member:
            parts.append(f"filter(s) on {member} keep the members that pass a Top N or a "
                         f"condition, which this statement does not compute")
        if not_col:
            parts.append(f"filter column(s) {not_col} are not columns of the native query")
        if no_value:
            parts.append(f"filter(s) on {no_value} carry no value the BI file recorded")
        if after_agg:
            parts.append(f"measure-stage filter(s) on {after_agg} apply after aggregation "
                         "and are not compiled")
        if no_rule:
            parts.append(f"no operator rule for {', '.join(no_rule)}")
        if multi:
            parts.append(f"{', '.join(multi)} received a multi-value filter and only a "
                         f"membership operator can express one")
        res.coverage = REFUSED
        res.reason = ("; ".join(parts)
                      + f" on {sorted({f['column'] for f in deferred})}; the filter would "
                        f"have been dropped, which changes the number")
        return res

    # DATA SOURCE filters apply to every row before any context filter, Top N or condition:
    # the innermost tier, and context filters next. One on a joined table's column keeps the
    # statement's own rows whose related row passes it: a semi-join (EXISTS over the joins
    # that reach that column) in the population every later step reads. Alone, a data
    # source filter on a joined column is read after the join, the same rows.
    ds_joined = [p for p in special["datasource"] if re.search(r"\bj\d+\.", p)]
    ctx_joined = [p for p in ctx_preds if re.search(r"\bj\d+\.", p)]
    early_joined = []
    if ctx_joined or (ds_joined and (ctx_preds or special["members"] or unit.get("top_n"))):
        try:
            early_joined = [_semi_join(p, joins, alias) for p in ds_joined + ctx_joined]
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"a data source or context filter on a column of a joined table: "
                          f"{exc}")
            return res
        res.rules_used.append("engine.filter_semi_join")
        ctx_preds = [p for p in ctx_preds if p not in ctx_joined]
        # Read on the joined rows too: a related table's own rows with no row of the
        # statement's table (an outer join) are kept only where they pass it.
        ds_joined = ds_joined + ctx_joined
    dim_preds = ds_joined + dim_preds
    try:
        dim_preds = _branch_split(dim_preds, plan)
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res
    base_preds = [p for p in special["datasource"] if p not in ds_joined] + ctx_preds
    src = source
    if early_joined:
        # The semi-joins read the statement's own row by its alias.
        inner = "\n       AND ".join(base_preds + early_joined)
        src = (f"(\n    SELECT {alias}.*\n    FROM   {source} {alias}\n"
               f"    WHERE  {inner}\n  )")
    elif base_preds:
        # Data source and context filters narrow the population BEFORE anything downstream
        # is computed. The boundary is structural: flattening it into the outer WHERE
        # produces a statement that runs and returns a different number.
        inner = "\n       AND ".join(_strip_alias(p, alias) for p in base_preds)
        src = f"(\n    SELECT *\n    FROM   {source}\n    WHERE  {inner}\n  )"
    try:
        member_preds, top_done = _member_filters(
            special["members"], unit, rules, alias, dialect, res, src=src, plan=plan,
            measures=measures, calc=_calc, calc_language=calc_language or measure_language,
            dim_preds=dim_preds, joins=joins,
            # Beside an aggregate over a level of detail the statement is re-aggregated, and
            # beside a table calculation the window runs over the kept members only (a Top N
            # keeps them BEFORE table calculations run): either way a Top N is its members,
            # never the outer statement's LIMIT (a LIMIT after the window would compute it
            # over every member).
            limit_ok=not any(r["mode"] == "grain" for r in lod_reg)
            and not any(" OVER (" in x for x in select),
            compile_by=lambda formula: _compile_measure(
                {"name": "top_n_by", "formula": formula,
                 "formula_language": measure_language}, rules, alias, dialect, res, colmap,
                measures, unit))
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res

    if unit.get("top_n") and not top_done and any(" OVER (" in x for x in select):
        # A Top N the member filters did not read (no filter names it) beside a table
        # calculation: the window would run over every member and the LIMIT keep some.
        res.coverage = REFUSED
        res.reason = (f"a table calculation beside a Top {int(unit['top_n'])} that no filter "
                      f"names: the calculation runs over the kept members only")
        return res
    lod_sub: dict[str, str] = {}
    if lod_reg:
        try:
            lod_joins, lod_sub = _lod_parts(lod_reg, src, alias, rules, dialect,
                                            columns.get(source) if not native else None,
                                            [p for p in dim_preds if not _agg_call(p)],
                                            plan=plan, plan_joins=list(joins))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = str(exc)
            return res
        joins = joins + lod_joins
    def _rows_without(drop: list) -> tuple[str, list[str]]:
        """The statement's FROM and row predicates with the filters on `drop` taken off
        (what ALL / REMOVEFILTERS of a column the visual filters reads)."""
        gone = {(str(c).lower(), t) for c, t in drop}

        def dropped(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table"))) if not native else None
            return (col, at) in gone or (col, None) in gone
        if unit.get("top_n") and not top_done:
            raise CompileError("an ALL / REMOVEFILTERS of a filtered column beside a Top N "
                               "kept by the statement's LIMIT")
        kept = [f for f in unit.get("filters") or [] if not dropped(f)]
        sp: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                               "display": []}
        c_p, d_p, deferred_ = _plan_filters(dict(unit, filters=kept), rules, alias, Result(),
                                            colmap, resolve=_calc, dialect=dialect,
                                            place=plan.at, text_key=text_key, special=sp)
        if deferred_:
            raise CompileError("a filter it keeps does not compile on its own")
        if sp["members"]:
            # A Top N filter it keeps: the SAME members, ranked within every filter of the
            # visual (seen live: ALL(status) beside a top 3 products by the Returned sales
            # reads those three products' rows of every status), so the predicate is the
            # statement's own.
            mem, _done = _member_filters(
                sp["members"], unit, rules, alias, dialect, Result(), src=src, plan=plan,
                measures=measures, calc=_calc,
                calc_language=calc_language or measure_language, dim_preds=dim_preds,
                joins=joins, limit_ok=False,
                compile_by=lambda formula: _compile_measure(
                    {"name": "top_n_by", "formula": formula,
                     "formula_language": measure_language}, rules, alias, dialect, Result(),
                    colmap, measures, unit))
        else:
            mem = []
        ds_j = [p for p in sp["datasource"] if re.search(r"\bj\d+\.", p)]
        base = [p for p in sp["datasource"] if p not in ds_j] + c_p
        from_ = source
        if base:
            inner_ = "\n       AND ".join(_strip_alias(p, alias) for p in base)
            from_ = f"(\n    SELECT *\n    FROM   {source}\n    WHERE  {inner_}\n  )"
        return from_, ds_j + [p for p in d_p if not _agg_call(p)] + mem

    # A predicate over an AGGREGATE (a filter on a calc that sums) is a HAVING, not a WHERE:
    # SQL refuses an aggregate in WHERE, and Tableau applies it after aggregation.
    _agg = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", re.I)
    having = [p for p in dim_preds if _agg.search(p)]
    for f in special["measure_tests"]:
        # A test of a measure's value on each row of the view (a filter on a measure): a
        # HAVING over the measure as the statement computes it, an empty value compared as
        # the filter's stand-in where it says so.
        name = str(f["measure"])
        mine = next((x for x in unit.get("measures") or []
                     if str(x.get("name")).lower() == name.lower()), None)
        try:
            if not measure_language and not (mine or {}).get("formula_language"):
                raise CompileError("no formula language to read the measure in")
            expr = _compile_measure(
                mine or {"name": "measure_filter", "formula": f"[{name}]",
                         "formula_language": measure_language},
                rules, alias, dialect, res, colmap, measures, unit,
                home=(table_of, source) if table_of and not native else None)
            if " OVER (" in expr:
                raise CompileError("its statement is a window, which a HAVING cannot test")
            pred, why = _test_pred(expr, str(f.get("op") or ""), f.get("value"), f, rules,
                                   dialect, res, force=True)
            if pred is None:
                raise CompileError(why or f"no rule for the test {f.get('op')!r}")
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"the filter on the measure {name!r} does not compile ({exc}); "
                          f"dropping it would show other rows")
            return res
        res.rules_used.append("engine.measure_filter")
        having.append(pred)
    row_joins = list(joins)         # the statement's joins before any context statement's
    if ctx.context_aggs:
        try:
            if any(_agg_call(p) for p in dim_preds):
                raise CompileError("beside a filter on an aggregate calculation: which rows "
                                   "the wider context holds then depends on its value")
            passing = None
            if having and unit.get("group_by"):
                # The rows a filter on a measure keeps are a filter on the visual's grouping
                # columns: ALLSELECTED keeps it (its statement reads only the rows of the
                # combinations that pass), ALL of a grouped column takes it off.
                gk = [_gkey(g, i) for i, g in enumerate(unit["group_by"])]
                nse_row = rules.operator("null_safe_eq", dialect)
                nse_emit = (nse_row or {}).get("emit") or {}
                nse = ((nse_emit.get("dialect_templates") or {}).get(str(dialect).lower())
                       or nse_emit.get("template"))
                if not nse:
                    raise CompileError(f"the rows a measure filter keeps are matched NULL to "
                                       f"NULL, which has no form for the {dialect} dialect")
                rows_ = [p for p in dim_preds if not _agg_call(p)] + member_preds
                inner = (f"SELECT {', '.join(f'{k} AS p{i}' for i, k in enumerate(gk))}\n"
                         f"FROM   {src} {alias}" + "".join(f"\n{j}" for j in row_joins)
                         + ("\nWHERE  " + "\n  AND  ".join(rows_) if rows_ else "")
                         + f"\nGROUP BY {', '.join(gk)}\nHAVING " + "\n  AND  ".join(having))
                passing = ("EXISTS (\n    SELECT 1 FROM (\n" + _indent(inner, "      ")
                           + "\n    ) p\n    WHERE  " + "\n      AND  ".join(
                               nse.replace("{0}", f"p.p{i}").replace("{1}", k)
                               for i, k in enumerate(gk)) + "\n  )")
                res.rules_used.append("engine.dax_context_beside_measure_filter")
            for a in ctx.context_aggs:
                if a.get("drop"):
                    a["src"], a["where"] = _rows_without(a["drop"])
                if a.get("selected") and passing:
                    a["where"] = ([p for p in dim_preds if not _agg_call(p)] + member_preds
                                  + [passing])
            ctx_joins, ctx_sub = _context_statements(
                ctx.context_aggs, src, alias, joins,
                [p for p in dim_preds if not _agg_call(p)] + member_preds, rules, dialect)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a non-additive aggregate over a wider context: {exc}"
            return res
        joins = joins + ctx_joins
        lod_sub.update(ctx_sub)
    if ctx.lifts:
        # A table calculation over another one's result: the grouped rows as a statement of
        # their own, the outer calculation run over them, joined back to each row of this
        # statement on its grouping (one row per group, so no aggregate changes).
        try:
            joins = joins + _lifted_joins(ctx, unit, src, alias, joins, [
                p for p in dim_preds if not _agg.search(p)] + member_preds, having, _gkey,
                rules, dialect, lod_reg)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a table calculation over another one: {exc}"
            return res
        res.rules_used.append("engine.table_calc_nested")
    sql = "SELECT " + ",\n       ".join(select) + f"\nFROM   {src} {alias}"
    for j in joins:
        sql += f"\n{j}"
    # A member filter's subquery aggregates inside itself; it is a row predicate.
    dim_preds = [p for p in dim_preds if not _agg.search(p)] + member_preds
    n_groups = len(unit.get("group_by") or [])
    if unit.get("blank_rows") == "hidden" and n_groups:
        # The view leaves out a row whose every value is empty: the statement keeps the rows
        # where at least one of its values is not NULL (`engine.blank_rows_hidden`). Each
        # term is a projection's own expression, which a reader of the statement recognises
        # as hiding empty rows, not as a restriction of the population.
        hide = _blank_rows_having(select[n_groups:])
        if hide:
            having.append(hide)
            res.rules_used.append("engine.blank_rows_hidden")
    windowed = any(" OVER (" in x for x in select)
    if special["display"] and not windowed:
        # Hidden members with nothing computed across the view's rows: leaving their rows out
        # of the statement leaves every other row as it is.
        dim_preds = dim_preds + special["display"]
        res.rules_used.append("engine.hidden_members")
    try:
        padded = _padded_crosstab(unit, select, src, alias, joins, dim_preds, having, lod_reg,
                                  windowed, rules, dialect, _gkey)
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res
    if padded:
        sql = padded
        res.rules_used.append("engine.table_calc_densify")
    if dim_preds and not padded:
        sql += "\nWHERE  " + "\n  AND  ".join(dim_preds)
    if unit.get("group_by") and not padded:
        sql += "\nGROUP BY " + ", ".join(_gkey(g, i) for i, g in enumerate(unit["group_by"]))
    if having:
        sql += "\nHAVING " + "\n  AND  ".join(having)
    def _with_wider(sql_: str) -> str:
        """The every-member statement with the value on an empty row of each measure that
        reads a wider context (`on_empty_ctx`): that context's own statement, grouped by the
        columns it keeps and joined to each row on them (NULL matching NULL), or one value."""
        if not on_empty_ctx:
            return sql_
        op = rules.operator("null_safe_eq", dialect)
        if op is None:
            raise CompileError(f"it joins the wider context to the rows, NULL matching NULL, "
                               f"which has no form for the {dialect} dialect")
        emit_ = op.get("emit") or {}
        tmpl = (emit_.get("dialect_templates") or {}).get(str(dialect).lower()) \
            or emit_["template"]
        gb = list(unit["group_by"])
        names = [(x.rsplit(" AS ", 1)[1] if " AS " in x else x.rsplit(".", 1)[-1]).strip()
                 for x in select[:n_groups]]
        gks = [_gkey(g, i) for i, g in enumerate(gb)]
        gcs = [_gcol(g, i) for i, g in enumerate(gb)]

        def outer(expr: str) -> str:
            for i, (k, c) in enumerate(zip(gks, gcs)):
                if expr == k:
                    return k.replace(c, f"w.{names[i]}") if c in k else f"w.{names[i]}"
            raise CompileError(f"a wider context kept by {expr!r}, which the rows do not show")
        extra, exprs, n = [], {}, 0
        for name, (esql, aggs) in on_empty_ctx.items():
            for a in aggs:
                n += 1
                if a["drop"]:
                    src_a, where_a = _rows_without(a["drop"])
                else:
                    src_a, where_a = src, list(dim_preds)
                body = f"FROM   {src_a} {alias}" + "".join(f"\n{j}" for j in row_joins)
                if where_a:
                    body += "\nWHERE  " + "\n  AND  ".join(where_a)
                body = body.replace("\n", "\n    ")
                if a["keep"]:
                    keys_ = ", ".join(f"{k} AS k{i}" for i, k in enumerate(a["keep"]))
                    on = "\n   AND ".join(tmpl.replace("{0}", f"e{n}.k{i}")
                                          .replace("{1}", outer(k))
                                          for i, k in enumerate(a["keep"]))
                    extra.append(f"LEFT JOIN (\n    SELECT {keys_}, {a['sql']} AS v\n    {body}"
                                 f"\n    GROUP BY {', '.join(a['keep'])}\n  ) e{n}\n    ON {on}")
                else:
                    extra.append(f"CROSS JOIN (\n    SELECT {a['sql']} AS v\n    {body}\n  ) e{n}")
                esql = esql.replace(f"__EMPTYAGG{a['n']}__", f"e{n}.v")
            exprs[name] = esql
        cols = names + [x.rsplit(" AS ", 1)[1].strip() for x in select[n_groups:]]
        sel = [f"COALESCE(w.{c}, {exprs[c]}) AS {c}" if c in exprs else f"w.{c}" for c in cols]
        res.rules_used.append("engine.value_on_empty_rows_wider")
        return ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n" + _indent(sql_)
                + "\n) w\n" + "\n".join(extra))

    if (on_empty or on_empty_ctx) and unit.get("blank_rows") != "shown":
        try:
            sql = _with_every_member(sql, select, n_groups, [_gcol(g, i) for i, g in
                                     enumerate(unit["group_by"])], src, alias,
                                     [j for j in plan.clauses if not j.startswith("__")],
                                     [p for p in dim_preds if p not in member_preds], rules,
                                     dialect, graph=list(model_graph or graph or []),
                                     members=member_preds, folded=set(folded))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"a measure not BLANK on an empty row makes the visual list every "
                          f"member of its grouping columns: {exc}")
            return res
        for name, empty in on_empty.items():
            sql = re.sub(rf"(?m)^(\s*)v\.{re.escape(name)}(,?)$",
                         lambda mm, e=empty, n=name: f"{mm.group(1)}COALESCE(v.{n}, {e}) AS {n}"
                                                     f"{mm.group(2)}", sql, count=1)
        try:
            sql = _with_wider(sql)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"a measure not BLANK on an empty row makes the visual list every "
                          f"member of its grouping columns: {exc}")
            return res
        res.rules_used.append("engine.value_on_empty_rows")
    if unit.get("blank_rows") == "shown" and n_groups:
        try:
            if unit.get("top_n") and not top_done:
                raise CompileError("beside a Top N kept by the statement's LIMIT, whose "
                                   "members are ranked over the rows")
            # A Top N FILTER keeps the members a ranking keeps: a filter on the column's own
            # table, which narrows that table's members (and the fact's).
            sql = _with_every_member(sql, select, n_groups, [_gcol(g, i) for i, g in
                                     enumerate(unit["group_by"])], src, alias, joins,
                                     [p for p in dim_preds if p not in member_preds], rules,
                                     dialect, graph=list(model_graph or graph or []),
                                     members=member_preds, folded=set(folded))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"the visual shows items with no data: {exc}"
            return res
        for name, empty in on_empty.items():
            # A member with no rows shows the measure's value on an empty row there too.
            sql = re.sub(rf"(?m)^(\s*)v\.{re.escape(name)}(,?)$",
                         lambda mm, e=empty, n=name: f"{mm.group(1)}COALESCE(v.{n}, {e}) AS {n}"
                                                     f"{mm.group(2)}", sql, count=1)
        try:
            sql = _with_wider(sql)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"the visual shows items with no data: {exc}"
            return res
        res.rules_used.append("engine.every_member")
    if unit.get("top_n") and not top_done:
        # A LIMIT with no ORDER BY returns ANY n rows: a different answer on every run. The
        # BI Top-N ranks by its measure, largest first; the order the file names wins.
        if unit.get("sort"):
            # The sort the BI file names is AUTHOR-CONTROLLED TEXT in the vendor's formula
            # language. It is compiled like a measure — never pasted into the statement —
            # and a sort the compiler cannot read refuses the unit: ordering by some other
            # measure returns a different n rows.
            try:
                order = _top_n_order(
                    str(unit["sort"]), unit, rules, alias, dialect, res, colmap, measures,
                    alias_of, group_expr=lambda n: next(
                        (_gkey(g, i) for i, g in enumerate(unit.get("group_by") or [])
                         if str(g).lower() == n), None))
            except CompileError as exc:
                res.coverage = REFUSED
                res.reason = (f"the Top {int(unit['top_n'])} ranks by {unit['sort']!r}, "
                              f"which does not compile ({exc}); ordering by another measure "
                              f"would return different rows")
                return res
            sql += f"\nORDER BY {order}"
            res.rules_used.append("engine.top_n_by_sort")
        elif ranked_by:
            sql += f"\nORDER BY {ranked_by} DESC"
            res.rules_used.append("engine.top_n_by_first_measure")
        else:
            res.coverage = REFUSED
            res.reason = (f"a Top {int(unit['top_n'])} with no measure to rank by: a LIMIT "
                          "without an order returns arbitrary rows")
            return res
        sql += "\n" + _limit(int(unit["top_n"]), dialect)

    grain = [r for r in lod_reg if r["mode"] == "grain"]
    if grain:
        # An aggregate over a FIXED or INCLUDE level of detail reads the view's rows at the
        # level of detail's dimensions too: a statement at that grain, aggregated again.
        try:
            if having or (unit.get("top_n") and not top_done):
                raise CompileError("beside a measure filter or a Top N kept by LIMIT")
            nse_row = rules.operator("null_safe_eq", dialect)
            nse_emit = (nse_row or {}).get("emit") or {}
            nse = ((nse_emit.get("dialect_templates") or {}).get(str(dialect).lower())
                   or nse_emit.get("template"))
            sql = _grain_statement(select, [_gcol(g, i) for i, g in
                                            enumerate(unit.get("group_by") or [])],
                                   grain, src, alias, joins, dim_preds, lod_sub, nse)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"an aggregate over a level-of-detail expression: {exc}"
            return res
        res.rules_used.append("engine.tableau_lod_grain")
    if special["display"] and windowed:
        # Hidden members beside a calculation across the view's rows (a running sum, a percent
        # of total): their rows are computed with the others, then left out of the view.
        shown = [re.sub(rf"\b(?:{re.escape(alias)}|j\d+)\.", "h.", p)
                 for p in special["display"]]
        cols = ",\n       ".join(f"h.{_out_name(x)}" for x in select)
        sql = (f"SELECT {cols}\nFROM   (\n" + sql + "\n) h\nWHERE  "
               + "\n  AND  ".join(shown))
        res.rules_used.append("engine.hidden_members")
    for key, val in lod_sub.items():
        sql = sql.replace(key, val)
    try:
        sql = _expand_branches(sql, plan, rules, dialect)
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res
    if unit.get("distinct_rows") and n_groups and not unit.get("measures") and not native \
            and not joins and not unit.get("top_n"):
        # A table of one table's columns only lists that table's BLANK member too when a
        # relationship into it has a key it lacks (seen live: from a second many-side table,
        # from an inactive relationship's empty keys, and through a chain: every product's
        # category exists, a sale's product does not, and the categories list a blank).
        test = _blank_member_test(source, list(model_graph or graph or []))
        kept_blank = [_blank_in(f) for f in unit.get("filters") or []]
        passes = all(k is True for k in kept_blank)
        if test and passes:
            nulls = ", ".join(f"NULL AS {_out_name(x)}" for x in select[:n_groups])
            sql = f"{sql}\nUNION\nSELECT {nulls}\nFROM   (SELECT 1 AS one) z\nWHERE  {test}"
            res.rules_used.append("engine.blank_member_of_a_table")
        elif test and None in kept_blank:
            res.gotchas.append("the table's blank member is not listed: a filter of the "
                               "visual whose test of a blank is not read here")

    if not n_groups and select and not windowed and not unit.get("top_n") \
            and not unit.get("distinct_rows") and all(_constant_item(x, alias) for x in select):
        # A card of measures that read no row (a constant, a parameter written in as its
        # value, a context test): one value, as the visual shows it, never one per row of the
        # table the statement would otherwise read.
        sql = "SELECT " + ",\n       ".join(select)
        res.rules_used.append("engine.constant_measure")
    res.sql = sql
    res.coverage = PARTIAL if (res.untranslatable or failed) else TRANSLATED
    if res.coverage == PARTIAL and not res.reason:
        # A partial with no failed measure: a declared construct outside the measures (a
        # filter that switches the view rather than the rows). Said, never left blank.
        res.reason = "; ".join(
            f"{g.get('construct')}: {g.get('why')}" if g.get("why") else str(g.get("construct"))
            for g in res.untranslatable)
    return res


def _lifted_joins(ctx: "_UnitContext", unit: dict, src: str, alias: str, joins: list[str],
                  preds: list[str], having: list[str], gkey, rules, dialect: str,
                  lods: list[dict]) -> list[str]:
    """The joins of each lifted table calculation (`ctx.lifts`): `lw<n>`, the statement's
    grouped rows (its FROM, WHERE, GROUP BY and HAVING) with the calculation's arguments as
    columns, the window over them, keyed by the grouping columns NULL matching NULL."""
    groups = list(unit.get("group_by") or [])
    if any((m.get("table_calc") or {}).get("pad") for m in unit.get("measures") or []):
        raise CompileError("on a densified crosstab (the empty cells are not grouped rows)")
    if any(r.get("mode") == "grain" for r in lods):
        raise CompileError("beside an aggregate over a level of detail read at its grain")
    keys = [gkey(g, i) for i, g in enumerate(groups)]
    op = rules.operator("null_safe_eq", dialect)
    emit = (op or {}).get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit.get("template")
    if groups and not nse:
        raise CompileError(f"its join on the grouping, NULL matching NULL, has no form for "
                           f"the {dialect} dialect")
    body = f"FROM   {src} {alias}" + "".join(f"\n{j}" for j in joins)
    if preds:
        body += "\nWHERE  " + "\n  AND  ".join(preds)
    if groups:
        body += "\nGROUP BY " + ", ".join(keys)
    if having:
        body += "\nHAVING " + "\n  AND  ".join(having)
    out = []
    for lift in ctx.lifts:
        n = lift["n"]
        cols = ([f"{k} AS k{i}" for i, k in enumerate(keys)]
                + [f"{a} AS a{i}" for i, a in enumerate(lift["args"])]
                + [f"{x} AS {name}" for name, x in lift["sorts"]])
        inner = "SELECT " + ",\n       ".join(cols) + "\n" + body
        if lift.get("recursive"):
            outer = _recursive_rows(lift["recursive"], inner, len(keys), len(lift["args"]),
                                    nse)
        else:
            outer = ("SELECT " + "".join(f"__q.k{i} AS k{i}, " for i in range(len(keys)))
                     + f"{lift['window']} AS v\nFROM   (\n" + _indent(inner) + "\n) __q")
        if keys:
            on = "\n   AND ".join(nse.replace("{0}", f"lw{n}.k{i}").replace("{1}", k)
                                   for i, k in enumerate(keys))
            out.append(f"LEFT JOIN (\n{_indent(outer)}\n) lw{n}\n    ON {on}")
        else:
            out.append(f"CROSS JOIN (\n{_indent(outer)}\n) lw{n}")
    return out


def _recursive_rows(rec: dict, inner: str, nkeys: int, nargs: int, nse: str | None) -> str:
    """The grouped rows (`inner`: k<i> the grouping, a<i> the formula's parts) numbered
    along the compute-using within each partition (`__b`), and the recursion over them
    (`__r`): the first row of each partition from its anchor, every next row from the row
    before (`__r.v`). One row per grouped row: k<i> and v."""
    ks = [f"k{i}" for i in range(nkeys)]
    part = [f"__q.k{p}" for p in rec["partition"]]
    pby = f"PARTITION BY {', '.join(part)} " if part else ""
    numbered = ("SELECT " + "".join(f"__q.{k}, " for k in ks)
                + "".join(f"__q.a{i}, " for i in range(nargs))
                + f"ROW_NUMBER() OVER ({pby}ORDER BY {rec['order']}) AS rn\n"
                + "FROM   (\n" + _indent(inner) + "\n) __q")
    carry = "".join(f"__b.{k}, " for k in ks)
    on = [f"__b.rn = __r.rn + 1"] + [
        (nse or "{0} = {1}").replace("{0}", f"__b.k{p}").replace("{1}", f"__r.k{p}")
        for p in rec["partition"]]
    # A dialect that caps a recursion's depth by default states its own cap on `__r`
    # (`recursion_clause`, e.g. MAX RECURSION LEVEL): one level per row of a partition.
    level = f" {rec['level']}" if rec.get("level") else ""
    text = (f"__b AS (\n{_indent(numbered)}\n), __r{level} AS (\n"
            f"    SELECT {carry}__b.rn, {rec['anchor']} AS v\n"
            f"    FROM   __b\n    WHERE  __b.rn = 1\n"
            f"    UNION ALL\n"
            f"    SELECT {carry}__b.rn, {rec['step']} AS v\n"
            f"    FROM   __r\n    JOIN   __b\n      ON " + "\n     AND ".join(on) + "\n)\n",
            "SELECT " + "".join(f"__r.{k} AS {k}, " for k in ks) + "__r.v AS v\nFROM   __r")
    if not rec.get("hoist"):
        return f"{rec['with']} {text[0]}{text[1]}"
    # A dialect that allows a WITH only at the top of a statement (`hoist`): the CTEs, named
    # for this recursion, are marked here and moved to the top (`_hoist_recursions`).
    n = rec["n"]
    named = [re.sub(r"\b__([br])\b", rf"__\g<1>{n}", t) for t in text]
    return f"/*__rec{n}__*/{named[0]}/*__sel{n}__*/{named[1]}/*__end{n}__*/"


def _hoist_recursions(sql: str, opener: str, suffix: str) -> str:
    """The recursions marked in `sql` (`_recursive_rows` with `hoist`) as CTEs at the top of
    the statement, each subquery reading its recursion's own CTE, and the statement's
    `suffix` (a dialect's recursion cap, e.g. OPTION (MAXRECURSION 0)) at its end."""
    defs: list[str] = []
    for m in list(re.finditer(r"/\*__rec(\d+)__\*/", sql)):
        n = m.group(1)
        start = sql.index(f"/*__rec{n}__*/")
        mid = sql.index(f"/*__sel{n}__*/", start)
        end = sql.index(f"/*__end{n}__*/", mid)
        defs.append(sql[start + len(f"/*__rec{n}__*/"):mid].rstrip())
        sql = sql[:start] + sql[mid + len(f"/*__sel{n}__*/"):end] + sql[end + len(f"/*__end{n}__*/"):]
    if not defs:
        return sql
    head = f"{opener} " + ",\n".join(defs)
    if re.match(r"(?is)^\s*WITH\s", sql):
        sql = head + ",\n" + re.sub(r"(?is)^\s*WITH\s+", "", sql, count=1)
    else:
        sql = head + "\n" + sql
    return sql + (f"\n{suffix}" if suffix else "")


def _labelled(expr: str, labels: list[dict], dialect: str | None) -> str:
    """A grouping column shown with its labels: each labelled value as its label, every
    other value as it is (as text, when a label makes the column text)."""
    whens = []
    for lab in labels:
        v = lab.get("value")
        test = f"{expr} IS NULL" if v is None else f"{expr} = {_literal(v, dialect)}"
        whens.append(f"WHEN {test} THEN {_quote(str(lab.get('label') or ''), dialect)}")
    numeric = any(lab.get("value") is not None and _NUM_LIT.fullmatch(str(lab["value"]))
                  for lab in labels)
    rest = f"CAST({expr} AS VARCHAR)" if numeric else expr
    return "CASE " + " ".join(whens) + f" ELSE {rest} END"


def _out_name(item: str) -> str:
    """The column name a select item yields: its alias, else its column."""
    if " AS " in item:
        return item.rsplit(" AS ", 1)[1].strip()
    return item.rsplit(".", 1)[-1].strip()


def _blend_parts(unit: dict, source: str, columns: dict[str, set[str]]) -> dict | None:
    """The unit split at its BLEND edges (`kind: blend`, from the statement's table): the
    primary unit (every measure, filter and table but the blended ones) and, per secondary
    table, its unit: the measures that aggregate its columns and the filters on it, grouped
    by the links whose primary column the view groups by (the active links). None when no
    measure or filter reads a blended table."""
    tables = list(unit.get("warehouse_tables") or [])
    blends = [e for e in unit.get("join_graph") or [] if str(e.get("kind")) == "blend"]
    # The PRIMARY is the table the blend edges start from, wherever the unit lists it (a
    # view that shows only the secondary's measures lists the secondary first).
    starts = {_bare(e.get("from_table")) for e in blends
              if any(_bare(t) == _bare(e.get("from_table")) for t in tables)}
    if len(starts) == 1 and _bare(source) not in starts:
        source = next(t for t in tables if _bare(t) in starts)
    edges = [e for e in blends if _bare(e.get("from_table")) == _bare(source)]
    if not edges:
        return None
    src_cols = _columns_of(source, columns)
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    gbt = list(unit.get("group_by_tables") or [])
    parts: dict[str, dict] = {}
    taken: set[int] = set()
    for sec in dict.fromkeys(e["to_table"] for e in edges):
        sec_cols = _columns_of(sec, columns)
        def on_secondary(m: dict) -> bool:
            # A shelf aggregate of a column only the secondary holds, or a calc of the
            # secondary's (every field its formula names is the secondary's alone).
            if not m.get("formula"):
                name = str(m.get("name") or "").lower()
                return name in sec_cols and name not in src_cols
            # A calc's pill carries a compute-using whether or not its formula calls a
            # table calculation; only one that calls one runs over the blended rows.
            refs = {r.lower() for r in re.findall(r"\[([^\[\]]+)\]", str(m["formula"]))}
            return bool(refs) and refs <= sec_cols and not refs & src_cols \
                and not (m.get("table_calc") and _calls_table_calc(str(m["formula"])))
        ms = [m for m in unit.get("measures") or [] if on_secondary(m)]
        # Its compute-using names the PRIMARY's view fields; a calc of the secondary that
        # calls no table calculation is a plain aggregate over the secondary's rows.
        sec_ms = [{k: v for k, v in m.items() if k != "table_calc"} for m in ms]
        fs = [f for f in unit.get("filters") or []
              if f.get("table") and _bare(f["table"]) == _bare(sec)]
        # A grouping column only the secondary holds: shown, never grouped by (its one value
        # per row of the primary, or '*').
        dims = [g for i, g in enumerate(unit.get("group_by") or [])
                if str(g).lower() in sec_cols and str(g).lower() not in src_cols
                and (not (i < len(gbt) and gbt[i]) or _bare(gbt[i]) == _bare(sec))]
        if not ms and not fs and not dims:
            continue
        links = []
        for e in edges:
            if e["to_table"] != sec:
                continue
            col = str(e.get("from_column") or "").lower()
            if col in gb:
                i = gb.index(col)
                hint = gbt[i] if i < len(gbt) else None
                if not hint or _bare(hint) == _bare(source):
                    links.append((unit["group_by"][i], str(e["to_column"])))
        parts[sec] = {"measures": sec_ms, "filters": fs, "links": links, "dims": dims}
        taken |= {id(x) for x in ms + fs}
    if not parts:
        return None
    blended = set(parts)
    shown = {str(g).lower() for p in parts.values() for g in p["dims"]}
    keep = [i for i, g in enumerate(unit.get("group_by") or []) if str(g).lower() not in shown]
    primary = dict(unit, measures=[m for m in unit.get("measures") or [] if id(m) not in taken],
                   filters=[f for f in unit.get("filters") or [] if id(f) not in taken],
                   group_by=[unit["group_by"][i] for i in keep],
                   group_by_tables=[gbt[i] if i < len(gbt) else None for i in keep]
                   if gbt else gbt,
                   warehouse_tables=[source] + [t for t in tables
                                                if t not in blended and t != source],
                   join_graph=[e for e in unit.get("join_graph") or []
                               if str(e.get("kind")) != "blend"])
    return {"primary": primary, "secondary": parts}


def _blend_statement(unit: dict, blended: dict, again, rules, dialect: str) -> Result:
    """A BLEND as one statement: the primary unit's rows (`p`), and each secondary table's
    measures aggregated over the active links under its own filters (`s1`, ...), LEFT JOINed
    on the links, NULL matching NULL (seen live: a NULL member of the primary gets the
    secondary's NULL member; a member only the secondary has is not shown). A filter on the
    secondary also drops the primary's rows whose links it leaves no row for (an inner join).
    A dimension of the secondary never splits the primary's rows: each shows its one value
    there, or '*' (`agg.single_value`). With no active link the secondary's one total row is
    beside every row (seen live)."""
    res = Result()
    primary = blended["primary"]
    gb = list(primary.get("group_by") or [])
    p_res = again(primary) if (gb or primary.get("measures")) else None
    if p_res is not None and not p_res.sql:
        return p_res
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        res.coverage = REFUSED
        res.reason = (f"a data blend joins the secondary on its links, NULL matching NULL, "
                      f"which has no form for {dialect}")
        return res
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    names_of: dict[str, str] = {}
    froms: list[str] = []
    if p_res is not None:
        pg, pv = _leaf_names(p_res, primary)
        names_of.update({v: f"p.{v}" for v in pv})
        froms.append(f"(\n{p_res.sql}\n) p")
        for k in ("rules_used", "provisional_rules", "unknown", "untranslatable", "gotchas"):
            getattr(res, k).extend(getattr(p_res, k))
    for k, (sec, part) in enumerate(blended["secondary"].items(), start=1):
        links = part["links"]
        attrs = [{"name": d, "formula": None, "formula_language": None,
                  "aggregation": "single_value"} for d in part["dims"]]
        sub = dict(unit, measures=part["measures"] + attrs, filters=part["filters"],
                   warehouse_tables=[sec], join_graph=[],
                   group_by=[c for _g, c in links], group_by_tables=[sec for _ in links],
                   top_n=None, sort=None)
        sub.pop("totals", None)
        r = again(sub)
        if not r.sql:
            res.coverage = REFUSED
            res.reason = f"the blend's secondary table {sec}: {r.reason}"
            return res
        sg, sv = _leaf_names(r, sub)
        names_of.update({v: f"s{k}.{v}" for v in sv})
        if p_res is None:
            froms.append(f"(\n{r.sql}\n) s{k}")
        elif links:
            on = " AND ".join(tmpl.format(f"p.{pg[gb.index(g)]}", f"s{k}.{sg[i]}")
                              for i, (g, _c) in enumerate(links))
            # A filter on the secondary also leaves out the primary's rows whose links keep
            # no secondary row (seen live): the join keeps only linked rows then.
            kind = "INNER JOIN" if part["filters"] else "LEFT JOIN"
            froms.append(f"{kind} (\n{r.sql}\n) s{k} ON {on}")
        else:
            froms.append(f"CROSS JOIN (\n{r.sql}\n) s{k}")
        for x in ("rules_used", "provisional_rules", "unknown", "untranslatable", "gotchas"):
            getattr(res, x).extend(getattr(r, x))
    keyed = dict(zip([str(g).lower() for g in gb], pg if p_res is not None else []))
    keys = []
    for g in unit.get("group_by") or []:
        if str(g).lower() in keyed:
            keys.append(f"p.{keyed[str(g).lower()]}")
        elif _measure_alias(g) in names_of:
            keys.append(f"{names_of[_measure_alias(g)]} AS {g}")
    vals = [names_of[_measure_alias(m["name"])] + f" AS {_measure_alias(m['name'])}"
            for m in unit.get("measures") or [] if _measure_alias(m["name"]) in names_of]
    res.sql = "SELECT " + ",\n       ".join(keys + vals) + "\nFROM   " + "\n".join(froms)
    res.rules_used.append("engine.blend")
    res.coverage = PARTIAL if res.untranslatable else TRANSLATED
    return res


def _leaf_names(res: Result, unit: dict) -> tuple[list[str], list[str]]:
    """The output names of a compiled unit's grouping columns and of the measures it holds."""
    sql = res.sql or ""
    groups = [(_measure_alias(g) if re.search(rf"\bAS {re.escape(_measure_alias(g))}\b", sql)
               else str(g)) for g in unit.get("group_by") or []]
    vals = [_measure_alias(m["name"]) for m in unit.get("measures") or []
            if re.search(rf"\bAS {re.escape(_measure_alias(m['name']))}\b", sql)]
    return groups, vals


def _detached_statement(unit: dict, plan: _JoinPlan, graph: list[dict], again) -> Result:
    """A visual that groups by a column of a table its measures' rows do not reach in the
    direction filters flow (one-to-many against a single-direction relationship, or along an
    inactive one). That table filters nothing: every measure has the value of the rows the
    other columns and filters choose, beside EVERY member of the column (the members its own
    table's filters leave, its blank member included), as a grouping over unrelated tables
    crosses their members. A filter on such a table narrows only its members."""
    det = plan.detached
    gb, gbt = list(unit.get("group_by") or []), list(unit.get("group_by_tables") or [])

    def detached_at(col: str, hint: Any) -> str | None:
        hit = det.get(str(col).lower())
        if hit is None:
            return None
        if hint and hit[1] and _bare(hint) != _bare(hit[1]):
            return None
        return hit[0]
    leaf_gb: list = []
    leaf_gbt: list = []
    own: list = []
    for i, g in enumerate(gb):
        t = detached_at(g, gbt[i] if i < len(gbt) else None)
        if t is None:
            leaf_gb.append(g)
            leaf_gbt.append(gbt[i] if i < len(gbt) else None)
        else:
            own.append((i, g, t))
    on_table: dict[str, list[dict]] = {}
    leaf_filters = []
    for f in unit.get("filters") or []:
        t = detached_at(f.get("column"), f.get("table")) if f.get("column") else None
        if t is None:
            leaf_filters.append(f)
        else:
            on_table.setdefault(_bare(t), []).append(f)
    leaf_unit = dict(unit, group_by=leaf_gb, filters=leaf_filters)
    leaf_unit.pop("group_by_tables", None)
    if any(leaf_gbt):
        leaf_unit["group_by_tables"] = leaf_gbt
    if unit.get("top_n"):
        res = Result()
        res.coverage = REFUSED
        res.reason = ("a Top N beside a column of a table that does not filter the measures: "
                      "the ranking would run over members that all share one value")
        return res
    leaf = again(leaf_unit)
    if not leaf.sql or not own:
        # Only filters on such tables: they choose no row of the measures.
        return leaf
    lgroups, lvals = _leaf_names(leaf, leaf_unit)
    parts, cols = [], {}
    tables = list(dict.fromkeys(t for _i, _g, t in own))
    for k, t in enumerate(tables):
        mine = [(i, g) for i, g, tt in own if tt == t]
        mu = {"unit_id": f"{unit.get('unit_id')}::members", "title": unit.get("title"),
              "measures": [], "group_by": [g for _i, g in mine],
              "group_by_tables": [t] * len(mine), "filters": on_table.get(_bare(t), []),
              "warehouse_tables": [t], "join_graph": []}
        m = again(mu)
        if not m.sql:
            return m
        body = m.sql
        blank = _blank_member_test(t, graph)
        if blank and f"FROM   {t} t" in body:
            # Its BLANK member: a row of the table with every column empty, kept by the
            # table's own filters as any row is.
            b = body.replace(f"FROM   {t} t",
                             f"FROM   (SELECT 1 AS one_) o_\nLEFT JOIN {t} t ON 1 = 0", 1)
            head, sep, tail = b.partition("\nGROUP BY")
            head += ("\n  AND  " if "\nWHERE  " in head else "\nWHERE  ") + f"({blank})"
            body = f"{body}\nUNION\n{head}{sep}{tail}"
        parts.append(f"(\n{body}\n) d{k}")
        for (i, g), name in zip(mine, _leaf_names(m, mu)[0]):
            cols[i] = f"d{k}.{name}"
    li = iter(lgroups)
    keys = [cols[i] if i in cols else f"v.{next(li)}" for i in range(len(gb))]
    sql = ("SELECT " + ",\n       ".join(keys + [f"v.{x}" for x in lvals])
           + "\nFROM   " + "\nCROSS JOIN ".join(parts)
           + "\nCROSS JOIN (\n" + leaf.sql + "\n) v")
    if lvals:
        sql += "\nWHERE  (" + "\n    OR ".join(f"v.{x} IS NOT NULL" for x in lvals) + ")"
    leaf.sql = sql
    leaf.rules_used = list(leaf.rules_used) + ["engine.detached_members"]
    return leaf


# Scalar functions a composite measure is computed through, over its parts' values.
_SCALAR_WRAPPERS = {"IF", "DIVIDE", "ISBLANK", "COALESCE", "ABS", "ROUND", "ROUNDUP",
                    "ROUNDDOWN", "INT", "NOT", "AND", "OR", "SWITCH", "BLANK", "TRUE", "FALSE",
                    "IFERROR", "SIGN", "POWER", "SQRT", "MOD", "IF.EAGER"}


def _statement_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                    graph: list[dict], row_formulas: set[str]) -> bool:
    """Whether a formula compiles only as a statement of its own (a rank, a window, a member
    iterator)."""
    return bool(_rank_form(formula, measures, table_of, row_formulas, graph=graph)
                or _window_form(formula, measures, table_of, row_formulas)
                or _member_grain(formula, measures, table_of, graph, row_formulas)
                or _treatas_form(formula, measures, table_of, graph))


def _treatas_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                  graph: list[dict], depth: int = 0) -> dict | None:
    """`CALCULATE(e, TREATAS(VALUES(T[c]), F[x]), ...)` where T is a table no relationship of
    the model touches (a target table at another grain): {formula, edges, members, over}, or
    None. The formula is the CALCULATE without its TREATAS arguments; each becomes a virtual
    relationship F[x] -> T[c] (`edges`) that only this measure joins along, and T's members are
    always joined (`members`): seen live, the measure reads the rows whose F[x] is one of the
    values T holds in the row's context (its grouping and its own filters; a blank value
    matches the blank), and it replaces the visual's filter on F[x] (`over`)."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures:
            return _treatas_form(measures[key], measures, table_of, graph, depth + 1)
        return None
    head, inner = _call_head(toks)
    if head != "CALCULATE":
        return None
    parts = _split_top(inner)
    tmap = {k.lower(): v for k, v in table_of.items()}
    touched = {_bare(e.get(k)) for e in graph or [] for k in ("from_table", "to_table")}
    keep, edges, members, over = [parts[0]], [], [], []
    for arg in parts[1:]:
        th, tin = _call_head(arg)
        if th != "TREATAS":
            keep.append(arg)
            continue
        tparts = _split_top(tin)
        if len(tparts) != 2:
            return None
        vh, vin = _call_head(tparts[0])
        if vh not in ("VALUES", "DISTINCT") or len(vin) != 1 \
                or vin[0][0] not in ("qualified", "quoted_qualified") \
                or len(tparts[1]) != 1 \
                or tparts[1][0][0] not in ("qualified", "quoted_qualified"):
            return None
        t_phys = tmap.get(str(_table_ref(vin[0][1]) or "").lower())
        f_phys = tmap.get(str(_table_ref(tparts[1][0][1]) or "").lower())
        if not t_phys or not f_phys or _bare(t_phys) in touched \
                or _bare(t_phys) == _bare(f_phys):
            return None
        c, x = _column_ref(vin[0][1]), _column_ref(tparts[1][0][1])
        edges.append({"from_table": f_phys, "from_column": x, "to_table": t_phys,
                      "to_column": c, "cardinality": "M:M", "kind": "relationship",
                      "is_active": True, "join_type": "inner",
                      "name": f"TREATAS {_bare(t_phys)}.{c} -> {_bare(f_phys)}.{x}"})
        members.append((c, t_phys))
        over.append((x, f_phys))
    if not edges:
        return None
    body = keep[0] if len(keep) == 1 else (
        [("func", "CALCULATE"), ("lparen", "(")]
        + [t for i, a in enumerate(keep) for t in ([("comma", ",")] if i else []) + a]
        + [("rparen", ")")])
    return {"formula": _text_of(body), "edges": edges, "members": members, "over": over}


def _treatas_statement(unit: dict, m: dict, tr: dict, again) -> Result:
    """One measure of `_treatas_form` as a statement: the unit with the measure's virtual
    relationships added to its graph, the target tables' members always joined (inner, a
    blank matching a blank), and the visual's filters on the replaced columns taken off."""
    if len({_bare(t) for _c, t in tr["members"]}) < len(tr["members"]):
        res = Result()
        res.reason = (f"measure {m['name']!r}: two TREATAS of one table's columns in one "
                      f"CALCULATE are each a set of their own (every pair of their values, not "
                      f"the table's rows), which one join of the table's members does not hold")
        return res
    over = {(str(x).lower(), _bare(t)) for x, t in tr["over"]}
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    gbt = list(unit.get("group_by_tables") or [])
    tables = list(unit.get("warehouse_tables") or [])
    grouped = [i for i, g in enumerate(gb)
               if (g, _bare(gbt[i] if i < len(gbt) and gbt[i] else tables[0])) in over]
    if grouped:
        # The visual groups by a column TREATAS replaces: the measure has the value of the
        # row's other columns beside every member of it the visual lists (seen live: every
        # channel row shows the same value; a filter on the channel leaves its members).
        ug = dict(unit, group_by=[g for i, g in enumerate(unit["group_by"]) if i not in grouped],
                  totals=[])
        if gbt:
            ug["group_by_tables"] = [t for i, t in enumerate(gbt) if i not in grouped]
        r = _treatas_statement(ug, m, tr, again)
        if not r.sql:
            return r
        mu = dict(unit, measures=[], totals=[], top_n=None,
                  group_by=[unit["group_by"][i] for i in grouped])
        if gbt:
            mu["group_by_tables"] = [gbt[i] for i in grouped]
        mem = again(mu)
        if not mem.sql:
            return mem
        lg, lv = _leaf_names(r, ug)
        mg = _leaf_names(mem, mu)[0]
        li, mi = iter(lg), iter(mg)
        keys = [f"d.{next(mi)}" if i in grouped else f"v.{next(li)}" for i in range(len(gb))]
        r.sql = ("SELECT " + ",\n       ".join(
            [f"{k} AS {_out_name(k.split('.', 1)[1])}" for k in keys] + [f"v.{x}" for x in lv])
            + "\nFROM   (\n" + _indent(mem.sql) + "\n) d\nCROSS JOIN (\n" + _indent(r.sql)
            + "\n) v" + ("\nWHERE  (" + "\n    OR ".join(f"v.{x} IS NOT NULL" for x in lv)
                          + ")" if lv else ""))
        r.rules_used.append("engine.detached_members")
        return r

    def replaced(f: dict) -> bool:
        return bool(f.get("column")) and (str(f["column"]).lower(), _bare(
            f.get("table") or tables[0])) in over
    homes = [t for t in dict.fromkeys(e["from_table"] for e in tr["edges"])]
    sub = dict(unit, measures=[{**m, "formula": tr["formula"]}],
               filters=[f for f in unit.get("filters") or [] if not replaced(f)],
               join_graph=list(unit.get("join_graph") or []) + tr["edges"],
               warehouse_tables=homes + [t for t in dict.fromkeys(
                   tables + [t for _c, t in tr["members"]]) if t not in homes],
               member_joins=[{"column": c, "table": t} for c, t in tr["members"]])
    r = again(sub)
    if r.sql:
        r.rules_used.append("engine.dax_treatas_table")
        r.gotchas.append(
            f"{m['name']}: TREATAS of "
            + ", ".join(f"{_bare(t)}.{c}" for c, t in tr["members"])
            + ": the rows whose "
            + ", ".join(f"{_bare(t)}.{x}" for x, t in tr["over"])
            + " is one of the values that table holds in the row's context (a blank matches "
              "a blank); the visual's own filter on that column is replaced")
    return r


def _tables_read(formula: str, measures: dict[str, str], table_of: dict[str, str]
                 ) -> set[str]:
    """The warehouse tables (bare) a DAX formula's columns and table names read, through
    the measures it names."""
    tmap = {k.lower(): v for k, v in table_of.items()}
    out: set[str] = set()
    try:
        toks = _expanded_tokens(formula, measures)
    except CompileError:
        return out
    for k, v in toks:
        t = None
        if k in ("qualified", "quoted_qualified"):
            t = _table_ref(v)
        elif k == "str" and v.startswith("'"):
            t = v[1:-1].replace("''", "'")
        elif k == "func" and v.lower() in tmap:
            t = v
        phys = tmap.get(str(t or "").lower())
        if phys:
            out.add(_bare(phys))
    return out


# Aggregates a composite measure's part may compute over another table than the statement's.
_FOREIGN_AGGREGATES = {"MIN", "MAX", "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTROWS",
                       "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "COUNTBLANK", "SUMX",
                       "AVERAGEX", "MINX", "MAXX", "COUNTX", "CALCULATE", "FIRSTDATE",
                       "LASTDATE"}


def _composite_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                    graph: list[dict], row_formulas: set[str], home: str | None = None
                    ) -> tuple[list, list[str]] | None:
    """A measure that is a scalar expression over parts, at least one of which compiles only
    as a statement of its own: (the expression's tokens with each part replaced by a
    `[__part<k>]` reference, each part's formula). None for any other formula (a top-level
    form, one with a variable or a bare column, or one whose parts are all plain).

    With `home` (the statement's table), a part that aggregates ONE other table only
    (`MIN(Date[Date])` beside a measure of the fact) is such a part too: in DAX it is that
    table's aggregate in the row's context, which a statement over that table computes
    (`_measure_homes`), joined on the visual's rows."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if _statement_form(formula, measures, table_of, graph, row_formulas):
        return None
    if home is not None and len(_tables_read(formula, measures, table_of)) < 2:
        home = None
    parts: list[str] = []

    def own_statement(f: str) -> bool:
        if _statement_form(f, measures, table_of, graph, row_formulas):
            return True
        if home is None:
            return False
        # An aggregate (or a measure) of one other table; a context test such as
        # HASONEVALUE reads no rows and stays in the statement.
        try:
            ft = _strip_parens(tokenize(f))
        except CompileError:
            return False
        fh = _call_head(ft)[0]
        agg = (fh in _FOREIGN_AGGREGATES or (len(ft) == 1 and ft[0][0] == "col"))
        read = _tables_read(f, measures, table_of)
        return agg and len(read) == 1 and _bare(home) not in read

    def walk(t: list) -> list | None:
        out, i = [], 0
        while i < len(t):
            k, v = t[i]
            if k == "func" and i + 1 < len(t) and t[i + 1][0] == "lparen":
                end, args = _call_args(t, i + 1)
                if end is None:
                    return None
                up = v.upper()
                scalar_minmax = up in ("MIN", "MAX") and len(args) == 2
                if up in _SCALAR_WRAPPERS or scalar_minmax:
                    out += [t[i], t[i + 1]]
                    for j, a in enumerate(args):
                        w = walk(a)
                        if w is None:
                            return None
                        if j:
                            out.append(("comma", ","))
                        out += w
                    out.append(("rparen", ")"))
                else:
                    parts.append(_text_of(t[i:end + 1]))
                    out.append(("col", f"[__part{len(parts) - 1}]"))
                i = end + 1
                continue
            if k == "func" and v.upper() in ("VAR", "RETURN"):
                return None
            if k in ("qualified", "quoted_qualified"):
                return None
            if k == "col":
                key = _column_ref(v).lower()
                if key not in measures or key in row_formulas:
                    return None
                parts.append(measures[key])
                out.append(("col", f"[__part{len(parts) - 1}]"))
                i += 1
                continue
            out.append(t[i])
            i += 1
        return out
    outer = walk(toks)
    if outer is None or not any(own_statement(f) for f in parts):
        return None
    return outer, parts


def _composite_statement(unit: dict, measures: dict[str, str], table_of: dict[str, str],
                         graph: list[dict], row_formulas: set[str], again, rules,
                         dialect: str) -> Result | None:
    """The unit with each composite measure (`_composite_form`) split into its parts: the
    parts compiled as measures of their own (joined on the visual's rows), then each
    composite's expression over its parts' values, row by row with DAX's blank rules. A row
    where every measure the visual shows is blank is left out, as the visual leaves it out."""
    comps: dict[str, tuple[list, list[str]]] = {}
    for m in unit.get("measures") or []:
        if m.get("formula_language") == "dax" and m.get("formula"):
            c = _composite_form(m["formula"], measures, table_of, graph, row_formulas,
                                home=(unit.get("warehouse_tables") or [None])[0])
            if c is not None:
                comps[m["name"]] = c
    if not comps:
        return None
    inner: list[dict] = [m for m in unit.get("measures") or [] if m["name"] not in comps]
    names: dict[str, list[str]] = {}
    for n, (_outer, parts) in comps.items():
        names[n] = []
        for k, f in enumerate(parts):
            pname = f"__{_measure_alias(n)}_part{k}"
            names[n].append(pname)
            inner.append({"name": pname, "formula": f, "formula_language": "dax",
                          "aggregation": None})
    sub = dict(unit, measures=inner)
    r = again(sub)
    if r.coverage == REFUSED or not r.sql:
        return r
    groups, _vals = _leaf_names(r, sub)
    cols = [f"j.{g} AS {g}" if g != _out_name(g) else f"j.{g}" for g in groups]
    shown = []
    for m in unit.get("measures") or []:
        alias = _measure_alias(m["name"])
        if m["name"] in comps:
            outer, _parts = comps[m["name"]]
            text = _text_of(outer)
            for k, pname in enumerate(names[m["name"]]):
                text = text.replace(f"[__part{k}]", f"[{_measure_alias(pname)}]")
            try:
                expr, p = compile_expression(text, "dax", rules, "j", dialect)
            except CompileError as exc:
                res = Result()
                res.reason = f"measure {m['name']!r}: {exc}"
                return res
            r.rules_used += list(p.used)
            cols.append(f"{expr} AS {alias}")
        else:
            cols.append(f"j.{alias}")
        shown.append(cols[-1].rsplit(" AS ", 1)[0])
    body = "\n".join("    " + ln if ln.strip() else ln for ln in r.sql.splitlines())
    r.sql = ("SELECT " + ",\n       ".join(cols) + f"\nFROM   (\n{body}\n) j"
             + ("\nWHERE  " + "\n    OR ".join(f"({e}) IS NOT NULL" for e in shown)
                if shown else ""))
    r.rules_used.append("engine.dax_composite_measure")
    # A total grain of the unit: the same split, each total's parts over its own rows.
    r.totals = []
    for tot in unit.get("totals") or []:
        tu = dict(unit, group_by=list(tot.get("group_by") or []), totals=[])
        gbt = list(unit.get("group_by_tables") or [])
        if gbt:
            gb = list(unit.get("group_by") or [])
            tu["group_by_tables"] = [gbt[gb.index(g)] for g in tu["group_by"]
                                     if g in gb and gb.index(g) < len(gbt)]
        tr = _composite_statement(tu, measures, table_of, graph, row_formulas, again, rules,
                                  dialect) or again(tu)
        r.totals.append({"kind": tot.get("kind"), "group_by": list(tot.get("group_by") or []),
                         "sql": tr.sql or "", "reason": tr.reason or None})
    return r


def _constant_item(item: str, alias: str) -> bool:
    """Whether one select item reads no row of the statement: no column of any alias, no
    aggregate, no subquery."""
    expr = item.rsplit(" AS ", 1)[0] if " AS " in item else item
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_\"]", expr) or "SELECT" in expr.upper() \
            or "__" in expr:
        # A column, a subquery, or a placeholder another statement fills (`__LODAGG1__`).
        return False
    return not re.search(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|STDDEV\w*|VAR\w*|LISTAGG|"
                         r"ARRAY_AGG|ANY_VALUE|BOOL_\w+)\s*\(", expr, re.I)


def _joined_statements(unit: dict, parts: list[tuple[dict, Result]], rules,
                       dialect: str) -> Result:
    """Measures that join the tables differently (USERELATIONSHIP, CROSSFILTER) are one
    statement each, over the same grouping and filters; their rows are joined on the grouping
    columns, NULL matching NULL, keeping a row any of them has (a visual lists a row where any
    value is not blank)."""
    if len(parts) == 1:
        return parts[0][1]
    res = Result()
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        res.coverage = REFUSED
        res.reason = (f"measures joining the tables along different relationships are joined "
                      f"on their rows, NULL matching NULL, which has no form for {dialect}")
        return res
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    gb = list(unit.get("group_by") or [])
    names_of: dict[str, str] = {}
    froms, keyexprs = [], [[] for _ in gb]
    for k, (sub, r) in enumerate(parts):
        if not r.sql:
            res.coverage = REFUSED
            res.reason = r.reason
            res.untranslatable = list(r.untranslatable)
            return res
        groups, vals = _leaf_names(r, sub)
        for v in vals:
            names_of[v] = f"s{k}.{v}"
        if k == 0:
            froms.append(f"(\n{r.sql}\n) s{k}")
        else:
            on = " AND ".join(tmpl.format(f"s{k}.{groups[i]}",
                                          "COALESCE(" + ", ".join(keyexprs[i]) + ")"
                                          if len(keyexprs[i]) > 1 else keyexprs[i][0])
                              for i in range(len(gb)))
            froms.append(f"FULL OUTER JOIN (\n{r.sql}\n) s{k} ON {on}" if gb else
                         f"CROSS JOIN (\n{r.sql}\n) s{k}")
        for i in range(len(gb)):
            keyexprs[i].append(f"s{k}.{groups[i]}")
        res.rules_used += r.rules_used
        res.provisional_rules += r.provisional_rules
        res.unknown += r.unknown
        res.untranslatable += r.untranslatable
    keys = [(f"COALESCE({', '.join(x)})" if len(x) > 1 else x[0])
            + f" AS {_out_name(x[0])}" for x in keyexprs]
    vals = [names_of[_measure_alias(m["name"])] + f" AS {_measure_alias(m['name'])}"
            for m in unit.get("measures") or [] if _measure_alias(m["name"]) in names_of]
    res.sql = "SELECT " + ",\n       ".join(keys + vals) + "\nFROM   " + "\n".join(froms)
    res.rules_used.append("engine.statements_per_relationship")
    failed = [r.reason for _s, r in parts if r.reason]
    res.coverage = PARTIAL if (res.untranslatable or failed) else TRANSLATED
    res.reason = "; ".join(failed) if failed else ""
    return res


_ITERATORS = ("SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX")


def _text_of(toks: list) -> str:
    return " ".join(v for _k, v in toks)


def _strip_parens(toks: list) -> list:
    while len(toks) >= 2 and toks[0][0] == "lparen" and toks[-1][0] == "rparen" \
            and _call_args([("func", "X")] + toks, 1)[0] == len(toks):
        toks = toks[1:-1]
    return toks


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


def _not_row_level(toks: list, measures: dict[str, str], row_formulas: set[str]) -> bool:
    """Whether an iterator's expression computes over other rows than the iterated one: a
    measure (a context transition), an aggregate, a CALCULATE or RELATEDTABLE."""
    for k, v in toks:
        if k == "col" and _column_ref(v).lower() in measures \
                and _column_ref(v).lower() not in row_formulas \
                and not _context_free(_column_ref(v).lower(), measures, "dax"):
            return True
        if k == "func" and v.upper() in ("RELATEDTABLE", "CALCULATE", "COUNTROWS", "SUM",
                                         "AVERAGE", "MIN", "MAX", "COUNT", "DISTINCTCOUNT",
                                         "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX"):
            return True
    return False


def _member_grain(formula: str, measures: dict[str, str], table_of: dict[str, str],
                  graph: list[dict], row_formulas: set[str], depth: int = 0,
                  columns: dict[str, set[str]] | None = None) -> dict | None:
    """A DAX measure that iterates the MEMBERS of a table or a column and evaluates, per
    member, an expression over other rows (`AVERAGEX(Customer, COUNTROWS(RELATEDTABLE(
    Sales)))`, `SUMX(VALUES(Date[Month]), [Sales])`, `COUNTROWS(FILTER(VALUES(Customer[Key]),
    [Sales] > 1000))`): {iterator, table, keys, expr, pred, blank_member}, or None for any
    other formula.

    Each member is a row of the visual's rows at a finer grain (the visual's grouping plus
    the members' keys), so the measure is the statement at that grain, aggregated again
    (`_member_grain_statement`). Iterating a TABLE skips its blank row (seen live: 200
    customers, not 201); VALUES / DISTINCT of a column include its blank member."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures and key not in row_formulas:
            return _member_grain(measures[key], measures, table_of, graph, row_formulas,
                                 depth + 1, columns)
        return None
    head, inner = _call_head(toks)
    if head == "CALCULATE":
        # CALCULATE(e, FILTER(VALUES(k) or a table, test of a measure)): e over the members
        # that pass, each read in its own context. For an e that sums its rows that is the
        # sum of e over those members, SUMX(FILTER(...), e) (seen live, the same number).
        cparts = _split_top(inner)
        if len(cparts) != 2 or _call_head(cparts[1])[0] != "FILTER":
            return None
        if _additive(_text_of(cparts[0]), measures):
            head, inner = "SUMX", cparts[1] + [("comma", ",")] + cparts[0]
        else:
            # A non-additive expression over the members that pass, read per member and
            # combined as it combines: DISTINCTCOUNT of the iterated column counts the members
            # that pass (its blank one too, as DISTINCTCOUNT counts a blank); MIN / MAX are
            # the least / greatest of the members' own. Anything else is not such a form.
            eh, ein = _call_head(_strip_parens(cparts[0]))
            fh, fin = _call_head(cparts[1])
            ftab = _split_top(fin)[0] if fin else []
            vh, vin = _call_head(ftab)
            if eh == "DISTINCTCOUNT" and vh in ("VALUES", "DISTINCT") and len(ein) == 1 \
                    and len(vin) == 1 and ein[0][0] in ("qualified", "quoted_qualified") \
                    and vin[0][0] in ("qualified", "quoted_qualified") \
                    and _column_ref(ein[0][1]).lower() == _column_ref(vin[0][1]).lower() \
                    and str(_table_ref(ein[0][1]) or "").lower() == str(
                        _table_ref(vin[0][1]) or "").lower():
                head, inner = "COUNTROWS", cparts[1]
            elif eh in ("MIN", "MAX") and len(_split_top(ein)) == 1:
                head = eh + "X"
                inner = cparts[1] + [("comma", ","), ("func", "CALCULATE"),
                                     ("lparen", "(")] + cparts[0] + [("rparen", ")")]
            else:
                return None
    if head not in _ITERATORS + ("COUNTROWS",):
        return None
    parts = _split_top(inner)
    if head == "COUNTROWS":
        if len(parts) != 1:
            return None
        table_toks, expr = parts[0], None
    else:
        if len(parts) != 2:
            return None
        table_toks, expr = parts
    pred = None
    top = None
    th, tin = _call_head(table_toks)
    if th == "TOPN":
        # TOPN(n, members, order by[, ASC | DESC]): the n members first by the expression
        # (DESC when not said), every member tied with the n-th kept, in the row's context.
        tparts = _split_top(tin)
        if len(tparts) not in (3, 4):
            return None
        nt = _text_of(tparts[0]).replace(" ", "")
        if not nt.isdigit() or int(nt) < 1:
            return None
        order = "DESC"
        if len(tparts) == 4:
            ot = _text_of(tparts[3]).replace(" ", "").upper()
            if ot not in ("ASC", "DESC", "0", "1"):
                return None
            order = "ASC" if ot in ("ASC", "1") else "DESC"
        top = {"n": int(nt), "by": tparts[2], "order": order}
        table_toks = tparts[1]
        th, tin = _call_head(table_toks)
    if th == "FILTER":
        fparts = _split_top(tin)
        if len(fparts) != 2:
            return None
        table_toks, pred = fparts
        th, tin = _call_head(table_toks)
    tmap = {k.lower(): v for k, v in table_of.items()}
    keys: list[tuple[str, str]] = []           # (column, warehouse table)
    if th in ("VALUES", "DISTINCT"):
        if len(tin) != 1 or tin[0][0] not in ("qualified", "quoted_qualified"):
            return None
        phys = tmap.get(str(_table_ref(tin[0][1]) or "").lower())
        if not phys:
            return None
        keys = [(_column_ref(tin[0][1]), phys)]
        table, blank_member = phys, True
        # The values of a column of a table no relationship ends on (a fact's column): its
        # members are the ones its rows hold in the row's context.
        held = not any(_bare(e.get("to_table")) == _bare(phys) and e.get("is_active", True)
                       for e in graph)
    elif th is None and len(table_toks) == 1 and table_toks[0][0] in ("func", "str"):
        name = table_toks[0][1]
        if table_toks[0][0] == "str":
            if not name.startswith("'"):
                return None
            name = name[1:-1].replace("''", "'")
        phys = tmap.get(name.lower())
        if not phys:
            return None
        # The table's key: the column the relationships INTO it end on. A table no
        # relationship ends on (a fact) has no key here, and its rows are not members.
        ends = sorted({str(e.get("to_column")) for e in graph
                       if _bare(e.get("to_table")) == _bare(phys) and e.get("to_column")
                       and e.get("is_active", True)})
        rows = False
        if len(ends) == 1:
            keys = [(ends[0], phys)]
        elif not ends and columns:
            # A table no relationship ends on (a fact): each ROW is iterated, and a measure
            # in it is read in that row's context (a context transition), which filters every
            # column of the table to the row's values. Rows alike in every column are one
            # filter, so the members are the distinct rows, each weighed by how many rows it
            # stands for (`rows`).
            cols = next((sorted(c) for t, c in columns.items() if _bare(t) == _bare(phys)), [])
            if not cols:
                return None
            keys, rows = [(c, phys) for c in cols], True
        else:
            return None
        table, blank_member, held = phys, rows, False
    else:
        return None
    body = [t for t in (expr, pred, top["by"] if top else None) if t]
    if head == "COUNTROWS" and pred is None and top is None:
        return None
    if not any(_not_row_level(t, measures, row_formulas) for t in body):
        return None

    def per_member(t: list) -> str:
        # RELATEDTABLE(R) is R's rows of the member: at the member's grain, R itself.
        out, i = [], 0
        while i < len(t):
            k, v = t[i]
            if k == "func" and v.upper() == "RELATEDTABLE" and i + 1 < len(t) \
                    and t[i + 1][0] == "lparen":
                end, args = _call_args(t, i + 1)
                if end is None or len(args) != 1:
                    raise CompileError("RELATEDTABLE takes one table")
                out += args[0]
                i = end + 1
                continue
            out.append((k, v))
            i += 1
        return _text_of(out)
    try:
        return {"iterator": head, "table": table, "keys": keys,
                "expr": per_member(expr) if expr else None,
                "pred": per_member(pred) if pred else None,
                "top": ({**top, "by": per_member(top["by"])} if top else None),
                "blank_member": blank_member, "held": held,
                "rows": bool(th is None and blank_member and len(keys) > 1)}
    except CompileError:
        return None


def _dax_table_name(grain: dict, table_of: dict[str, str]) -> str:
    """The model's name of the table a member iterator iterates, quoted for DAX."""
    name = next((k for k, v in table_of.items() if _bare(v) == _bare(grain["table"])),
                _bare(grain["table"]))
    return "'" + str(name).replace("'", "''") + "'"


def _formula_home(formula: str, measures: dict[str, str], table_of: dict[str, str],
                  default: str) -> str:
    """The one warehouse table a formula aggregates (the first one it names), else
    `default`."""
    tmap = {k.lower(): v for k, v in table_of.items()}
    for k, v in _expanded_tokens(formula, measures):
        t = _table_ref(v) if k in ("qualified", "quoted_qualified") else (
            v if k == "func" and v.lower() in tmap else None)
        phys = tmap.get(str(t or "").lower())
        if phys:
            return phys
    return default


def _member_grain_statement(unit: dict, m: dict, grain: dict, rules, dialect: str,
                            again, table_of: dict[str, str], measures: dict[str, str]
                            ) -> Result:
    """One member-iterating measure (`_member_grain`) as a statement: the unit at the grain
    of the visual's grouping plus the members' keys, with the expression (and the FILTER's
    predicate) evaluated per member, then aggregated to the visual's rows by the iterator's
    own aggregate (blanks skipped, as DAX skips them)."""
    res = Result()
    groups = list(unit.get("group_by") or [])
    gtabs = list(unit.get("group_by_tables") or [None] * len(groups))
    tables = list(unit.get("warehouse_tables") or [])
    sub_g, sub_t = list(groups), list(gtabs)
    key_names: list[str] = []
    for col, phys in grain["keys"]:
        hit = next((i for i, (g, t) in enumerate(zip(groups, gtabs))
                    if str(g).lower() == str(col).lower()
                    and _bare(t or tables[0]) == _bare(phys)), None)
        if hit is None:
            sub_g.append(col)
            sub_t.append(phys)
        key_names.append(col)
    hidden = []
    if grain["expr"]:
        hidden.append({"name": "member value", "formula": grain["expr"],
                       "formula_language": "dax"})
    if grain["pred"]:
        hidden.append({"name": "member test", "formula": f"IF ( {grain['pred']} , 1 )",
                       "formula_language": "dax"})
    if grain.get("top"):
        hidden.append({"name": "member rank by", "formula": grain["top"]["by"],
                       "formula_language": "dax"})
    if grain.get("rows"):
        # How many of the table's rows each distinct row stands for.
        hidden.append({"name": "member rows", "formula": f"COUNTROWS ( {_dax_table_name(grain, table_of)} )",
                       "formula_language": "dax"})
    exists = (grain.get("held") and not grain.get("rows") and len(_tables_read(
        " ".join(h["formula"] for h in hidden), measures, table_of)
        | {_bare(grain["table"])}) > 1)
    if exists:
        # A value or test reading another table (`MIN(Date[Date])` in the test) is joined on
        # the rows from a statement of its own, which lists rows of the members' grain that
        # the iterated table does not hold in that row's context. The members are only the
        # ones it holds (VALUES in the row's context): a row of the table there.
        hidden.append({"name": "member exists",
                       "formula": f"COUNTROWS ( {_dax_table_name(grain, table_of)} )",
                       "formula_language": "dax"})
    home = _formula_home(" ".join(h["formula"] for h in hidden), measures, table_of,
                         tables[0] if tables else grain["table"])
    sub = {k: v for k, v in unit.items() if k not in ("totals", "top_n", "sort")}
    sub.update(group_by=sub_g, group_by_tables=sub_t, measures=hidden, blank_rows="hidden",
               warehouse_tables=[home] + [t for t in dict.fromkeys(tables + [grain["table"]])
                                          if _bare(t) != _bare(home)])
    r = again(sub)
    if not r.sql or r.coverage == REFUSED:
        res.coverage = REFUSED
        res.reason = (f"measure {m['name']!r} iterates the members of {grain['table']}: "
                      f"{r.reason or 'the statement at their grain does not compile'}")
        res.untranslatable = list(r.untranslatable)
        return res
    names, vals = _leaf_names(r, sub)
    if len(vals) != len(hidden):
        res.coverage = REFUSED
        res.reason = f"measure {m['name']!r}: the per-member values did not compile"
        return res
    outer = names[:len(groups)]
    key_cols = [names[sub_g.index(k)] if k in sub_g else None for k in key_names]
    if exists:
        present, vals = f"g.{vals[-1]}", vals[:-1]
    if grain.get("rows"):
        vals_wo = vals[:-1]
    else:
        vals_wo = vals
    rank_by = None
    if grain.get("top"):
        # The rank-by value is the hidden measure after the value and the test.
        k = (1 if grain["expr"] else 0) + (1 if grain["pred"] else 0)
        rank_by = vals_wo[k]
        vals = [v for v in vals if v != rank_by]
    val = f"g.{vals[0]}" if grain["expr"] else None
    weight = f"g.{vals[-1]}" if grain.get("rows") else None
    test = (f"g.{vals[-2 if weight else -1]} = 1" if grain["pred"] else None)
    if weight:
        # Each distinct row counts as many times as rows it stands for.
        v = f"CASE WHEN {test} THEN {val} END" if test and val else val
        it = grain["iterator"]
        if it == "COUNTROWS":
            agg = f"NULLIF(SUM(CASE WHEN {test} THEN {weight} END), 0)"
        elif it == "SUMX":
            agg = f"SUM({weight} * {v})"
        elif it == "AVERAGEX":
            agg = (f"SUM({weight} * {v}) / NULLIF(SUM(CASE WHEN {v} IS NOT NULL THEN "
                   f"{weight} END), 0)")
        elif it in ("MINX", "MAXX"):
            agg = f"{it[:3]}({v})"
        else:
            agg = f"NULLIF(SUM(CASE WHEN {v} IS NOT NULL THEN {weight} END), 0)"
    elif grain["iterator"] == "COUNTROWS":
        agg = f"NULLIF(COUNT(CASE WHEN {test} THEN 1 END), 0)"
    else:
        emit = (rules.function("dax", grain["iterator"], dialect) or {}).get("emit") or {}
        tmpl = emit.get("row_template") or emit.get("template")
        if not tmpl:
            res.coverage = REFUSED
            res.reason = f"no rule row for {grain['iterator']}"
            return res
        arg = f"CASE WHEN {test} THEN {val} END" if test else val
        agg = _apply({"template": tmpl}, [arg], grain["iterator"])
    where = ([f"g.{k} IS NOT NULL" for k in key_cols if k] if not grain["blank_member"]
             else [])
    if exists:
        where.append(f"{present} IS NOT NULL")
    # A text column grouped by its folded key (`engine.text_key_group`) shows one spelling
    # per member at the finer grain; at the visual's grain the spellings are one value again.
    folded = {n for n in outer if re.search(rf"(?m)^(?:SELECT|\s+)\s*MIN\(.*\) AS {re.escape(n)},?$",
                                            r.sql.split("\nFROM", 1)[0])
              and "OVER (" not in r.sql.split("\nFROM", 1)[0].split(f" AS {n}")[0][-200:]}
    sel = [(f"MIN(g.{n}) AS {n}" if n in folded else f"g.{n} AS {n}") for n in outer]
    sel.append(f"{agg} AS {_measure_alias(m['name'])}")
    inner_sql = r.sql
    if rank_by:
        # TOPN's members: ranked within the visual's row (RANK: every member tied with the
        # n-th is kept), a BLANK ordered as zero.
        t = grain["top"]
        part = f"PARTITION BY {', '.join('g0.' + n for n in outer)} " if outer else ""
        base = r.sql
        if (t["order"] == "ASC" or grain["iterator"] == "COUNTROWS") and not grain["held"] \
                and not grain.get("rows") and len(grain["keys"]) == 1:
            # The members TOPN ranks are every member of the column (its table's own filters
            # only), a member with no rows in the row's context ranking as a BLANK: first when
            # ascending (seen live: the bottom two products of a year are a never-sold one
            # and the least sold), counted by COUNTROWS.
            base = _every_member_rows(unit, grain, r, sub, names, again, rules, dialect)
            if base is None:
                res.coverage = REFUSED
                res.reason = (f"measure {m['name']!r}: TOPN ranks every member of "
                              f"{grain['table']}, whose members do not compile")
                return res
        if exists:
            # Only the members the iterated table holds in the row's context are ranked.
            base = (f"SELECT x.*\nFROM   (\n{_indent(r.sql)}\n) x\n"
                    f"WHERE  x.{present[2:]} IS NOT NULL")
        inner_sql = (f"SELECT g0.*,\n       RANK() OVER ({part}ORDER BY COALESCE(g0.{rank_by}, 0) "
                     f"{t['order']}) AS member_rank\nFROM   (\n" + _indent(base) + "\n) g0")
        where.append(f"g.member_rank <= {int(t['n'])}")
    sql = ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n"
           + "\n".join("    " + ln for ln in inner_sql.splitlines()) + "\n) g")
    if where:
        sql += "\nWHERE  " + "\n  AND  ".join(where)
    if outer:
        sql += "\nGROUP BY " + ", ".join(_Parser._text_key(f"g.{n}") if n in folded
                                          else f"g.{n}" for n in outer)
        if unit.get("blank_rows") == "hidden":
            sql += f"\nHAVING {agg} IS NOT NULL"
    res.sql = sql
    res.rules_used = list(r.rules_used) + ["engine.dax_member_iterator"]
    res.provisional_rules, res.unknown = list(r.provisional_rules), list(r.unknown)
    res.untranslatable = list(r.untranslatable)
    res.coverage = PARTIAL if r.coverage == PARTIAL else TRANSLATED
    res.reason = r.reason if r.coverage == PARTIAL else ""
    res.gotchas.append(
        f"{m['name']}: {grain['iterator']} over the members of {grain['table']} "
        f"({', '.join(k for k in key_names)}): the value per member, at the visual's grouping "
        f"and that key, then {'counted where the test holds' if grain['iterator'] == 'COUNTROWS' else 'aggregated'}"
        + ("; the table's blank row is not iterated" if not grain["blank_member"] else
           "; the blank member is a member"))
    return res


def _every_member_rows(unit: dict, grain: dict, r: Result, sub: dict, names: list[str],
                       again, rules, dialect: str) -> str | None:
    """The member grain's rows with every member of the iterated column beside each of the
    visual's rows: the statement's own rows, and a row of NULL values for each member it does
    not hold there (members of the column's table under that table's own filters)."""
    col, phys = grain["keys"][0]
    mu = {"unit_id": f"{unit.get('unit_id')}::members", "title": unit.get("title"),
          "measures": [], "group_by": [col], "group_by_tables": [phys],
          "filters": [f for f in unit.get("filters") or []
                      if f.get("table") and _bare(f["table"]) == _bare(phys)],
          "warehouse_tables": [phys], "join_graph": []}
    mem = again(mu)
    op = rules.operator("null_safe_eq", dialect)
    if not mem.sql or op is None:
        return None
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    groups = names[:len(sub["group_by"])]
    vals = [n for n in names[len(sub["group_by"]):]] if len(names) > len(sub["group_by"]) \
        else []
    gb = [str(g).lower() for g in sub["group_by"]]
    k = gb.index(str(col).lower())
    outer = [g for i, g in enumerate(groups) if i != k]
    mname = _leaf_names(mem, mu)[0][0]
    leaf_names, leaf_vals = _leaf_names(r, sub)
    sel = [(f"mm.{mname}" if i == k else f"o.{g}") + f" AS {g}" for i, g in enumerate(groups)]
    sel += [f"NULL AS {v}" for v in leaf_vals]
    on = " AND ".join([nse.format(f"v.{g}", f"o.{g}") for g in outer]
                      + [nse.format(f"v.{groups[k]}", f"mm.{mname}")])
    o = (f"(\n    SELECT DISTINCT {', '.join('x.' + g for g in outer)}\n    FROM   (\n"
         + _indent(_indent(r.sql)) + "\n    ) x\n) o\nCROSS JOIN " if outer else "")
    return (f"SELECT {', '.join(groups + leaf_vals)}\nFROM   (\n{_indent(r.sql)}\n) v0\n"
            f"UNION ALL\nSELECT " + ",\n       ".join(sel) + "\nFROM   " + o
            + f"(\n{_indent(mem.sql)}\n) mm\nWHERE  NOT EXISTS (\n    SELECT 1\n    FROM   (\n"
            + _indent(_indent(r.sql)) + f"\n    ) v\n    WHERE  {on}\n)")


def _rank_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
               row_formulas: set[str], depth: int = 0,
               graph: list[dict] | None = None) -> dict | None:
    """`RANKX(ALL(T[c]) | ALLSELECTED(T[c]), expr[, value[, ASC | DESC[, SKIP | DENSE]]])`
    as {column, table, kind, expr, value, order, ties}, or None for any other formula.
    `ALL(T)` / `ALLSELECTED(T)` of a whole table ranks its rows, each by the column the
    relationships into it end on (`table_wide`: every filter and grouping on T is removed)."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures and key not in row_formulas:
            return _rank_form(measures[key], measures, table_of, row_formulas, depth + 1,
                              graph)
        return None
    head, inner = _call_head(toks)
    if head != "RANKX":
        return None
    parts = _split_top(inner)
    if not 2 <= len(parts) <= 5:
        return None
    rh, rin = _call_head(parts[0])
    if rh not in ("ALL", "ALLSELECTED") or len(rin) != 1:
        return None
    tmap = {k.lower(): v for k, v in table_of.items()}
    table_wide = False
    if rin[0][0] in ("qualified", "quoted_qualified"):
        phys = tmap.get(str(_table_ref(rin[0][1]) or "").lower())
        column = _column_ref(rin[0][1])
    elif rin[0][0] in ("func", "str") and graph is not None:
        name = rin[0][1]
        if rin[0][0] == "str":
            if not name.startswith("'"):
                return None
            name = name[1:-1].replace("''", "'")
        phys = tmap.get(name.lower())
        ends = sorted({str(e.get("to_column")) for e in graph
                       if phys and _bare(e.get("to_table")) == _bare(phys)
                       and e.get("to_column") and e.get("is_active", True)})
        if len(ends) != 1:
            return None
        column, table_wide = ends[0], True
    else:
        return None
    if not phys:
        return None

    def word(i: int, allowed: tuple[str, ...], default: str) -> str | None:
        if len(parts) <= i or not parts[i]:
            return default
        w = parts[i][0][1].upper().strip("'\"") if len(parts[i]) == 1 else ""
        if w in ("0", "FALSE"):
            w = allowed[0]
        elif w in ("1", "TRUE"):
            w = allowed[1]
        return w if w in allowed else None
    order = word(3, ("DESC", "ASC"), "DESC")
    ties = word(4, ("SKIP", "DENSE"), "SKIP")
    if order is None or ties is None:
        return None
    value = _text_of(parts[2]) if len(parts) > 2 and parts[2] else None
    return {"column": column, "table": phys, "kind": rh, "table_wide": table_wide,
            "expr": _text_of(parts[1]), "value": value, "order": order, "ties": ties}


_ADDITIVE = {"SUM", "SUMX", "COUNT", "COUNTA", "COUNTROWS", "CALCULATE", "RELATED",
             "KEEPFILTERS", "FILTER"}


def _additive(formula: str, measures: dict[str, str]) -> bool:
    """Whether a formula is a sum of its rows' values (a SUM, a COUNT, a SUMX, their sums and
    differences): what the members of a window add up to."""
    toks = _expanded_tokens(formula, measures)
    depth_x = 0
    stack: list[bool] = []
    for i, (k, v) in enumerate(toks):
        if k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
            if v.upper() not in _ADDITIVE:
                return False
        if k == "lparen":
            prev = toks[i - 1] if i else ("", "")
            inside = prev[0] == "func" and prev[1].upper() in ("SUMX", "FILTER")
            stack.append(inside)
            depth_x += inside
        elif k == "rparen" and stack:
            depth_x -= stack.pop()
        elif k == "op" and v == "/":
            return False
        elif k == "op" and v == "*" and depth_x == 0:
            return False
    return True


def _window_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                 row_formulas: set[str], depth: int = 0) -> dict | None:
    """`CALCULATE(expr, OFFSET(n, rel, ORDERBY(...)))`, `INDEX(n, rel, ORDERBY(...))` or
    `WINDOW(from, ABS|REL, to, ABS|REL, rel, ORDERBY(...))`, rel ALL / ALLSELECTED of columns
    of one table: {fn, n | bounds, columns [(col, table)], kind, order [(col, dir)], expr}, or
    None for any other formula."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures and key not in row_formulas:
            return _window_form(measures[key], measures, table_of, row_formulas, depth + 1)
        return None
    head, inner = _call_head(toks)
    if head != "CALCULATE":
        return None
    parts = _split_top(inner)
    if len(parts) != 2:
        return None
    fn, fin = _call_head(parts[1])
    if fn not in ("OFFSET", "INDEX", "WINDOW", "TOPN"):
        return None
    args = _split_top(fin)
    tmap = {k.lower(): v for k, v in table_of.items()}

    def number(t: list) -> int | None:
        txt = _text_of(t).replace(" ", "")
        return int(txt) if re.fullmatch(r"-?\d+", txt) else None
    out: dict = {"fn": fn, "expr": _text_of(parts[0])}
    if fn == "TOPN":
        # TOPN(n, ALL | ALLSELECTED(columns), order expression[, ASC | DESC]): the members
        # ranked by the expression (a blank as 0, ties kept), the first n.
        if len(args) not in (3, 4):
            return None
        n = number(args[0])
        rh, rin = _call_head(args[1])
        d = _text_of(args[3]).upper() if len(args) == 4 else "DESC"
        d = {"0": "DESC", "FALSE": "DESC", "1": "ASC", "TRUE": "ASC"}.get(d, d)
        if n is None or n < 1 or rh not in ("ALL", "ALLSELECTED") or d not in ("ASC", "DESC"):
            return None
        cols = []
        for p in _split_top(rin):
            if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
                return None
            phys = tmap.get(str(_table_ref(p[0][1]) or "").lower())
            if not phys:
                return None
            cols.append((_column_ref(p[0][1]), phys))
        if not cols or len({_bare(t) for _c, t in cols}) != 1 or not _additive(
                out["expr"], measures):
            return None
        out.update(n=n, columns=cols, kind=rh, by=_text_of(args[2]), direction=d,
                   order=[(c, "ASC") for c, _t in cols])
        return out
    if fn == "WINDOW":
        if len(args) < 6:
            return None
        a, at, b, bt = number(args[0]), _text_of(args[1]).upper(), number(args[2]), \
            _text_of(args[3]).upper()
        if a is None or b is None or at not in ("ABS", "REL") or bt not in ("ABS", "REL"):
            return None
        out["bounds"] = ((a, at), (b, bt))
        rest = args[4:]
    else:
        if len(args) < 3:
            return None
        n = number(args[0])
        if n is None or (fn == "INDEX" and n == 0):
            return None
        out["n"] = n
        rest = args[1:]
    rh, rin = _call_head(rest[0])
    if rh not in ("ALL", "ALLSELECTED"):
        return None
    cols = []
    for p in _split_top(rin):
        if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
            return None
        phys = tmap.get(str(_table_ref(p[0][1]) or "").lower())
        if not phys:
            return None
        cols.append((_column_ref(p[0][1]), phys))
    if not cols or len({_bare(t) for _c, t in cols}) != 1:
        return None
    oh, oin = _call_head(rest[1]) if len(rest) > 1 else (None, [])
    if oh != "ORDERBY":
        return None
    # The optional arguments after ORDERBY: how blanks sort (DEFAULT / KEEP: a blank is the
    # lowest; FIRST; LAST) and PARTITIONBY (the window moves inside each combination of
    # those columns). MATCHBY is not read.
    blanks, partition = "DEFAULT", []
    for extra in rest[2:]:
        eh, ein = _call_head(extra)
        if eh == "PARTITIONBY":
            for p in _split_top(ein):
                if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
                    return None
                c = _column_ref(p[0][1])
                if c.lower() not in {x.lower() for x, _t in cols}:
                    return None
                partition.append(c)
        elif len(extra) == 1 and extra[0][1].upper() in ("DEFAULT", "KEEP", "FIRST", "LAST"):
            blanks = extra[0][1].upper()
        elif extra:
            return None
    order, ob = [], _split_top(oin)
    i = 0
    while i < len(ob):
        p = ob[i]
        if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
            return None
        d = "ASC"
        if i + 1 < len(ob) and len(ob[i + 1]) == 1 and ob[i + 1][0][1].upper() in (
                "ASC", "DESC", "0", "1", "TRUE", "FALSE"):
            w = ob[i + 1][0][1].upper()
            d = "DESC" if w in ("DESC", "0", "FALSE") else "ASC"
            i += 1
        c = _column_ref(p[0][1])
        if c.lower() not in {x.lower() for x, _t in cols}:
            return None
        order.append((c, d))
        i += 1
    if {c.lower() for c, _d in order} | {c.lower() for c in partition} != {
            c.lower() for c, _t in cols} or {c.lower() for c, _d in order} & {
            c.lower() for c in partition}:
        return None
    if fn == "WINDOW" and not _additive(out["expr"], measures):
        return None
    out.update(columns=cols, kind=rh, order=order, partition=partition, blanks=blanks)
    return out


def _window_statement(unit: dict, m: dict, win: dict, rules, dialect: str, again,
                      measures: dict[str, str] | None = None) -> Result:
    """OFFSET / INDEX / WINDOW over the members of the relation's columns (`_window_form`),
    the visual's other grouping columns kept: the expression for every member (a member with
    no rows is BLANK and keeps its place), ordered by ORDERBY, and the row's value read at the
    member OFFSET moves to, the INDEX-th member, or the sum over the WINDOW's members. The
    visual lists every member of its grouping columns whose value is not blank (the previous
    year's sales show on a year with none of its own)."""
    res = Result()
    op = rules.operator("null_safe_eq", dialect)
    row = rules.function("dax", win["fn"], dialect) or {}
    tmpl = (row.get("emit") or {}).get("window_template")
    if op is None or not tmpl:
        res.coverage = REFUSED
        res.reason = (f"{win['fn']} has no form for the {dialect} dialect (null-safe equality "
                      f"and the dax.{win['fn'].lower()} row's window)")
        return res
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    groups = list(unit.get("group_by") or [])
    tables = list(unit.get("warehouse_tables") or [])
    gtabs = list(unit.get("group_by_tables") or [None] * len(groups))
    rel = {(c.lower(), _bare(t)) for c, t in win["columns"]}

    def in_rel(g: str, t: str | None) -> bool:
        return (str(g).lower(), _bare(t or tables[0])) in rel
    grouped_rel = {(str(g).lower(), _bare(t or tables[0])) for g, t in zip(groups, gtabs)
                   if in_rel(g, t)}
    # A row with no current member (a total, or a visual that does not group by the
    # relation): the union of the members each member's window reaches (seen live: the
    # previous year at the grand total is every year but the last).
    # The visual grouping by the PARTITIONBY columns only: each partition's row has no
    # current member either, the union is taken within the partition (seen live: a year's
    # row shows the months its months' previous months reach).
    part_set = {(c.lower(), _bare(t)) for c, t in win["columns"]
                if c.lower() in {p.lower() for p in win.get("partition") or []}}
    union = win["fn"] != "TOPN" and (not grouped_rel or (part_set and grouped_rel == part_set))
    if win["fn"] != "TOPN" and grouped_rel and grouped_rel != rel and not union:
        res.coverage = REFUSED
        res.reason = (f"measure {m['name']!r}: {win['fn']} moves along "
                      f"{[c for c, _t in win['columns']]}, of which the visual groups by only "
                      f"some")
        return res
    if union and win["fn"] == "OFFSET" and not _additive(win["expr"], measures or {}):
        res.coverage = REFUSED
        res.reason = (f"measure {m['name']!r}: OFFSET on a row with no current member is the "
                      f"union of the members it reaches, which a non-additive expression is "
                      f"not summed over")
        return res
    others = [(g, t) for g, t in zip(groups, gtabs) if not in_rel(g, t)]
    rel_cols = [c for c, _t in win["columns"]]
    rel_tab = win["columns"][0][1]

    def on_rel(f: dict) -> bool:
        return (str(f.get("column") or "").lower(), _bare(f.get("table") or tables[0])) in rel
    filters = list(unit.get("filters") or [])
    base_filters = [f for f in filters if not on_rel(f)] if win["kind"] == "ALL" else filters
    member_filters = [] if win["kind"] == "ALL" else filters
    plain = {k: v for k, v in unit.items() if k not in ("totals", "top_n", "sort")}
    if tables and _bare(rel_tab) not in {_bare(t) for t in tables}:
        plain["warehouse_tables"] = tables + [rel_tab]

    def stmt(gb, gt, formula, flt, shown, by: str | None = None) -> Result:
        ms = [{"name": "member value", "formula": formula, "formula_language": "dax"}]
        if by:
            ms.append({"name": "member order", "formula": by, "formula_language": "dax"})
        return again(dict(plain, group_by=gb, group_by_tables=gt, filters=flt, blank_rows=shown,
                          measures=ms))
    s_r = stmt(groups, gtabs, "BLANK ( )", filters, "shown")
    if not groups:
        # A card: one row, with no grouping column.
        s_r.sql = "SELECT 1 AS one"
    b_r = stmt([g for g, _t in others] + rel_cols, [t for _g, t in others] + [rel_tab] * len(
        rel_cols), win["expr"], base_filters, "hidden", win.get("by"))
    m_r = stmt(rel_cols, [rel_tab] * len(rel_cols), "BLANK ( )", member_filters, "shown")
    for what, r in (("the visual's rows", s_r), ("the members' values", b_r),
                    ("the members", m_r)):
        if not r.sql or r.coverage == REFUSED:
            res.coverage = REFUSED
            res.reason = (f"measure {m['name']!r}: {win['fn']} over {win['kind']}"
                          f"({', '.join(rel_cols)}): {what} do not compile ({r.reason})")
            return res
    s_names, _sv = _leaf_names(s_r, dict(plain, group_by=groups, measures=[]))
    b_names, b_vals = _leaf_names(b_r, dict(plain, group_by=[g for g, _t in others] + rel_cols,
                                            measures=[{"name": "member value"},
                                                      {"name": "member order"}]))
    m_names, _mv = _leaf_names(m_r, dict(plain, group_by=rel_cols, measures=[]))
    if not b_vals:
        res.coverage = REFUSED
        res.reason = f"measure {m['name']!r}: the members' values did not compile"
        return res
    n_o = len(others)
    o_names = [f"o{i}" for i in range(n_o)]
    # f: every (other grouping, member) pair, with the member's value.
    s_other = [s_names[i] for i, (g, t) in enumerate(zip(groups, gtabs)) if not in_rel(g, t)]
    frame_sel = [f"o.{s} AS {o}" for s, o in zip(s_other, o_names)]
    frame_sel += [f"mm.{n} AS r{i}" for i, n in enumerate(m_names)]
    frame_sel.append(f"b.{b_vals[0]} AS v")
    if win.get("by"):
        frame_sel.append(f"b.{b_vals[1]} AS ov" if len(b_vals) > 1 else "NULL AS ov")
    on_b = [nse.format(f"b.{b_names[i]}", f"o.{s_other[i]}") for i in range(n_o)]
    on_b += [nse.format(f"b.{b_names[n_o + i]}", f"mm.{n}") for i, n in enumerate(m_names)]
    o_part = (f"(\n    SELECT DISTINCT {', '.join('s0.' + x for x in s_other)}\n    FROM   (\n"
              + "\n".join("        " + ln for ln in s_r.sql.splitlines()) + "\n    ) s0\n  ) o"
              ) if n_o else "(SELECT 1 AS one) o"
    frame = ("SELECT " + ", ".join(frame_sel) + f"\n  FROM   {o_part}\n  CROSS JOIN (\n"
             + "\n".join("    " + ln for ln in m_r.sql.splitlines()) + "\n  ) mm"
             + "\n  LEFT JOIN (\n" + "\n".join("    " + ln for ln in b_r.sql.splitlines())
             + "\n  ) b\n    ON  " + "\n    AND ".join(on_b))
    pos = {c.lower(): i for i, c in enumerate(rel_cols)}
    blanks = win.get("blanks") or "DEFAULT"

    def nulls(d: str, reverse: bool = False) -> str:
        # DEFAULT: a blank sorts lowest; FIRST / LAST: at that end whatever the direction.
        if blanks == "FIRST":
            return "NULLS LAST" if reverse else "NULLS FIRST"
        if blanks == "LAST":
            return "NULLS FIRST" if reverse else "NULLS LAST"
        low_first = (d == "ASC") != reverse
        return "NULLS FIRST" if low_first else "NULLS LAST"
    order = ", ".join(_sort_term(f"f.r{pos[c.lower()]}", d, nulls(d).split()[1], dialect)
                      for c, d in win["order"])
    pcols = [f"f.o{i}" for i in range(n_o)] + [f"f.r{pos[c.lower()]}"
                                               for c in win.get("partition") or []]
    part = (f"PARTITION BY {', '.join(pcols)} " if pcols else "")
    if win["fn"] == "WINDOW":
        (a, at), (b, bt) = win["bounds"]

        def bound(k: int, kind: str, lo: bool) -> str:
            if kind == "REL":
                return ("CURRENT ROW" if k == 0 else f"{abs(k)} PRECEDING" if k < 0
                        else f"{k} FOLLOWING")
            if k == 1 and lo:
                return "UNBOUNDED PRECEDING"
            if k == -1 and not lo:
                return "UNBOUNDED FOLLOWING"
            raise CompileError(f"WINDOW from an absolute position {k} other than the first "
                               f"or the last")
        try:
            spec = (f"{part}ORDER BY {order} ROWS BETWEEN {bound(a, at, True)} AND "
                    f"{bound(b, bt, False)}")
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"measure {m['name']!r}: {exc}"
            return res
        n = ""
    else:
        n = str(abs(win["n"]))
        if win["fn"] == "INDEX" and win["n"] < 0:
            # The order reversed, a blank (the lowest) last where it was first.
            order = ", ".join(_sort_term(f"f.r{pos[c.lower()]}",
                                         "ASC" if d == "DESC" else "DESC",
                                         nulls(d, True).split()[1], dialect)
                              for c, d in win["order"])
        spec = (f"{part}ORDER BY {order}" + (" ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED "
                                            "FOLLOWING" if win["fn"] == "INDEX" else ""))
    if union:
        # Each member's position (`rn`) of `cnt`; the members some member's window reaches.
        if win["fn"] == "OFFSET":
            k = win["n"]
            cond = f"f.rn <= f.cnt - {-k}" if k < 0 else f"f.rn > {k}"
        elif win["fn"] == "INDEX":
            k = win["n"]
            cond = f"f.rn = {k}" if k > 0 else f"f.rn = f.cnt + 1 - {-k}"
        else:
            (a, at), (b, bt) = win["bounds"]
            lo = (f"{1 + a}" if at == "REL" else str(a) if a > 0 else f"f.cnt + 1 - {-a}")
            hi = (f"f.cnt + {b}" if bt == "REL" else str(b) if b > 0 else f"f.cnt + 1 - {-b}")
            cond = f"f.rn >= {lo} AND f.rn <= {hi}"
        agg = "MAX" if win["fn"] == "INDEX" else "SUM"
        ordered = order.replace("f.r", "f0.r")
        p0 = part.replace("f.o", "f0.o").replace("f.r", "f0.r")
        frame = (f"SELECT f0.*, ROW_NUMBER() OVER ({p0}ORDER BY "
                 f"{ordered}) AS rn,\n         COUNT(*) OVER ({p0.strip()}) AS cnt"
                 f"\n  FROM   (\n  {frame}\n  ) f0")
        w = f"{agg}(CASE WHEN {cond} THEN f.v END)"
        spec = n = ""
    if win["fn"] == "TOPN":
        n = str(win["n"])
        spec = part.strip()
        rank_over = f"{part}ORDER BY COALESCE(f0.ov, 0) {win['direction']}"
    key = ("lag" if win.get("n", 0) < 0 else "lead") if win["fn"] == "OFFSET" else "value"
    if not union:
        w = (tmpl.get(key) if isinstance(tmpl, dict) else tmpl).replace("{0}", "f.v").replace(
            "{n}", n).replace("{over}", spec).replace("{rank}", "f.rk")
    if win["fn"] == "TOPN":
        rk = (tmpl.get("rank") or "").replace("{over}", rank_over.replace("f.o", "f0.o"))
        frame = (f"SELECT f0.*, {rk} AS rk\n  FROM   (\n  {frame}\n  ) f0")
    sel = [f"s.{x} AS {x}" for x in s_names] + [f"x.w AS {_measure_alias(m['name'])}"]
    s_grouped = {str(g).lower() for g, t in zip(groups, gtabs) if in_rel(g, t)}
    rcols = ([f"f.r{i}" for i, c in enumerate(rel_cols) if c.lower() in s_grouped] if union
             else [f"f.r{i}" for i in range(len(rel_cols))]
             if win["fn"] != "TOPN" else
             [f"f.r{i}" for i, c in enumerate(rel_cols) if c.lower() in {
                 str(g).lower() for g, t in zip(groups, gtabs) if in_rel(g, t)}])
    s_rel_by_col = {str(g).lower(): s_names[i] for i, (g, t) in enumerate(zip(groups, gtabs))
                    if in_rel(g, t)}
    on_x = [nse.format(f"x.{o}", f"s.{s}") for o, s in zip(o_names, s_other)]
    on_x += [nse.format(f"x.r{i}", f"s.{s_rel_by_col[c.lower()]}") for i, c in
             enumerate(rel_cols) if c.lower() in s_rel_by_col]
    sql = ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n"
           + "\n".join("    " + ln for ln in s_r.sql.splitlines()) + "\n) s"
           + f"\nLEFT JOIN (\n  SELECT {'' if union else 'DISTINCT '}{', '.join(['f.' + o for o in o_names] + rcols + ['1 AS one'])},"
           + f"\n         {w} AS w\n  FROM   (\n  {frame}\n  ) f"
           + (f"\n  GROUP BY {', '.join(['f.' + o for o in o_names] + rcols)}"
              if union and (o_names or rcols) else "")
           + "\n) x\n  ON  " + ("\n  AND ".join(on_x) if on_x else "1 = 1"))
    if unit.get("blank_rows") != "shown":
        sql += "\nWHERE  x.w IS NOT NULL"
    res.sql = sql
    res.rules_used = list(dict.fromkeys(s_r.rules_used + b_r.rules_used + m_r.rules_used
                                        + [f"engine.dax_{win['fn'].lower()}"]))
    partial = any(r.coverage == PARTIAL for r in (s_r, b_r, m_r))
    res.coverage = PARTIAL if partial else TRANSLATED
    res.reason = b_r.reason if partial else ""
    res.gotchas.append(
        f"{m['name']}: {win['fn']} over {win['kind']}({', '.join(rel_cols)}) ordered by "
        f"{', '.join(c + ' ' + d for c, d in win['order'])}: every member keeps its place, a "
        f"member with no rows too")
    return res


def _rank_statement(unit: dict, m: dict, rank: dict, rules, dialect: str, again) -> Result:
    """RANKX over the members of a column (`_rank_form`), seen live: the row's value is
    compared with the expression evaluated for every member of ALL(column) (ignoring the
    visual's filters on it) or ALLSELECTED(column) (under them), the visual's other grouping
    columns kept; a BLANK compares as 0; SKIP gives 1 + the members that beat it, DENSE 1 + the
    distinct values that do. RANKX is never blank, so the visual lists every member of its
    grouping columns (`blank_rows: shown`)."""
    res = Result()
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        res.coverage = REFUSED
        res.reason = (f"RANKX joins the members to their values, NULL matching NULL, which "
                      f"has no form for the {dialect} dialect")
        return res
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    groups = list(unit.get("group_by") or [])
    tables = list(unit.get("warehouse_tables") or [])
    gtabs = list(unit.get("group_by_tables") or [None] * len(groups))
    col, phys = rank["column"], rank["table"]

    wide = bool(rank.get("table_wide"))

    def is_c(g: str, t: str | None) -> bool:
        # ALL(T) of a whole table: every column of T is ranked over, none kept.
        return ((wide or str(g).lower() == str(col).lower())
                and _bare(t or tables[0]) == _bare(phys))

    def on_c(f: dict) -> bool:
        return ((wide or str(f.get("column") or "").lower() == str(col).lower())
                and _bare(f.get("table") or tables[0]) == _bare(phys))
    others = [(g, t) for g, t in zip(groups, gtabs) if not is_c(g, t)]
    filters = list(unit.get("filters") or [])
    base_filters = [f for f in filters if not on_c(f)] if rank["kind"] == "ALL" else filters
    member_filters = [] if rank["kind"] == "ALL" else filters
    plain = {k: v for k, v in unit.items() if k not in ("totals", "top_n", "sort")}
    if tables and _bare(phys) not in {_bare(t) for t in tables}:
        plain["warehouse_tables"] = tables + [phys]

    def stmt(gb: list, gt: list, formula: str, flt: list, shown: str) -> Result:
        return again(dict(plain, group_by=gb, group_by_tables=gt, filters=flt,
                          blank_rows=shown, measures=[{"name": "rank value", "formula": formula,
                                                       "formula_language": "dax"}]))
    s_r = stmt(groups, gtabs, rank["value"] or rank["expr"], filters, "shown" if groups
               else "hidden")
    b_r = stmt([g for g, _t in others] + [col], [t for _g, t in others] + [phys], rank["expr"],
               base_filters, "hidden")
    m_r = stmt([col], [phys], "BLANK ( )", member_filters, "shown")
    for what, r in (("the row's value", s_r), ("the members' values", b_r),
                    ("the members", m_r)):
        if not r.sql or r.coverage == REFUSED:
            res.coverage = REFUSED
            res.reason = (f"measure {m['name']!r} ranks over {rank['kind']}({col}): "
                          f"{what} do not compile ({r.reason})")
            res.untranslatable = list(r.untranslatable)
            return res
    s_names, s_vals = _leaf_names(s_r, dict(plain, group_by=groups,
                                            measures=[{"name": "rank value"}]))
    b_names, b_vals = _leaf_names(b_r, dict(plain, group_by=[g for g, _t in others] + [col],
                                            measures=[{"name": "rank value"}]))
    m_names, _m = _leaf_names(m_r, dict(plain, group_by=[col], measures=[]))
    if not s_vals or not b_vals:
        res.coverage = REFUSED
        res.reason = f"measure {m['name']!r}: the ranked values did not compile"
        return res
    sv, bv = f"COALESCE(s.{s_vals[0]}, 0)", f"COALESCE(b.{b_vals[0]}, 0)"
    beats = f"{bv} {'>' if rank['order'] == 'DESC' else '<'} {sv}"
    if rank["ties"] == "DENSE":
        count = f"COUNT(DISTINCT CASE WHEN {beats} THEN {bv} END)"
    else:
        count = f"COUNT(CASE WHEN {beats} THEN 1 END)"
    # The row's other grouping columns, by name in each statement.
    s_other = [s_names[i] for i, (g, t) in enumerate(zip(groups, gtabs)) if not is_c(g, t)]
    on = [nse.format(f"b.{b_names[i]}", f"s.{s_other[i]}") for i in range(len(others))]
    on.append(nse.format(f"b.{b_names[-1]}", f"mm.{m_names[0]}"))

    def block(sql: str) -> str:
        return "\n".join("    " + ln for ln in sql.splitlines())
    keys = [f"s.{n}" for n in s_names]
    sel = [f"{k} AS {n}" for k, n in zip(keys, s_names)]
    sel.append(f"1 + {count} AS {_measure_alias(m['name'])}")
    sql = ("SELECT " + ",\n       ".join(sel)
           + f"\nFROM   (\n{block(s_r.sql)}\n) s"
           + f"\nCROSS JOIN (\n    SELECT DISTINCT mm0.{m_names[0]}\n    FROM   (\n"
           + "\n".join("    " + ln for ln in block(m_r.sql).splitlines())
           + f"\n    ) mm0\n) mm"
           + f"\nLEFT JOIN (\n{block(b_r.sql)}\n) b\n  ON  " + "\n  AND ".join(on)
           + "\nGROUP BY " + ", ".join(keys + [f"s.{s_vals[0]}"]))
    res.sql = sql
    res.rules_used = list(dict.fromkeys(s_r.rules_used + b_r.rules_used + m_r.rules_used
                                        + ["engine.dax_rankx"]))
    res.untranslatable = s_r.untranslatable + b_r.untranslatable
    partial = any(r.coverage == PARTIAL for r in (s_r, b_r, m_r))
    res.coverage = PARTIAL if partial else TRANSLATED
    res.reason = "; ".join(r.reason for r in (s_r, b_r) if r.reason) if partial else ""
    res.gotchas.append(
        f"{m['name']}: RANKX over {rank['kind']}({col}) {rank['order']} {rank['ties']}: "
        f"1 + the members of {col} whose value beats the row's (a blank is 0); RANKX is never "
        f"blank, so every member of the visual's grouping columns is a row")
    return res


def compile_unit(unit: dict, rules, dialect: str, *, table_sql: dict[str, str] | None = None,
                 **kw) -> Result:
    """One question unit -> one statement (`_compile_unit`, which documents every argument),
    with each table that IS a statement (`table_sql`) read as that statement. A unit with the
    view's own calculations (`visual_matrix`) is read off the view's matrix, each level of it
    compiled here (readers/visualcalc.py)."""
    inner = kw.pop("_inner", False)
    if unit.get("visual_matrix"):
        from metrics_agent.plugs.readers import visualcalc as _vcalc  # noqa: PLC0415
        res = _vcalc.compile_matrix(
            unit, rules, dialect,
            lambda u: compile_unit(u, rules, dialect, table_sql=table_sql, _inner=True, **kw),
            kw)
        if not inner:
            _hoisted(res, rules, dialect)
        return res
    res = _compile_unit(unit, rules, dialect, **kw)
    if res.sql and table_sql:
        res.sql = _read_statements(res.sql, table_sql)
    if res.sql and unit.get("totals"):
        _compile_totals(unit, res, rules, dialect, table_sql, kw)
    if not inner:
        _hoisted(res, rules, dialect)
    return res


def _hoisted(res: Result, rules, dialect: str) -> None:
    """Each statement of `res` with its marked recursions moved to the top (a dialect whose
    WITH stands only at the top of a statement)."""
    if "/*__rec" not in (res.sql or "") and not any(
            "/*__rec" in str(t.get("sql") or "") for t in getattr(res, "totals", None) or []):
        return
    emit = (rules.operator("recursive_rows", dialect) or {}).get("emit") or {}
    dia = str(dialect or "").lower()
    opener = (emit.get("dialect_templates") or {}).get(dia) or emit.get("template")
    suffix = (emit.get("statement_suffix") or {}).get(dia) or ""
    res.sql = _hoist_recursions(res.sql, opener, suffix) if res.sql else res.sql
    for t in getattr(res, "totals", None) or []:
        if t.get("sql"):
            t["sql"] = _hoist_recursions(t["sql"], opener, suffix)


def _compile_totals(unit: dict, res: Result, rules, dialect: str,
                    table_sql: dict[str, str] | None, kw: dict) -> None:
    """Each total row the view shows (`unit.totals`) as its own statement: the unit grouped by
    only the columns the total keeps, with every filter, join and measure as the leaf rows
    have them. A total is the measures over the total's context, so a distinct count, a
    ratio or a measure over ALL comes out as the tool shows it, never as a sum of rows. A
    total that does not compile is named (the unit is then partial), never summed instead."""
    groups = list(unit.get("group_by") or [])
    tables = list(unit.get("group_by_tables") or [])
    for t in unit["totals"]:
        keep = [g for g in groups if g in set(t.get("group_by") or [])]
        entry = {"kind": t.get("kind") or ("subtotal" if keep else "grand_total"),
                 "group_by": keep, "sql": None, "reason": None}
        limited = {"engine.top_n_by_sort", "engine.top_n_by_first_measure"} & set(
            res.rules_used)
        if limited and keep != groups:
            # A Top N kept by LIMIT ranks the leaf rows; the total over the kept members is
            # not that LIMIT on fewer rows.
            entry["reason"] = (f"the Top {unit['top_n']} is kept by a LIMIT over the leaf "
                               f"rows, which a total over fewer columns cannot reuse")
        else:
            sub = {k: v for k, v in unit.items() if k not in ("totals", "group_by",
                                                              "group_by_tables")}
            sub["group_by"] = keep
            if tables and len(tables) == len(groups):
                sub["group_by_tables"] = [tables[groups.index(g)] for g in keep]
            r = _compile_unit(sub, rules, dialect, **kw)
            if r.sql and r.coverage != REFUSED:
                entry["sql"] = _read_statements(r.sql, table_sql) if table_sql else r.sql
                if r.coverage == PARTIAL:
                    entry["reason"] = r.reason or "partial"
                res.rules_used += [x for x in r.rules_used if x not in res.rules_used]
            else:
                entry["reason"] = r.reason or "the total does not compile"
        if entry["sql"] is None:
            res.coverage = PARTIAL
            res.untranslatable.append({"construct": "total row",
                                       "why": f"{entry['kind']} over {keep}: "
                                              f"{entry['reason']}"})
        res.totals.append(entry)
    if any(e["sql"] is None for e in res.totals) and not res.reason:
        res.reason = "; ".join(f"total {e['kind']} over {e['group_by']}: {e['reason']}"
                               for e in res.totals if e["sql"] is None)


def _limited(res: Result, unit: dict, dialect: str | None = None) -> Result:
    """A compiled statement cut to the unit's Top N rows by LIMIT: ordered by the unit's sort
    (`[name] DESC`, a grouping column or a measure, by its output name), else by its first
    measure descending; a blank sorts last."""
    if not res.sql or res.coverage == REFUSED:
        return res
    n = int(unit["top_n"])
    m = re.fullmatch(r"\s*\[([^\]]+)\]\s*(ASC|DESC)?\s*", str(unit.get("sort") or ""), re.I)
    names = [str(g) for g in unit.get("group_by") or []]
    if m:
        name, direction = m.group(1), (m.group(2) or "DESC").upper()
        hit = next((g for g in names if g.lower() == name.lower()), None)
        col = (_measure_alias(hit) if hit and re.search(
            rf"\bAS {re.escape(_measure_alias(hit))}\b", res.sql) else hit) or _measure_alias(name)
    elif unit.get("measures"):
        col, direction = _measure_alias(unit["measures"][0]["name"]), "DESC"
    else:
        res.coverage = REFUSED
        res.reason = (f"a Top {n} with no measure to rank by: a LIMIT without an order returns "
                      "arbitrary rows")
        res.sql = ""
        return res
    body = "\n".join("    " + ln if ln.strip() else ln for ln in res.sql.splitlines())
    res.sql = (f"SELECT *\nFROM   (\n{body}\n) lim\nORDER BY "
               f"{_sort_term(f'lim.{col}', direction, 'LAST', dialect)}\n{_limit(n, dialect)}")
    res.rules_used.append("engine.top_n_limit_after")
    return res


def _read_statements(sql: str, table_sql: dict[str, str]) -> str:
    """Every `FROM <table>` / `JOIN <table>` of a table the reader gave a statement, as that
    statement. The reader names such a table by a name no warehouse table has, so a reference
    this misses fails loudly at the warehouse rather than reading the bare table."""
    for name, stmt in sorted(table_sql.items(), key=lambda kv: -len(kv[0])):
        body = "(\n" + "\n".join("    " + ln if ln.strip() else ln
                                  for ln in str(stmt).strip().rstrip(";").splitlines()) + "\n)"
        sql = re.sub(r"(\b(?:FROM|JOIN)\s+)" + re.escape(name) + r"(?=\s|$)",
                     lambda m: m.group(1) + body, sql, flags=re.I)
    return sql


def _compile_time_unit(unit: dict, rules, dialect: str, *, alias: str, columns, measures,
                       table_of, source: str, plan: "_JoinPlan", joins: list[str],
                       ctx: "_UnitContext", gcol, calc, native) -> Result:
    """A unit with a time-intelligence measure: one sub-statement per date set, combined.

    WHY A STATEMENT OF ITS OWN. A date set (`_DateSet`) is a function of the dates the
    visual's row stands for: the year-to-date of March 2025 reads 1 January to 31 March, the
    same period last year reads March 2024. No filter over the fact's own rows says that, so
    each date set gets a sub-statement that first lists, per visual row, the date table's
    dates under the visual's date filters (the row's context), derives the set from them, and
    reads the fact rows whose date is in it, through the relationship on that date column.
    The date table's own filters are replaced by the set, as the tool replaces them; every
    other filter and grouping stays.

    Every aggregate is a slot: an aggregate outside any date set is read by the ordinary
    statement, and each sub-statement fills the slots of its own date set. The rows are
    combined by the visual's grouping columns (UNION ALL, then one row per key), and each
    measure's expression reads its slots back, so `DIVIDE([Sales] - [Sales LY], [Sales LY])`
    is computed from both.
    """
    res = Result()
    res.rules_used.append("engine.dax_time_intelligence")
    if native:
        res.reason = "time intelligence over a native query's result set is not compiled"
        return res
    top_filters = [f for f in unit.get("filters") or []
                   if str(f.get("op") or "").lower() == "top_n"
                   and isinstance(f.get("value"), dict)
                   and str(f["value"].get("population") or "") == "filtered"]
    if unit.get("top_n") and not top_filters:
        # A Top N kept by the statement's LIMIT (the latest point of a KPI's trend, a visual's
        # item limit): the time intelligence is computed for every row, which no row's value
        # depends on, and the rows are then ordered and cut, as the visual cuts them.
        inner = _compile_time_unit({k: v for k, v in unit.items() if k not in ("top_n",)},
                                   rules, dialect, alias=alias, columns=columns,
                                   measures=measures, table_of=table_of, source=source,
                                   plan=plan, joins=joins, ctx=ctx, gcol=gcol, calc=calc,
                                   native=native)
        return _limited(inner, unit, dialect)
    row = rules.function("dax", "DATESYTD", "") or {}
    math = ((row.get("emit") or {}).get("date_math") or {}).get((dialect or "").lower())
    if not math:
        res.reason = (f"time intelligence has no date arithmetic for the {dialect or '?'} "
                      f"dialect (the DATESYTD row's date_math)")
        return res

    slots: list[dict] = []
    exprs: list[tuple[dict, str]] = []
    failed: list[str] = []
    home = (table_of, source) if table_of else None
    for m in unit.get("measures", []):
        if m.get("formula_language") != "dax" or not m.get("formula"):
            failed.append(f"{m.get('name')!r}: only a DAX measure is compiled beside time "
                          f"intelligence")
            continue
        seen = len(res.untranslatable)
        before = len(slots)
        try:
            expr = _compile_measure(m, rules, alias, dialect, res, None, measures, unit,
                                    home=home, ctx=ctx, slots=slots)
        except CompileError as exc:
            del slots[before:]
            failed.append(f"{m.get('name')!r}: {exc}")
            new = res.untranslatable[seen:]
            for gap in new:
                gap.setdefault("in_measure", m.get("name") or "")
            if not new:
                res.untranslatable.append({"construct": "measure", "in_measure": m.get("name")
                                           or "", "why": str(exc)})
            continue
        if not re.search(r"\bu\.s\d+\b", expr):
            # Nothing it reads is an aggregate (a constant): read once per row as it is.
            pass
        exprs.append((m, expr))
    if not exprs:
        res.coverage = REFUSED
        res.reason = "; ".join(f"measure {f}" for f in failed) or "no measure compiled"
        return res

    columns_used = {sl["date_set"].column for sl in slots if sl["date_set"] is not None}
    if len(columns_used) != 1:
        res.coverage = REFUSED
        res.reason = ("time intelligence over two date columns in one visual is not compiled"
                      if columns_used else "no date set compiled")
        return res
    dkey = next(iter(columns_used))
    info = ctx.date_keys[dkey]
    dtab, dcol, fcol = info["table"], info["column"], info["fact_column"]
    dbare = _bare(dtab)

    sp: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                           "display": []}
    try:
        ctx_preds, dim_preds, deferred = _plan_filters(unit, rules, alias, res, None,
                                                       resolve=calc, dialect=dialect,
                                                       place=plan.at, text_key=ctx.text_key,
                                                       special=sp)
    except CompileError as exc:
        res.reason = f"filter calc: {exc}"
        return res
    if deferred or ctx_preds or sp["datasource"] or sp["measure_tests"] or sp["display"] \
            or any(f not in top_filters for f in sp["members"]):
        res.coverage = REFUSED
        res.reason = ("a filter time intelligence cannot carry (a context filter, or one the "
                      "compiler does not read) is applied to this visual")
        return res

    def top_preds(members: list[dict]) -> list[str]:
        """The members each Top N filter keeps, ranked within every filter of the visual by
        its measure as a plain statement computes it, as row predicates."""
        out = []
        for f in members:
            out.append(_ranked_members(
                f, calc(str(f["column"]), f.get("table"))
                or f"{plan.at(str(f['column']), f.get('table'))}.{f['column']}",
                f["value"], alias, dialect, res, src=source, joins=joins,
                dim_preds=dim_preds, compile_by=_rank_by))
        return out

    def _rank_by(formula: str) -> str:
        try:
            return _compile_measure({"name": "top_n_by", "formula": formula,
                                     "formula_language": "dax"}, rules, alias, dialect,
                                    Result(), None, measures, unit, home=home)
        except _TimeIntelligence:
            raise CompileError("its ranking measure is itself time intelligence") from None
    try:
        member_preds = top_preds(sp["members"])
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = f"time intelligence under a Top N filter: {exc}"
        return res
    if member_preds:
        res.rules_used.append("engine.dax_time_intelligence_top_n")

    # The date table's alias in the ordinary plan, when the visual reads it.
    d_alias = next((a for a, t in plan.table_at.items() if t == dbare and a != alias), None)
    d_ref = re.compile(rf"\b{re.escape(d_alias)}\.") if d_alias else None
    other_joins = [j for j in joins if not (d_alias and re.search(rf"\s{re.escape(d_alias)}\s+ON\s", j))]
    if d_ref and any(d_ref.search(j) for j in other_joins):
        res.coverage = REFUSED
        res.reason = f"a table joined through the date table {dtab} is not compiled beside " \
                     f"time intelligence"
        return res
    date_preds = [x for x in dim_preds if d_ref and d_ref.search(x)]
    other_preds = [x for x in dim_preds if x not in date_preds]
    if any(re.search(r"\bj\d+\.", d_ref.sub("", x)) or re.search(rf"\b{re.escape(alias)}\.", x)
           for x in date_preds) if d_ref else False:
        res.coverage = REFUSED
        res.reason = "a filter mixing the date table and another table is not compiled"
        return res
    # A Top N filter is a filter on its column's rows. On a column of the date table it
    # narrows the row's dates (the date set then replaces it, as the date table's other
    # filters): its outer test reads the date table's own alias in the row's dates.
    member_dates: list[str] = []
    date_preds_members: list[str] = []       # the date-table Top N predicates, in that order
    for p in member_preds:
        key = re.match(r"\(?\s*((\w+)\.\w+)\s+IN\s*\(", p)
        if d_alias and key and key.group(2) == d_alias:
            outer = key.group(1)
            col_ = outer.split(".", 1)[1]
            member_dates.append(
                p.replace(f"({outer} IN (", f"(d.{col_} IN (", 1)
                .replace(f"OR ({outer} IS NULL AND", f"OR (d.{col_} IS NULL AND", 1))
            date_preds.append(p)
            date_preds_members.append(p)
            res.rules_used.append("engine.dax_time_intelligence_top_n_dates")
        else:
            other_preds.append(p)
    dim_preds = dim_preds + member_preds

    # A CALCULATE filter that REPLACES one the visual applies (`slot_drops`): the slots it
    # holds read their rows without the visual's filter on that column, so their
    # sub-statement's WHERE is planned again without it. The date table's own filters are
    # replaced by the date set already; a dropped one there is the date set's business.
    def preds_without(drops: tuple) -> list[str]:
        if not drops:
            return other_preds
        gone = {(str(c).lower(), t) for c, t in drops}

        def dropped(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table")))
            return (col, at) in gone or (col, None) in gone
        kept = [f for f in unit.get("filters") or [] if not dropped(f)]
        sp2: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                                "display": []}
        c_p, d_p, deferred_ = _plan_filters(dict(unit, filters=kept), rules, alias, Result(),
                                            None, resolve=calc, dialect=dialect,
                                            place=plan.at, text_key=ctx.text_key, special=sp2)
        if deferred_ or c_p:
            raise CompileError("a filter it keeps does not compile on its own")
        res.rules_used.append("engine.dax_time_intelligence_override")
        # A Top N filter it keeps: the same members (ranked within every filter).
        mem = [p for f, p in zip(sp["members"], member_preds)
               if not dropped(f) and p not in date_preds]
        return [x for x in d_p if not (d_ref and d_ref.search(x))] + mem

    def date_preds_without(drops: tuple) -> tuple[list[str], list[str]]:
        """The visual's date table filters a slot keeps: (as the ordinary statement reads
        them, as the date table `d` reads them). An ALL of a date table column takes its
        filter off the row's dates too."""
        gone = {(str(c).lower(), t) for c, t in drops}

        def dropped(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table")))
            return (col, at) in gone or (col, None) in gone
        if not drops or not any(dropped(f) for f in unit.get("filters") or []):
            return (date_preds, ([d_ref.sub("d.", x) for x in date_preds
                                 if x not in member_preds] + member_dates) if d_ref else [])
        kept = [f for f in unit.get("filters") or [] if not dropped(f)]
        _c, d_p, _x = _plan_filters(dict(unit, filters=kept), rules, alias, Result(), None,
                                    resolve=calc, dialect=dialect, place=plan.at,
                                    text_key=ctx.text_key,
                                    special={"datasource": [], "members": [],
                                             "measure_tests": [], "display": []})
        plain_d = [x for x in d_p if d_ref and d_ref.search(x)]
        kept_dates = [p for f, p in zip(sp["members"], member_preds)
                      if p in date_preds_members and not dropped(f)]
        return (plain_d + kept_dates,
                [d_ref.sub("d.", x) for x in plain_d]
                + [member_dates[date_preds_members.index(p)] for p in kept_dates])

    groups = list(unit.get("group_by", []))
    keys: list[str] = []            # each group's SQL in the ordinary statement
    date_cols: dict[int, str] = {}  # group index -> the date table's column
    for i, g in enumerate(groups):
        expr = gcol(g, i)
        keys.append(expr)
        if d_alias and expr == f"{d_alias}.{g}":
            date_cols[i] = g
        elif d_ref and d_ref.search(expr):
            res.coverage = REFUSED
            res.reason = (f"grouping {g!r} is an expression over the date table; time "
                          f"intelligence is compiled for its plain columns")
            return res

    def fmt(name: str, *a: str) -> str:
        return math[name].format(*a)

    n_slots = len(slots)

    def select_slots(own: list[int]) -> list[str]:
        return [f"{slots[i]['sql'] if i in own else 'NULL'} AS s{i + 1}"
                for i in range(n_slots)]

    def tail(group_exprs: list[str]) -> str:
        return ("\nGROUP BY " + ", ".join(group_exprs)) if group_exprs else ""

    branches: list[str] = []
    def drops_of(sl: dict) -> tuple:
        return tuple(sl.get("drops") or ())

    def keep_of(sl: dict) -> tuple[int, ...] | None:
        """The grouping columns (by index) a slot's CALCULATE keeps, or None for all."""
        kept = sl.get("keep")
        if kept is None:
            return None
        idx = tuple(i for i, (_c, _t, sql) in enumerate(ctx.groups) if sql in kept)
        return None if len(idx) == len(keys) else idx

    def kept_rows(branch: str, kept: tuple[int, ...] | None, own: list[int]) -> str:
        """A branch grouped by only the columns its CALCULATE keeps, on every row of the
        visual those columns' values stand for: crossed with the members of the columns it
        removes (the visual's rows list them under the visual's own filters; seen live, ALL
        of a grouped channel shows every channel's year to date on each channel's row)."""
        if kept is None:
            return branch
        removed = [i for i in range(len(keys)) if i not in kept]
        owners = {}
        for i in removed:
            at = re.match(r"(\w+)\.", keys[i])
            if not at:
                raise CompileError(f"grouping {groups[i]!r} is an expression")
            owners.setdefault(at.group(1), []).append(i)
        parts = []
        for a, idx in owners.items():
            cols = ", ".join(f"{keys[i]} AS k{i + 1}" for i in idx)
            if a == alias:
                body = (f"SELECT DISTINCT {cols}\n    FROM   {source} {alias}"
                        + "".join(f"\n    {j}" for j in joins)
                        + ("\n    WHERE  " + "\n      AND  ".join(dim_preds)
                           if dim_preds else ""))
            else:
                clause = next((m for m in (_JOIN_CLAUSE.match(j) for j in joins)
                               if m and m.group(2) == a), None)
                if clause is None:
                    raise CompileError(f"the members of {groups[idx[0]]!r} are not read")
                mine = [p for p in dim_preds if p not in member_preds
                        and set(re.findall(r"\b(\w+)\.", re.sub(r"'(?:[^']|'')*'", "", p)))
                        <= {a}]
                # A Top N filter on the table's column narrows its members.
                mine += [p for p in member_preds
                         if (re.match(r"\(?\s*(\w+)\.\w+\s+IN\s*\(", p) or [None, None])[1]
                         == a]
                body = (f"SELECT DISTINCT {cols}\n    FROM   {clause.group(1)} {a}"
                        + ("\n    WHERE  " + "\n      AND  ".join(mine) if mine else ""))
            parts.append(f"(\n    {body}\n  ) m{len(parts)}")
        where_m = {i: f"m{n}" for n, (_a, idx) in enumerate(owners.items()) for i in idx}
        sel = [f"{where_m[i]}.k{i + 1}" if i in where_m else f"w.k{i + 1}"
               for i in range(len(keys))]
        sel = [f"{x} AS k{i + 1}" for i, x in enumerate(sel)] + [
            f"w.s{i + 1}" for i in range(n_slots)]
        res.rules_used.append("engine.dax_time_intelligence_window")
        return ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n" + _indent(branch)
                + "\n) w\nCROSS JOIN " + "\nCROSS JOIN ".join(parts)
                + "\nWHERE  " + "\n   OR  ".join(f"w.s{i + 1} IS NOT NULL" for i in own))

    by_drops: dict[tuple, list[int]] = {}
    for i, sl in enumerate(slots):
        if sl["date_set"] is None:
            by_drops.setdefault((drops_of(sl), keep_of(sl)), []).append(i)
    for (drops, kept), plain in by_drops.items():
        try:
            preds = (date_preds_without(drops)[0] + preds_without(drops) if drops
                     else dim_preds)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a CALCULATE filter replacing the visual's: {exc}"
            return res
        mine = [i for i in range(len(keys)) if kept is None or i in kept]
        sel = [f"{keys[i]} AS k{i + 1}" for i in mine] + select_slots(plain)
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {source} {alias}"
        for j in joins:
            sql += f"\n{j}"
        if preds:
            sql += "\nWHERE  " + "\n  AND  ".join(preds)
        try:
            branches.append(kept_rows(sql + tail([keys[i] for i in mine]), kept, plain))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = str(exc)
            return res

    by_set: dict[tuple, list[int]] = {}
    for i, sl in enumerate(slots):
        if sl["date_set"] is not None:
            by_set.setdefault((sl["date_set"].key, drops_of(sl), keep_of(sl)), []).append(i)
    for (_key, drops, kept), own in by_set.items():
        chain: _DateChain = slots[own[0]]["date_set"]
        try:
            set_preds = preds_without(drops)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a CALCULATE filter replacing the visual's: {exc}"
            return res
        # A CALCULATE filter on a date table column beside the date set is intersected with
        # it (seen live: DATESYTD beside MONTH <= 6 is, from July on, the first half): the
        # date table is joined again on the fact's date for the slots that read it.
        date_join = ([j for j in joins if d_alias and re.search(
            rf"\s{re.escape(d_alias)}\s+ON\s", j)]
                     if d_ref and any(d_ref.search(slots[i]["sql"]) for i in own) else [])
        if date_join:
            res.rules_used.append("engine.dax_date_set_date_filter")
        mine_d = [i for i in sorted(date_cols) if kept is None or i in kept]
        dk = [f"d.{date_cols[i]} AS k{i + 1}" for i in mine_d]
        dg = [f"d.{date_cols[i]}" for i in mine_d]
        kc = [f"k{i + 1}" for i in mine_d]
        dwhere = date_preds_without(drops)[1]
        where_d = ("\n    WHERE  " + "\n      AND  ".join(dwhere)) if dwhere else ""
        # The date table's BLANK member (a row of another relationship into it with no date:
        # seen live, the ship date's empty keys give the date table a blank year) is a row of
        # the visual too; a date set that does not read the row's dates (two literal dates, a
        # period back from a literal date) fills it like any other.
        blank = (_blank_member_test(dtab, [e for e in ctx.graph if e.get("is_active") is False])
                 if dk and not dwhere else None)
        constant = all(ds.kind == "between" or (ds.kind == "in_period" and ds.anchor)
                       or ds.open_end for ds in chain.steps[0])
        if len(chain.steps) == 1 and len(chain.steps[0]) == 1:
            ds = chain.steps[0][0]
        else:
            ds = None
        anchored = ds is None and any(x.anchor_fact for step in chain.steps for x in step)
        if ds is not None and ds.anchor_fact:
            # The anchor is the last date of the measure's table in the row's whole context
            # (every grouping column and filter), so the row's context is that statement.
            mine_all = [i for i in range(len(keys)) if kept is None or i in kept]
            anchor_sql = ("SELECT " + ", ".join([f"{keys[i]} AS k{i + 1}" for i in mine_all]
                                                + [f"MAX({alias}.{ds.anchor_fact}) AS hi"])
                          + f"\nFROM   {source} {alias}" + "".join(f"\n{j}" for j in joins)
                          + ("\nWHERE  " + "\n  AND  ".join(dim_preds) if dim_preds else "")
                          + (("\nGROUP BY " + ", ".join(keys[i] for i in mine_all))
                             if mine_all else ""))
            lo, hi = _date_set_range(ds, fmt)
            nse_row = rules.operator("null_safe_eq", dialect)
            nse_emit = (nse_row or {}).get("emit") or {}
            nse = ((nse_emit.get("dialect_templates") or {}).get(str(dialect).lower())
                   or nse_emit.get("template"))
            if not nse:
                res.coverage = REFUSED
                res.reason = f"no NULL-matching comparison for the {dialect} dialect"
                return res
            same = [nse.replace("{0}", f"c.k{i + 1}").replace("{1}", keys[i])
                    for i in mine_all if i not in date_cols]
            body = (f"FROM   (\n{_indent(anchor_sql)}\n) c\n"
                    f"JOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}\n"
                    f"JOIN {source} {alias} ON {alias}.{fcol} = s.{dcol}")
            set_preds = list(set_preds) + same
            res.rules_used.append("engine.dax_date_set_fact_anchor")
            mine = mine_all
            bkeys = [f"c.k{i + 1}" for i in mine]
            sel = [f"{k} AS k{i + 1}" for i, k in zip(mine, bkeys)] + select_slots(own)
            sql = "SELECT " + ",\n       ".join(sel) + "\n" + body
            for j in other_joins + date_join:
                sql += f"\n{j}"
            if set_preds:
                sql += "\nWHERE  " + "\n  AND  ".join(set_preds)
            try:
                branches.append(kept_rows(sql + tail(bkeys), kept, own))
            except CompileError as exc:
                res.coverage = REFUSED
                res.reason = str(exc)
                return res
            res.rules_used.append("engine.dax_date_set_in_period")
            continue
        if ds is not None and ds.kind == "shift":
            part = f"PARTITION BY {', '.join(dg)} " if dg else ""
            ctx_sql = ("(\n    SELECT " + ", ".join(dk + [f"d.{dcol} AS dt",
                                                     f"LAG(d.{dcol}) OVER ({part}ORDER BY "
                                                     f"d.{dcol}) AS prev"])
                       + f"\n    FROM   {dtab} d" + where_d + "\n  ) c")
            lo, hi = _shift_bounds(ds, fmt)
            ck = [f"c.k{i + 1}" for i in mine_d]
            setq = ("(\n  SELECT DISTINCT " + ", ".join(ck + [f"s.{dcol} AS dt"])
                    + f"\n  FROM   {ctx_sql}\n  JOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}"
                    + "\n) c")
            body = f"FROM   {setq}\nJOIN {source} {alias} ON {alias}.{fcol} = c.dt"
        elif ds is not None:
            ctx_sql = ("(\n  SELECT " + ", ".join(dk + [f"MIN(d.{dcol}) AS lo",
                                                   f"MAX(d.{dcol}) AS hi"])
                       + f"\n  FROM   {dtab} d" + where_d.replace("\n    ", "\n  ")
                       .replace("\n      AND", "\n    AND")
                       + (("\n  GROUP BY " + ", ".join(dg)) if dg else ""))
            if blank and constant:
                nulls = ", ".join(["NULL"] * (len(dk) + 2))
                ctx_sql += (f"\n  UNION ALL\n  SELECT {nulls}\n  FROM   (SELECT 1 AS one) z"
                            f"\n  WHERE  {blank}")
                res.rules_used.append("engine.dax_date_set_blank_member")
            ctx_sql += "\n) c"
            lo, hi = _date_set_range(ds, fmt)
            body = (f"FROM   {ctx_sql}\nJOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}"
                    f"\nJOIN {source} {alias} ON {alias}.{fcol} = s.{dcol}")
        else:
            # Several steps, or several date sets in one CALCULATE: each step reads the dates
            # (one row per visual row and date) the step before it left, the first the
            # row's own dates under the visual's date filters.
            # A period back from the last date of the measure's own table in the chain:
            # that date is read over the fact rows of the step's dates under the row's whole
            # context, so the row's other grouping columns ride along with its dates.
            nondate = ([i for i in range(len(keys)) if (kept is None or i in kept)
                        and i not in date_cols] if anchored else [])
            kn = [f"k{i + 1}" for i in nondate]
            rel = ("SELECT " + ", ".join(dk + [f"n.{k}" for k in kn] + [f"d.{dcol} AS dt"])
                   + f"\nFROM   {dtab} d"
                   + (("\nCROSS JOIN (\n" + _indent(
                       "SELECT DISTINCT " + ", ".join(f"{keys[i]} AS k{i + 1}" for i in nondate)
                       + f"\nFROM   {source} {alias}" + "".join(f"\n{j}" for j in joins)
                       + ("\nWHERE  " + "\n  AND  ".join(dim_preds) if dim_preds else ""))
                       + "\n) n") if kn else "")
                   + where_d.replace("\n    ", "\n").replace("\n      AND", "\n  AND"))
            if blank and constant and not kn:
                rel += ("\nUNION ALL\nSELECT " + ", ".join(["NULL"] * (len(dk) + 1))
                        + f"\nFROM   (SELECT 1 AS one) z\nWHERE  {blank}")
                res.rules_used.append("engine.dax_date_set_blank_member")
            kc_all = kc + kn
            nse_row = rules.operator("null_safe_eq", dialect)
            nse = (((nse_row or {}).get("emit") or {}).get("dialect_templates") or {}).get(
                str(dialect).lower()) or ((nse_row or {}).get("emit") or {}).get("template")
            if anchored and kn and not nse:
                res.coverage = REFUSED
                res.reason = f"no NULL-matching comparison for the {dialect} dialect"
                return res

            def anchor_of(ds_x):
                def make(r_sql: str) -> str:
                    on = [f"{alias}.{fcol} = r.dt"] + [
                        nse.replace("{0}", f"r.k{i + 1}").replace("{1}", keys[i])
                        for i in nondate]
                    return ("SELECT " + ", ".join([f"r.{k}" for k in kc_all]
                                                  + [f"MAX({alias}.{ds_x.anchor_fact}) AS hi"])
                            + f"\nFROM   (\n{_indent(r_sql)}\n) r"
                            + f"\nJOIN {source} {alias} ON " + "\n  AND ".join(on)
                            + "".join(f"\n{j}" for j in other_joins)
                            + ("\nWHERE  " + "\n  AND  ".join(set_preds) if set_preds else "")
                            + (f"\nGROUP BY {', '.join('r.' + k for k in kc_all)}"
                               if kc_all else ""))
                return make if ds_x.anchor_fact else None
            if anchored:
                res.rules_used.append("engine.dax_date_set_fact_anchor")
            try:
                for step in chain.steps:
                    outs = [_date_step(x, rel, kc_all, dtab, dcol, fmt, anchor_of(x))
                            for x in step]
                    if len(outs) > 1:
                        res.rules_used.append("engine.dax_date_sets_intersected")
                    rel = "\nINTERSECT\n".join(
                        "SELECT " + ", ".join([f"x{n}.{k}" for k in kc_all] + [f"x{n}.dt"])
                        + f"\nFROM   (\n{_indent(o)}\n) x{n}" for n, o in enumerate(outs))
                    for x in step:
                        res.rules_used.append(f"engine.dax_date_set_{x.kind}")
            except CompileError as exc:
                res.coverage = REFUSED
                res.reason = str(exc)
                return res
            if len(chain.steps) > 1:
                res.rules_used.append("engine.dax_date_set_chain")
            body = (f"FROM   (\n{_indent(rel)}\n) c\n"
                    f"JOIN {source} {alias} ON {alias}.{fcol} = c.dt")
            if kn:
                body += "".join(
                    "\n  AND " + nse.replace("{0}", f"c.k{i + 1}").replace("{1}", keys[i])
                    for i in nondate)
        mine = [i for i in range(len(keys)) if kept is None or i in kept]
        bkeys = [f"c.k{i + 1}" if i in date_cols else keys[i] for i in mine]
        sel = [f"{k} AS k{i + 1}" for i, k in zip(mine, bkeys)] + select_slots(own)
        sql = "SELECT " + ",\n       ".join(sel) + "\n" + body
        for j in other_joins + date_join:
            sql += f"\n{j}"
        if set_preds:
            sql += "\nWHERE  " + "\n  AND  ".join(set_preds)
        try:
            branches.append(kept_rows(sql + tail(bkeys), kept, own))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = str(exc)
            return res
        if ds is not None:
            res.rules_used.append(f"engine.dax_date_set_{ds.kind}")

    outer_keys = [f"u.k{i + 1}" for i in range(len(keys))]
    sel = [f"u.k{i + 1} AS {_measure_alias(g)}" for i, g in enumerate(groups)]
    sel += [f"{e} AS {_measure_alias(m['name'])}" for m, e in exprs]
    union = "\n  UNION ALL\n".join(branches)
    sql = ("SELECT " + ",\n       ".join(sel) + "\nFROM (\n" + union + "\n) u"
           + (("\nGROUP BY " + ", ".join(outer_keys)) if outer_keys else ""))
    if unit.get("blank_rows") == "hidden" and outer_keys:
        # The rows whose every value is empty are left out, as in the plain statement.
        hide = _blank_rows_having(sel[len(groups):])
        if hide:
            sql += f"\nHAVING {hide}"
            res.rules_used.append("engine.blank_rows_hidden")
    res.sql = sql
    if failed:
        res.reason = "; ".join(f"measure {f}" for f in failed)
    res.coverage = PARTIAL if (failed or res.untranslatable) else TRANSLATED
    res.gotchas.append(
        f"time intelligence over {dtab}.{dcol}: each row's dates are the date table's under "
        f"the visual's date filters, and a date set reads the fact's rows through "
        f"{_bare(source)}.{fcol}")
    return res


def _indent(sql: str, by: str = "    ") -> str:
    return "\n".join(by + ln for ln in sql.splitlines())


def _shift_bounds(ds: "_DateSet", fmt) -> tuple[str, str]:
    """The dates a shift date set reads for one of the row's dates (`c.dt`, the date before
    it among the row's `c.prev`): a month-end date whose day before is also the row's
    extends to the end of the month it lands in."""
    if ds.part == "day":
        lo = fmt("add_days", "c.dt", str(ds.n))
        return lo, lo
    lo = fmt("add_months", "c.dt", str(ds.n))
    hi = (f"CASE WHEN c.dt = {fmt('month_end', 'c.dt')} AND c.prev = "
          f"{fmt('add_days', 'c.dt', '-1')} THEN "
          f"{fmt('month_end', fmt('add_months', 'c.dt', str(ds.n)))} ELSE {lo} END")
    return lo, hi


def _date_step(ds: "_DateSet", rel: str, kc: list[str], dtab: str, dcol: str, fmt,
               anchor=None) -> str:
    """One date set read from the dates `rel` holds (columns `kc` and `dt`): the dates it
    gives for each of `rel`'s rows of the visual, as the same columns. A period back from
    the last date of the measure's own table reads that date over the fact rows of `rel`'s
    dates (`anchor(rel)`: the statement of `kc` and `hi`)."""
    inner = _indent(rel, "      ")
    part = f"PARTITION BY {', '.join(kc)} " if kc else ""
    ck = [f"c.{k}" for k in kc]
    if ds.anchor_fact and anchor is not None:
        lo, hi = _date_set_range(ds, fmt)
        ctx_sql = "(\n" + _indent(anchor(rel), "    ") + "\n  ) c"
    elif ds.kind == "shift":
        lo, hi = _shift_bounds(ds, fmt)
        ctx_sql = ("(\n    SELECT " + ", ".join(kc + ["dt", f"LAG(dt) OVER ({part}ORDER BY dt) "
                                                           f"AS prev"])
                   + f"\n    FROM   (\n{inner}\n    ) r\n  ) c")
    else:
        lo, hi = _date_set_range(ds, fmt)
        ctx_sql = ("(\n    SELECT " + ", ".join(kc + ["MIN(dt) AS lo", "MAX(dt) AS hi"])
                   + f"\n    FROM   (\n{inner}\n    ) r"
                   + (f"\n    GROUP BY {', '.join(kc)}" if kc else "") + "\n  ) c")
    return ("SELECT DISTINCT " + ", ".join(ck + [f"s.{dcol} AS dt"])
            + f"\nFROM   {ctx_sql}\nJOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}")


def _date_set_range(ds: "_DateSet", fmt) -> tuple[str, str]:
    """The first and last date of a range date set, from the row's first (`c.lo`) and last
    (`c.hi`) date."""
    part_of = {1: "month", 3: "quarter", 12: "year"}
    if ds.kind == "to_date":
        if ds.year_end and ds.year_end[0] != 12:
            # The fiscal year holding the last date starts the day after its year end:
            # move the date on by the months after the year-end month, take that calendar
            # year's start, and move it back.
            k = 12 - ds.year_end[0]
            lo = fmt("add_months", fmt("trunc", fmt("add_months", "c.hi", str(k)), "year"),
                     str(-k))
        elif ds.period is None:
            # A running total: every date of the date table up to the row's last; with an
            # open end (DATESBETWEEN from a blank start), a row with no date (the date
            # table's blank row) reads every date.
            lo = fmt("date", "0001-01-01")
            if ds.open_end:
                return lo, f"COALESCE(c.hi, {fmt('date', '9999-12-31')})"
        else:
            lo = fmt("trunc", "c.hi", ds.period)
        return lo, "c.hi"
    if ds.kind == "parallel":
        part = part_of[ds.months]
        if ds.previous:
            return (fmt("add_months", fmt("trunc", "c.lo", part), str(-ds.months)),
                    fmt("add_days", fmt("trunc", "c.lo", part), "-1"))
        return (fmt("add_months", fmt("trunc", "c.lo", part), str(ds.n * ds.months)),
                fmt("add_days", fmt("add_months", fmt("trunc", "c.hi", part),
                                    str((ds.n + 1) * ds.months)), "-1"))
    if ds.kind == "in_period":
        # From the row's last date, or from a literal date (the same dates on every row).
        end = (ds.anchor if isinstance(ds.anchor, _SqlDate) else fmt("date", ds.anchor)) \
            if ds.anchor else "c.hi"
        if ds.part == "day":
            edge = fmt("add_days", end, str(ds.n))
        else:
            # From a month's last day the edge is the last day of the month it lands in
            # (seen live: 30 November back three months starts on 1 September, not 31
            # August), otherwise the same day, clamped.
            moved = fmt("add_months", end, str(ds.n))
            edge = (f"CASE WHEN {end} = {fmt('month_end', end)} THEN "
                    f"{fmt('month_end', moved)} ELSE {moved} END")
        if ds.n < 0:
            return fmt("add_days", edge, "1"), end
        if ds.n > 0:
            return end, fmt("add_days", edge, "-1")
        raise CompileError("DATESINPERIOD over zero intervals is empty")
    if ds.kind == "between":
        return tuple(b if isinstance(b, _SqlDate) else fmt("date", b) for b in (ds.lo, ds.hi))
    raise CompileError(f"date set {ds.kind} has no range")


_SORT = re.compile(r"^(?P<expr>.+?)\s+(?P<dir>ASC|DESC)\s*$", re.I | re.S)


def _top_n_order(sort: str, unit: dict, rules, alias: str, dialect: str, res: Result,
                 colmap, measures, alias_of: dict[str, str], group_expr=None) -> str:
    """`<expression> ASC|DESC` -> the ORDER BY term. The expression is read in the unit's
    formula language; when it is a measure the statement already selects, its alias."""
    m = _SORT.match(sort.strip())
    if not m:
        raise CompileError("a sort is `<expression> ASC|DESC`")
    direction = m.group("dir").upper()
    # A sort that NAMES one of the measures this statement selects orders by that measure.
    named = m.group("expr").strip().strip("[]").lower()
    for x in unit.get("measures", []):
        a = _measure_alias(x["name"])
        if named in (str(x["name"]).lower(), a.lower()) and a in alias_of.values():
            return f"{a} {direction}"
    # One of the columns it groups by (the latest point of a trend: its axis, descending).
    if group_expr is not None:
        g = group_expr(named)
        if g:
            return f"{g} {direction}"
    language = next((x.get("formula_language") for x in unit.get("measures", [])
                     if x.get("formula_language")), None) or unit.get("expression_language")
    if language is None:
        raise CompileError("no formula language to read the sort expression in")
    expr = _compile_measure({"name": "top_n_sort", "formula": m.group("expr"),
                             "formula_language": language}, rules, alias, dialect, res,
                            colmap, measures, unit)
    return f"{alias_of.get(expr, expr)} {direction}"


def _rows_not_on_source(ctx: "_UnitContext") -> list[str]:
    """The tables other than the statement's own the visual groups or filters by. A visual
    lists its grouping columns' members WITHIN one table only where they have rows under its
    filters on that table (seen live); across tables, a row shows wherever a measure is not
    blank, rows of the statement's table or not."""
    if not ctx.groups:
        return []
    out = [t for _c, t, _s in ctx.groups if t and t != ctx.source]
    out += [t for _c, t in ctx.filters if t and t != ctx.source]
    return sorted(set(out))


def _blank_on_empty(m: dict, rules, alias: str, dialect: str, measures, unit: dict,
                    table_of, source: str, ctx: "_UnitContext") -> bool | None:
    """Whether a measure is BLANK on a row with no rows of the statement's table: True, False,
    or None when that cannot be shown. Its formula is compiled with every aggregate BLANK
    (and one summed across the visual's other rows not), and the constant evaluated."""
    scratch = Result()
    try:
        sql = _compile_measure(m, rules, alias, dialect, scratch, None, measures, unit,
                               home=(table_of, source) if table_of else None, ctx=ctx,
                               empty=True)
    except (CompileError, _TimeIntelligence):
        return None
    try:
        import sqlglot  # noqa: PLC0415 - loaded by A3 already
        tree = sqlglot.parse_one(f"SELECT {sql}", read=dialect or None).expressions[0]
        return _const(tree) is None
    except Exception:  # noqa: BLE001 - anything not a constant we can read: not shown
        return None


def _empty_value(m: dict, rules, alias: str, dialect: str, measures, unit: dict, table_of,
                 source: str, ctx: "_UnitContext") -> str | None:
    """The SQL of a measure's value on a row with no rows of the statement's table (every
    aggregate BLANK), when it reads no column and no window: a constant the warehouse
    evaluates with the measure's own blank rules. None otherwise."""
    scratch = Result()
    try:
        sql = _compile_measure(m, rules, alias, dialect, scratch, None, measures, unit,
                               home=(table_of, source) if table_of else None, ctx=ctx,
                               empty=True)
    except (CompileError, _TimeIntelligence):
        return None
    if re.search(r"\b\w+\.\w+|\bOVER\b|__CTXAGG", sql):
        return None
    return sql


def _empty_context_value(m: dict, rules, alias: str, dialect: str, measures, unit: dict,
                         table_of, source: str, ctx: "_UnitContext"
                         ) -> tuple[str, list[dict]] | None:
    """A measure's value on a row with no rows of the statement's table when it reads an
    aggregate over a wider context (a total over ALL, an ALLEXCEPT): its SQL with each such
    aggregate a marker `__EMPTYAGGn__`, and the aggregates ({n, sql, keep, drop}), each read
    from its own statement grouped by the columns it keeps. None when it reads anything
    else of the row."""
    scratch, aggs = Result(), []
    try:
        sql = _compile_measure(m, rules, alias, dialect, scratch, None, measures, unit,
                               home=(table_of, source) if table_of else None, ctx=ctx,
                               empty=True, empty_aggs=aggs)
    except (CompileError, _TimeIntelligence):
        return None
    if not aggs or re.search(r"\b\w+\.\w+|\bOVER\b|__CTXAGG", sql):
        return None
    return sql, aggs


class _NotConstant(Exception):
    pass


def _const(n) -> Any:
    """A constant SQL expression's value, NULL as None, in SQL's three-valued logic. Only
    what an aggregate-free measure compiles to; anything else raises `_NotConstant`."""
    import sqlglot.expressions as E  # noqa: PLC0415

    def num(v):
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return v
        try:
            return float(v)
        except (TypeError, ValueError):
            return v

    if isinstance(n, E.Null):
        return None
    if isinstance(n, E.Boolean):
        return bool(n.this)
    if isinstance(n, E.Literal):
        return n.this if n.is_string else float(n.this)
    if isinstance(n, (E.Paren, E.Alias)):
        return _const(n.this)
    if isinstance(n, E.Cast):
        return _const(n.this)
    if isinstance(n, E.Neg):
        v = _const(n.this)
        return None if v is None else -num(v)
    if isinstance(n, (E.Add, E.Sub, E.Mul, E.Div, E.Pow, E.Mod)):
        a, b = _const(n.this), _const(n.expression)
        if a is None or b is None:
            return None
        return 1.0      # some number: only blank or not matters here
    if isinstance(n, E.DPipe):
        a, b = _const(n.this), _const(n.expression)
        return None if a is None or b is None else f"{a}{b}"
    if isinstance(n, E.Coalesce):
        for x in [n.this] + list(n.expressions):
            v = _const(x)
            if v is not None:
                return v
        return None
    if isinstance(n, E.Nullif):
        a, b = _const(n.this), _const(n.expression)
        return None if a is None or (b is not None and num(a) == num(b)) else a
    if isinstance(n, E.Is):
        v = _const(n.this)
        if not isinstance(n.expression, E.Null):
            raise _NotConstant
        return v is None
    if isinstance(n, E.Not):
        v = _const(n.this)
        return None if v is None else not v
    if isinstance(n, E.And):
        a, b = _const(n.this), _const(n.expression)
        if a is False or b is False:
            return False
        return None if a is None or b is None else True
    if isinstance(n, E.Or):
        a, b = _const(n.this), _const(n.expression)
        if a is True or b is True:
            return True
        return None if a is None or b is None else False
    if isinstance(n, (E.EQ, E.NEQ, E.GT, E.LT, E.GTE, E.LTE)):
        a, b = _const(n.this), _const(n.expression)
        if a is None or b is None:
            return None
        a, b = num(a), num(b)
        try:
            return {E.EQ: a == b, E.NEQ: a != b, E.GT: a > b, E.LT: a < b,
                    E.GTE: a >= b, E.LTE: a <= b}[type(n)]
        except TypeError:
            raise _NotConstant from None
    if isinstance(n, E.Case):
        subject = _const(n.this) if n.this is not None else None
        for branch in n.args.get("ifs") or []:
            cond = _const(branch.this)
            if n.this is not None:
                cond = None if subject is None or cond is None else num(subject) == num(cond)
            if cond is True:
                return _const(branch.args["true"])
        default = n.args.get("default")
        return _const(default) if default is not None else None
    if isinstance(n, E.If):
        cond = _const(n.this)
        if cond is True:
            return _const(n.args["true"])
        f = n.args.get("false")
        return _const(f) if f is not None else None
    if isinstance(n, (E.Upper, E.Lower, E.Trim, E.Abs, E.Round, E.Floor, E.Ceil)):
        v = _const(n.this)
        return None if v is None else v
    if isinstance(n, (E.Least, E.Greatest)):
        vals = [_const(x) for x in [n.this] + list(n.expressions)]
        return None if any(v is None for v in vals) else vals[0]
    raise _NotConstant


def _compile_measure(m: dict, rules, alias: str, dialect: str, res: Result,
                     colmap: dict[str, str] | None = None,
                     measures: dict[str, str] | None = None, unit: dict | None = None,
                     home: tuple[dict[str, str], str] | None = None,
                     ctx: "_UnitContext | None" = None, slots: list | None = None,
                     empty: bool = False, empty_aggs: list | None = None) -> str:
    """A measure is either a formula (compile it) or a column plus a shelf aggregation."""
    formula, language = m.get("formula"), m.get("formula_language")
    tc = m.get("table_calc")
    if tc and tc.get("refusal"):
        # Computed over the view's rows in a way the reader could not state: its bare
        # aggregate would be a different number, so the measure is refused by name.
        res.untranslatable.append({"construct": "table calculation",
                                   "in_measure": m.get("name") or "", "why": tc["refusal"]})
        raise CompileError(f"table calculation: {tc['refusal']}")
    if formula and language == "sql":
        # Already SQL. One that names no table is used as written; one that does is placed by
        # compile_unit, and reaching here means it could not be.
        if _declared.qualifiers(formula):
            raise CompileError("a SQL measure that names tables is placed in its model, "
                               "and none was supplied")
        return formula
    agg0 = (m.get("aggregation") or "").lower()
    if formula and language in _LOD_LANGUAGES and agg0 in _SHELF_LOD \
            and _reaches_lod(formula, measures or {}):
        # A calc that IS one level of detail, on an aggregating shelf: the shelf's aggregate
        # around the level of detail (`SUM({FIXED …})`), never per row then summed.
        formula, m = f"{_SHELF_LOD[agg0]}({formula.strip()})", {**m, "aggregation": "custom"}
    if formula and language:
        # The parser is built OUTSIDE the try so what it learned survives the exception. It
        # accumulates the named untranslatable constructs and the unknown functions as it
        # goes, and those are the whole point of a failed compile: a refusal that cannot say
        # WHAT it could not do teaches the review pass nothing and teaches the rule table
        # nothing either.
        p = _Parser(_tokens(formula, language), rules, language, alias, dialect, colmap,
                    measures if language in ("dax", "tableau-calc") else None,
                    inlining=(m["name"].lower(),))
        p.home = home
        p.ctx, p.slots, p.empty = ctx, slots, empty
        p.empty_aggs = empty_aggs
        if ctx is not None:
            ctx.table_calc = _over_of(tc, ctx) if tc else None
            p.lods = ctx.lods
        try:
            sql = p.parse()
            if p.calc_columns and unit is not None and ctx is None:
                clash = p.calc_columns & (
                    {g.lower() for g in unit.get("group_by", [])}
                    | {str(f.get("column") or "").lower() for f in unit.get("filters", [])})
                if clash:
                    # DAX's CALCULATE REPLACES the visual's own filter on that column; a CASE
                    # inside the aggregate intersects with it. Different numbers, so refused.
                    raise CompileError(f"CALCULATE filters {sorted(clash)}, which the visual "
                                       f"also groups or filters by; DAX overrides that filter "
                                       f"and one statement cannot")
            agg = (m.get("aggregation") or "").lower()
            if "__LODROW" in sql and agg in ("", "custom", "none") \
                    and not _AGGREGATE_CALL.search(sql) and "__LODAGG" not in sql \
                    and not re.search(r"\b(COUNT\(DISTINCT|MEDIAN|STDDEV|VAR_)", sql):
                # A level of detail read per row, with nothing aggregating it: the view's
                # rows would be the table's rows.
                raise _lod_refusal("FIXED", "a value per row, which no aggregate reads")
            if language == "tableau-calc" and agg and agg not in ("custom", "none"):
                # TRAP 1: a Tableau field's aggregation is the SHELF's. A row-level calc on a
                # SUM shelf is SUM(calc); emitting the calc bare yields a non-aggregate beside
                # a GROUP BY. A calc that aggregates itself is `usr`/custom and stays as is.
                rule = rules.aggregation(agg)
                if rule is None:
                    p.unknown.append(f"aggregation:{agg}")
                    raise CompileError(f"no rule for aggregation {agg!r}")
                p.used.append(rule["id"])
                sql = _apply(_agg_emit(rule, agg, dialect), [f"({sql})"], agg)
            if ctx is not None and "__LODROW" in sql and _AGGREGATE_CALL.search(sql):
                sql = _lods_at_their_grain(sql, ctx.lods, alias)
            return sql
        finally:
            if ctx is not None:
                ctx.table_calc = None
            res.rules_used += p.used
            res.provisional_rules += p.provisional
            res.untranslatable += p.untranslatable
            res.unknown += p.unknown
    if not m.get("aggregation"):
        # No formula and no aggregation chosen where it is used: a name the model does not
        # define (a measure missing from the extract), never a column to sum. Summed, it read
        # a column of that name that does not exist, and the gates stopped the whole run.
        raise CompileError(f"{m['name']!r} has no formula and no aggregation: the model does "
                           f"not define it")
    agg = str(m["aggregation"]).lower()
    rule = rules.aggregation(agg)
    if rule is None:
        res.unknown.append(f"aggregation:{agg}")
        raise CompileError(f"no rule for aggregation {agg!r}")
    res.rules_used.append(rule["id"])
    if rule.get("tier") == "provisional":
        res.provisional_rules.append(rule["id"])
    if colmap is not None:
        src = colmap.get(m["name"].lower())
        if src is None:
            # A named measure with no formula text is not a column. Emitting its name as one
            # gave `SUM(t.New Accounts (Latest))`, which does not parse.
            raise CompileError(f"{m['name']!r} has no formula and is not a column of the "
                               f"native query")
        return _apply(_agg_emit(rule, agg, dialect), [f"{alias}.{_ident(src)}"], agg)
    # The field's own name. Bare when it is a legal identifier (the warehouse folds its case
    # as it did when the column was created); quoted only when it cannot stand bare, so
    # `Unit Price` gives `t."Unit Price"` instead of `t.Unit Price`, which does not parse.
    name = m["name"]
    col = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) else _ident(name)
    return _apply(_agg_emit(rule, agg, dialect), [f"{alias}.{col}"], agg)


def _agg_call(pred: str) -> bool:
    """Whether a predicate tests an aggregate (a HAVING, not a WHERE)."""
    return bool(re.search(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", pred, re.I))


def _context_statements(aggs: list[dict], src: str, alias: str, joins: list[str],
                        where: list[str], rules, dialect: str
                        ) -> tuple[list[str], dict[str, str]]:
    """The statements a non-additive aggregate over a wider context reads (`_combine`).

    Each is the same aggregate over the SAME rows as the statement (its FROM, its joins, its
    row filters: ALLSELECTED keeps the visual's filters, and ALL of a grouped column the
    visual does not filter removes nothing else), grouped by only the columns the context
    keeps. With none kept it is one value, a scalar subquery; otherwise a derived table
    joined back on the kept columns, NULL matching NULL (the blank member is a member), one
    row per kept combination so the join never multiplies the statement's rows."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"its statement joins back on the kept columns, NULL matching "
                           f"NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    def body_of(src_: str, where_: list[str]) -> str:
        b = f"FROM   {src_} {alias}" + "".join(f"\n{j}" for j in joins)
        if where_:
            b += "\nWHERE  " + "\n  AND  ".join(where_)
        return b
    out_joins, sub = [], {}
    for a in aggs:
        mark, n = _CTX_MARK.format(n=a["n"]), a["n"]
        body = body_of(a.get("src") or src, a["where"] if "where" in a else where)
        if not a["keep"]:
            # One value: a one-row derived table beside every row, read back as an
            # aggregate so the statement keeps its own grouping (none on a card).
            out_joins.append(f"CROSS JOIN (\n    SELECT {a['sql']} AS v\n    "
                             + body.replace("\n", "\n    ") + f"\n  ) c{n}")
            sub[mark] = f"MAX(c{n}.v)"
            continue
        keys = ", ".join(f"{k} AS k{i}" for i, k in enumerate(a["keep"]))
        on = "\n   AND ".join(tmpl.replace("{0}", f"c{n}.k{i}").replace("{1}", k)
                              for i, k in enumerate(a["keep"]))
        out_joins.append(
            f"LEFT JOIN (\n    SELECT {keys}, {a['sql']} AS v\n    "
            + body.replace("\n", "\n    ")
            + f"\n    GROUP BY {', '.join(a['keep'])}\n  ) c{n}\n    ON {on}")
        sub[mark] = f"MAX(c{n}.v)"
    return out_joins, sub


def _lods_at_their_grain(sql: str, lods: list[dict], alias: str) -> str:
    """An aggregate of an expression that reads FIXED / INCLUDE levels of detail and no field
    of the rows themselves is evaluated once per combination of the view's dimensions and
    theirs, then aggregated (checked live: SUM(IIF({INCLUDE [order] : SUM(x)} > n, 1, 0))
    counts orders, not order lines; with a row field beside it, it is read on every row)."""
    marks = {r["n"]: _LOD_MARK["row"].format(n=r["n"]) for r in lods
             if r.get("mode") == "row" and r["kind"] in ("FIXED", "INCLUDE")}
    left = sql
    for m in marks.values():
        left = left.replace(m, "")
    if re.search(rf"\b{re.escape(alias)}\.", left) or re.search(r"__LODROW\d+__", left):
        return sql
    for r in lods:
        if r["n"] in marks and marks[r["n"]] in sql:
            r["mode"], r["outer"] = "grain", None
            if r["kind"] == "INCLUDE" and r.get("value_out") is None:
                return sql
            sql = sql.replace(marks[r["n"]], _LOD_MARK["grain"].format(n=r["n"]))
    return sql


def _lod_related_columns(unit: dict, source: str, columns: dict[str, set[str]],
                         measures: dict[str, str] | None, graph: list[dict] | None = None
                         ) -> list[tuple[str, str]]:
    """(column, table) of each field a level-of-detail expression of the unit reads that the
    statement's own table lacks and exactly one other table it relates to holds."""
    texts = [m.get("formula") or "" for m in unit.get("measures") or []
             if m.get("formula_language") in _LOD_LANGUAGES]
    texts += [measures[str(g).lower()] for g in unit.get("group_by") or []
              if measures and str(g).lower() in measures]
    own = {c.lower() for c in columns.get(source) or set()}
    others = list(dict.fromkeys(
        t for t in list(unit.get("warehouse_tables") or [])
        + [e.get(k) for e in graph or [] for k in ("from_table", "to_table")]
        if t and _bare(t) != _bare(source)))
    out: list[tuple[str, str]] = []
    for text in texts:
        if not _reaches_lod(text, measures or {}):
            continue
        try:
            toks = _expanded_tokens(text, measures)
        except CompileError:
            continue
        for k, v in toks:
            if k != "col":
                continue
            col = _column_ref(v).lower()
            if col in own or (measures and col in measures):
                continue
            holders = [t for t in others
                       if col in {c.lower() for c in columns.get(t) or columns.get(_bare(t)) or set()}]
            if len(holders) == 1 and (col, holders[0]) not in out:
                out.append((col, holders[0]))
    return out


def _padded_crosstab(unit: dict, select: list[str], src: str, alias: str, joins: list[str],
                     dim_preds: list[str], having: list[str], lods: list, windowed: bool,
                     rules, dialect: str, gkey) -> str | None:
    """The statement over a DENSIFIED crosstab, or None. A table calculation on a crosstab
    (`table_calc.pad`: the columns on its rows and on its columns) runs over every row tuple
    laid out against every column tuple, the empty cells included (checked live: a missing
    cell shows, the running value carried into it). The grouped rows are read from that
    domain (the row tuples crossed with the column tuples, over the rows the filters keep),
    each joined to its own rows, NULL matching NULL; an empty cell has NULL values."""
    pad = next(((m.get("table_calc") or {}).get("pad") for m in unit.get("measures") or []
                if (m.get("table_calc") or {}).get("pad")), None)
    groups = list(unit.get("group_by") or [])
    if not pad or not windowed or not groups:
        return None
    if having or lods:
        # Not left to a statement over the grouped rows only, which would run the calculation
        # without the empty cells (a silently different answer).
        raise CompileError("a table calculation on a densified crosstab beside "
                           + ("a filter on an aggregate" if having else
                              "a level of detail") + ": the empty cells it adds are not "
                           "compiled there")
    names = [str(g).lower() for g in groups]
    rows = [names.index(str(n).lower()) for n in pad.get("rows") or [] if str(n).lower() in names]
    cols = [names.index(str(n).lower()) for n in pad.get("cols") or [] if str(n).lower() in names]
    if not rows or not cols or sorted(rows + cols) != list(range(len(groups))):
        return None
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        return None
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    keys = [gkey(g, i) for i, g in enumerate(groups)]
    # Over joins, the layout's members are those of the rows that hold a row of the
    # statement's own table (checked live: a product no sale names adds no blank status
    # column), so its rows carry a marker.
    own = f"(SELECT {alias}.*, 1 AS __own FROM {src} {alias})" if joins else src
    held = [f"{alias}.__own = 1"] if joins else []
    base = (f"{own} {alias}" + "".join("\n      " + j.replace("\n", "\n      ") for j in joins)
            + ("\n      WHERE  " + "\n        AND  ".join(dim_preds) if dim_preds else ""))
    domain = (f"{own} {alias}" + "".join("\n      " + j.replace("\n", "\n      ")
                                         for j in joins)
              + ("\n      WHERE  " + "\n        AND  ".join(dim_preds + held)
                 if dim_preds or held else ""))
    # Over joined tables each column the statement reads is carried by name (`<alias>__<col>`)
    # out of the joined rows, so one alias reads them all.
    carried: dict[str, str] = {}
    if joins:
        for x in list(select) + keys:
            for m in re.finditer(rf"\b({re.escape(alias)}|j\d+)\.(\w+)\b", x):
                carried.setdefault(m.group(0), f"{m.group(1)}__{m.group(2)}")

    def distinct(idx: list[int]) -> str:
        return ("SELECT DISTINCT " + ", ".join(f"{keys[i]} AS __pad{i}" for i in idx)
                + f"\n      FROM   {domain}")
    on = "\n     AND ".join(nse.replace("{0}", f"x.__key{i}").replace("{1}", f"d.__pad{i}")
                           for i in range(len(groups)))
    body = (f"  SELECT d.*, x.*\n  FROM   (\n    SELECT * FROM (\n      {distinct(rows)}\n"
            f"    ) pr\n    CROSS JOIN (\n      {distinct(cols)}\n    ) pc\n  ) d\n"
            f"  LEFT JOIN (\n    SELECT " + ", ".join(f"{k} AS __key{i}" for i, k in enumerate(keys))
            + (", " + ", ".join(f"{c} AS {n}" for c, n in carried.items()) if carried
               else f", {alias}.*") + f"\n    FROM   {base}\n  ) x\n    ON {on}")
    order = sorted(range(len(keys)), key=lambda i: -len(keys[i]))
    out = []
    for j, x in enumerate(select):
        bare = j < len(groups) and not re.search(r"\sAS\s+\S+$", x)
        name = x.rsplit(".", 1)[-1].strip() if bare else None
        for i in order:
            x = x.replace(keys[i], f"{alias}.__pad{i}")
        if carried:
            x = re.sub(rf"\b({re.escape(alias)}|j\d+)\.(\w+)\b",
                       lambda m: (f"{alias}.{carried[m.group(0)]}" if m.group(0) in carried
                                  else m.group(0)), x)
        out.append(f"{x} AS {name}" if bare else x)
    return ("SELECT " + ",\n       ".join(out) + f"\nFROM   (\n{body}\n) {alias}"
            + "\nGROUP BY " + ", ".join(f"{alias}.__pad{i}" for i in range(len(groups))))


def _inner(join: str) -> str:
    """A join clause as an inner join: only the rows that reach the joined table."""
    return re.sub(r"^\s*(FULL\s+OUTER|LEFT(\s+OUTER)?|RIGHT(\s+OUTER)?)\s+JOIN\b", "JOIN",
                  join, flags=re.I)


def _related(text: str, side: str, prefix: str, plan: "_JoinPlan | None",
             source_columns: set[str] | None) -> tuple[str, set[str]]:
    """`side.col` of a column the statement's own table does not hold, as the related table's
    alias the join plan reads it under (`prefix` + alias): (text, the aliases it needs)."""
    if plan is None or not source_columns:
        return text, set()
    used: set[str] = set()

    def place(m) -> str:
        col = m.group(1)
        if col.lower() in source_columns or col.startswith("__lodin") \
                or col.startswith(("__lodd", "__lodr")) or re.fullmatch(r"e\d+", col):
            return m.group(0)
        at = next((a for (c, _t), a in list(plan._at.items()) + list(plan.measure_at.items())
                   if c == col.lower() and a != plan.alias), None)
        if at is None:
            return m.group(0)
        used.add(at)
        return f"{prefix}{at}.{col}"
    return re.sub(rf"\b{re.escape(side)}\.(\w+)", place, text), used


def _lod_parts(lods: list[dict], src: str, alias: str, rules, dialect: str,
               source_columns: set[str] | None, dim_preds: list[str] | None = None,
               plan: "_JoinPlan | None" = None, plan_joins: list[str] | None = None
               ) -> tuple[list[str], dict[str, str]]:
    """The joins a statement's levels of detail need and what each placeholder reads.

    A FIXED level of detail is its own statement over the rows the data source and context
    filters keep (`src`, before the view's dimension filters), grouped by its dimensions and
    joined to the statement's rows on them, NULL matching NULL (a member Tableau shows as
    Null is a member). One with no dimension is one value for the whole table."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"a level-of-detail expression joins on its dimensions, NULL "
                           f"matching NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    joins, sub = [], {}
    for r in lods:
        if r["mode"] == "grain" and r["kind"] == "INCLUDE":
            # Read over the view's own rows: its fields where the statement reads them.
            r["dims_out"] = [_related(d, alias, "", plan, source_columns)[0]
                             for d in r["dims_out"]]
            if r.get("value_out"):
                r["value_out"] = _related(r["value_out"], alias, "", plan, source_columns)[0]
            continue
        # A level of detail over a related table's field reads the rows through the same
        # relationships as the statement: its own joins, under its own aliases (`s<alias>`).
        r["value_in"], need = _related(r["value_in"], "s", "s", plan, source_columns)
        dims = [_related(d, "s", "s", plan, source_columns) for d in r["dims_in"]]
        r["dims_in"] = [d for d, _u in dims]
        need |= {a for _d, u in dims for a in u}
        # (Read again, a record already placed names its related aliases itself.)
        need |= set(re.findall(r"\bs(j\d+)\.", " ".join([r["value_in"]] + r["dims_in"])))
        r["dims_out"] = [_related(d, alias, "", plan, source_columns)[0] for d in r["dims_out"]]
        if r.get("value_out"):
            r["value_out"] = _related(r["value_out"], alias, "", plan, source_columns)[0]
        text = " ".join([r["value_in"]] + r["dims_in"])
        text = re.sub(r"\bs\.(__lodin\d+|__lodr\d+|e\d+)\b", "", text) if r.get("from") \
            else text
        text = re.sub(r"\bsj\d+\.\w+", "", text)
        def renamed(text: str) -> str:
            return re.sub(r"\bj(\d+)\b", r"sj\1",
                          re.sub(rf"\b{re.escape(alias)}\.", "s.", text))
        preds = [renamed(p) for p in dim_preds or []] if r.get("after_filters") else []
        if any(re.search(r"\bsj\d+\.", p) for p in preds):
            need = need or {"*"}
        rows = f"{src} s"
        if need:
            # Only the rows that reach the related table count (checked live: a fact row with
            # no category is in no category's FIXED value, and that member's value is empty).
            rows += "".join(f"\n    {_inner(renamed(j))}" for j in plan_joins or []
                            if not j.startswith("__"))
        if r.get("pre"):
            # A level of detail among its dimensions: a column of the rows it reads.
            cols = ", ".join(f"{w} AS {c}" for c, w in r["pre"])
            where = f" WHERE {' AND '.join(preds)}" if preds and not r.get("from") else ""
            rows = f"(\n      SELECT s.*, {cols}\n      FROM {src} s{where}\n    ) s"
            preds = [] if where else preds
        if r.get("from"):
            # Nested levels of detail over the rows (after the view's dimension filters, for
            # one an INCLUDE or EXCLUDE holds).
            kept = (f"(SELECT s.* FROM {src} s WHERE {' AND '.join(preds)})" if preds
                    else src)
            rows = (f"(\n      {r['from'].replace('{src}', kept).replace('{src_all}', src)}"
                    f"\n    ) s")
        elif preds:
            # INCLUDE / EXCLUDE: after the view's dimension filters.
            rows += f"\n    WHERE {' AND '.join(preds)}"
        if source_columns:
            missing = sorted({c for c in re.findall(r"\bs\.(\w+)", text)
                              if c.lower() not in source_columns
                              and not c.startswith(("__lodd", "__lodr"))})
            if missing:
                raise CompileError(f"a level-of-detail expression reads {missing}, which are "
                                   f"not columns of the statement's own table")
        n = r["n"]
        if r["mode"] == "scalar":
            sub[_LOD_MARK["scalar"].format(n=n)] = f"(SELECT {r['value_in']} FROM {rows})"
            continue
        if r["mode"] == "members":
            # A Top N set: its members, ranked by the expression (an empty value last).
            d = r["dims_in"][0]
            sub[f"__LODSET{n}__"] = (
                f"(\n    SELECT k FROM (\n      SELECT {d} AS k, {r['value_in']} AS v\n"
                f"      FROM   {rows}\n"
                + (f"      WHERE  {r['among']}\n" if r.get("among") else "")
                + f"      GROUP BY {d}\n    ) m\n"
                f"    ORDER BY {_sort_term('v', r['direction'], 'LAST', dialect)}\n"
                f"    {_limit(int(r['limit']), dialect)}\n  )")
            continue
        keys = ", ".join(f"{d} AS k{i}" for i, d in enumerate(r["dims_in"]))
        on = "\n   AND ".join(tmpl.replace("{0}", f"l{n}.k{i}").replace("{1}", d)
                              for i, d in enumerate(r["dims_out"]))
        joins.append(f"LEFT JOIN (\n    SELECT {keys}, {r['value_in']} AS v\n    FROM   {rows}"
                     f"\n    GROUP BY {', '.join(r['dims_in'])}\n  ) l{n}\n    ON {on}")
        if r["mode"] == "row":
            sub[_LOD_MARK["row"].format(n=n)] = f"l{n}.v"
    return joins, sub


def _grain_statement(select: list[str], groups: list[str], grain: list[dict], src: str,
                     alias: str, joins: list[str], where: list[str],
                     lod_sub: dict[str, str], nse: str | None = None) -> str:
    """The statement for aggregates over FIXED / INCLUDE levels of detail: the view's rows at
    the grain of its own dimensions and every such level of detail's (`g`), then aggregated
    to the view. A plain SUM / COUNT / MIN / MAX beside it is computed at that grain and
    aggregated again. Any other aggregate of the view's rows beside it (an average, a
    distinct count, a median) is computed at the view's own grain in a statement of its own
    (`p`), joined to the view's rows on its dimensions, NULL matching NULL (`nse`)."""
    def name_of(entry: str) -> str:
        if re.search(r"\sAS\s+\w+$", entry):
            return entry.rsplit(" AS ", 1)[1].strip()
        return entry.rsplit(".", 1)[-1].strip()
    inner, outer = [], []
    direct: dict[str, str] = {}
    for i, g in enumerate(groups):
        inner.append(f"{g} AS g{i}")
        outer.append(f"g.g{i} AS {name_of(select[i])}")
    dims = list(dict.fromkeys(d for r in grain for d in r["dims_out"]))
    inner += [f"{d} AS d{j}" for j, d in enumerate(dims)]
    for r in grain:
        n = r["n"]
        inner.append(f"MAX(l{n}.v) AS x{n}" if r["kind"] == "FIXED"
                     else f"{r['value_out']} AS x{n}")
    atoms: dict[str, str] = {}
    simple = re.compile(r"\b(SUM|MIN|MAX|COUNT)\(([^()]*)\)")
    for entry in select[len(groups):]:
        expr, label = entry.rsplit(" AS ", 1)
        for r in grain:
            if r["outer"] is None:
                # An expression of the level of detail, evaluated at the grain (`g`) and
                # aggregated by the aggregate around it.
                expr = expr.replace(_LOD_MARK["grain"].format(n=r["n"]), f"g.x{r['n']}")
                continue
            fn = _LOD_OUTER[r["outer"]]
            close = ")" if "(" in fn else ""
            expr = expr.replace(_LOD_MARK["grain"].format(n=r["n"]),
                                f"{fn}{'' if close else '('}"
                                                    f"{' ' if close else ''}g.x{r['n']})")

        def atom(m) -> str:
            fn, arg = m.group(1).upper(), m.group(2)
            if arg.strip().upper().startswith("DISTINCT") or arg.strip().startswith("g."):
                return m.group(0)
            key = f"{fn}({arg})"
            if key not in atoms:
                atoms[key] = f"a{len(atoms)}"
            return f"{'SUM' if fn in ('SUM', 'COUNT') else fn}(g.{atoms[key]})"
        expr = simple.sub(atom, expr)
        expr = _view_grain_atoms(expr, alias, direct)
        for i in sorted(range(len(groups)), key=lambda i: -len(groups[i])):
            # A window beside it runs over the view's rows: its columns are the view's own.
            expr = expr.replace(groups[i], f"g.g{i}")
        if re.search(rf"\b{re.escape(alias)}\.|\bl\d+\.", expr):
            raise CompileError(f"{label.strip()} beside it reads the rows in a way that does "
                               f"not aggregate again from the grain of the level of detail")
        outer.append(f"{expr} AS {label.strip()}")
    inner += [f"{k} AS {v}" for k, v in atoms.items()]
    sql = "SELECT " + ",\n       ".join(inner) + f"\nFROM   {src} {alias}"
    # An INCLUDE over a related table's field reads only the rows that reach it (checked
    # live: the rows with no category are in no INCLUDE of a product, and no row shows them).
    related = any(r["kind"] == "INCLUDE" and re.search(r"\bj\d+\.", " ".join(r["dims_out"]))
                  for r in grain)
    for j in joins:
        lod_join = re.search(r"\)\s+l\d+\s+ON\b", j) is not None
        sql += f"\n{_inner(j) if related and not lod_join else j}"
    if where:
        sql += "\nWHERE  " + "\n  AND  ".join(where)
    keys = groups + dims
    if keys:
        sql += "\nGROUP BY " + ", ".join(keys)
    out = "SELECT " + ",\n       ".join(outer) + "\nFROM   (\n" + sql + "\n) g"
    if direct:
        if groups and not nse:
            raise CompileError("an aggregate beside it joins on the view's dimensions, NULL "
                               "matching NULL, which has no form for this dialect")
        body = ("SELECT " + ",\n       ".join([f"{g} AS g{i}" for i, g in enumerate(groups)]
                                              + [f"{v} AS {k}" for k, v in direct.items()])
                + f"\nFROM   {src} {alias}")
        for j in joins:
            if not re.match(r"(?s)LEFT JOIN \(\n    SELECT .* \) l\d+\n", j):
                body += f"\n{j}"
        if where:
            body += "\nWHERE  " + "\n  AND  ".join(where)
        if groups:
            body += "\nGROUP BY " + ", ".join(groups)
        on = "\n   AND ".join(nse.replace("{0}", f"p.g{i}").replace("{1}", f"g.g{i}")
                              for i in range(len(groups))) if groups else "1 = 1"
        out += f"\nLEFT JOIN (\n{body}\n) p\n    ON {on}"
    if groups:
        out += "\nGROUP BY " + ", ".join(f"g.g{i}" for i in range(len(groups)))
    for key, val in lod_sub.items():
        out = out.replace(key, val)
    return out


# An aggregate of the view's rows that does not aggregate again from a finer grain.
_VIEW_GRAIN_CALL = re.compile(r"\b(AVG|COUNT|MEDIAN|STDDEV_SAMP|STDDEV_POP|VAR_SAMP|VAR_POP|"
                              r"PERCENTILE_CONT|SUM|MIN|MAX)\s*\(", re.I)


def _view_grain_atoms(expr: str, alias: str, direct: dict[str, str]) -> str:
    """Each aggregate call in `expr` that still reads the view's rows (`alias.`) and not the
    grain's (`g.`) becomes a column of the view-grain statement: `MAX(p.b<k>)`, one value per
    view row."""
    out, i = "", 0
    while True:
        m = _VIEW_GRAIN_CALL.search(expr, i)
        if m is None:
            return out + expr[i:]
        depth, j = 0, m.end() - 1
        while j < len(expr):
            depth += {"(": 1, ")": -1}.get(expr[j], 0)
            if depth == 0:
                break
            j += 1
        call = expr[m.start():j + 1]
        after = expr[j + 1:].lstrip()
        if (re.search(rf"\b{re.escape(alias)}\.", call) and "g." not in call
                and not after.upper().startswith("OVER")):
            key = next((k for k, v in direct.items() if v == call), None)
            if key is None:
                key = f"b{len(direct)}"
                direct[key] = call
            out += expr[i:m.start()] + f"MAX(p.{key})"
            i = j + 1
        else:
            out += expr[i:m.end()]
            i = m.end()


_SHELF_LOD = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
              "count_distinct": "COUNTD", "median": "MEDIAN"}


def _reaches_lod(formula: str, measures: dict[str, str], depth: int = 0) -> bool:
    """Whether a formula holds a level-of-detail expression, itself or through the calcs it
    names: the shelf's aggregate is then put around it in the formula, where the parser reads
    an aggregate over a level of detail at its grain."""
    if "{" in _without_literals(formula or ""):
        return True
    if depth > 16:
        return False
    return any(_reaches_lod(measures[k], measures, depth + 1)
               for k in {m.lower() for m in re.findall(r"\[([^\]]+)\]", formula or "")}
               if k in measures)


def _over_of(tc: dict, ctx: "_UnitContext") -> dict:
    """A measure's table calculation as the window it runs in: the grouped columns' SQL it
    restarts on (partition) and runs along, in order (order). A column the statement does not
    group by is refused: the view's rows would not be the statement's."""
    by_name = {name: sql for name, _table, sql in ctx.groups}
    out: dict[str, list[str]] = {}
    for key, want in (("partition", "partitioning"), ("order", "addressing")):
        cols = []
        for c in tc.get(want) or []:
            sql = by_name.get(str(c).lower())
            if sql is None:
                raise CompileError(f"a table calculation runs over {c!r}, which the statement "
                                   f"does not group by")
            cols.append(sql)
        out[key] = cols
    # A sorted addressed column runs in the view's order: its SQL with the sort, and the SQL
    # of the columns a nested sort orders within.
    sorts = {}
    for c, rec in (tc.get("sort") or {}).items():
        sql = by_name.get(str(c).lower())
        if sql is None:
            continue
        within = [by_name[str(w).lower()] for w in rec.get("within") or []
                  if str(w).lower() in by_name]
        sorts[sql] = {**rec, "within_sql": within}
    if sorts:
        out["sorts"] = sorts
    if tc.get("nested"):
        # A calc the measure reaches runs by its own compute-using: {calc, lower: window}.
        out["nested"] = {str(k).lower(): _over_of(v, ctx) for k, v in tc["nested"].items()}
    return out


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


def _edge_pairs(e: dict) -> list[tuple[str, str]]:
    pairs = [(str(p["from_column"]), str(p["to_column"])) for p in e.get("columns") or []]
    return pairs or [(str(e["from_column"]), str(e["to_column"]))]


class _Step:
    """One hop of a join path: along relationship `edge` (its index in the unit's graph),
    forwards (from end -> to end) or back, onto `table`."""

    def __init__(self, idx: int, edge: dict, forward: bool, table: str, distinct: bool,
                 keep: bool, keep_far: bool = False) -> None:
        self.idx, self.edge, self.forward, self.table = idx, edge, forward, table
        # keep: the near end's unmatched rows survive; keep_far: the reached end's too (a
        # relationship that keeps what does not match on either side).
        self.distinct, self.keep, self.keep_far = distinct, keep, keep_far
        pairs = _edge_pairs(edge)
        # (column on the table the hop leaves, column on the table it reaches)
        self.pairs = pairs if forward else [(b, a) for a, b in pairs]

    @property
    def key(self) -> tuple[int, bool]:
        return (self.idx, self.forward)

    @property
    def name(self) -> str:
        e = self.edge
        return str(e.get("name") or f"{_bare(e.get('from_table'))}.{e.get('from_column')} -> "
                                     f"{_bare(e.get('to_table'))}.{e.get('to_column')}")


class _JoinPlan:
    """The joins a statement needs, and where each column it groups or filters by is read.

    `at(column, table)` is the alias a column is read under: the statement's own alias, or the
    alias of the table a declared relationship path reaches."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.clauses: list[str] = []
        self.unreachable: set[str] = set()
        self.reasons: list[str] = []
        self.full = False          # a hop keeps the reached table's unmatched rows too
        self._at: dict[tuple[str, str], str] = {}
        # Each joined alias -> the key columns it is joined on, so an expression over a
        # joined table's row can tell an unmatched row (all NULL) from a matched one.
        self.keys: dict[str, list[str]] = {}
        # (column, warehouse table) a MEASURE reads through a joined table -> its alias, and
        # why one could not be reached (a measure that needs it is refused, not the unit).
        self.measure_at: dict[tuple[str, str], str] = {}
        self.unplaced: dict[tuple[str, str], str] = {}
        # Each joined alias -> the warehouse table it reads (bare).
        self.table_at: dict[str, str] = {}
        # Columns on a table the relationships reach only against their filter direction or
        # along an inactive relationship: (column -> (table, hint)). Such a table does not
        # filter the statement's rows; the visual lists its members beside every row.
        self.detached: dict[str, tuple[str, str | None]] = {}
        # A path through an end that is not one row (many-to-many, or one-to-many along a
        # relationship that filters both ways): its root alias -> how it is read
        # (`_expand_branches`), and the row predicates on its tables, which belong inside it.
        self.branches: dict[str, dict] = {}
        self.branch_preds: dict[str, list[str]] = {}
        self.graph: list[dict] = []

    def at(self, column: str, table: str | None = None) -> str:
        col = str(column).lower()
        return (self._at.get((col, _bare(table))) or self._at.get((col, ""))
                or self.alias)


def _blank_rows_having(values: list[str]) -> str | None:
    """`(a IS NOT NULL OR b IS NOT NULL ...)` over the statement's value projections (each
    `expr AS alias`); None when there is none, or one is a window (a HAVING cannot test it)."""
    exprs = [x.rsplit(" AS ", 1)[0] for x in values]
    if not exprs or any(" OVER (" in e for e in exprs):
        return None
    return "(" + "\n       OR ".join(f"{e} IS NOT NULL" for e in exprs) + ")"


_JOIN_CLAUSE = re.compile(r"^\s*(?:LEFT\s+|FULL\s+(?:OUTER\s+)?|INNER\s+)?JOIN\s+(.+?)\s+(\w+)"
                          r"\s+ON\s+(.*)$", re.S | re.I)


def _with_every_member(leaf: str, select: list[str], n_groups: int, gexprs: list[str],
                       src: str, alias: str, joins: list[str], preds: list[str], rules,
                       dialect: str, graph: list[dict] | None = None,
                       members: list[str] | None = None,
                       folded: set[int] | frozenset = frozenset()) -> str:
    """"Show items with no data": every member of the grouping columns, with the leaf
    statement's values where it has a row (NULL elsewhere).

    The members of the columns read on ONE table are the combinations that table holds
    (under the filters on it, and on the tables it reaches along its own many-to-one hops,
    which filter it); columns of different tables are crossed. A filter on a table only
    REACHED FROM a grouped table (the fact, when a dimension is grouped) does not narrow that
    dimension's members. The leaf's own rows are kept too (a FULL JOIN), so a member only the
    rows have (the blank member of unmatched keys) stays."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"it joins the members to the rows, NULL matching NULL, which has "
                           f"no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    # The join tree: each joined alias -> (its table, its ON clause, the alias it hangs off).
    tree: dict[str, tuple[str, str, str]] = {}
    for j in joins:
        m = _JOIN_CLAUSE.match(j)
        if not m:
            raise CompileError("a join of the statement this cannot read")
        parents = [a for a in re.findall(r"\b(\w+)\.", m.group(3)) if a != m.group(2)]
        if len(set(parents)) != 1:
            raise CompileError("a join of the statement that hangs off no single table")
        tree[m.group(2)] = (m.group(1), j, parents[0])

    def below(a: str) -> list[str]:
        out, todo = [], [a]
        while todo:
            cur = todo.pop(0)
            kids = [k for k, (_t, _j, par) in tree.items() if par == cur]
            out += kids
            todo += kids
        return out

    def aliases_in(text: str) -> set[str]:
        return {a for a in re.findall(r"\b(\w+)\.", re.sub(r"'(?:[^']|'')*'", "", text))
                if a == alias or a in tree}

    owner: list[str] = []
    for e in gexprs:
        on = aliases_in(e)
        if len(on) != 1:
            raise CompileError(f"a grouping column read on {len(on)} tables at once")
        owner.append(next(iter(on)))
    outs = []
    for x in select[:n_groups]:
        name = x.rsplit(" AS ", 1)[1] if " AS " in x else x.rsplit(".", 1)[-1]
        outs.append(name.strip())
    parts = []
    for k, a in enumerate(dict.fromkeys(owner)):
        reach = [a] + below(a)
        # Only a filter on the table itself narrows its members (seen live: SUMMARIZECOLUMNS
        # crosses a dimension's every member with the others under a filter on a table its
        # rows reach, e.g. every product beside the categories a department filter keeps);
        # the fact, which reaches every dimension, is narrowed by all of them.
        mine = [p for p in preds if aliases_in(p) and (
            aliases_in(p) <= {a} or (a == alias and aliases_in(p) <= set(reach)))]
        # A member filter (the members a Top N keeps) is a predicate on its key's table,
        # whatever its ranking subquery reads.
        for p in members or []:
            key = re.match(r"\(?\s*(\w+)\.\w+\s+IN\s*\(", p)
            if key and (key.group(1) == a or (a == alias and key.group(1) in reach)):
                mine.append(p)
        # A text column the model compares case- and trailing-space-insensitively is one
        # member per folded value (seen live: 'north' and 'North' are one region), shown by
        # the spelling the rows show.
        mine_idx = [i for i, o in enumerate(owner) if o == a]
        fold = [i for i in mine_idx if i in folded]
        cols = [f"MIN({gexprs[i]}) AS g{i}" if i in folded else f"{gexprs[i]} AS g{i}"
                for i in mine_idx]
        frm = (f"{src} {alias}" if a == alias else f"{tree[a][0]} {a}")
        body = (("SELECT " if fold else "SELECT DISTINCT ")
                + ",\n                   ".join(cols)
                + f"\n            FROM   {frm}"
                + "".join(f"\n            {tree[b][1]}" for b in below(a)))
        if mine:
            body += "\n            WHERE  " + "\n              AND  ".join(mine)
        if fold:
            body += "\n            GROUP BY " + ", ".join(
                _Parser._text_key(gexprs[i]) if i in folded else gexprs[i] for i in mine_idx)
        if not mine and a != alias:
            # The table's BLANK member: a row reaching it with no match (an unmatched key) is
            # a member of its own, with no filter on the table to leave it out.
            path, cur = [], a
            while cur in tree:
                path.insert(0, tree[cur][1])
                cur = tree[cur][2]
            key = re.search(rf"\b{re.escape(a)}\.(\w+)", tree[a][1].split(" ON ", 1)[-1])
            nulls = ", ".join(f"NULL AS g{i}" for i, o in enumerate(owner) if o == a)
            if cur == alias and key:
                body = (f"{body}\n            UNION\n            SELECT {nulls}"
                        f"\n            FROM   {src} {alias}"
                        + "".join(f"\n            {j}" for j in path)
                        + f"\n            WHERE  {a}.{key.group(1)} IS NULL")
            # Another relationship into the table (an inactive one too) whose keys it lacks
            # makes the same blank member (seen live: the ship date's empty keys give the
            # date table a blank year).
            other = _blank_member_test(tree[a][0], [e for e in graph or []
                                                    if e.get("is_active") is False])
            if other:
                body = (f"{body}\n            UNION\n            SELECT {nulls}"
                        f"\n            FROM   (SELECT 1 AS one) z\n            WHERE  {other}")
        parts.append(f"(\n            {body}\n        ) m{k}")
    idx = {a: k for k, a in enumerate(dict.fromkeys(owner))}
    on = " AND ".join(tmpl.format(_Parser._text_key(f"m{idx[o]}.g{i}"),
                                  _Parser._text_key(f"v.{outs[i]}")) if i in folded
                      else tmpl.format(f"m{idx[o]}.g{i}", f"v.{outs[i]}")
                      for i, o in enumerate(owner))
    keys = [f"COALESCE(m{idx[o]}.g{i}, v.{outs[i]}) AS {outs[i]}" for i, o in enumerate(owner)]
    vals = [f"v.{x.rsplit(' AS ', 1)[1].strip()}" for x in select[n_groups:]]
    inner = "\n".join("    " + ln for ln in leaf.splitlines())
    members = "\n    CROSS JOIN ".join(parts)
    return ("SELECT " + ",\n       ".join(keys + vals)
            + f"\nFROM   {members}\nFULL JOIN (\n{inner}\n) v\n  ON  {on}")


_REF = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def _branch_split(preds: list[str], plan: _JoinPlan) -> list[str]:
    """The row predicates that stay on the statement; the ones over a branch's tables only go
    inside it (`plan.branch_preds`): outside, a member that passes would repeat a row."""
    if not plan.branches:
        return preds
    owner = {al: root for root, br in plan.branches.items() for al in br["aliases"]}
    out = []
    for p in preds:
        text = re.sub(r"'(?:[^']|'')*'", "", p)
        hit = {owner.get(m.group(1)) for m in _REF.finditer(text)
               if m.group(1) in owner or m.group(1) == plan.alias
               or m.group(1) in plan.table_at}
        roots = {h for h in hit if h is not None}
        if not roots:
            out.append(p)
            continue
        if len(roots) > 1 or None in hit:
            raise CompileError("a filter over a many-to-many (or both-ways) path and another "
                               "table at once, which one distinct set of members cannot hold")
        plan.branch_preds.setdefault(next(iter(roots)), []).append(p)
    return out


def _blank_in(f: dict) -> bool | None:
    """Whether a filter keeps a blank member: an `in` listing the blank, a `not_in` not
    listing it; a test of the empty value (`is_null`), a text test (a blank holds no text:
    `contains` fails it, `not_contains` keeps it) and a comparison read with the filter's
    stand-in for an empty cell (`null_as`: 0, "" or the zero date). None for a filter whose
    test of a blank is not read here (a Top N, a relative date)."""
    op, v = str(f.get("op") or "").lower(), f.get("value")
    vals = v if isinstance(v, list) else [v]
    if op in ("in", "=") or (op == "eq" and isinstance(v, list)):
        return any(x is None for x in vals)
    if op in ("not_in", "ne", "<>"):
        return not any(x is None for x in vals)
    if op in ("is_null", "not_null"):
        return op == "is_null"
    if op in _TEXT_OPS:
        return op.startswith("not_")
    if op in ("eq", "filter", "gt", "lt", "neq", "lte", "gte", "range") and v is not None:
        return _empty_passes("eq" if op == "filter" else op, v, f.get("null_as"))
    if op in ("any_of", "all_of") and isinstance(v, list) and v:
        parts = [_blank_in({**part, "null_as": f.get("null_as")}) for part in v
                 if isinstance(part, dict)]
        if None in parts or len(parts) != len(v):
            return None
        return any(parts) if op == "any_of" else all(parts)
    return None


def _blank_member_test(table: str, graph: list[dict], _seen: frozenset = frozenset()
                       ) -> str | None:
    """When `table` has a BLANK member: the condition that some relationship into its one end
    holds a key it lacks (or no key), or that the table on its many side has a blank member
    of its own (whose key is blank: seen live, a category lists a blank when a sale's product
    is unknown, every product's category known). None when no relationship ends on it as
    one."""
    tests = []
    for e in graph or []:
        fs, ts = _card_ends(e.get("cardinality"))
        if str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        for many_t, one_t, many_end, one_end, flip in (
                (e.get("from_table"), e.get("to_table"), fs, ts, False),
                (e.get("to_table"), e.get("from_table"), ts, fs, True)):
            if _bare(one_t) != _bare(table) or one_end != "one" or many_end != "many":
                continue
            pairs = _edge_pairs(e)
            pairs = [(b, a) for a, b in pairs] if flip else pairs
            on = " AND ".join(f"y_.{oc} = x_.{mc}" for mc, oc in pairs)
            tests.append(f"EXISTS (SELECT 1 FROM {many_t} x_ "
                         f"WHERE NOT EXISTS (SELECT 1 FROM {one_t} y_ WHERE {on}))")
            if _bare(many_t) not in _seen:
                farther = _blank_member_test(many_t, graph, _seen | {_bare(table)})
                if farther:
                    tests.append(farther)
    return " OR ".join(dict.fromkeys(tests)) or None


def _expand_branches(sql: str, plan: _JoinPlan, rules, dialect: str) -> str:
    """Each branch placeholder -> `INNER JOIN (SELECT DISTINCT member, columns ...) a ON
    member = key`, NULL matching NULL (a blank member is a member). The statement's references
    to the branch's tables are read off the distinct set."""
    if not plan.branches:
        return sql
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"a many-to-many (or both-ways) path joins on its members, NULL "
                           f"matching NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    for a, br in plan.branches.items():
        mine = set(br["aliases"])
        holder = f"__BRANCH_{a}__"
        head, _, tail = sql.partition(holder)
        if not _:
            continue
        # Each column the statement reads on the branch's tables is projected under its own
        # name, or `<alias>_<column>` where two of its tables share that name.
        found = {(m.group(1), m.group(2)) for m in _REF.finditer(head + "\n" + tail)
                 if m.group(1) in mine}
        count: dict[str, int] = {}
        for _al, col in found:
            count[col.lower()] = count.get(col.lower(), 0) + 1
        refs = {(al, col): (col if count[col.lower()] == 1 and not col.lower().startswith(
            f"{a}_m") else f"{al}_{col}") for al, col in found}

        def sub(m: re.Match, a: str = a, mine: set = mine, refs: dict = refs) -> str:
            al, col = m.group(1), m.group(2)
            if al not in mine:
                return m.group(0)
            return f"{a}.{refs[(al, col)]}"
        head, tail = _REF.sub(sub, head), _REF.sub(sub, tail)
        step = br["step"]
        e = step.edge
        near_table = e["from_table"] if step.forward else e["to_table"]
        pairs = step.pairs                    # (column on the table left, column reached)
        if br["near_one"]:
            # The member is the row of the one end each row reaches, or its blank member.
            member = [f"{a}_m.{sc}" for sc, _dc in pairs]
            via = (f"\n    LEFT JOIN {near_table} {a}_m ON "
                   + " AND ".join(f"{a}_m.{sc} = {a}.{dc}" for sc, dc in pairs))
        else:
            member = [f"{a}.{dc}" for _sc, dc in pairs]
            via = ""
        base = str(step.table)
        blank = _blank_member_test(base, plan.graph)
        if blank:
            base = (f"(\n      SELECT * FROM {step.table}\n      UNION ALL\n      SELECT b0_.*"
                    f"\n      FROM   (SELECT 1 AS one_) o_\n      LEFT JOIN {step.table} b0_ "
                    f"ON 1 = 0\n      WHERE  {blank}\n    )")
        cols = [f"{m} AS {a}_m{i}" for i, m in enumerate(member)]
        cols += [f"{al}.{col} AS {name}" for (al, col), name in refs.items()
                 if name not in {f"{a}_m{i}" for i in range(len(member))}]
        body = ("SELECT DISTINCT " + ",\n           ".join(dict.fromkeys(cols))
                + f"\n    FROM   {base} {a}" + via
                + "".join(f"\n    {c}" for c in br["clauses"]))
        preds = plan.branch_preds.get(a) or []
        if preds:
            body += "\n    WHERE  " + "\n      AND  ".join(preds)
        on = " AND ".join(tmpl.format(f"{a}.{a}_m{i}", f"{br['prev']}.{sc}")
                          for i, (sc, _dc) in enumerate(pairs))
        sql = head + f"INNER JOIN (\n    {body}\n  ) {a} ON {on}" + tail
    return sql


def _tuple_columns(f: dict) -> list[dict]:
    """The columns a `tuple` filter tests together ({column, table} each), else none."""
    v = f.get("value")
    if str(f.get("op") or "").lower() != "tuple" or not isinstance(v, dict):
        return []
    return [c for c in v.get("columns") or [] if isinstance(c, dict) and c.get("column")]


def _filter_reads_column(f: dict) -> bool:
    """A filter entry the compiler turns into a predicate on its column."""
    if (f.get("stage") or "").lower() in ("measure", "table-calc"):
        return False
    if str(f.get("op") or "").lower() == "topn":
        return False
    return not (f.get("value") is None and f.get("value_kind") == "absent")


def _plan_joins(unit: dict, source: str, alias: str, columns: dict[str, set[str]],
                extra: list[tuple[str, str]] | tuple = (), graph: list[dict] | None = None,
                fields: list[tuple[str, str]] | tuple = (), whole_model: bool = False
                ) -> _JoinPlan:
    """The joins that reach every column the unit groups or filters by.

    THE SEMANTICS ARE THE RELATIONSHIP'S, read off the common relationship fields, never the
    tool's name:

    * Only an ACTIVE relationship is walked (`is_active`), and never a blend, a range or an
      as-of one. A table only those reach is refused by name.
    * A hop never multiplies the statement's rows. From the many end to the one end (or across
      a one-to-one) it is a plain join. Onto an end that is not one (many-to-many, or a
      multiplicity the model does not state) the reached table is read as its DISTINCT rows
      over the columns the statement needs, so each row of the statement's table is counted
      once per member, as a relationship that aggregates each table at its own grain does.
      From the one end to the many end it is walked only when the relationship filters both
      ways (`cross_filter: both`), and then read the same distinct way; otherwise refused.
    * Unmatched rows follow `join_type`: a `left` relationship keeps a many-end row whose key
      the one end lacks (a NULL member, the tool's blank one), a `full` one keeps an unmatched
      row of either end, an `inner` one neither. A row of the statement's table is kept
      (LEFT JOIN) exactly when its end is one of those the relationship keeps: for `left`, the
      many end, however the model stored the relationship round. Under `full` the reached
      table's unmatched rows are kept too (FULL OUTER JOIN: a member with no fact rows shows,
      with NULL measures). A filter still applies after the join: one on the statement's
      table removes such a member, as Tableau's own answer does (seen live).
    * Any number of hops (a snowflaked dimension two tables away). When two different paths
      of the same length reach a table, which one the model means is not knowable here: the
      column is refused by name, never joined along the first.
    * A column is read on the table the unit says it is on (`group_by_tables`, a filter's
      `table`); otherwise on the statement's own table when that holds it, else on the one
      unit table that does. Two candidate tables and no word on which is refused by name.
    * `extra` are columns a MEASURE reads on another table (a CALCULATE filter on a
      dimension's column, RELATED): reached only along hops that never repeat a row of the
      statement's table, and always kept by a LEFT JOIN, since the measure reading them must
      not change which rows the statement has. One that cannot be reached is recorded in
      `unplaced`; the measure is refused, not the unit.
    """
    plan = _JoinPlan(alias)
    gb = list(unit.get("group_by") or [])
    gbt = list(unit.get("group_by_tables") or [])
    # (column, table, is a grouping, is a measure's)
    requests: list[tuple[str, str | None, bool, bool]] = []
    for i, g in enumerate(gb):
        requests.append((str(g).lower(), gbt[i] if i < len(gbt) else None, True, False))
    for f in unit.get("filters") or []:
        if f.get("column") and _filter_reads_column(f):
            requests.append((str(f["column"]).lower(), f.get("table"), False, False))
        for c in _tuple_columns(f):
            requests.append((str(c["column"]).lower(), c.get("table"), False, False))
    for col, table in extra:
        requests.append((str(col).lower(), table, False, True))
    for col, table in fields:
        requests.append((str(col).lower(), table, False, False))
    for mj in unit.get("member_joins") or []:
        # A table whose members the statement always joins (a TREATAS of its values): read
        # like a filter's column, so the join keeps only the rows that match a member.
        requests.append((str(mj["column"]).lower(), mj["table"], False, False))
    src = _bare(source)
    plan.table_at[alias] = src
    if not requests:
        return plan

    tables = unit.get("warehouse_tables") or []
    graph = unit.get("join_graph") or [] if graph is None else graph

    def _locate(col: str, hint: str | None, grouping: bool) -> tuple[str | None, str | None]:
        if hint:
            return (None if _bare(hint) == src else str(hint)), None
        if col in _columns_of(source, columns):
            return None, None
        cands = list(dict.fromkeys(t for t in tables if _bare(t) != src
                                   and col in _columns_of(t, columns)))
        if len(cands) == 1:
            return cands[0], None
        if not cands:
            # A filter whose column no table is known to hold stays on the statement's table
            # (the compiler cannot place it elsewhere, and EXPLAIN says so); a grouping column
            # with no home is refused, as it always was.
            return None, ("" if grouping else None)
        return None, (f"column {col!r} is on {sorted(cands)} and the BI file does not say "
                      f"which")

    blocked: dict[str, list[str]] = {}
    # Tables blocked only by a relationship's filter direction or by its being inactive: the
    # relationship is there, it filters nothing towards the statement's rows.
    soft: set[str] = set()
    hard: set[str] = set()

    def _steps_from(cur: str):
        for i, e in enumerate(graph):
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            if cur not in (a, b) or a == b:
                continue
            other_end = b if cur == a else a
            other_table = str(e["to_table"] if cur == a else e["from_table"])
            label = str(e.get("name") or f"{a} -> {b}")
            kind = str(e.get("kind") or "relationship")
            if kind in _NOT_WALKED:
                blocked.setdefault(other_end, []).append(f"{_NOT_WALKED[kind]} ({label})")
                hard.add(other_end)
                continue
            if e.get("is_active") is False:
                blocked.setdefault(other_end, []).append(
                    f"an inactive relationship ({label}), which the model uses only where a "
                    f"measure activates it")
                soft.add(other_end)
                continue
            fs, ts = _card_ends(e.get("cardinality"))
            jt = str(e.get("join_type") or "left").lower()
            both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
            forward = cur == a
            near, far = (fs, ts) if forward else (ts, fs)
            # Is a row of the end the statement stands on kept when it matches nothing? A
            # `left` relationship keeps its MANY end's (its from end's, when that says
            # nothing): the blank member is always on the other side, whichever way round
            # the model stored the relationship.
            primary_is_from = not (fs == "one" and ts == "many")
            keep = jt == "full" or (jt == "left" and forward == primary_is_from)
            if fs == "one" and ts == "one" and jt == "left":
                # One-to-one: each end has a blank member for the other's unmatched rows, so
                # a row of the end the statement stands on is kept whichever way it is walked.
                keep = True
            far_kept = jt == "full"
            if far == "one":
                yield other_end, _Step(i, e, forward, other_table, False, keep, far_kept)
            elif far is None or (far == "many" and near == "many"):
                if forward or both:
                    yield other_end, _Step(i, e, forward, other_table, True, keep, far_kept)
                else:
                    blocked.setdefault(other_end, []).append(
                        f"relationship {label}, which filters only towards {cur}")
                    soft.add(other_end)
            elif both:
                yield other_end, _Step(i, e, forward, other_table, True, keep, far_kept)
            else:
                blocked.setdefault(other_end, []).append(
                    f"relationship {label} from its one end to its many end, which repeats "
                    f"rows and does not filter that way")
                soft.add(other_end)

    # Breadth first, keeping up to two shortest paths per table: a second is an ambiguity.
    dist: dict[str, int] = {src: 0}
    paths: dict[str, list[tuple[_Step, ...]]] = {src: [()]}
    frontier = [src]
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            for other, step in _steps_from(cur):
                if other == src:
                    continue
                if other not in dist:
                    dist[other] = dist[cur] + 1
                    paths[other] = []
                    nxt.append(other)
                if dist[other] != dist[cur] + 1:
                    continue
                for p in paths[cur]:
                    if len(paths[other]) < 2 and all(s.key != step.key for s in p):
                        paths[other].append(p + (step,))
        frontier = nxt

    # Every table some relationship (other than a blend or a condition) connects to the
    # statement's table, whichever way it filters and whether it is active.
    relaxed, todo = {src}, [src]
    while todo:
        cur = todo.pop()
        for e in graph:
            if str(e.get("kind") or "relationship") in _NOT_WALKED:
                continue
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            for x, y in ((a, b), (b, a)):
                if x == cur and y not in relaxed:
                    relaxed.add(y)
                    todo.append(y)

    alias_of_path: dict[tuple, str] = {}
    hops: list[tuple[str, _Step, str]] = []               # (alias, step, alias it leaves)
    needs: dict[str, list[str]] = {}                       # alias -> columns read on it

    def _need(a: str, col: str) -> None:
        if col.lower() not in [c.lower() for c in needs.setdefault(a, [])]:
            needs[a].append(col)

    structural: set[tuple] = set()
    for col, hint, grouping, measure in requests:
        if measure:
            target = str(hint)
            tb = _bare(target)
            found = paths.get(tb) or []
            if tb == src:
                continue
            why = None
            if len(found) != 1:
                why = (f"two relationship paths of the same length reach {target}"
                       if len(found) > 1 else
                       "; ".join(dict.fromkeys(blocked.get(tb) or []))
                       or "no declared relationship reaches it")
            elif any(st.distinct for st in found[0]):
                why = (f"a relationship on the way to {target} does not end on one row, so "
                       f"joining it would repeat the measure's rows")
            if why:
                plan.unplaced.setdefault((col, tb), why)
                continue
            prev = alias
            for n in range(1, len(found[0]) + 1):
                key = tuple(s.key for s in found[0][:n])
                step = found[0][n - 1]
                if key not in alias_of_path:
                    a = f"j{len(alias_of_path) + 1}"
                    alias_of_path[key] = a
                    hops.append((a, step, prev))
                prev = alias_of_path[key]
            _need(prev, col)
            plan.measure_at[(col, tb)] = prev
            continue
        target, why = _locate(col, hint, grouping)
        if why is not None:
            plan.unreachable.add(col)
            if why and why not in plan.reasons:
                plan.reasons.append(why)
            continue
        if target is None:
            # On the statement's own table. Recorded, so a same-named column another request
            # reads on a joined table does not take this one's place.
            plan._at[(col, _bare(hint))] = alias
            plan._at.setdefault((col, ""), alias)
            continue
        tb = _bare(target)
        found = paths.get(tb) or []
        if len(found) != 1:
            if len(found) > 1:
                why = (f"two relationship paths of the same length reach {target} from "
                       f"{source} ({' / '.join(' -> '.join(s.name for s in p) for p in found)})"
                       f"; which one the model means is not knowable here")
            elif blocked.get(tb):
                why = (f"{target} is reached only through "
                       + "; ".join(dict.fromkeys(blocked[tb])))
            else:
                why = ""
            # A table no relationship of the model links to the statement's table at all
            # (`whole_model`: the graph is every relationship the model has) filters nothing
            # either: seen live, a target table beside the fact lists its members beside
            # every row, and a filter on the fact leaves the target's measure as it is.
            unlinked = whole_model and tb not in relaxed
            if (not found and tb not in hard
                    and (unlinked or (tb in relaxed and (tb in soft or not blocked.get(tb))))):
                # The relationships exist and filter nothing towards these rows (against
                # their direction, or inactive): the column is listed beside them, not
                # joined (`_detached_statement`).
                plan.detached[col] = (str(target), hint)
                continue
            # A filter too: read off the statement's table under the same name, it would
            # narrow a different column, or none.
            plan.unreachable.add(col)
            if why and why not in plan.reasons:
                plan.reasons.append(why)
            continue
        prev = alias
        for n in range(1, len(found[0]) + 1):
            key = tuple(s.key for s in found[0][:n])
            step = found[0][n - 1]
            structural.add(key)
            if key not in alias_of_path:
                a = f"j{len(alias_of_path) + 1}"
                alias_of_path[key] = a
                hops.append((a, step, prev))
            prev = alias_of_path[key]
        orig = next((g for g in gb if str(g).lower() == col), col)
        _need(prev, orig if grouping else col)
        plan._at[(col, _bare(hint))] = prev
        plan._at.setdefault((col, ""), prev)

    for a, step, prev in hops:
        for sc, dc in step.pairs:
            _need(a, dc)
            # The key the hop leaves from: a table read as its distinct rows must carry it
            # for the next table to join on.
            if prev != alias:
                _need(prev, sc)
    key_of = {a: k for k, a in alias_of_path.items()}
    # A BRANCH: a hop onto an end that is not one row, along a relationship whose
    # multiplicity the model states (many-to-many, or one-to-many filtering both ways). The
    # tables from it on are read as ONE distinct set of (member of the table it leaves, the
    # columns needed beyond), filtered inside, and joined on the member: each row of the
    # statement's table is counted once per member however many paths lead to it.
    children: dict[str, list[str]] = {}
    for a, _step, prev in hops:
        children.setdefault(prev, []).append(a)
    in_branch: dict[str, str] = {}
    for a, step, prev in hops:
        fs, ts = _card_ends(step.edge.get("cardinality"))
        if (step.distinct and fs and ts and prev not in in_branch
                and key_of[a] in structural):
            todo = [a]
            while todo:
                cur = todo.pop()
                in_branch[cur] = a
                todo += children.get(cur, [])
            near = fs if step.forward else ts
            plan.branches[a] = {"step": step, "prev": prev, "near_one": near == "one",
                                "aliases": [], "clauses": []}
    for a, step, prev in hops:
        plan.keys[a] = [dc for _sc, dc in step.pairs]
        plan.table_at[a] = _bare(step.table)
        on = " AND ".join(f"{a}.{dc} = {prev}.{sc}" for sc, dc in step.pairs)
        kw = {(True, True): "FULL OUTER JOIN", (True, False): "LEFT JOIN",
              (False, True): "RIGHT JOIN", (False, False): "INNER JOIN"}[
            (step.keep, step.keep_far)]
        if key_of[a] not in structural:
            kw = "LEFT JOIN"                # read by a measure only: changes no row
        root = in_branch.get(a)
        if root is not None:
            plan.branches[root]["aliases"].append(a)
            if root == a:
                plan.clauses.append(f"__BRANCH_{a}__")
            else:
                # Inside the branch every hop is a plain join: the distinct set it is read
                # as already counts each member once.
                plan.branches[root]["clauses"].append(
                    f"LEFT JOIN {step.table} {a} ON {on}")
            continue
        plan.full = plan.full or step.keep_far
        if step.distinct:
            body = ", ".join(needs.get(a, []))
            plan.clauses.append(f"{kw} (\n    SELECT DISTINCT {body}\n    FROM   {step.table}"
                                f"\n  ) {a} ON {on}")
        else:
            plan.clauses.append(f"{kw} {step.table} {a} ON {on}")
    if plan.branches:
        plan.graph = graph
    return plan


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


def _edge_key(e: dict) -> tuple:
    return (_bare(e.get("from_table")), str(e.get("from_column") or "").lower(),
            _bare(e.get("to_table")), str(e.get("to_column") or "").lower())


def _measure_graph(unit: dict, source: str, measures: dict[str, str] | None,
                   table_of: dict[str, str] | None, model_graph: list[dict] | None
                   ) -> tuple[list[dict], list[tuple[str, str]], set, bool]:
    """The relationships a statement may join along, the columns its DAX measures read on
    other tables, the relationship changes its measures make, and whether its measures need
    statements of their own.

    The graph is the unit's own plus the model's, on warehouse tables. A measure that is a
    CALCULATE with USERELATIONSHIP(a, b) joins along that relationship instead of the active
    one between the same two tables; CROSSFILTER(a, b, Both | None | OneWay) makes that
    relationship filter both ways, not at all, or one way. When every measure makes the same
    changes they apply to the whole statement. When they differ, each set of measures is a
    statement of its own (`_joined_statements`), except a USERELATIONSHIP whose far table the
    visual reads nothing of: it changes no row, and the measures share one statement."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}

    def phys(name: Any) -> Any:
        return tmap.get(str(name or "").lower(), name)

    graph: list[dict] = []
    seen: set[tuple] = set()
    for e in list(unit.get("join_graph") or []) + list(model_graph or []):
        e = {**e, "from_table": phys(e.get("from_table")), "to_table": phys(e.get("to_table"))}
        if _edge_key(e) in seen:
            continue
        seen.add(_edge_key(e))
        graph.append(e)
    src = _bare(source)
    extra: list[tuple[str, str]] = []
    specs = [_relationship_spec(m, measures, tmap, src, extra)
             for m in unit.get("measures") or []]
    named = {x for x in specs if x}
    if not named:
        return graph, extra, set(), False
    used = {k for sp in named for k in _spec_keys(sp)}
    if len(named) == 1 and all(x == next(iter(named)) for x in specs):
        return _apply_spec(graph, next(iter(named))), extra, used, False
    spec = next(iter(named))
    if len(named) == 1 and all(k[0] == "use" for k in spec):
        ends = set()
        for k in spec:
            hit = _spec_edge(graph, k[1])
            if hit is not None:
                ends |= {_bare(graph[hit].get("from_table")), _bare(graph[hit].get("to_table"))}
        read = {_bare(t) for t in (unit.get("warehouse_tables") or [])[1:]}
        read |= {_bare(t) for t in unit.get("group_by_tables") or [] if t}
        read |= {_bare(f.get("table")) for f in unit.get("filters") or [] if f.get("table")}
        if not ((ends - {src}) & read):
            return graph, extra, used, False
    return graph, extra, used, True


def _measure_homes(unit: dict, source: str, measures: dict[str, str] | None,
                   table_of: dict[str, str] | None) -> dict[str, str]:
    """Each DAX measure's own table: the statement's table when its formula reads it (or no
    table at all), else the one other warehouse table it reads, when there is exactly one.
    A measure reading several other tables keeps the statement's table (and is refused by
    name there, as before)."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}
    src = _bare(source)
    out: dict[str, str] = {}
    if not tmap:
        return out
    for m in unit.get("measures") or []:
        if m.get("formula_language") != "dax" or not m.get("formula"):
            continue
        reads = []
        for k, v in _expanded_tokens(m["formula"], measures):
            t = None
            if k in ("qualified", "quoted_qualified"):
                t = _table_ref(v)
            elif k == "str" and v.startswith("'"):
                t = v[1:-1].replace("''", "'")
            elif k == "func" and v.lower() in tmap:
                t = v
            phys = tmap.get(str(t or "").lower())
            if phys:
                reads.append(phys)
        named = {_bare(p): p for p in reads}
        if not named or src in named:
            continue
        if len(named) == 1:
            out[m["name"]] = next(iter(named.values()))
    return out


def _spec_keys(spec: frozenset) -> set:
    """The relationship keys (frozenset of (column, table) ends) a spec names."""
    return {k[1] for k in spec}


def _relationship_spec(m: dict, measures: dict[str, str] | None, tmap: dict, src: str,
                       extra: list) -> frozenset | None:
    """The relationship changes one DAX measure makes: {("use", ends)} for USERELATIONSHIP,
    {("cross", ends, direction)} for CROSSFILTER. None for a measure that is not DAX; empty
    for one that changes nothing. Also appends to `extra` the columns it reads on other
    tables."""
    if m.get("formula_language") != "dax" or not m.get("formula"):
        return None
    toks = _expanded_tokens(m["formula"], measures)
    out = set()
    for i, (k, v) in enumerate(toks):
        if k in ("qualified", "quoted_qualified"):
            t = _table_ref(v)
            physical = tmap.get(str(t).lower()) if t else None
            if physical and _bare(physical) != src:
                req = (_column_ref(v).lower(), physical)
                if req not in extra:
                    extra.append(req)
        up = v.upper() if k == "func" else ""
        if up in ("USERELATIONSHIP", "CROSSFILTER") and i + 5 < len(toks) \
                and toks[i + 1][0] == "lparen" and toks[i + 3][0] == "comma":
            ends = []
            for tk in (toks[i + 2], toks[i + 4]):
                t = _table_ref(tk[1]) if tk[0] != "col" else None
                ends.append((_column_ref(tk[1]).lower(), _bare(tmap.get(str(t).lower(), t))))
            if up == "USERELATIONSHIP":
                out.add(("use", frozenset(ends)))
            elif i + 6 < len(toks) and toks[i + 5][0] == "comma":
                out.add(("cross", frozenset(ends), str(toks[i + 6][1]).strip("'\"").lower()))
    return frozenset(out)


def _spec_edge(graph: list[dict], ends: frozenset) -> int | None:
    return next((i for i, e in enumerate(graph)
                 if frozenset({(str(e.get("from_column") or "").lower(), _bare(e.get("from_table"))),
                               (str(e.get("to_column") or "").lower(), _bare(e.get("to_table")))})
                 == ends), None)


def _apply_spec(graph: list[dict], spec: frozenset) -> list[dict]:
    """The graph a measure's relationship changes leave: USERELATIONSHIP activates its
    relationship and deactivates the others between the same two tables; CROSSFILTER Both
    filters both ways, None filters nothing (the relationship is not walked), OneWay one way."""
    out = [dict(e) for e in graph]
    for k in sorted(spec, key=lambda x: x[0] != "use"):
        hit = _spec_edge(out, k[1])
        if hit is None:
            continue
        if k[0] == "use":
            ends = {_bare(out[hit].get("from_table")), _bare(out[hit].get("to_table"))}
            for i, e in enumerate(out):
                if i == hit:
                    e["is_active"] = True
                elif {_bare(e.get("from_table")), _bare(e.get("to_table"))} == ends:
                    e["is_active"] = False
        else:
            how = k[2]
            if how == "none":
                out[hit]["is_active"] = False
            elif how == "both":
                out[hit]["cross_filter"] = "both"
            elif how.startswith("oneway"):
                out[hit]["cross_filter"] = "single"
    return out


_DATE_TYPES = re.compile(r"(?i)^(date|datetime|timestamp)")


def _date_keys(graph: list[dict], source: str, types: dict[tuple[str, str], str]
               ) -> dict[tuple[str, str], dict]:
    """(date column, date table) -> how the statement's table reaches it: every active
    relationship from the measure's table to a table whose ONE end is that column. A column
    the model types as other than a date is not one."""
    src = _bare(source)
    out: dict[tuple[str, str], dict] = {}
    for e in graph:
        if e.get("is_active") is False or str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        fs, ts = _card_ends(e.get("cardinality"))
        for near, far, ncol, fcol, far_end in (
                (e.get("from_table"), e.get("to_table"), e.get("from_column"),
                 e.get("to_column"), ts),
                (e.get("to_table"), e.get("from_table"), e.get("to_column"),
                 e.get("from_column"), fs)):
            if _bare(near) != src or far_end != "one" or _bare(far) == src:
                continue
            dtype = types.get((_bare(far), str(fcol).lower())) \
                or types.get((str(far).lower(), str(fcol).lower()))
            if dtype and not _DATE_TYPES.match(dtype):
                continue
            out[(str(fcol).lower(), _bare(far))] = {
                "table": far, "column": str(fcol), "fact_column": str(ncol)}
    return out


def _measure_elsewhere(m: dict, unit: dict, source: str,
                       columns: dict[str, set[str]]) -> str | None:
    """Why a shelf-aggregated column measure cannot be computed over `source`, or None.

    Only a measure with no formula (a column plus an aggregation), only when the schema
    evidence says the column is not on the statement's table and is on another of the unit's
    tables. A blend link names the post-aggregation join that table needs."""
    if m.get("formula") or not columns or not _columns_of(source, columns):
        return None
    col = str(m.get("name") or "").lower()
    if col in _columns_of(source, columns):
        return None
    homes = [t for t in unit.get("warehouse_tables") or []
             if _bare(t) != _bare(source) and col in _columns_of(t, columns)]
    if not homes:
        return None
    blend = any(str(e.get("kind")) == "blend" and {_bare(e.get("from_table")),
                                                   _bare(e.get("to_table"))}
                == {_bare(source), _bare(homes[0])} for e in unit.get("join_graph") or [])
    if blend:
        return (f"aggregates a column of {homes[0]}, which a data blend links to {source}: "
                f"a blend aggregates the secondary at the link grain and left-joins it after "
                f"aggregation, which is not compiled")
    return (f"aggregates a column of {homes[0]}, not of {source}: a statement over {source} "
            f"does not aggregate another table at that table's grain")


def _columns_of(table: str, columns: dict[str, set[str]]) -> set[str]:
    """What the parser resolved as reachable on a table. Absent evidence means the compiler
    cannot PLACE the column, which is a refusal rather than an assumption."""
    hit = columns.get(table)
    if hit is None:
        hit = next((v for k, v in columns.items() if _bare(k) == _bare(table)), None)
    return set(hit or ())


def _bare(table: Any) -> str:
    return str(table or "").split(".")[-1].lower()


def _strip_alias(pred: str, alias: str, to: str = "") -> str:
    """`pred` with every `alias.` qualifier removed (or replaced by `to`), OUTSIDE quotes
    only. A context filter runs inside a subquery that has no alias; a plain text replace
    also rewrote literals (`'gift.card'` lost its `t.`)."""
    out, i, n, q = [], 0, len(pred), None
    head = alias + "."
    while i < n:
        ch = pred[i]
        if q:
            out.append(ch)
            if ch == q:
                if i + 1 < n and pred[i + 1] == q:      # a doubled quote is an escape
                    out.append(pred[i + 1])
                    i += 1
                else:
                    q = None
            i += 1
            continue
        if ch in ("'", '"'):
            q = ch
            out.append(ch)
            i += 1
            continue
        if pred.startswith(head, i) and (i == 0 or not (pred[i - 1].isalnum()
                                                        or pred[i - 1] in "_.")):
            i += len(head)
            if to:
                out.append(to + ".")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# A filter that keeps the MEMBERS of a field passing a test over the population (a Top N by
# an expression, a condition): not a row predicate, compiled by the statement around it.
MEMBER_OPS = ("top_n", "condition")


_RELATIVE_PERIODS = ("day", "month", "quarter", "year")


def _relative_date(target: str, v: dict, rules, dialect: str | None, res: Result
                   ) -> tuple[str | None, str | None]:
    """A relative date filter as a range of whole periods: from the start of the period
    `first` periods from today (or from the anchor) to the end of the period `last` periods
    from it. A week starts on the value's `week_start`. "To date" (`include_future` false)
    also ends the range at today, or at the moment the query runs for a time column (the
    current time, read as the session's wall clock). The date arithmetic per dialect is rule
    data (`op.relative_date`). -> (predicate, None) or (None, why it is refused)."""
    period = str(v.get("period") or "").lower()
    if v.get("unknown"):
        return None, (f"a relative date filter with setting(s) {v['unknown']} the compiler "
                      f"does not read, which may move its window")
    rule = rules.operator("relative_date", dialect or "")
    emit = (rule or {}).get("emit") or {}
    d = str(dialect or "").lower()
    shift = (emit.get("shift") or {}).get(d)
    if rule is None or not shift:
        return None, f"no relative date rule for the {dialect or 'run'} dialect"
    start = str(v.get("week_start") or "").lower()
    iso = (emit.get("weekday_iso") or {}).get(d)
    if period == "week" and not (start in _ISO_DAY and iso):
        return None, ("a relative date filter by week whose first day of the week the file "
                      "does not state" if start not in _ISO_DAY else
                      f"a relative date filter by week, which has no weekday form for the "
                      f"{dialect or 'run'} dialect")
    if period not in _RELATIVE_PERIODS and period != "week":
        return None, (f"a relative date filter by {period or 'an unnamed period'}: only "
                      f"{', '.join(_RELATIVE_PERIODS + ('week',))} are compiled")
    # The time zone the BI tool reads the current moment in (the viewer file's): "today" and
    # "now" are both read there, so the window's periods and a "to date" end agree with what
    # the tool shows across the warehouse session's midnight.
    tz, zoned = v.get("time_zone"), None
    if tz:
        if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)*", str(tz)) or len(str(tz)) > 64:
            return None, f"a relative date filter in a time zone {tz!r} that is not one"
        zoned = (emit.get("now_in_zone") or {}).get(d)
        if not zoned:
            return None, (f"a relative date filter in the time zone {tz}, which has no form "
                          f"for the {dialect or 'run'} dialect")
        names = (emit.get("zone_names") or {}).get(d)
        if names is not None:
            if str(tz) not in names:
                return None, (f"a relative date filter in the time zone {tz}, which the "
                              f"{dialect} dialect names by its own names, and the rule lists "
                              f"no name for it")
            tz = names[str(tz)]
        zoned = zoned.replace("{tz}", str(tz))
        res.rules_used.append("engine.relative_date_time_zone")
    if v.get("anchor"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v["anchor"])):
            return None, f"a relative date anchor {v['anchor']!r} that is not a date"
        anchor = f"CAST('{v['anchor']}' AS DATE)"
    else:
        # Today as the dialect spells it (`today`), else the standard CURRENT_DATE.
        anchor = (f"CAST({zoned} AS DATE)" if zoned
                  else (emit.get("today") or {}).get(d) or "CURRENT_DATE")
    # The start of a period (`trunc`, {p} the period, {d} the date), else DATE_TRUNC.
    cut = (emit.get("trunc") or {}).get(d) or "DATE_TRUNC('{p}', {d})"

    def trunc(expr: str) -> str:
        if period != "week":
            return cut.format(p=period, d=expr)
        # The week's first day: the day moved back to the weekday the data source starts on.
        return shift.format(p="day", n="-" + _mod(f"{iso.format(expr)} - {_ISO_DAY[start]} + 7",
                                                  "7", dialect),
                            d=expr)

    def at(n: int) -> str:
        return trunc(shift.format(p=period, n=int(n), d=anchor) if n else anchor)
    res.rules_used.append(rule["id"])
    pred = f"{target} >= {at(int(v['first']))} AND {target} < {at(int(v['last']) + 1)}"
    if not v.get("include_future", True):
        # "To date": nothing after today (the anchor), or after now on a time column.
        # The current moment in the time zone the BI tool reads it in (the viewer's), else
        # the session's wall clock.
        now = zoned or (emit.get("now") or {}).get(d)
        if not now:
            return None, (f"a relative date filter to date, which has no current-time form "
                          f"for the {dialect or 'run'} dialect")
        end = (f"{shift.format(p='day', n=1, d=anchor)}" if v.get("anchor") else now)
        pred += (f" AND {target} < {end}" if v.get("anchor") else f" AND {target} <= {end}")
        res.rules_used.append("engine.relative_date_to_date")
    pred = f"({pred})"
    if v.get("include_null"):
        pred = f"({pred} OR {target} IS NULL)"
    return pred, None


# Tests on a column's text (the common format's `contains` family), each an operator row.
_TEXT_OPS = {"contains": "contains", "not_contains": "contains", "starts_with": "starts_with",
             "not_starts_with": "starts_with"}
# The comparison of each op, for deciding whether an empty cell's stand-in (`null_as`) passes.
_CMP = {"eq": lambda a, b: a == b, "gt": lambda a, b: a > b, "lt": lambda a, b: a < b,
        "neq": lambda a, b: a != b}
_DATE_LIT = re.compile(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?")
_NUM_LIT = re.compile(r"-?\d+(\.\d+)?")


def _foldable(v) -> bool:
    """A filter value compared as text the model folds (case, trailing spaces): a string that
    is not a number or a date literal. A key column's numbers (`ORDER_ID IN (1, 2)`) and a
    date are compared as they are; folding them would compare a number as text."""
    return isinstance(v, str) and not _NUM_LIT.fullmatch(v) and not _DATE_LIT.fullmatch(v)


def _stand_in(literal: Any, null_as: dict):
    """(empty cell's stand-in, literal) as two comparable values, by the literal's type."""
    if isinstance(literal, bool):
        return None, None
    if isinstance(literal, (int, float)) or _NUM_LIT.fullmatch(str(literal)):
        n = null_as.get("number")
        return (None, None) if n is None else (float(n), float(literal))
    if _DATE_LIT.fullmatch(str(literal)):
        d = null_as.get("date")
        return (None, None) if d is None else (str(d), str(literal)[:10])
    t = null_as.get("text")
    return (None, None) if t is None else (str(t).casefold(), str(literal).casefold())


def _empty_passes(op: str, value: Any, null_as: dict | None) -> bool:
    """Whether an EMPTY cell passes the comparison, with the empty value read as the
    filter's stand-in (`null_as`, the tool's comparison rule). False without one."""
    if not null_as or value is None:
        return False
    if op in ("range", "lte") and isinstance(value, dict):
        ok = True
        for bound, cmp in (("min", lambda a, b: a >= b), ("max", lambda a, b: a <= b)):
            if value.get(bound) is None:
                continue
            a, b = _stand_in(value[bound], null_as)
            ok = ok and a is not None and cmp(a, b)
        return ok
    if op in _CMP and not isinstance(value, (list, dict)):
        a, b = _stand_in(value, null_as)
        return a is not None and _CMP[op](a, b)
    return False


def _test_pred(target: str, op: str, value: Any, f: dict, rules, dialect: str | None,
               res: "Result", text_key: bool = False, force: bool = False
               ) -> tuple[str | None, str | None]:
    """The predicates of the common format's tests that the generic path does not write:
    a membership with the EMPTY member (a JSON null in the list), the text tests, `any_of` /
    `all_of`, and a comparison an empty cell passes under the filter's `null_as`.
    -> (predicate, None); (None, why) when it cannot be written; (None, None) when the
    generic path writes it (unless `force`, which writes every comparison here)."""
    null_as = f.get("null_as")
    if op in ("any_of", "all_of"):
        if not isinstance(value, list) or not value:
            return None, f"{op} with no tests"
        parts = []
        for part in value:
            if not isinstance(part, dict) or part.get("value") is None:
                return None, f"a part of {op} with no value"
            pred, why = _test_pred(target, str(part.get("op") or ""), part["value"], f,
                                   rules, dialect, res, text_key, force=True)
            if pred is None:
                return None, why or f"a part of {op} ({part.get('op')}) has no rule"
            parts.append(pred)
        res.rules_used.append(f"engine.{op}")
        return "(" + (" OR " if op == "any_of" else " AND ").join(parts) + ")", None
    if op in _TEXT_OPS:
        rule = rules.operator(_TEXT_OPS[op], dialect or "")
        if rule is None:
            return None, f"no operator rule for {_TEXT_OPS[op]}"
        res.rules_used.append(rule["id"])
        lit = str(value).upper() if text_key else str(value)
        col = _Parser._text_key(target) if text_key else target
        pred = _apply(_emit_for(rule, dialect), [col, _literal(lit, dialect)], op)
        # An empty cell holds no text: it fails the test, and passes its negation.
        return ((f"NOT COALESCE(({pred}), FALSE)" if op.startswith("not_") else f"({pred})"),
                None)
    if op in ("is_null", "not_null"):
        if not force:
            return None, None
        return f"{target} IS {'NOT ' if op == 'not_null' else ''}NULL", None
    if op in ("in", "not_in") or (op in ("eq", "filter") and isinstance(value, list)):
        vals = value if isinstance(value, list) else [value]
        if None not in vals and not force:
            return None, None
        rest = [v for v in vals if v is not None]
        keyed = text_key and rest and all(_foldable(v) for v in rest)
        col = _Parser._text_key(target) if keyed else target
        lits = _literal_list([v.upper() for v in rest] if keyed else rest, dialect)
        if op == "not_in":
            res.rules_used.append("engine.empty_member")
            if not rest:
                return f"{target} IS NOT NULL", None
            if None in vals:
                return f"({target} IS NOT NULL AND {col} NOT IN ({lits}))", None
            return f"({target} IS NULL OR {col} NOT IN ({lits}))", None
        if not rest:
            return f"{target} IS NULL", None
        member = f"{col} IN ({lits})" if len(rest) > 1 else f"{col} = {lits}"
        if None in vals:
            res.rules_used.append("engine.empty_member")
            return f"({target} IS NULL OR {member})", None
        return member, None
    if op in ("eq", "filter", "gt", "lt", "neq", "lte", "range"):
        op = "eq" if op == "filter" else op
        passes = _empty_passes(op, value, null_as)
        if not passes and not force:
            return None, None
        if isinstance(value, dict):
            preds = []
            for bound, row in (("min", "range"), ("max", "lte")):
                if value.get(bound) is None:
                    continue
                rule = rules.operator(row)
                if rule is None:
                    return None, f"no operator rule for {row}"
                res.rules_used.append(rule["id"])
                preds.append(_apply(_emit_for(rule, dialect),
                                    [target, _literal(value[bound], dialect)], row))
            if not preds:
                return None, "a range with no bound"
            pred = " AND ".join(preds)
        else:
            rule = rules.operator(op, dialect or "")
            if rule is None:
                return None, f"no operator rule for {op}"
            res.rules_used.append(rule["id"])
            if text_key and op in ("eq", "neq") and isinstance(value, str) \
                    and not _NUM_LIT.fullmatch(value) and not _DATE_LIT.fullmatch(value):
                pred = _apply(_emit_for(rule, dialect), [_Parser._text_key(target),
                                                       _literal(value.upper(), dialect)], op)
            else:
                pred = _apply(_emit_for(rule, dialect), [target, _literal(value, dialect)], op)
        if passes:
            # An empty cell compares as its stand-in and passes: the rows with no value stay.
            res.rules_used.append("engine.empty_compares_as")
            return f"({pred} OR {target} IS NULL)", None
        return (f"({pred})" if " AND " in pred else pred), None
    return None, None


def _tuple_pred(f: dict, rules, dialect: str | None, res: "Result", text_key: bool,
                target_of) -> tuple[str | None, str | None]:
    """A `tuple` filter -> an OR of one AND per selected combination ((c1 = a AND c2 = b)
    OR ...), a NULL member as IS NULL; `not` (the value's `negated`) keeps the rows matching
    none. -> (predicate, None) or (None, why)."""
    v = f["value"]
    cols = _tuple_columns(f)
    rows = [r for r in v.get("rows") or [] if isinstance(r, list)]
    if not cols or not rows or any(len(r) != len(cols) for r in rows):
        return None, (f"a filter over {len(cols)} column(s) together whose selected "
                      f"combinations do not each name every column")
    rule = rules.operator("eq", dialect or "")
    if rule is None:
        return None, "no operator rule for eq"
    res.rules_used.append(rule["id"])
    targets = [target_of(c["column"], c.get("table")) for c in cols]
    alts = []
    for r in rows:
        parts = []
        for tgt, val in zip(targets, r):
            if val is None:
                parts.append(f"{tgt} IS NULL")
            elif text_key and isinstance(val, str) and not _NUM_LIT.fullmatch(val) \
                    and not _DATE_LIT.fullmatch(val):
                parts.append(_apply(_emit_for(rule, dialect), [_Parser._text_key(tgt),
                                                             _literal(val.upper(), dialect)],
                                    "eq"))
            else:
                parts.append(_apply(_emit_for(rule, dialect), [tgt, _literal(val, dialect)],
                                    "eq"))
        alts.append("(" + " AND ".join(parts) + ")")
    res.rules_used.append("engine.tuple_filter")
    pred = "(" + "\n       OR ".join(alts) + ")"
    if v.get("negated"):
        pred = f"NOT COALESCE({pred}, FALSE)"
    return pred, None


def _relative_range(target: str, v: dict, rules, dialect: str | None, res: "Result"
                    ) -> tuple[str | None, str | None]:
    """A range whose bounds are computed from the moment the query runs (in UTC): each bound
    a list of steps from now, a move (`add`) or a truncation to the start of a unit (`span`).
    A lower bound is `>=`; an upper bound ending in a span covers that span to its end
    (`<` the start of the next one), else `<=`. Per-dialect SQL is rule data
    (`op.relative_range`). -> (predicate, None) or (None, why)."""
    rule = rules.operator("relative_range", dialect or "")
    emit = (rule or {}).get("emit") or {}
    d = str(dialect or "").lower()
    now, shift, trunc = ((emit.get(k) or {}).get(d) for k in ("now", "shift", "trunc"))
    if rule is None or not (now and shift and trunc):
        return None, f"no relative range rule for the {dialect or 'run'} dialect"
    units = set(emit.get("units") or ())
    # A WEEK starts on the day the filter states (`week_start`, the reader's word for the
    # tool's rule): its start is the day truncation moved back to that weekday, the ISO day
    # number per dialect being rule data (`weekday_iso`).
    start = str(v.get("week_start") or "").lower()
    iso = (emit.get("weekday_iso") or {}).get(d)

    def week_start(expr: str) -> str:
        day = trunc.format(p="day", d=expr)
        back = "-" + _mod(f"{iso.format(day)} - {_ISO_DAY[start]} + 7", "7", dialect)
        return shift.format(p="day", n=back, d=day)
    preds = []
    for side in ("lower", "upper"):
        b = v.get(side)
        if b is None:
            continue
        expr, last = now, None
        for st in (b or {}).get("steps") or []:
            unit = str(st[-1]).lower()
            if unit == "week" and st[0] == "span" and not (start in _ISO_DAY and iso):
                return None, ("a relative date bound cut to a week, whose first day the "
                              "filter does not state" if start not in _ISO_DAY else
                              f"a relative date bound cut to a week, which has no weekday "
                              f"form for the {dialect or 'run'} dialect")
            if unit not in units and unit != "week":
                return None, (f"a relative date or time bound moved or cut by {unit}: only "
                              f"{', '.join(sorted(units | {'week'}))} are compiled")
            if st[0] == "add":
                expr = shift.format(p=unit, n=int(st[1]), d=expr)
            elif unit == "week":
                expr = week_start(expr)
            else:
                expr = trunc.format(p=unit, d=expr)
            last = st
        if side == "lower":
            preds.append(f"{target} >= {expr}")
        elif last is not None and last[0] == "span":
            preds.append(f"{target} < {shift.format(p=str(last[-1]).lower(), n=1, d=expr)}")
        else:
            preds.append(f"{target} <= {expr}")
    if not preds:
        return None, "a relative range with no bound"
    res.rules_used.append(rule["id"])
    return "(" + " AND ".join(preds) + ")", None


_AGG_SQL = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|STDDEV\w*|VAR\w*)\s*\(", re.I)
_READER_DIALECTS = (yaml.safe_load((pathlib.Path(__file__).parent / "data"
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


def _limit(n: int, dialect: str | None) -> str:
    """`LIMIT n`, or the dialect's own form (`limit` in data/reader_dialects.yml) after an
    ORDER BY."""
    return _LIMIT.get(str(dialect or "").lower(), "LIMIT {n}").format(n=int(n))


def _mod(x: str, m: str, dialect: str | None) -> str:
    """`x` modulo `m` as the dialect spells it (`modulo` in data/reader_dialects.yml), else
    MOD(x, m). The sign is the dividend's in every dialect listed, as MOD's is."""
    return _MODULO.get(str(dialect or "").lower(), "MOD({0}, {1})").format(x, m)


def _member_filters(members: list[dict], unit: dict, rules, alias: str, dialect: str,
                    res: Result, *, src: str, plan, measures, calc, calc_language,
                    dim_preds: list[str], joins: list[str] | tuple = (),
                    compile_by=None, limit_ok: bool = True) -> tuple[list[str], bool]:
    """Filters that keep the MEMBERS of a field passing a test, as row predicates:
    (predicates, whether a Top N among them replaces the statement's LIMIT).

    A Top N by an expression and a condition on an aggregate are computed over the
    population the data source and context filters leave, independently of the other
    dimension filters, and then intersected with them: the field's value is in the members
    a subquery over that population keeps (a NULL member included, which `IN` alone would
    drop). Ranking inside the filtered rows instead answers the context-filter question. A
    Top N on the one field the sheet groups by, with nothing else filtering, is the same
    rows as the statement's own ORDER BY … LIMIT and is left to it. A condition with no
    aggregate is a test of each row's own value.

    A Top N whose `population` is `filtered` ranks the members within EVERY other filter of
    the view (the statement's own rows, its joins and its row predicates), by the ranking
    measure as the statement computes it (`compile_by`); `empty_members: excluded` leaves out
    a member whose measure is empty, and `ties` keeps every member tied with the Nth (a rank,
    not a row count)."""
    if not members:
        return [], False
    if not calc_language:
        raise CompileError(
            f"filter(s) on {sorted({str(f['column']) for f in members})} keep the members an "
            f"expression selects, and the unit names no language to compile it in")
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    preds: list[str] = []
    top_done = False

    def compile_over(expr: str, on: str) -> str:
        p = _Parser(_tokens(expr, calc_language), rules, calc_language, on, dialect, None,
                    measures, None, ())
        sql = p.parse()
        res.rules_used += p.used
        res.provisional_rules += p.provisional
        res.unknown += p.unknown
        return sql

    for f in members:
        col, op, v = str(f["column"]), str(f["op"]).lower(), f["value"]
        what = "Top N" if op == "top_n" else "condition"
        key_t = calc(col, f.get("table")) or f"{plan.at(col, f.get('table'))}.{col}"
        if op == "top_n" and str(v.get("population") or "") == "filtered":
            preds.append(_ranked_members(f, key_t, v, alias, dialect, res, src=src,
                                         joins=joins, dim_preds=dim_preds,
                                         compile_by=compile_by))
            top_done = top_done or bool(unit.get("top_n"))
            continue
        if re.search(r"\bj\d+\.", key_t):
            # A column of a joined table: its members ranked (or tested) over the statement's
            # own rows through the same joins, before the view's dimension filters (a Top N
            # or a condition is computed over every row that reaches the members).
            if op == "top_n":
                # Its members are the joined table's: a fact row that reaches no row of it
                # (the blank member the view shows for it) is not ranked (checked live: the
                # bottom department by quantity is a department, never that blank).
                held = _joined_row_test(key_t, joins)
                preds.append(_ranked_members(f, key_t, v, alias, dialect, res, src=src,
                                             joins=joins, dim_preds=[held] if held else [],
                                             compile_by=compile_by))
                top_done = top_done or bool(unit.get("top_n"))
                continue
            preds.append(_tested_members(f, key_t, v, alias, res, src=src, joins=joins,
                                         compile_by=compile_by))
            continue
        key_m = _strip_alias(key_t, alias, "m")
        try:
            if op == "top_n":
                by = compile_over(str(v["by"]), "m")
                n = int(v["n"])
                if (unit.get("top_n") == n and gb == [col.lower()] and not dim_preds
                        and len(members) == 1 and limit_ok):
                    continue        # the statement's own ORDER BY … LIMIT is these rows
                order = _sort_term(by, "ASC" if v.get("direction") == "asc" else "DESC",
                                   "LAST", dialect)
                sub = (f"      SELECT {key_m} AS member_key\n      FROM   {src} m\n"
                       f"      GROUP BY {key_m}\n      ORDER BY {order}\n"
                       f"      {_limit(n, dialect)}")
                res.rules_used.append("engine.top_n_members")
                top_done = top_done or bool(unit.get("top_n"))
            else:
                cond = compile_over(str(v["expression"]), "m")
                negate = bool(v.get("exclude"))
                if not _AGG_SQL.search(cond):
                    row = _strip_alias(cond, "m", alias)
                    preds.append(f"NOT COALESCE(({row}), FALSE)" if negate else f"({row})")
                    res.rules_used.append("engine.condition_rows")
                    continue
                test = f"NOT COALESCE(({cond}), FALSE)" if negate else f"({cond})"
                sub = (f"      SELECT {key_m} AS member_key\n      FROM   {src} m\n"
                       f"      GROUP BY {key_m}\n      HAVING {test}")
                res.rules_used.append("engine.condition_members")
        except CompileError as exc:
            raise CompileError(f"the {what} filter on {col!r} does not compile ({exc}); "
                               f"dropping it would return other members") from exc
        preds.append(f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q\n  )\n"
                     f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
                     f"    ) q WHERE q.member_key IS NULL\n  )))")
    return preds, top_done


def _joined_row_test(key_t: str, joins: list[str]) -> str | None:
    """A test that the row reached a row of the joined table `key_t` reads (`j<n>.col`): a
    column its join matches on is not NULL. None where the join names none."""
    m = re.match(r"^(j\d+)\.", key_t)
    if not m:
        return None
    for j in joins:
        if re.search(rf"\)\s*{m.group(1)}\s+ON\b|\s{m.group(1)}\s+ON\b", j):
            on = j.split(" ON ", 1)[-1]
            col = re.search(rf"\b{m.group(1)}\.(\w+)\b", on)
            if col:
                return f"{m.group(1)}.{col.group(1)} IS NOT NULL"
    return None


def _tested_members(f: dict, key_t: str, v: dict, alias: str, res: Result, *, src: str,
                    joins, compile_by) -> str:
    """The members of a joined table's column a condition filter keeps: the statement's own
    rows through its joins, grouped by the column, those whose aggregate passes (Exclude:
    those whose test is not true)."""
    col = str(f["column"])
    if compile_by is None:
        raise CompileError(f"the condition on {col!r} names no way to compile its expression")
    try:
        cond = compile_by(str(v["expression"]))
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        raise CompileError(f"the condition filter on {col!r} does not compile ({exc}); "
                           f"dropping it would return other members") from exc
    test = f"NOT COALESCE(({cond}), FALSE)" if v.get("exclude") else f"({cond})"
    if not _AGG_SQL.search(cond):
        res.rules_used.append("engine.condition_rows")
        return test
    sub = f"      SELECT {key_t} AS member_key\n      FROM   {src} {alias}"
    for j in joins:
        sub += "\n      " + j.replace("\n", "\n      ")
    sub += f"\n      GROUP BY {key_t}\n      HAVING {test}"
    res.rules_used.append("engine.condition_members")
    return (f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q\n  )\n"
            f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
            f"    ) q WHERE q.member_key IS NULL\n  )))")


def _ranked_members(f: dict, key_t: str, v: dict, alias: str, dialect: str, res: Result, *,
                    src: str, joins, dim_preds: list[str], compile_by) -> str:
    """The members a Top N keeps when it ranks within every other filter of the view: the
    statement's own rows grouped by the field, ranked by the measure, those ranked N or
    better kept (with ties when the filter says so; a member with an empty measure left out
    when it says so). A NULL member is kept by its own test: `IN` never matches a NULL."""
    col = str(f["column"])
    if compile_by is None:
        raise CompileError(f"the Top N on {col!r} names no way to compile its measure")
    try:
        by = compile_by(str(v["by"]))
        n = int(v["n"])
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        raise CompileError(f"the Top N filter on {col!r} does not compile ({exc}); dropping "
                           f"it would return other members") from exc
    if " OVER (" in by:
        raise CompileError(f"the Top N on {col!r} ranks by a window, which a ranking over "
                           f"the members does not compute")
    order = _sort_term(by, "ASC" if str(v.get("direction")).lower() == "asc" else "DESC",
                       "LAST", dialect)
    rank = "RANK()" if v.get("ties") else "ROW_NUMBER()"
    rows = [p for p in dim_preds if not _AGG_SQL.search(p)]
    sub = (f"      SELECT {key_t} AS member_key,\n"
           f"             {rank} OVER (ORDER BY {order}) AS member_rank\n"
           f"      FROM   {src} {alias}")
    for j in joins:
        sub += "\n      " + j.replace("\n", "\n      ")
    if rows:
        sub += "\n      WHERE  " + "\n        AND  ".join(rows)
    sub += f"\n      GROUP BY {key_t}"
    if str(v.get("empty_members") or "") == "excluded":
        sub += f"\n      HAVING {by} IS NOT NULL"
    res.rules_used.append("engine.top_n_ranked_members")
    return (f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q "
            f"WHERE q.member_rank <= {n}\n  )\n"
            f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
            f"    ) q WHERE q.member_key IS NULL AND q.member_rank <= {n}\n  )))")


def _plan_filters(unit: dict, rules, alias: str, res: Result,
                  colmap: dict[str, str] | None = None, resolve=None,
                  dialect: str | None = None, place=None, text_key: bool = False,
                  special: dict | None = None):
    """The unit's filters as predicates: (context, dimension, deferred). With `text_key` (a
    model that compares text without case and without a column's trailing spaces), a filter
    on text values compares the column's key with each value's.

    With `special`, a caller that places them itself receives the `datasource`-stage
    predicates (applied to every row before any context filter) in `special["datasource"]`
    and the member filters (`MEMBER_OPS`) in `special["members"]`; without it the former are
    context predicates and the latter are deferred (refused by name)."""
    ctx, dim, deferred = [], [], []
    for f in unit.get("filters", []):
        column = f.get("column")
        op = str(f.get("op") or "").lower()
        if op == "top_rank" and unit.get("top_n"):
            # The rank filter of a Top N, already recovered into `top_n`: emitting it as a
            # predicate yields `WHERE rank = NULL`, which runs and returns nothing.
            continue
        if op in MEMBER_OPS:
            if f.get("value") is None:
                deferred.append({**f, "_why": "no-value"})
            elif special is not None and colmap is None:
                special["members"].append(f)
            else:
                deferred.append({**f, "_why": "member-filter"})
            continue
        if colmap is not None and f.get("value") is not None and (
                (f.get("stage") or "").lower() not in ("measure", "table-calc")):
            if str(column).lower() not in colmap:
                # A calc over the query's columns (Tableau custom SQL) resolves below; only a
                # name that is neither is deferred.
                if not (resolve and resolve(column, f.get("table"))):
                    deferred.append({**f, "_why": "not-a-column"})
                    continue
            else:
                column = _ident(colmap[str(column).lower()])
        stage = (f.get("stage") or "").lower()
        if (stage in ("measure", "table-calc") and f.get("value") is None
                and f.get("value_kind") == "absent"):
            # A filter card on a value with nothing set (seen in a service-saved report: an
            # `Advanced` filter on the visual's measure, no condition): it narrows nothing.
            continue
        if stage == "measure" and f.get("measure") and special is not None and colmap is None:
            # A test of a MEASURE's value on each row of the view: placed by the caller, as a
            # HAVING over the compiled measure.
            if f.get("value") is None:
                deferred.append({**f, "_why": "no-value"})
            else:
                special.setdefault("measure_tests", []).append(f)
            continue
        if stage in ("measure", "table-calc"):
            # A measure-stage filter is applied after aggregation. Skipping it would ship a
            # statement missing a filter the widget applies (it used to be skipped whenever
            # the sheet had a Top N, which silently dropped a date range), so it is named.
            deferred.append({**f, "_why": "measure-stage"})
            continue
        # Where this filter's predicates go: a data source filter's own list when the caller
        # places it, else the context tier for it and for a context filter; a filter of what
        # the view SHOWS (`display`) to its own list, applied after every calculation.
        if stage == "datasource" and special is not None:
            into = special["datasource"]
        elif stage == "display" and special is not None:
            into = special.setdefault("display", [])
        elif stage in ("context", "datasource"):
            into = ctx
        else:
            into = dim
        if op == "relative_date" and isinstance(f.get("value"), dict):
            on = place(column, f.get("table")) if place and colmap is None else alias
            target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
            pred, why = _relative_date(target, f["value"], rules, dialect, res)
            if why:
                res.untranslatable.append({"construct": "relative date filter", "why": why})
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if op in ("not_null", "is_null"):
            on = place(column, f.get("table")) if place and colmap is None else alias
            target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
            into.append(f"{target} IS {'NOT ' if op == 'not_null' else ''}NULL")
            res.rules_used.append("engine.null_filter")
            continue
        if op == "tuple" and isinstance(f.get("value"), dict):
            # Several columns tested together (a hierarchy slicer's selected paths): a row
            # passes when its columns equal one selected combination, an empty member read
            # as NULL. One column's members alone would keep combinations never selected.
            pred, why = _tuple_pred(f, rules, dialect, res, text_key,
                                    lambda c, t: ((resolve(c, t) if resolve else None)
                                                  or f"{(place(c, t) if place and colmap is None else alias)}.{c}"))
            if pred is None:
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if f.get("value") is None and f.get("value_kind") == "absent":
            continue        # a filter that narrows nothing (nothing selected): no predicate
        if f.get("value") is None or (isinstance(f.get("value"), dict)
                                      and op not in ("relative_range",)
                                      and f["value"].get("min") is None
                                      and f["value"].get("max") is None):
            # The widget applies this filter, but the file recorded no value for it. Dropping
            # it would compute a different number that still runs; it is named instead.
            deferred.append({**f, "_why": "no-value"})
            continue
        # The alias the column is read under: a joined table's for a column of another
        # table (`_plan_joins`), else the statement's own.
        on = place(column, f.get("table")) if place and colmap is None else alias
        target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
        if op == "relative_range" and isinstance(f["value"], dict):
            pred, why = _relative_range(target, f["value"], rules, dialect, res)
            if why:
                res.untranslatable.append({"construct": "relative date filter", "why": why})
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if f.get("null_as") is not None or op in _TEXT_OPS or op in ("any_of", "all_of") \
                or (isinstance(f["value"], list) and None in f["value"]):
            pred, why = _test_pred(target, op, f["value"], f, rules, dialect, res, text_key)
            if pred is not None:
                into.append(pred)
                continue
            if why:
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
        if isinstance(f["value"], dict):
            # A RANGE carries both bounds, either of which may be open. Each bound is its own
            # operator row: `range` (>=) for the lower, `lte` for the upper.
            preds, missing = [], []
            for bound, op in (("min", "range"), ("max", "lte")):
                if f["value"].get(bound) is None:
                    continue
                rule = rules.operator(op)
                if rule is None:
                    missing.append(op)
                    continue
                res.rules_used.append(rule["id"])
                if rule.get("tier") == "provisional":
                    res.provisional_rules.append(rule["id"])
                preds.append(_apply(_emit_for(rule, dialect),
                                    [target, _literal(f["value"][bound], dialect)], op))
            if missing:
                res.unknown += [f"operator:{op}" for op in missing]
                deferred.append({**f, "op": ",".join(missing)})
                continue
            if f["value"].get("or_null"):
                # The range, and the rows with no value ("include null values").
                preds = [f"({' AND '.join(preds)} OR {target} IS NULL)"]
                res.rules_used.append("engine.range_or_null")
            into.extend(preds)
            continue
        vals = f["value"] if isinstance(f["value"], (list, tuple, set)) else [f["value"]]
        if resolve and not re.search(rf"\b({re.escape(alias)}|j\d+)\.", target):
            # A filter on a calc that reads NO column (a parameter-driven sheet switch) is a
            # display control, not a data predicate: emitting `'Trend' IN ('Bar')` returns
            # nothing. Declared, never emitted.
            res.untranslatable.append({"construct": "parameter-only filter",
                                       "why": f"filter on {column!r} controls which view is "
                                              f"shown, not which rows"})
            continue
        if text_key and vals and all(_foldable(v) and v != "%null%" for v in vals) \
                and str(f.get("op") or "eq").lower() in ("in", "eq", "filter", "not_in"):
            # Text as the model holds it (seen live: 'North' and 'north' are one value): the
            # column's key against each value's, upper-cased here so the value stays a
            # literal a reader of the statement can still see.
            keyed = _literal_list([v.upper() for v in vals], dialect)
            res.rules_used.append("engine.text_key_filter")
            key = _Parser._text_key(target)
            if str(f.get("op") or "").lower() == "not_in":
                pred = f"({target} IS NULL OR {key} NOT IN ({keyed}))"
            elif len(vals) == 1:
                pred = f"{key} = {keyed}"
            else:
                pred = f"{key} IN ({keyed})"
            into.append(pred)
            continue
        if str(f.get("op") or "").lower() == "not_in":
            # A Tableau EXCLUDE filter. It keeps NULL rows unless `%null%` is itself excluded;
            # a bare SQL NOT IN would silently drop them too.
            excl_null = "%null%" in [str(v) for v in vals]
            rest = [v for v in vals if str(v) != "%null%"]
            if rest:
                pred = (f"({target} IS NOT NULL AND {target} NOT IN ({_literal_list(rest, dialect)}))"
                        if excl_null else
                        f"({target} IS NULL OR {target} NOT IN ({_literal_list(rest, dialect)}))")
            else:
                pred = f"{target} IS NOT NULL"
            res.rules_used.append("engine.tableau_exclude")
            into.append(pred)
            continue
        if "%null%" in [str(v) for v in vals]:
            # Tableau's member `%null%` IS NULL; `IN ('%null%')` matches a literal string.
            rest = [v for v in vals if str(v) != "%null%"]
            pred = (f"({target} IS NULL OR {target} IN ({_literal_list(rest, dialect)}))"
                    if rest else f"{target} IS NULL")
            into.append(pred)
            continue
        rule = rules.operator(str(f.get("op") or "eq").lower(), dialect or "")
        if rule is None:
            res.unknown.append(f"operator:{f.get('op')}")
            deferred.append(f)
            continue
        emit = rule.get("emit") or {}
        multi = isinstance(f.get("value"), (list, tuple, set))
        if multi and not emit.get("list_arg"):
            # A Tableau filter on several members carries a LIST. Only a membership operator
            # can express it; anything else would have to flatten it, and a flattened list is
            # a literal nobody wrote.
            res.unknown.append(f"operator:{f.get('op')}:multi-value")
            deferred.append({**f, "_why": "multi-value"})
            continue
        res.rules_used.append(rule["id"])
        if rule.get("tier") == "provisional":
            res.provisional_rules.append(rule["id"])
        value = (_literal_list(f["value"], dialect) if multi
                 else _literal(f.get("value"), dialect))
        pred = _apply(_emit_for(rule, dialect), [target, value], str(f.get("op")))
        into.append(pred)
    return ctx, dim, deferred


# --------------------------------------------------------------------------- the reader hooks
# Constructs with no SQL counterpart, by language. A unit needing one is `partial` with the
# construct NAMED — never quietly rewritten into something that merely runs.
UNTRANSLATABLE = {
    "dax": ["CALCULATE", "SAMEPERIODLASTYEAR", "DATESYTD", "DATESQTD", "DATESMTD",
            "TOTALYTD", "PARALLELPERIOD", "DATEADD", "USERELATIONSHIP", "ALLEXCEPT"],
    "tableau-calc": ["PREVIOUS_VALUE", "SCRIPT_", "RAWSQL"],
}


def _untranslatable(unit: dict) -> list[dict]:
    found = []
    for m in unit["measures"]:
        f = (m.get("formula") or "").upper()
        lang = m.get("formula_language") or ""
        for tok in UNTRANSLATABLE.get(lang, []):
            if tok.upper() in f:
                found.append({"construct": tok.strip("("), "in_measure": m["name"]})
    return found


def _apply_item(formula: str, item: str, measure: str) -> str:
    """A calculation item's DAX applied to one measure: SELECTEDMEASURE() is the measure's own
    formula, SELECTEDMEASURENAME() its name, ISSELECTEDMEASURE([m], ...) whether it is one of
    them."""
    toks = tokenize(item)
    out, i = [], 0
    while i < len(toks):
        k, v = toks[i]
        up = v.upper() if k == "func" else ""
        if up in ("SELECTEDMEASURE", "SELECTEDMEASURENAME", "ISSELECTEDMEASURE") and \
                i + 1 < len(toks) and toks[i + 1][0] == "lparen":
            end, args = _call_args(toks, i + 1)
            if end is None:
                raise CompileError(f"{v} is not closed")
            if up == "SELECTEDMEASURE":
                out += [("lparen", "(")] + tokenize(formula) + [("rparen", ")")]
            elif up == "SELECTEDMEASURENAME":
                out.append(("str", '"' + measure.replace('"', '""') + '"'))
            else:
                names = {_column_ref(a[0][1]).lower() for a in args if len(a) == 1}
                out.append(("func", "TRUE" if measure.lower() in names else "FALSE"))
            i = end + 1
            continue
        out.append((k, v))
        i += 1
    return _text_of(out)


def _calculation_items(unit: dict, groups: dict[str, dict], again, rules, dialect: str
                       ) -> Result | None:
    """A unit that groups or filters by a CALCULATION GROUP's column: each item kept is the
    unit with every DAX measure replaced by the item's DAX over it, and the statements are
    stacked, the item's name in the column's place (a matrix of Current / YTD / PY columns).
    None when the unit names no calculation group."""
    gb = list(unit.get("group_by") or [])
    gbt = list(unit.get("group_by_tables") or [None] * len(gb))

    by_column: dict[str, list[dict]] = {}
    for cg0 in groups.values():
        if all(cg0 is not x for x in by_column.get(cg0["column"], [])):
            by_column.setdefault(cg0["column"], []).append(cg0)

    def group_of(col: Any, table: Any) -> dict | None:
        cg = groups.get(str(table or "").lower()) or groups.get(_bare(table or "").lower())
        if cg is None and not table and len(by_column.get(str(col).lower(), [])) == 1:
            # The unit does not name the column's table (a calculation group reads no
            # warehouse table): its column, when one calculation group holds it.
            cg = by_column[str(col).lower()][0]
        return cg if cg and str(col).lower() == cg["column"] else None
    at = [i for i, (g, t) in enumerate(zip(gb, gbt)) if group_of(g, t)]
    flt = [f for f in unit.get("filters") or [] if f.get("column")
           and group_of(f["column"], f.get("table"))]
    if not at and not flt:
        return None
    res = Result()
    # SEVERAL calculation groups: the one of lowest precedence is applied to the measure
    # first, and the others around its result (seen live: PY of Per Order is the prior year's
    # sales per prior-year order, Time Calc being of higher precedence). This expands the
    # lowest; each of its statements expands the next (`again`).
    present: list[dict] = []
    for cg_ in [group_of(gb[i], gbt[i]) for i in at] + [group_of(f["column"], f.get("table"))
                                                       for f in flt]:
        if all(cg_ is not x for x in present):
            present.append(cg_)
    if len([i for i in at if group_of(gb[i], gbt[i]) is present[0]]) > 1 \
            or any(len([i for i in at if group_of(gb[i], gbt[i]) is x]) > 1 for x in present):
        res.coverage = REFUSED
        res.reason = "one calculation group's column twice in one visual is not compiled"
        return res
    if len(present) > 1:
        precs = [x.get("precedence") if x.get("precedence") is not None else 0
                 for x in present]
        if len(set(precs)) < len(precs):
            res.coverage = REFUSED
            res.reason = ("two calculation groups of the same precedence in one visual: which "
                          "is applied around the other the model does not say")
            return res
        cg = present[precs.index(min(precs))]
        at = [i for i in at if group_of(gb[i], gbt[i]) is cg]
        flt = [f for f in flt if group_of(f["column"], f.get("table")) is cg]
    else:
        cg = present[0]
    items = list(cg["items"])
    for f in flt:
        vals = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
        keep = {str(v).lower() for v in vals}
        op = str(f.get("op") or "in")
        if op not in ("in", "not_in", "eq", "="):
            res.coverage = REFUSED
            res.reason = f"a {op!r} filter on a calculation group's column is not compiled"
            return res
        items = [it for it in items if (it["name"].lower() in keep) == (op != "not_in")]
    if not items:
        res.coverage = REFUSED
        res.reason = "the visual's filters keep no calculation item"
        return res
    if not at and len(items) > 1:
        res.coverage = REFUSED
        res.reason = ("several calculation items in the filter of a visual that does not show "
                      "them: Power BI applies no one item there")
        return res
    base = {k: v for k, v in unit.items() if k != "totals"}
    base["filters"] = [f for f in unit.get("filters") or [] if f not in flt]
    if at:
        i0 = at[0]
        base["group_by"] = gb[:i0] + gb[i0 + 1:]
        base["group_by_tables"] = gbt[:i0] + gbt[i0 + 1:]
    parts = []
    for it in items:
        try:
            ms = [{**m, "formula": _apply_item(m["formula"], it["expression"], m["name"])}
                  if m.get("formula_language") == "dax" and m.get("formula") else m
                  for m in unit.get("measures") or []]
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"calculation item {it['name']!r}: {exc}"
            return res
        r = again(dict(base, measures=ms))
        if not r.sql or r.coverage == REFUSED:
            res.coverage = REFUSED
            res.reason = f"calculation item {it['name']!r}: {r.reason}"
            res.untranslatable = list(r.untranslatable)
            return res
        parts.append((it, r))
    if not at:
        r = parts[0][1]
        r.gotchas.append(f"calculation item {items[0]['name']!r} applied to every measure"
                         + _item_formats(items))
        r.rules_used.append("engine.dax_calculation_item")
        return r
    names, vals = _leaf_names(parts[0][1], dict(base, measures=unit.get("measures") or []))
    out_col = _measure_alias(gb[at[0]])
    blocks = []
    for it, r in parts:
        n_k, n_v = _leaf_names(r, dict(base, measures=unit.get("measures") or []))
        sel = [f"p.{n} AS {names[j]}" for j, n in enumerate(n_k)]
        sel.insert(at[0], f"{_sql_string(chr(39) + it['name'].replace(chr(39), chr(39) * 2) + chr(39), dialect)} AS {out_col}")
        sel += [f"p.{v} AS {v}" if v in n_v else f"NULL AS {v}" for v in vals]
        blocks.append("SELECT " + ", ".join(sel) + "\nFROM   (\n"
                      + "\n".join("    " + ln for ln in r.sql.splitlines()) + "\n) p")
        res.rules_used += [x for x in r.rules_used if x not in res.rules_used]
        res.untranslatable += r.untranslatable
    res.sql = "\nUNION ALL\n".join(blocks)
    res.rules_used.append("engine.dax_calculation_items")
    partial = any(r.coverage == PARTIAL for _it, r in parts)
    for t in unit.get("totals") or []:
        # A total keeping the items' column is the items again over fewer columns; one that
        # does not has no item in context, so each measure is itself (one kept by a filter
        # still applies).
        keep = [g for g in gb if g in set(t.get("group_by") or [])]
        ktabs = [gbt[gb.index(g)] for g in keep]
        entry = {"kind": t.get("kind") or ("subtotal" if keep else "grand_total"),
                 "group_by": keep, "sql": None, "reason": None}
        sub = dict(unit, group_by=keep, group_by_tables=ktabs, totals=None)
        if gb[at[0]] in keep:
            r = _calculation_items(sub, groups, again, rules, dialect)
        else:
            one = items if len(items) == 1 and flt else []
            r = again(dict(sub, filters=base["filters"], measures=[
                {**m, "formula": _apply_item(m["formula"], one[0]["expression"], m["name"])}
                if one and m.get("formula_language") == "dax" and m.get("formula") else m
                for m in unit.get("measures") or []]))
        if r is not None and r.sql and r.coverage != REFUSED:
            entry["sql"] = r.sql
        else:
            entry["reason"] = (r.reason if r is not None else "") or "the total does not compile"
            partial = True
            res.untranslatable.append({"construct": "total row",
                                       "why": f"{entry['kind']} over {keep}: {entry['reason']}"})
        res.totals.append(entry)
    res.coverage = PARTIAL if partial else TRANSLATED
    res.reason = "; ".join([r.reason for _it, r in parts if r.reason]
                           + [f"total {e['kind']} over {e['group_by']}: {e['reason']}"
                              for e in res.totals if e["sql"] is None])
    res.gotchas.append(f"{gb[at[0]]}: one statement per calculation item "
                       f"({', '.join(it['name'] for it, _r in parts)}), each measure with the "
                       f"item's DAX applied" + _item_formats(items))
    for _it, r in parts:
        res.gotchas += [g for g in r.gotchas if g not in res.gotchas]
    return res


def _item_formats(items: list[dict]) -> str:
    """The gotcha's words on the items' format strings: they change how a value is shown,
    never the value, so the statement leaves them out (they stay in bi_facts)."""
    fmt = [f"{it['name']} {it['format_string']}" for it in items if it.get("format_string")]
    return ("; format strings are not applied (they change how a value is shown, not the "
            "value" + (f": {', '.join(fmt)}" if fmt else "") + ")")


class FormulaCompiler:
    """The compile part of the reader socket (`sockets.BIReader`), shared by the built-in
    readers. A reader mixes it in and declares its `languages`; A3 calls `prepare` once per
    model and `compile_unit` once per unit, and the A3b gate calls `compile_unit` with no model
    on each regression case."""

    compiler_rules = RULES
    compiler_corpus = CORPUS

    def prepare(self, facts: dict) -> dict:
        """What compiling a unit of this ONE model needs: names are looked up only within the
        model that declares them."""
        cols_of = {t["name"]: {(c["source_column"] or c["name"]).lower() for c in t["columns"]}
                   for t in facts["tables"]}
        # Every DAX measure's formula, so a measure that references another is compiled from
        # it.
        measure_formulas = {m["name"].lower(): m["formula"] for m in facts["measures"]
                            if m.get("formula") and m.get("formula_language") == "dax"}
        # TABLEAU: a calc references other calcs (and dimension calcs) by name too, so they
        # are inlined the same way. And a TABLEAU PARAMETER (`[Parameters].[X]`) is fixed to
        # its DECLARED DEFAULT — trap 4: the branch the dashboard shows when nobody touches
        # the control — and every object compiled through one says so in a gotcha.
        tp = _tparams.Params(facts)
        interactions = list(facts.get("interactions") or [])
        for k, f in tp.inline_index().items():
            measure_formulas.setdefault(k, f)
        # A DAX CALCULATED COLUMN is an expression over its own row, not a warehouse column:
        # a unit grouping or filtering by it, and a measure aggregating it, compile its
        # formula. Keyed by `table[column]` (how DAX names it) and, where no plain column or
        # measure takes the bare name and no other table's calculated column shares it, by
        # the bare name too.
        plain = {(c.get("source_column") or c["name"]).lower() for t in facts["tables"]
                 for c in t["columns"] if not c.get("is_calculated")}
        bare: dict[str, set[str]] = {}
        row_formulas: set[str] = set()
        for t in facts["tables"]:
            for c in t["columns"]:
                if not (c.get("is_calculated") and c.get("formula")
                        and c.get("formula_language") == "dax"):
                    continue
                for tn in {str(t.get("logical_name") or ""), str(t["name"])} - {""}:
                    key = f"{tn.lower()}[{c['name'].lower()}]"
                    measure_formulas.setdefault(key, c["formula"])
                    row_formulas.add(key)
                bare.setdefault(c["name"].lower(), set()).add(c["formula"])
        for k, formulas in bare.items():
            if len(formulas) == 1 and k not in plain and k not in measure_formulas:
                measure_formulas[k] = next(iter(formulas))
                row_formulas.add(k)
        # Each column's data type, by every name its table goes by: text is compared as the
        # model holds it, and a date set is read only over a date column.
        types: dict[tuple[str, str], str] = {}
        for t in facts["tables"]:
            names = {str(t.get("logical_name") or ""), str(t["name"]), _bare(t["name"])} - {""}
            for c in t["columns"]:
                if not c.get("data_type"):
                    continue
                for tn in names:
                    for cn in {c["name"], c.get("source_column") or c["name"]}:
                        types.setdefault((tn.lower(), str(cn).lower()), str(c["data_type"]))
        # Model table (lower) -> the warehouse table it reads, so a measure naming a column of
        # another table is refused instead of being read off this statement's table.
        table_of = {str(t.get("logical_name") or t["name"]).lower(): t["name"]
                    for t in facts["tables"]}
        # A `formula_language: sql` measure (the core's own language) is PLACED in its model by
        # ROLE (lib/declared): two roles over one table are two joins, and a name the formula
        # uses is expanded to what it stands for. No other language reads it.
        declared = (_declared.Model.of(facts)
                    if any(m.get("formula_language") == "sql" for m in facts["measures"])
                    else None)
        # Every relationship of the model on warehouse tables: a measure may read a column of
        # a table its unit does not (a CALCULATE filter on a dimension's column).
        graph = [{**r, "from_table": table_of.get(str(r.get("from_table") or "").lower(),
                                                  r.get("from_table")),
                  "to_table": table_of.get(str(r.get("to_table") or "").lower(),
                                           r.get("to_table"))}
                 for r in facts.get("relationships") or []
                 if r.get("from_table") and r.get("to_table")]
        # A model whose formulas are DAX compares text as DAX does: without case, and a
        # column's value without its trailing spaces (seen live). Decided from the model's
        # own language, never from which tool it came from.
        text_key = any(m.get("formula_language") == "dax" for m in facts["measures"]) or any(
            c.get("formula_language") == "dax" for t in facts["tables"] for c in t["columns"])
        # A table the reader gave a statement of its own (`source_lineage.statement`), by its
        # name; and each column a formula names otherwise than its table reads it.
        statements = {t["name"]: (t.get("source_lineage") or {})["statement"]
                      for t in facts["tables"]
                      if (t.get("source_lineage") or {}).get("statement")}
        renames: dict[tuple[str, str], str] = {}
        for t in facts["tables"]:
            for c in t["columns"]:
                src = c.get("source_column")
                if src and not c.get("is_calculated") and str(src).lower() != str(
                        c["name"]).lower():
                    renames[(_bare(t["name"]), str(c["name"]).lower())] = str(src)
        # The labels a view shows for a column's values (aliases), by (table, column) and by
        # the bare column where one table holds it.
        labels: dict[tuple[str, str], list[dict]] = {}
        for t in facts["tables"]:
            for c in t["columns"]:
                if c.get("value_labels"):
                    for tn in {str(t.get("logical_name") or ""), str(t["name"]), ""} - {None}:
                        for cn in {c["name"], c.get("source_column") or c["name"]}:
                            labels.setdefault((tn.lower(), str(cn).lower()),
                                              c["value_labels"])
        # Calculation groups by every name their table goes by: the column naming the items
        # and each item's DAX (`_calculation_items`).
        calc_groups: dict[str, dict] = {}
        for t in facts["tables"]:
            cg = t.get("calculation_group")
            if cg:
                for key in {str(t.get("logical_name") or ""), str(t["name"]),
                            _bare(t["name"])} - {""}:
                    calc_groups[key.lower()] = {"column": str(cg["column"]).lower(),
                                                "items": list(cg.get("items") or []),
                                                "precedence": cg.get("precedence")}
        # A table whose rows the file fixes and no relationship reaches: a parameter, read
        # off its rows per unit (`fixedtables`).
        from metrics_agent.plugs.readers import fixedtables as _fixed  # noqa: PLC0415
        fixed = _fixed.index(facts, facts.get("relationships") or [])
        return {"columns": cols_of, "measures": measure_formulas, "params": tp,
                "calc_groups": calc_groups, "fixed": fixed, "interactions": interactions,
                "labels": labels,
                "table_of": table_of, "declared": declared, "graph": graph,
                "types": types, "row_formulas": row_formulas, "text_key": text_key,
                "statements": statements, "renames": renames,
                # Tables read from the source on every query (DirectQuery): there the source
                # compares and groups text, with case and trailing spaces.
                "source_compared": {str(t["name"]).lower() for t in facts["tables"]
                                    if t.get("storage_mode") == "directquery"}}

    def compile_unit(self, unit: dict, rules, dialect: str, *, model: dict | None = None,
                     columns: dict[str, set[str]] | None = None,
                     logical: dict[str, str] | None = None,
                     table_of: dict[str, str] | None = None,
                     types: dict[tuple[str, str], str] | None = None) -> Compiled:
        if model is None:
            return compile_unit(unit, rules, dialect, columns=columns, logical=logical,
                                table_of=table_of, types=types)
        tp = model["params"]
        model_measures = list(unit["measures"])
        # A filter's own expression (a condition, a Top N's ranking) names parameters too, and
        # a Top N's count may be one (`n_parameter`, already its current value).
        exprs = [str(v.get(k)) for f in unit.get("filters") or []
                 for v in [f.get("value")] if isinstance(v, dict)
                 for k in ("expression", "by") if v.get(k)]
        used = sorted(set(tp.used([m.get("formula") or "" for m in unit["measures"]] + exprs))
                      | {str(f["value"]["n_parameter"]) for f in unit.get("filters") or []
                         if isinstance(f.get("value"), dict)
                         and f["value"].get("n_parameter") in tp.default})

        def fixed(f: dict) -> dict:
            v = f.get("value")
            if not isinstance(v, dict) or not any(v.get(k) for k in ("expression", "by")):
                return f
            return {**f, "value": {**v, **{k: tp.fix(v[k]) for k in ("expression", "by")
                                           if v.get(k)}}}
        unit = {**unit, "measures": [{**m, "formula": tp.fix(m.get("formula"))}
                                     for m in unit["measures"]],
                "filters": [fixed(f) for f in unit.get("filters") or []]}
        labels = model.get("labels") or {}
        if labels:
            gbt = list(unit.get("group_by_tables") or [])
            shown = {}
            for i, g in enumerate(unit.get("group_by") or []):
                t = str(gbt[i] if i < len(gbt) and gbt[i] else "").lower()
                hit = labels.get((t, str(g).lower())) or labels.get(("", str(g).lower()))
                if hit:
                    shown[str(g).lower()] = hit
            if shown:
                unit["member_labels"] = shown
        if model.get("fixed"):
            from metrics_agent.plugs.readers import fixedtables as _fixed  # noqa: PLC0415
            done = _fixed.compile_with_fixed(
                unit, model, lambda u, m: self.compile_unit(
                    u, rules, dialect, model=m, columns=columns, logical=logical,
                    table_of=table_of, types=types), dialect)
            if done is not None:
                return done
        if model.get("calc_groups"):
            expanded = _calculation_items(unit, model["calc_groups"], lambda u: self.compile_unit(
                u, rules, dialect, model=model, columns=columns, logical=logical,
                table_of=table_of, types=types), rules, dialect)
            if expanded is not None:
                return expanded
        res = compile_unit(unit, rules, dialect, columns=model["columns"],
                           measures=model["measures"], table_of=model["table_of"],
                           model=model.get("declared"), model_graph=model.get("graph"),
                           types=model.get("types"), row_formulas=model.get("row_formulas"),
                           text_key=("source" if any(
                               str(t).lower() in (model.get("source_compared") or set())
                               for t in unit.get("warehouse_tables") or [])
                               else bool(model.get("text_key"))),
                           renames=model.get("renames"), table_sql=model.get("statements"))
        if res.coverage == REFUSED or not res.sql:
            return res
        # A partial result names its own gaps (`compile_unit`); they are kept as they are. The
        # old rescan of the formulas for a fixed token list replaced nothing it could name with
        # an EMPTY list, which is how a partial unit reached its candidate saying nothing.
        if tp.viewer and tp.viewer_used(
                [m.get("formula") or "" for m in model_measures] + exprs
                + [f"[{g}]" for g in unit.get("group_by") or []]
                + [f"[{f.get('column')}]" for f in unit.get("filters") or []
                   if f.get("column")]):
            res.gotchas.append(
                f"Compiled for the run's viewer ({tp.viewer.get('source') or 'viewer.json'}): "
                f"its domain and group tests return what this identity sees; another viewer "
                f"sees other rows.")
        if used:
            res.gotchas.append(
                "Tableau parameter(s) fixed at their DECLARED DEFAULT: "
                + ", ".join(f"[{p}] = {tp.value.get(p, tp.default.get(p))}" for p in used)
                + ". On the dashboard a user can change them; this statement is the default "
                  "view only.")
        # A SET OR PARAMETER AN ACTION CHANGES (`bi_facts.interactions`): the statement is
        # for the state the file saved; the dashboard's follows the viewer's selection.
        text = " ".join([m.get("formula") or "" for m in model_measures] + exprs
                        + [f"[{g}]" for g in unit.get("group_by") or []]
                        + [f"[{f.get('column')}]" for f in unit.get("filters") or []
                           if f.get("column")]).lower()
        for act in model.get("interactions") or []:
            hit = (act["target"] in used if act["kind"] == "parameter_action"
                   else f"[{str(act['target']).lower()}]" in text)
            if hit:
                what = ("set" if act["kind"] == "set_action" else "parameter")
                src = (f" from {', '.join(act['sources'])}" if act.get("sources") else "")
                res.gotchas.append(
                    f"The {what} [{act['target']}] is changed by an action"
                    f"{(' (' + act['activation'] + src + ')') if act.get('activation') else src}"
                    f": compiled for the {'members' if what == 'set' else 'value'} the "
                    f"workbook saved; on the dashboard it follows the viewer's selection.")
        return res
