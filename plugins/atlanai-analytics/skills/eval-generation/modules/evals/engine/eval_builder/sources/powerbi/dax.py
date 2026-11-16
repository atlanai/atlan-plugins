"""DAX, parsed, and translated to SQL where the translation is exact.

Two translations, because DAX evaluates in two contexts:

    row      a calculated column or a date-table attribute: one value per row
             of its table. SWITCH/IF/comparisons/arithmetic/text functions, and
             a lookup into a DATATABLE (FILTER + MAXX/COUNTROWS) as a
             correlated subquery.
    measure  an aggregate over the tile's filtered rows: SUM/AVERAGE/MIN/MAX/
             COUNT/COUNTA/COUNTROWS/DISTINCTCOUNT, DIVIDE, CALCULATE with
             column filters, IF/ISBLANK over aggregates, measure references.

Anything outside those subsets raises `Refuse` with the construct named:
time intelligence (DATESBETWEEN, DATESYTD, SAMEPERIODLASTYEAR), context
modifiers (ALL, REMOVEFILTERS, ALLSELECTED), iterators over other tables,
SELECTEDVALUE, text-returning measures. A wrong number is worse than no eval.

The translators do not know SQL dialects. They call back into a `Resolver`
for every column and table, and the tile assembles the dialect-specific SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol


class Refuse(Exception):
    """The DAX cannot be translated without guessing."""


# ---------------------------------------------------------------- tokenizer

_TOK = re.compile(r"""
    \s*(?:
      (?P<num>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)
    | (?P<str>"(?:[^"]|"")*")
    | (?P<col>(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)\s*\[[^\]]+\])
    | (?P<ref>\[[^\]]+\])
    | (?P<tbl>'(?:[^']|'')+')
    | (?P<id>[A-Za-z_][\w.]*)
    | (?P<op>&&|\|\||<>|>=|<=|==|[-+*/&(),=<>{}^!])
    )""", re.X)


_COMMENT = re.compile(r'"(?:[^"]|"")*"|//[^\n]*|--[^\n]*|/\*.*?\*/', re.S)


def tokenize(src: str) -> list[tuple[str, str]]:
    # comments go, string literals stay: "http://…" is not a comment
    s = _COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else " ", src)
    out, i = [], 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = _TOK.match(s, i)
        if not m or m.end() == i:
            raise Refuse(f"DAX: unparsed text near {s[i:i + 25]!r}")
        kind, val = m.lastgroup, m.group(m.lastgroup)
        head = re.match(r"(RETURN|IN|VAR|NOT|AND|OR)\s*\[", val, re.I) if kind == "col" else None
        if head:                                # `RETURN [Measure]`: a keyword, then a reference
            out.append(("id", head.group(1)))
            i = m.start(kind) + head.end() - 1
            continue
        out.append((kind, val))
        i = m.end()
    return out


def _unquote_table(t: str) -> str:
    t = t.strip()
    return t[1:-1].replace("''", "'") if t.startswith("'") else t


def split_col(tok: str) -> tuple[str, str]:
    m = re.match(r"\s*('(?:[^']|'')+'|[A-Za-z_][\w.]*)\s*\[([^\]]+)\]\s*$", tok)
    if not m:
        raise Refuse(f"DAX: bad column reference {tok!r}")
    return _unquote_table(m.group(1)), m.group(2).strip()


# ---------------------------------------------------------------- parser
#
# Nodes are tuples:
#   ("num", "1.5") ("str", "x") ("bool", True) ("col", table, column) ("ref", name)
#   ("tbl", table) ("fn", NAME, [args]) ("bin", op, a, b) ("neg", x) ("not", x)
#   ("in", x, [values]) ("set", [[row values]])

_PREC = [("||",), ("&&",), ("=", "==", "<>", "<", ">", "<=", ">=", "IN"), ("&",), ("+", "-"), ("*", "/"), ("^",)]


class _Parser:
    def __init__(self, toks):
        self.t, self.i, self.vars = toks, 0, {}

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def eat(self, val=None):
        kind, v = self.peek()
        if val is not None and (v or "").upper() != val:
            raise Refuse(f"DAX: expected {val}, got {v}")
        self.i += 1
        return kind, v

    def expr(self, level=0):
        if level == len(_PREC):
            return self.unary()
        node = self.expr(level + 1)
        while (self.peek()[1] or "").upper() in _PREC[level]:
            op = self.eat()[1].upper()
            if op == "IN":
                node = ("in", node, self._set_values())
                continue
            node = ("bin", "=" if op == "==" else op, node, self.expr(level + 1))
        return node

    def _set_values(self):
        self.eat("{")
        vals = [self.expr()]
        while self.peek()[1] == ",":
            self.eat()
            vals.append(self.expr())
        self.eat("}")
        return vals

    def unary(self):
        v = self.peek()[1]
        if v == "-":
            self.eat()
            return ("neg", self.unary())
        if v == "+":
            self.eat()
            return self.unary()
        if v == "!" or (v or "").upper() == "NOT" and self.peek(1)[1] != "(":
            self.eat()
            return ("not", self.unary())
        return self.atom()

    def args(self):
        self.eat("(")
        out = []
        if self.peek()[1] != ")":
            out.append(self.expr())
            while self.peek()[1] == ",":
                self.eat()
                out.append(self.expr())
        self.eat(")")
        return out

    def atom(self):
        kind, v = self.peek()
        if kind == "num":
            self.eat()
            return ("num", v)
        if kind == "str":
            self.eat()
            return ("str", v[1:-1].replace('""', '"'))
        if kind == "col":
            self.eat()
            return ("col",) + split_col(v)
        if kind == "ref":
            self.eat()
            name = v[1:-1].strip()
            return self.vars.get(name.upper(), ("ref", name))
        if kind == "tbl":
            self.eat()
            return ("tbl", _unquote_table(v))
        if v == "(":
            self.eat()
            e = self.expr()
            self.eat(")")
            return e
        if v == "{":
            return self._table_ctor()
        if kind == "id":
            name = self.eat()[1]
            up = name.upper()
            if up == "VAR":
                return self._var_block()
            if self.peek()[1] == "(":
                args = self.args()
                if up in ("TRUE", "FALSE") and not args:
                    return ("bool", up == "TRUE")
                return ("fn", up, args)
            if up in ("TRUE", "FALSE"):
                return ("bool", up == "TRUE")
            if up in self.vars:
                return self.vars[up]
            return ("tbl", name)            # a bare table name: COUNTROWS(Sales)
        raise Refuse(f"DAX: unexpected token {v!r}")

    def _table_ctor(self):
        self.eat("{")
        rows = []
        while self.peek()[1] != "}":
            if self.peek()[1] in ("(", "{"):            # a row: (a, b) in table constructors, {a, b} in DATATABLE
                close = ")" if self.eat()[1] == "(" else "}"
                row = [self.expr()]
                while self.peek()[1] == ",":
                    self.eat()
                    row.append(self.expr())
                self.eat(close)
            else:
                row = [self.expr()]
            rows.append(row)
            if self.peek()[1] == ",":
                self.eat()
        self.eat("}")
        return ("set", rows)

    def _var_block(self):
        while True:
            name = self.eat()[1].upper()
            self.eat("=")
            self.vars[name] = self.expr()
            nxt = (self.peek()[1] or "").upper()
            if nxt == "VAR":
                self.eat()
                continue
            self.eat("RETURN")
            return self.expr()


def parse(src: str):
    if not (src or "").strip():
        raise Refuse("DAX: empty expression")
    p = _Parser(tokenize(src))
    node = p.expr()
    if p.peek()[0] is not None:
        raise Refuse(f"DAX: trailing tokens from {p.peek()[1]!r}")
    return node


# ---------------------------------------------------------------- shared literal helpers

def lit(node) -> str:
    """A DAX literal as SQL."""
    k = node[0]
    if k == "str":
        return "'" + node[1].replace("'", "''") + "'"
    if k == "num":
        return node[1]
    if k == "bool":
        return "TRUE" if node[1] else "FALSE"
    if k == "neg" and node[1][0] == "num":
        return "-" + node[1][1]
    if k == "fn" and node[1] == "BLANK" and not node[2]:
        return "NULL"
    raise Refuse("DAX: value is not a literal")


def returns_text(node) -> bool:
    """A measure whose value is text (a label, a symbol, an SVG): a display
    helper on a card, never a number a person asks for."""
    k = node[0]
    if k == "str":
        return True
    if k == "bin" and node[1] == "&":
        return True
    if k == "fn":
        name, args = node[1], node[2]
        if name in ("FORMAT", "UNICHAR", "REPT", "CONCATENATE", "CONCATENATEX", "COMBINEVALUES",
                    "UPPER", "LOWER", "LEFT", "RIGHT", "MID", "SUBSTITUTE", "TRIM"):
            return True
        if name in ("IF", "SWITCH"):
            branch = args[1:] if name == "IF" else args[2::2] + ([args[-1]] if len(args) % 2 == 0 else [])
            return any(returns_text(a) for a in branch)
    return False


# ---------------------------------------------------------------- resolver interface

class Resolver(Protocol):
    def column(self, table: str, column: str) -> str: ...       # SQL for a column in the tile's row scope
    def measure(self, name: str) -> str | None: ...             # DAX of a measure, or None
    def count_rows(self, table: str) -> None: ...               # COUNTROWS(table) is allowed on the fact
    def lookup(self, table: str) -> "Lookup | None": ...        # a DATATABLE-backed table for FILTER


@dataclass
class Lookup:
    """A small in-model table (DATATABLE) a row expression may search."""
    cte: str                                    # SQL name of its CTE
    column: Callable[[str], str]                # inner column -> SQL, qualified by the inner alias
    alias: str


_DATE_PART = {"YEAR": "YEAR", "MONTH": "MONTH", "DAY": "DAY", "QUARTER": "QUARTER"}
_TEXT1 = {"LOWER": "LOWER", "UPPER": "UPPER", "TRIM": "TRIM", "LEN": "LENGTH"}


# ---------------------------------------------------------------- row context

class RowEmitter:
    """A calculated column or date attribute, one value per row."""

    def __init__(self, resolver: Resolver, depth: int = 0):
        self.r, self.depth = resolver, depth

    def emit(self, n) -> str:
        k = n[0]
        if k in ("num", "str", "bool"):
            return lit(n)
        if k == "col":
            return self.r.column(n[1], n[2])
        if k == "ref":
            bare = getattr(self.r, "bare", None)        # ADDCOLUMNS: [Date] is the row's own column
            if bare is not None:
                return bare(n[1])
            raise Refuse(f"DAX: [{n[1]}] in row context (a measure or an unqualified column)")
        if k == "neg":
            return f"(-{self.emit(n[1])})"
        if k == "not":
            return f"(NOT {self.emit(n[1])})"
        if k == "in":
            return f"({self.emit(n[1])} IN ({', '.join(lit(v) for v in n[2])}))"
        if k == "bin":
            return self._bin(n)
        if k == "fn":
            return self._fn(n[1], n[2])
        raise Refuse(f"DAX: unsupported {k} in row context")

    def _bin(self, n) -> str:
        op, a, b = n[1], n[2], n[3]
        if op == "&":
            return f"CONCAT({self.emit(a)}, {self.emit(b)})"
        if op in ("&&", "||"):
            return f"({self.emit(a)} {'AND' if op == '&&' else 'OR'} {self.emit(b)})"
        if op == "/":
            return f"({self.emit(a)} / NULLIF({self.emit(b)}, 0))"
        if op == "^":
            return f"POWER({self.emit(a)}, {self.emit(b)})"
        # DAX treats BLANK as equal to "" and 0 in comparisons; an explicit
        # comparison with BLANK() is an IS NULL test.
        if op in ("=", "<>") and (b[0] == "fn" and b[1] == "BLANK"):
            return f"({self.emit(a)} IS {'NOT ' if op == '<>' else ''}NULL)"
        return f"({self.emit(a)} {op} {self.emit(b)})"

    def _fn(self, f: str, a: list) -> str:
        if f == "BLANK" and not a:
            return "NULL"
        if f == "ISBLANK" and len(a) == 1:
            return f"({self.emit(a[0])} IS NULL)"
        if f == "IF" and len(a) in (2, 3):
            other = self.emit(a[2]) if len(a) == 3 else "NULL"
            return f"(CASE WHEN {self.emit(a[0])} THEN {self.emit(a[1])} ELSE {other} END)"
        if f == "SWITCH" and len(a) >= 3:
            subject, pairs = a[0], a[1:]
            other = self.emit(pairs.pop()) if len(pairs) % 2 else "NULL"
            if subject == ("bool", True):
                whens = " ".join(f"WHEN {self.emit(c)} THEN {self.emit(v)}" for c, v in zip(pairs[::2], pairs[1::2]))
            else:
                s = self.emit(subject)
                whens = " ".join(f"WHEN {s} = {self.emit(c)} THEN {self.emit(v)}" for c, v in zip(pairs[::2], pairs[1::2]))
            return f"(CASE {whens} ELSE {other} END)"
        if f in _TEXT1 and len(a) == 1:
            return f"{_TEXT1[f]}({self.emit(a[0])})"
        if f == "CONTAINSSTRING" and len(a) == 2:          # case-insensitive in DAX
            return f"(LOWER({self.emit(a[0])}) LIKE CONCAT('%', LOWER({self.emit(a[1])}), '%'))"
        if f == "CONTAINSSTRINGEXACT" and len(a) == 2:
            return f"({self.emit(a[0])} LIKE CONCAT('%', {self.emit(a[1])}, '%'))"
        if f in ("AND", "OR") and len(a) == 2:
            return f"({self.emit(a[0])} {f} {self.emit(a[1])})"
        if f == "NOT" and len(a) == 1:
            return f"(NOT {self.emit(a[0])})"
        if f in _DATE_PART and len(a) == 1:
            return f"{_DATE_PART[f]}({self.emit(a[0])})"
        if f == "DATE" and len(a) == 3:
            return self._date(a)
        if f == "EOMONTH" and len(a) == 2 and a[1] == ("num", "0"):
            return f"LAST_DAY({self.emit(a[0])})"
        if f in ("INT", "TRUNC") and len(a) == 1:
            return f"FLOOR({self.emit(a[0])})"
        if f == "ABS" and len(a) == 1:
            return f"ABS({self.emit(a[0])})"
        if f == "ROUND" and len(a) == 2:
            return f"ROUND({self.emit(a[0])}, {self.emit(a[1])})"
        if f == "COALESCE" and a:
            return f"COALESCE({', '.join(self.emit(x) for x in a)})"
        if f == "DIVIDE" and len(a) in (2, 3):
            alt = self.emit(a[2]) if len(a) == 3 else "NULL"
            return f"COALESCE({self.emit(a[0])} / NULLIF({self.emit(a[1])}, 0), {alt})"
        if f in ("MAXX", "MINX") and len(a) == 2:
            return self._lookup_agg("MAX" if f == "MAXX" else "MIN", a[0], a[1])
        if f == "COUNTROWS" and len(a) == 1:
            return self._lookup_agg("COUNT", a[0], None)
        if f in ("TODAY", "UTCTODAY") and not a:
            # Time-relative, like Sigma's frozen dates: the builder pins an
            # as-of anchor for CURRENT_DATE, and the question spec names it.
            return "CURRENT_DATE"
        if f in ("NOW", "UTCNOW") and not a:
            return "CURRENT_TIMESTAMP"
        raise Refuse(f"DAX: unsupported function {f} in row context")

    def _date(self, a) -> str:
        """DATE(YEAR(x), MONTH(x), 1) and DATE(YEAR(x), 1, 1) are truncations;
        any other DATE() is refused rather than built per dialect."""
        y, m, d = a
        if y[0] == "fn" and y[1] == "YEAR" and d == ("num", "1"):
            base = y[2][0]
            if m[0] == "fn" and m[1] == "MONTH" and m[2][0] == base:
                return f"TRUNC(CAST({self.emit(base)} AS DATE), 'MONTH')"
            if m == ("num", "1"):
                return f"TRUNC(CAST({self.emit(base)} AS DATE), 'YEAR')"
        raise Refuse("DAX: DATE() other than a month or year start")

    def _lookup_agg(self, agg: str, table_expr, value) -> str:
        """MAXX(FILTER('Lookup', cond), 'Lookup'[c]) / COUNTROWS(FILTER(...)):
        a correlated subquery over the lookup table's CTE."""
        if not (table_expr[0] == "fn" and table_expr[1] == "FILTER" and len(table_expr[2]) == 2
                and table_expr[2][0][0] == "tbl"):
            raise Refuse("DAX: an iterator over something other than FILTER(<lookup table>, …)")
        name, cond = table_expr[2][0][1], table_expr[2][1]
        lk = self.r.lookup(name)
        if lk is None:
            raise Refuse(f"DAX: FILTER over '{name}', which is not an in-model lookup table")
        inner = _InnerRow(self.r, name, lk)
        what = "COUNT(*)" if value is None else f"{agg}({inner.emit(value)})"
        return f"(SELECT {what} FROM {lk.cte} {lk.alias} WHERE {inner.emit(cond)})"


class _InnerRow(RowEmitter):
    """Row context inside FILTER(lookup, …): the lookup's columns resolve to
    the inner alias, everything else to the outer row."""

    def __init__(self, outer: Resolver, table: str, lk: Lookup):
        super().__init__(outer)
        self.table, self.lk = table, lk

    def emit(self, n) -> str:
        if n[0] == "col" and n[1] == self.table:
            return self.lk.column(n[2])
        return super().emit(n)


# ---------------------------------------------------------------- measure context

_AGG = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT", "COUNTA": "COUNT"}
_AGGX = {"SUMX": "SUM", "AVERAGEX": "AVG", "MINX": "MIN", "MAXX": "MAX"}
_CTX = ("ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "ALLNOBLANKROW", "USERELATIONSHIP",
        "CROSSFILTER", "TREATAS")
_TIME = ("DATESBETWEEN", "DATESYTD", "DATESMTD", "DATESQTD", "TOTALYTD", "TOTALMTD", "TOTALQTD",
         "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD", "PREVIOUSMONTH", "PREVIOUSYEAR",
         "PREVIOUSQUARTER", "PREVIOUSDAY", "NEXTMONTH", "NEXTYEAR", "DATESINPERIOD", "STARTOFMONTH",
         "ENDOFMONTH", "STARTOFYEAR", "ENDOFYEAR", "LASTDATE", "FIRSTDATE")


class MeasureEmitter:
    """A measure at the tile's grain: aggregates over the filtered fact rows.

    `fact` is the one table whose rows are aggregated; it is fixed by the first
    aggregate and every later one must agree, so a measure that mixes facts is
    refused instead of joined.
    """

    def __init__(self, resolver: Resolver, fact_of: Callable[[str], str]):
        self.r, self.fact_of = resolver, fact_of
        self.fact: str | None = None
        self.depth = 0
        self.home: list[str] = []                # the table of the measure being expanded
        self.refs: list[str] = []                # measures inlined, for provenance

    def _use(self, table: str) -> None:
        fact = self.fact_of(table)
        if self.fact is None:
            self.fact = fact
        elif self.fact != fact:
            raise Refuse(f"DAX: the measure aggregates two tables ({self.fact}, {fact})")

    def emit(self, n, conds: tuple[str, ...] = ()) -> str:
        k = n[0]
        if k in ("num", "str", "bool"):
            return lit(n)
        if k == "ref":
            dax = self.r.measure(n[1])
            if dax is None:
                raise Refuse(f"DAX: [{n[1]}] is not a measure in the model (a bare column in measure context)")
            self.depth += 1
            if self.depth > 16:
                raise Refuse("DAX: measure references nest too deep")
            self.refs.append(n[1])
            home = getattr(self.r, "measure_home", lambda _: None)(n[1])
            self.home.append(home)
            out = self.emit(parse(dax), conds)
            self.home.pop()
            self.depth -= 1
            return out
        if k == "col":
            if self.r.measure(n[2]) is not None:    # Table[Measure] is measure syntax too
                return self.emit(("ref", n[2]), conds)
            raise Refuse(f"DAX: bare column {n[1]}[{n[2]}] in a measure (needs an aggregation)")
        if k == "neg":
            return f"(-{self.emit(n[1], conds)})"
        if k == "not":
            return f"(NOT {self.emit(n[1], conds)})"
        if k == "bin":
            op, a, b = n[1], self.emit(n[2], conds), self.emit(n[3], conds)
            if op == "/":
                return f"({a} / NULLIF({b}, 0))"
            if op in ("&&", "||"):
                return f"({a} {'AND' if op == '&&' else 'OR'} {b})"
            if op == "&":
                raise Refuse("DAX: text concatenation in a measure")
            if op in ("=", "<>") and n[3][0] == "fn" and n[3][1] == "BLANK":
                return f"({a} IS {'NOT ' if op == '<>' else ''}NULL)"
            return f"({a} {op} {b})"
        if k == "fn":
            return self._fn(n[1], n[2], conds)
        raise Refuse(f"DAX: unsupported {k} in a measure")

    def _where(self, conds) -> str:
        return " AND ".join(conds) if conds else "TRUE"

    def _guard(self, conds, value: str) -> str:
        """The value, only on rows the CALCULATE filters keep."""
        return f"CASE WHEN {self._where(conds)} THEN {value} END" if conds else value

    def _unqualified(self, a: list) -> list:
        """SUM([col]) inside a measure: the column of the measure's home table."""
        if len(a) == 1 and a[0][0] == "ref" and self.r.measure(a[0][1]) is None and self.home and self.home[-1]:
            return [("col", self.home[-1], a[0][1])]
        return a

    def _fn(self, f, a, conds) -> str:
        if f in _AGG or f in ("DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "COUNTBLANK"):
            a = self._unqualified(a)
        if f in _AGG and len(a) == 1 and a[0][0] == "col":
            self._use(a[0][1])
            return f"{_AGG[f]}({self._guard(conds, self.r.column(*a[0][1:]))})"
        if f in ("DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK") and len(a) == 1 and a[0][0] == "col":
            self._use(a[0][1])
            return f"COUNT(DISTINCT {self._guard(conds, self.r.column(*a[0][1:]))})"
        if f == "COUNTROWS" and len(a) == 1 and a[0][0] == "tbl":
            self._use(a[0][1])
            self.r.count_rows(a[0][1])
            return f"COUNT({self._guard(conds, '1')})" if conds else "COUNT(*)"
        if f == "COUNTBLANK" and len(a) == 1 and a[0][0] == "col":
            self._use(a[0][1])
            return f"COUNT(CASE WHEN {self._where(conds)} AND {self.r.column(*a[0][1:])} IS NULL THEN 1 END)"
        if f in _AGGX and len(a) == 2 and a[0][0] == "tbl":
            self._use(a[0][1])
            row = RowEmitter(self.r).emit(a[1])
            return f"{_AGGX[f]}(CASE WHEN {self._where(conds)} THEN {row} END)"
        if f == "DIVIDE" and len(a) in (2, 3):
            x, y = self.emit(a[0], conds), self.emit(a[1], conds)
            return f"COALESCE({x} / NULLIF({y}, 0), {self.emit(a[2], conds)})" if len(a) == 3 else f"({x} / NULLIF({y}, 0))"
        if f == "BLANK" and not a:
            return "NULL"
        if f == "ISBLANK" and len(a) == 1:
            return f"({self.emit(a[0], conds)} IS NULL)"
        if f == "IF" and len(a) in (2, 3):
            other = self.emit(a[2], conds) if len(a) == 3 else "NULL"
            return f"(CASE WHEN {self.emit(a[0], conds)} THEN {self.emit(a[1], conds)} ELSE {other} END)"
        if f in ("ABS", "ROUND") and a:
            return f"{f}({', '.join(self.emit(x, conds) for x in a)})"
        if f == "COALESCE" and a:
            return f"COALESCE({', '.join(self.emit(x, conds) for x in a)})"
        if f == "CALCULATE" and a:
            new = list(conds)
            for arg in a[1:]:
                new.extend(self._filter(arg))
            return self.emit(a[0], tuple(new))
        if f in _CTX:
            raise Refuse(f"DAX: context modifier {f}")
        if f in _TIME:
            raise Refuse(f"DAX: time intelligence {f}")
        if f in ("SELECTEDVALUE", "HASONEVALUE", "ISFILTERED", "ISINSCOPE", "VALUES", "FILTERS"):
            raise Refuse(f"DAX: {f} reads the visual's filter state")
        raise Refuse(f"DAX: unsupported function {f}")

    def _filter(self, arg) -> list[str]:
        """A CALCULATE filter argument as row predicates on the fact."""
        if arg[0] == "fn" and arg[1] == "KEEPFILTERS" and len(arg[2]) == 1:
            return self._filter(arg[2][0])
        if arg[0] == "fn" and arg[1] == "FILTER" and len(arg[2]) == 2 and arg[2][0][0] == "tbl":
            self._use(arg[2][0][1])
            return [RowEmitter(self.r).emit(arg[2][1])]
        if arg[0] in ("bin", "in", "not") and not _mentions_measure(arg):
            # The filter's tables do not fix the fact: a filter on a dimension
            # reaches the fact along a relationship, which the tile resolves.
            if not _tables(arg):
                raise Refuse("DAX: a CALCULATE filter that names no column")
            return [RowEmitter(self.r).emit(arg)]
        if arg[0] == "fn" and arg[1] in _CTX:
            raise Refuse(f"DAX: context modifier {arg[1]}")
        if arg[0] == "fn" and arg[1] in _TIME:
            raise Refuse(f"DAX: time intelligence {arg[1]}")
        raise Refuse(f"DAX: unsupported CALCULATE filter {arg[1] if arg[0] == 'fn' else arg[0]}")


def _tables(node) -> set[str]:
    if not isinstance(node, tuple):
        return set()
    if node[0] == "col":
        return {node[1]}
    out: set[str] = set()
    for x in node[1:]:
        if isinstance(x, tuple):
            out |= _tables(x)
        elif isinstance(x, list):
            for y in x:
                out |= _tables(y)
    return out


def _mentions_measure(node) -> bool:
    if not isinstance(node, tuple):
        return False
    if node[0] == "ref":
        return True
    return any(_mentions_measure(x) for x in node[1:] if isinstance(x, tuple)) or any(
        _mentions_measure(y) for x in node[1:] if isinstance(x, list) for y in x)


# ---------------------------------------------------------------- calculated tables

def datatable(src: str) -> tuple[list[str], list[list[str]]] | None:
    """DATATABLE("Col", TYPE, ..., {{v, ...}, ...}) -> (columns, rows of SQL
    literals); None when the table is not a DATATABLE."""
    try:
        n = parse(src)
    except Refuse:
        return None
    if not (n[0] == "fn" and n[1] == "DATATABLE" and n[2] and n[2][-1][0] == "set"):
        return None
    heads = n[2][:-1]
    cols = [h[1] for h in heads[0::2] if h[0] == "str"]
    try:
        rows = [[lit(v) for v in row] for row in n[2][-1][1]]
    except Refuse:
        return None                             # a value that is not a plain literal (DATE(…))
    if not cols or any(len(r) != len(cols) for r in rows):
        return None
    return cols, rows


def date_table(src: str) -> dict[str, object] | None:
    """A calendar table: CALENDAR/CALENDARAUTO, optionally wrapped in
    ADDCOLUMNS. Returns {"Date": None, "<attr>": <row node over [Date]>}, or
    None when the expression is not a calendar."""
    try:
        n = parse(src)
    except Refuse:
        return None
    attrs: dict[str, object] = {}
    if n[0] == "fn" and n[1] == "ADDCOLUMNS" and n[2]:
        base, rest = n[2][0], n[2][1:]
        for name, expr in zip(rest[0::2], rest[1::2]):
            if name[0] == "str":
                attrs[name[1]] = expr
        n = base
    if n[0] == "fn" and n[1] in ("CALENDAR", "CALENDARAUTO"):
        return {"Date": None, **attrs}
    return None


def field_parameter(src: str) -> dict[str, tuple[str, str]] | None:
    """A field parameter table {("Label", NAMEOF('T'[c]), 0), ...} -> {label:
    (table, column)}."""
    try:
        n = parse(src)
    except Refuse:
        return None
    if n[0] != "set":
        return None
    out = {}
    for row in n[1]:
        if len(row) >= 2 and row[0][0] == "str" and row[1][0] == "fn" and row[1][1] == "NAMEOF" \
                and row[1][2] and row[1][2][0][0] == "col":
            out[row[0][1]] = (row[1][2][0][1], row[1][2][0][2])
    return out or None
