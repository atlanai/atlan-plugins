"""Tableau's calculation language -> SQL, or a refusal that says why.

A calculation is not SQL in another dialect. The regex rewriter this replaces
got semantics wrong in ways that run and return a plausible number: `IIF`
became `IFF` (a NULL test takes the ELSE branch in SQL, returns NULL in
Tableau), `ZN(x)` became one-argument `COALESCE`, text `+` stayed arithmetic,
`x / 0` raised instead of returning NULL, `INT(-2.7)` rounded instead of
truncating, and `ISNULL(x)` became the unparseable `IS NULL(x)`.

So a formula is tokenized and parsed with Tableau's documented precedence,
references are resolved recursively through the datasource model, and every
node is translated by a rule whose semantics are stated next to it. Anything
without a rule raises `Refusal`; a formula translated wrongly would yield a
golden SQL that runs and is quietly wrong.

A FIXED level-of-detail expression, `{FIXED dims : agg}` or the table-scoped
`{agg}`, becomes a window column, `AGG(expr) OVER (PARTITION BY dims)`, that
`sql.py` computes on a row stage after the extract, datasource and context
filters and before the sheet's dimension filters: where Tableau computes it.
The body is translated with the partition as its window, so every aggregate in
it is windowed, and a FIXED nested inside another gets a stage of its own.
Aggregating a pure FIXED value (built from FIXED values and constants only) is
the grain-sensitive case: Tableau aggregates it at the LOD's grain, not per
row, so it is allowed under MIN, MAX, COUNTD and ATTR, or when every LOD
dimension is a view dimension (one value per mark, emitted as MIN), and refused
otherwise. Refused outright, because each
needs another query shape: INCLUDE and EXCLUDE expressions, table calculations,
sets, bins, blends, regex, locale-dependent parsing or formatting, scripts, and
user functions (USERNAME, ISMEMBEROF: row-level security, raised as
`ViewerDependent` so a filter built on one is recorded rather than applied).

Translated text is written in one house dialect (Snowflake's spelling, which
sqlglot reads exactly) and converted to the warehouse's once, when the tile SQL
is assembled. Where that spelling would convert into something wrong or missing
elsewhere, the rule uses a portable form: weekday arithmetic by DATEDIFF from a
date of known weekday, CONTAINS by POSITION, two-argument MIN/MAX with an
explicit NULL test (Redshift's LEAST ignores NULLs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect, NormalizationStrategy

from eval_builder.sources.tableau.model import DS, FieldDef, Relation

HOUSE = "snowflake"         # the dialect translated text is written in
OBJ = "[__tableau_internal_object_id__]."
LOD_TABLE = "__lod__"       # the alias FIXED values are read from until the stages are built
_LOD_REF = re.compile(rf"\b{LOD_TABLE}\.(lod_\d+)\b")


class Refusal(Exception):
    """A construct with no rule. The message is the reason a reviewer reads."""


class ViewerDependent(Refusal):
    """USERNAME()/ISMEMBEROF() and friends: the answer depends on the viewer."""


# ---------------------------------------------------------------------------
# Tokenizer and parser
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    (?P<ws>\s+) | (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<field>\[(?:[^\]]|\]\])*\](?:\.\[(?:[^\]]|\]\])*\])*)
  | (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
  | (?P<date>\#[^#]*\#)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|<>|!=|==|=|<|>|\+|-|\*|/|%|\^|\(|\)|,|\{|\}|:)
""", re.X | re.S)
KEYWORDS = {"IF", "THEN", "ELSEIF", "ELSE", "END", "CASE", "WHEN", "AND", "OR", "NOT", "IN",
            "TRUE", "FALSE", "NULL", "FIXED", "INCLUDE", "EXCLUDE"}


class Parser:
    """Nodes are tuples: ("lit", kind, value), ("field", ref), ("neg", x),
    ("not", x), ("bin", op, a, b), ("in", x, [items]), ("if", [(c, v)], else),
    ("case", subject, [(w, v)], else), ("call", NAME, [args]),
    ("lod", kind, [dims], body, source text)."""

    BIN = {"OR": 1, "AND": 2, "=": 4, "==": 4, "!=": 4, "<>": 4, "<": 4, "<=": 4, ">": 4,
           ">=": 4, "IN": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6, "^": 7}

    def __init__(self, src: str):
        self.src, self.toks, self.spans, pos = src, [], [], 0
        while pos < len(src):
            m = _TOKEN.match(src, pos)
            if not m:
                raise Refusal(f"unparseable formula near {src[pos:pos + 20]!r}")
            kind, text = m.lastgroup, m.group()
            if kind not in ("ws", "comment"):
                is_kw = kind == "ident" and text.upper() in KEYWORDS
                self.toks.append(("kw", text.upper()) if is_kw else (kind, text))
                self.spans.append((m.start(), m.end()))
            pos = m.end()
        self.toks.append(("eof", ""))
        self.spans.append((len(src), len(src)))
        self.i = 0

    def peek(self):
        return self.toks[self.i]

    def take(self, kind, text=None):
        t = self.toks[self.i]
        if t[0] != kind or (text and t[1].upper() != text):
            raise Refusal(f"unparseable formula: expected {text or kind}, got {t[1]!r}")
        self.i += 1
        return t

    def parse(self):
        node = self.expr(0)
        if self.peek()[0] != "eof":
            raise Refusal(f"unparseable formula: trailing {self.peek()[1]!r}")
        return node

    def _list(self):
        items = [self.expr(0)]
        while self.peek() == ("op", ","):
            self.i += 1
            items.append(self.expr(0))
        return items

    def expr(self, min_prec):
        left = self.prefix()
        while True:
            t = self.peek()
            op = t[1] if (t[0] == "kw" and t[1] in ("AND", "OR", "IN")) or (
                t[0] == "op" and t[1] in self.BIN) else None
            if op is None or self.BIN[op] < min_prec:
                return left
            self.i += 1
            if op == "IN":
                self.take("op", "(")
                left = ("in", left, self._list())
                self.take("op", ")")
                continue
            prec = self.BIN[op]
            left = ("bin", op, left, self.expr(prec if op == "^" else prec + 1))

    def prefix(self):
        k, v = self.peek()
        self.i += 1
        if k == "num":
            return ("lit", "num", v)
        if k == "str":
            return ("lit", "str", v[1:-1].replace(v[0] * 2, v[0]))
        if k == "date":
            return ("lit", "date", v.strip("#"))
        if k == "field":
            return ("field", v)
        if k == "kw" and v in ("TRUE", "FALSE"):
            return ("lit", "bool", v == "TRUE")
        if k == "kw" and v == "NULL":
            return ("lit", "null", None)
        if k == "kw" and v == "NOT":
            return ("not", self.expr(3))
        if (k, v) == ("op", "-"):
            return ("neg", self.expr(8))
        if (k, v) == ("op", "+"):
            return self.expr(8)
        if (k, v) == ("op", "("):
            e = self.expr(0)
            self.take("op", ")")
            return e
        self.i -= 1
        if k == "kw" and v == "IF":
            return self.if_()
        if k == "kw" and v == "CASE":
            return self.case_()
        if (k, v) == ("op", "{"):
            return self.lod()
        if k == "ident" and self.toks[self.i + 1] == ("op", "("):
            self.i += 2
            args = [] if self.peek() == ("op", ")") else self._list()
            self.take("op", ")")
            return ("call", v.upper(), args)
        raise Refusal(f"unparseable formula at {v!r}")

    def _branches(self, opener: str):
        out, other = [], None
        while True:
            t = self.peek()
            if t == ("kw", opener):
                self.i += 1
                c = self.expr(0)
                self.take("kw", "THEN")
                out.append((c, self.expr(0)))
            elif t == ("kw", "ELSE"):
                self.i += 1
                other = self.expr(0)
            elif t == ("kw", "END"):
                self.i += 1
                return out, other
            else:
                raise Refusal(f"unparseable formula: {t[1]!r} inside {opener}")

    def if_(self):
        self.take("kw", "IF")
        cond = self.expr(0)
        self.take("kw", "THEN")
        first = (cond, self.expr(0))
        rest, other = self._branches("ELSEIF")
        return ("if", [first] + rest, other)

    def case_(self):
        self.take("kw", "CASE")
        subject = self.expr(0)
        whens, other = self._branches("WHEN")
        return ("case", subject, whens, other)

    def lod(self):
        first = self.i
        self.take("op", "{")
        kind, dims = "FIXED", []
        if self.peek()[0] == "kw" and self.peek()[1] in ("FIXED", "INCLUDE", "EXCLUDE"):
            kind = self.take("kw")[1]
            if self.peek() != ("op", ":"):
                dims = self._list()
            self.take("op", ":")
        body = self.expr(0)
        self.take("op", "}")
        # The expression's own text for the refusal reason, comments left out.
        text = "".join((" " if j > first and self.spans[j][0] > self.spans[j - 1][1] else "")
                       + self.src[slice(*self.spans[j])] for j in range(first, self.i))
        return ("lod", kind, dims, body, text)


def parse_formula(src: str):
    return Parser(src).parse()


# ---------------------------------------------------------------------------
# Typed SQL fragments and the resolution context
# ---------------------------------------------------------------------------

NUMERIC, TEMPORAL = {"int", "real"}, {"date", "datetime"}
DATE_UNITS = {"year", "quarter", "month", "day", "hour", "minute", "second"}
TABLE_CALC_FUNCS = {
    "RUNNING_SUM", "RUNNING_AVG", "RUNNING_COUNT", "RUNNING_MAX", "RUNNING_MIN",
    "WINDOW_SUM", "WINDOW_AVG", "WINDOW_COUNT", "WINDOW_MAX", "WINDOW_MIN", "WINDOW_MEDIAN",
    "WINDOW_STDEV", "WINDOW_STDEVP", "WINDOW_VAR", "WINDOW_VARP", "WINDOW_PERCENTILE",
    "WINDOW_CORR", "WINDOW_COVAR", "WINDOW_COVARP", "INDEX", "RANK", "RANK_DENSE",
    "RANK_MODIFIED", "RANK_PERCENTILE", "RANK_UNIQUE", "LOOKUP", "TOTAL", "FIRST", "LAST",
    "SIZE", "PREVIOUS_VALUE"}
USER_FUNCS = {"USERNAME", "FULLNAME", "ISMEMBEROF", "USERDOMAIN", "ISFULLNAME", "ISUSERNAME",
              "USERATTRIBUTE", "USERATTRIBUTEINCLUDES"}
LOCALE_FUNCS = {"DATENAME", "DATEPARSE", "MAKEDATE", "MAKEDATETIME", "MAKETIME", "ISDATE"}
AGGREGATES = {"SUM", "AVG", "COUNT", "COUNTD", "MIN", "MAX", "ATTR"}
# (function, arity) -> (SQL over the translated arguments, result type; None keeps
# the first argument's). Each is a function whose Tableau and SQL meanings agree.
TEMPLATES = {
    ("ABS", 1): ("ABS({0})", None), ("FLOOR", 1): ("FLOOR({0})", "int"),
    ("CEILING", 1): ("CEIL({0})", "int"), ("UPPER", 1): ("UPPER({0})", "str"),
    ("LOWER", 1): ("LOWER({0})", "str"), ("TRIM", 1): ("TRIM({0})", "str"),
    ("LEN", 1): ("LENGTH({0})", "int"), ("ROUND", 1): ("ROUND({0})", "real"),
    ("ROUND", 2): ("ROUND({0}, {1})", "real"), ("YEAR", 1): ("DATE_PART(year, {0})", "int"),
    ("QUARTER", 1): ("DATE_PART(quarter, {0})", "int"), ("MONTH", 1): ("DATE_PART(month, {0})", "int"),
    ("DAY", 1): ("DATE_PART(day, {0})", "int"), ("ZN", 1): ("COALESCE({0}, 0)", None),
    ("IFNULL", 2): ("COALESCE({0}, {1})", None), ("ISNULL", 1): ("({0} IS NULL)", "bool"),
    ("CONTAINS", 2): ("(POSITION({1} IN {0}) > 0)", "bool"),
    ("LEFT", 2): ("LEFT({0}, {1})", "str"), ("RIGHT", 2): ("RIGHT({0}, {1})", "str"),
    # Tableau's two-argument MIN/MAX is NULL when either side is; Redshift's LEAST is not.
    ("MIN", 2): ("CASE WHEN {0} IS NULL OR {1} IS NULL THEN NULL ELSE LEAST({0}, {1}) END", None),
    ("MAX", 2): ("CASE WHEN {0} IS NULL OR {1} IS NULL THEN NULL ELSE GREATEST({0}, {1}) END", None),
}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def norm_type(t: str | None) -> str:
    return {"integer": "int", "real": "real", "string": "str", "boolean": "bool", "date": "date",
            "datetime": "datetime", "table": "objectid"}.get((t or "").lower(), "unknown")


@dataclass
class TExpr:
    sql: str                    # house-dialect text
    dtype: str = "unknown"      # int | real | str | bool | date | datetime | null | objectid
    agg: bool = False
    const: bool = False
    lit: object = None          # the Python value, when this is a literal


def sql_str(v: str) -> str:
    return exp.Literal.string(v).sql(HOUSE)


def ident(name: str, dialect: str | None) -> str:
    """Unquoted when the warehouse would fold it to the same name, quoted otherwise."""
    strategy = Dialect.get_or_raise(dialect).normalization_strategy
    folded = {NormalizationStrategy.UPPERCASE: name.upper(),
              NormalizationStrategy.LOWERCASE: name.lower()}.get(strategy, name)
    plain = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and folded == name
    return exp.to_identifier(name, quoted=not plain).sql(HOUSE)


def literal(kind: str, v) -> TExpr:
    if kind == "num":
        txt = v + "0" if v.endswith(".") else v
        real = "." in txt or "e" in txt.lower()
        return TExpr(txt, "real" if real else "int", const=True, lit=float(txt) if real else int(txt))
    if kind == "str":
        return TExpr(sql_str(v), "str", const=True, lit=v)
    if kind == "bool":
        return TExpr("TRUE" if v else "FALSE", "bool", const=True, lit=bool(v))
    if kind == "null":
        return TExpr("NULL", "null", const=True)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return TExpr(f"DATE {sql_str(v)}", "date", const=True, lit=v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?", v):
        return TExpr(f"TIMESTAMP {sql_str(v)}", "datetime", const=True, lit=v)
    raise Refusal(f"unsupported expression: date literal #{v}# is not ISO, its parse is locale-dependent")


def tableau_literal(text: str) -> TExpr:
    """A parameter value, group member or filter bound in Tableau literal syntax."""
    node = parse_formula(text)
    if node[0] == "lit":
        return literal(node[1], node[2])
    if node[0] == "neg" and node[1][:2] == ("lit", "num"):
        t = literal("num", node[1][2])
        return TExpr("-" + t.sql, t.dtype, const=True, lit=-t.lit)
    raise Refusal(f"unsupported expression: {text!r} is not a literal")


class Layer:
    """Where a bracketed name resolves: a datasource, the worksheet's own column
    copies (which hold ad-hoc calculations and win), and the published datasource
    behind a `sqlproxy`."""

    def __init__(self, ds: DS, params: dict, published: DS | None = None,
                 deps: dict | None = None, label: str = "workbook"):
        self.ds, self.params, self.published, self.deps, self.label = (
            ds, params, published, deps or {}, label)
        self._pub: Layer | None = None

    def published_layer(self) -> "Layer | None":
        if self.published is not None and self._pub is None:
            self._pub = Layer(self.published, self.published.params or self.params, label="published")
        return self._pub

    def lookup(self, name: str) -> FieldDef | None:
        fd = self.ds.field(name)
        dep = self.deps.get(name)
        if dep is None:
            return fd
        fd = fd or FieldDef(name=name, kind="unknown", element=dep)
        fd.caption = dep.get("caption") or fd.caption
        fd.datatype = dep.get("datatype") or fd.datatype
        fd.role = dep.get("role") or fd.role
        calc = dep.find("calculation")
        if calc is not None and calc.get("class") == "tableau" and calc.get("formula") is not None:
            fd.kind, fd.formula = "calc", calc.get("formula")
        elif calc is not None and calc.get("class") == "categorical-bin":
            fd.kind, fd.element = "group", dep
        return fd


@dataclass
class LOD:
    """A FIXED expression as a window column of the row stage (`sql.py`)."""

    name: str                   # lod_<n>: the column's name in the stage
    sql: str                    # house-dialect window expression over `src` and earlier LODs
    partition: list[str]        # house-dialect PARTITION BY keys; [] for the whole table
    depth: int                  # 1 + the depth of the deepest FIXED it reads
    text: str                   # the expression's own Tableau text, for reasons and notes
    dtype: str = "unknown"


@dataclass
class Ctx:
    """What one tile's translation touched, shared by all of its fields."""

    week_start: str | None = None
    dialect: str | None = None                      # the dialect the tile SQL is emitted in
    lods: list = field(default_factory=list)        # FIXED expressions (LOD), in creation order
    view_dims: list | None = None                   # house SQL of the tile's dimensions, once known
    alias_of: dict = field(default_factory=dict)    # (id(ds), relation) -> alias
    relations: dict = field(default_factory=dict)   # (id(ds), relation) -> (DS, Relation)
    memo: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)      # parameters read, at their saved value
    flags: set = field(default_factory=set)         # needs_as_of
    notes: list = field(default_factory=list)       # semantics a reviewer should know
    chain: list = field(default_factory=list)       # lineage hops of the field in hand
    physical: list = field(default_factory=list)    # warehouse columns it reads

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def relation_alias(ctx: Ctx, ds: DS, rel: Relation) -> str:
    """`r_<name>` for each leaf of a join tree, `src` otherwise: a relationship
    model is only rendered when the tile reads one of its tables."""
    key = (id(ds), rel.name)
    if key not in ctx.alias_of:
        name = re.sub(r"[^0-9a-z]+", "_", (rel.name or "rel").lower()).strip("_")
        ctx.alias_of[key] = f"r_{name}" if ds.top is not None and ds.top.kind == "join" else "src"
        ctx.relations[key] = (ds, rel)
    return ctx.alias_of[key]


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

class Translator:
    """`window` is None outside a FIXED body; inside one it is the body's
    PARTITION BY list, and every aggregate becomes a window aggregate."""

    def __init__(self, ctx: Ctx, window: list[str] | None = None, stack: list[str] | None = None):
        self.ctx = ctx
        self.window = window
        self.stack: list[str] = stack if stack is not None else []   # captions being translated

    def where(self) -> str:
        return self.stack[-1] if self.stack else "?"

    # -- references ----------------------------------------------------------

    def field(self, name: str, layer: Layer) -> TExpr:
        ctx = self.ctx
        if name.startswith("[Parameters]."):
            pname = name[len("[Parameters]."):]
            p = layer.params.get(pname)
            if p is None or p.get("value") is None:
                raise Refusal(f"unresolved field: parameter {pname} has no saved value")
            ctx.params[pname] = {"caption": p.get("caption") or pname, "value": p["value"]}
            t = tableau_literal(p["value"])
            return t if t.dtype != "unknown" else TExpr(t.sql, norm_type(p.get("datatype")), const=True, lit=t.lit)
        if name.startswith(OBJ) or self._is_object(name, layer):
            return self.row_count(name, layer)
        if re.match(r"^\[[^\]]+\]\.\[", name):
            raise Refusal(f"blend: {name} is a field of another datasource")
        # The same calculation reads differently inside a FIXED body (its
        # aggregates are windowed), so the window is part of the memo key.
        key = (id(layer.ds), layer.label, name, None if self.window is None else tuple(self.window))
        if key in ctx.memo:
            if ctx.memo[key] is None:
                raise Refusal(f"unresolved field: calculation {name} refers to itself")
            t, chain, physical = ctx.memo[key]
            ctx.chain.extend(chain)
            ctx.physical.extend(physical)
            return t
        fd = layer.lookup(name)
        if fd is None:
            raise Refusal(f"unresolved field: {name} is not defined in the {layer.label} datasource")
        if fd.kind == "calc":
            return self.calc(fd, layer, key)
        if fd.kind == "physical":
            return self.column(fd, layer)
        if fd.kind == "group":
            return self.group(fd, layer)
        if fd.kind == "set":
            raise Refusal(f'set "{fd.label}": membership is computed by Tableau (top-N or condition)')
        if fd.kind == "bin":
            raise Refusal(f'bin "{fd.label}"')
        raise Refusal(f'unresolved field: "{fd.label}" is a {fd.kind}')

    def calc(self, fd: FieldDef, layer: Layer, key) -> TExpr:
        ctx = self.ctx
        ctx.memo[key] = None                    # cycle marker
        c0, p0 = len(ctx.chain), len(ctx.physical)
        ctx.chain.append({"hop": "calculation", "field": fd.name, "caption": fd.caption,
                          "formula": fd.formula})
        self.stack.append(fd.label)
        try:
            t = self.tr(parse_formula(fd.formula), layer)
        except Refusal:
            del ctx.memo[key]
            raise
        finally:
            self.stack.pop()
        declared = norm_type(fd.datatype)
        out = TExpr(f"({t.sql})", t.dtype if t.dtype != "unknown" else declared, t.agg, t.const, t.lit)
        if (fd.customized and declared != "unknown" and out.dtype not in ("unknown", "null", declared)
                and not (out.dtype == "int" and declared == "real")):
            out = TExpr(type_override(out.sql, out.dtype, declared, fd.label, ctx), declared, out.agg)
        ctx.memo[key] = (out, ctx.chain[c0:], ctx.physical[p0:])
        return out

    def column(self, fd: FieldDef, layer: Layer) -> TExpr:
        ctx = self.ctx
        if layer.ds.kind == "sqlproxy":
            pub = layer.published_layer()
            if pub is None:
                raise Refusal(f"published datasource {layer.ds.repo_id} was not provided")
            target = f"[{fd.remote}]"
            if pub.ds.field(target) is None:
                raise Refusal(f"published datasource {layer.ds.repo_id} lacks field {target}")
            ctx.chain.append({"hop": "published", "field": fd.name, "remote": fd.remote,
                              "datasource": layer.ds.repo_id})
            t = self.field(target, pub)
            return t if t.dtype != "unknown" else TExpr(t.sql, norm_type(fd.datatype), t.agg, t.const, t.lit)
        rel = layer.ds.relations.get(fd.parent)
        if rel is None:
            raise Refusal(f"unresolved field: relation [{fd.parent}] of {fd.name} is not in the datasource")
        dialect = layer.ds.dialect()
        hop = {"hop": "column", "field": fd.name, "caption": fd.caption, "relation": rel.name,
               "relation_kind": rel.kind, "remote": fd.remote}
        if rel.kind == "text":
            hop["sql_expression"], base, _ = rel.lineage(fd.remote, dialect)
        else:
            base = [f"{rel.table_name}.{fd.remote}"] if rel.table_name else []
        hop["warehouse_columns"] = base
        ctx.chain.append(hop)
        ctx.physical.extend(base)
        col = f"{relation_alias(ctx, layer.ds, rel)}.{ident(fd.remote, dialect)}"
        src_t, tab_t = norm_type(fd.source_type), norm_type(fd.datatype)
        if fd.customized and src_t not in ("unknown", tab_t):
            col = type_override(col, src_t, tab_t, fd.label, ctx)
        return TExpr(col, tab_t)

    def row_count(self, name: str, layer: Layer) -> TExpr:
        """`[__tableau_internal_object_id__].[id]` or a bare object id: the rows of
        a logical table. Only COUNT may consume it."""
        target = layer.ds
        if layer.ds.kind == "sqlproxy":
            if layer.published is None:
                raise Refusal(f"published datasource {layer.ds.repo_id} was not provided")
            target = layer.published
        leaves = target.top.leaves() if target.top else []
        obj = name.split("].[", 1)[1][:-1] if "].[" in name else name[1:-1]
        rel = leaves[0] if len(leaves) == 1 else target.relations.get(target.objects.get(obj) or "")
        if rel is None:
            raise Refusal("unresolved field: row count of a logical table that cannot be identified")
        base = [f"{t}.*" for t in rel.tables(target.dialect())]
        self.ctx.chain.append({"hop": "row count", "relation": rel.name, "warehouse_columns": base})
        self.ctx.physical.extend(base)
        return TExpr(f"{relation_alias(self.ctx, target, rel)}.*", "objectid")

    @staticmethod
    def _is_object(name: str, layer: Layer) -> bool:
        inner = name[1:-1] if name.startswith("[") and "].[" not in name else None
        return bool(inner) and any(
            d is not None and inner in d.objects and d.field(name) is None
            for d in (layer.ds, layer.published))

    def group(self, fd: FieldDef, layer: Layer) -> TExpr:
        """A group (categorical-bin): listed members take the group's name,
        everything else keeps its own value."""
        calc = fd.element.find("calculation")
        if set(calc.attrib) - {"class", "column", "new-bin"}:
            raise Refusal(f'unsupported expression: group "{fd.label}" with options {sorted(set(calc.attrib) - {"class", "column", "new-bin"})}')
        src = self.field(calc.get("column"), layer)
        whens = []
        for b in calc.findall("bin"):
            if set(b.attrib) - {"default-name", "value"}:
                raise Refusal(f'unsupported expression: group "{fd.label}" has a bin with options')
            members = [tableau_literal(v.text or "") for v in b.findall("value")]
            label = tableau_literal(b.get("value") or "")
            whens.append(f"WHEN {src.sql} IN ({', '.join(m.sql for m in members)}) THEN {label.sql}")
        return TExpr(f"(CASE {' '.join(whens)} ELSE {src.sql} END)", src.dtype, src.agg)

    # -- expressions ---------------------------------------------------------

    def tr(self, node, layer: Layer) -> TExpr:
        k = node[0]
        if k == "lit":
            return literal(node[1], node[2])
        if k == "field":
            return self.field(node[1], layer)
        if k == "neg":
            x = self.tr(node[1], layer)
            if x.dtype not in NUMERIC | {"unknown"}:
                raise Refusal("unsupported expression: negation of a non-number")
            return TExpr(f"(-{x.sql})", x.dtype, x.agg, x.const, -x.lit if x.const and x.lit is not None else None)
        if k == "not":
            x = self.tr(node[1], layer)
            return TExpr(f"(NOT {x.sql})", "bool", x.agg, x.const,
                         (not x.lit) if isinstance(x.lit, bool) else None)
        if k == "bin":
            return self.binop(node[1], self.tr(node[2], layer), self.tr(node[3], layer))
        if k == "in":
            x, items = self.tr(node[1], layer), [self.tr(i, layer) for i in node[2]]
            return TExpr(f"({x.sql} IN ({', '.join(i.sql for i in items)}))", "bool",
                         x.agg or any(i.agg for i in items))
        if k == "if":
            return self.if_(node, layer)
        if k == "case":
            return self.case_(node, layer)
        if k == "lod":
            return self.lod(node, layer)
        if k == "call":
            return self.call(node[1], node[2], layer)
        raise Refusal(f"unparseable formula: node {k}")

    def lod(self, node, layer: Layer) -> TExpr:
        """`{FIXED dims : agg}` -> a reference to a window column of the row
        stage, `__lod__.lod_n`, row-level to the enclosing expression. One
        column per distinct (body, partition); a FIXED read by another's body
        or dimensions is one stage deeper. INCLUDE and EXCLUDE work at the
        view's grain after the dimension filters and need a grouped side query,
        so they are refused."""
        kind, text = node[1], " ".join(node[4].split())
        short = text if len(text) <= 200 else text[:199] + "…"
        if kind != "FIXED":
            raise Refusal(f'LOD {kind} in "{self.where()}": {short}')
        ctx, partition = self.ctx, []
        for d in node[2]:
            dt = Translator(ctx, None, self.stack).tr(d, layer)
            if dt.agg:
                raise Refusal(f'LOD FIXED in "{self.where()}": a dimension of {short} is an aggregate')
            if not dt.const:                # a constant partitions nothing
                partition.append(dt.sql)
        body = Translator(ctx, partition, self.stack).tr(node[3], layer)
        if not any(True for _ in sqlglot.parse_one(body.sql, read=HOUSE).find_all(exp.Window)):
            raise Refusal(f'LOD FIXED in "{self.where()}": the body of {short} has no aggregate')
        for l in ctx.lods:
            if l.sql == body.sql and l.partition == partition:
                return TExpr(f"{LOD_TABLE}.{l.name}", l.dtype)
        refs = lod_refs(body.sql + " " + " ".join(partition))
        depth = 1 + max((l.depth for l in ctx.lods if l.name in refs), default=0)
        made = LOD(f"lod_{len(ctx.lods) + 1}", body.sql, partition, depth, text, body.dtype)
        ctx.lods.append(made)
        ctx.note("FIXED expressions are window columns computed after the extract, datasource and "
                 "context filters and before the sheet's dimension filters (Tableau's order of operations)")
        return TExpr(f"{LOD_TABLE}.{made.name}", body.dtype)

    def if_(self, node, layer: Layer) -> TExpr:
        # A test that folds to a constant (a parameter at its saved value) decides
        # here, as Tableau does, so a dead branch cannot block the tile: a FALSE
        # branch can never be taken, and a TRUE one ends the chain.
        parts, agg, dtype, other_node = [], False, "unknown", node[2]
        for c, v in node[1]:
            ct = self.tr(c, layer)
            if ct.const and isinstance(ct.lit, bool):
                if not ct.lit:
                    continue
                if not parts:
                    return self.tr(v, layer)
                other_node = v
                break
            vt = self.tr(v, layer)
            parts.append(f"WHEN {ct.sql} THEN {vt.sql}")
            agg = agg or ct.agg or vt.agg
            dtype = dtype if dtype not in ("unknown", "null") else vt.dtype
        if not parts:
            return self.tr(other_node, layer) if other_node is not None else literal("null", None)
        other = ""
        if other_node is not None:
            et = self.tr(other_node, layer)
            other, agg = f" ELSE {et.sql}", agg or et.agg
            dtype = dtype if dtype not in ("unknown", "null") else et.dtype
        return TExpr(f"CASE {' '.join(parts)}{other} END", dtype, agg)

    def case_(self, node, layer: Layer) -> TExpr:
        st = self.tr(node[1], layer)
        whens = [(self.tr(w, layer), v) for w, v in node[2]]
        if st.const and st.lit is not None and all(w.const for w, _ in whens):
            for w, v in whens:
                if w.lit == st.lit and isinstance(w.lit, bool) == isinstance(st.lit, bool):
                    return self.tr(v, layer)
            return self.tr(node[3], layer) if node[3] is not None else literal("null", None)
        parts, agg, dtype = [], st.agg, "unknown"
        for wt, v in whens:
            vt = self.tr(v, layer)
            parts.append(f"WHEN {wt.sql} THEN {vt.sql}")
            agg = agg or vt.agg
            dtype = dtype if dtype not in ("unknown", "null") else vt.dtype
        other = ""
        if node[3] is not None:
            et = self.tr(node[3], layer)
            other, agg = f" ELSE {et.sql}", agg or et.agg
        return TExpr(f"CASE {st.sql} {' '.join(parts)}{other} END", dtype, agg)

    def binop(self, op: str, a: TExpr, b: TExpr) -> TExpr:
        agg, const, ta, tb = a.agg or b.agg, a.const and b.const, a.dtype, b.dtype
        if op in ("AND", "OR"):
            lit = None
            if const and isinstance(a.lit, bool) and isinstance(b.lit, bool):
                lit = (a.lit and b.lit) if op == "AND" else (a.lit or b.lit)
            return TExpr(f"({a.sql} {op} {b.sql})", "bool", agg, const, lit)
        if op in ("=", "==", "!=", "<>", "<", "<=", ">", ">="):
            if {ta, tb} & {"str"} and {ta, tb} & NUMERIC:
                raise Refusal("unsupported expression: comparison of a string with a number")
            sop = {"==": "=", "!=": "<>"}.get(op, op)
            lit = None
            if const and ta == tb and a.lit is not None and b.lit is not None:
                lit = {"=": a.lit == b.lit, "<>": a.lit != b.lit, "<": a.lit < b.lit,
                       "<=": a.lit <= b.lit, ">": a.lit > b.lit, ">=": a.lit >= b.lit}[sop]
            return TExpr(f"({a.sql} {sop} {b.sql})", "bool", agg, const, lit)
        num = "real" if "real" in (ta, tb) else "int"
        if op == "+":
            if "str" in (ta, tb) and {ta, tb} <= {"str", "unknown", "null"}:   # + concatenates text
                return TExpr(f"({a.sql} || {b.sql})", "str", agg, const)
            if ta in NUMERIC and tb in NUMERIC:
                return TExpr(f"({a.sql} + {b.sql})", num, agg, const)
            if ta in TEMPORAL and tb in NUMERIC:
                return TExpr(f"DATEADD(day, {b.sql}, {a.sql})", ta, agg, const)
            if tb in TEMPORAL and ta in NUMERIC:
                return TExpr(f"DATEADD(day, {a.sql}, {b.sql})", tb, agg, const)
            raise Refusal(f"unsupported expression: '+' on {ta}/{tb}, concatenation or addition cannot be decided")
        if op == "-":
            if ta in NUMERIC and tb in NUMERIC:
                return TExpr(f"({a.sql} - {b.sql})", num, agg, const)
            if ta in TEMPORAL and tb in NUMERIC:
                return TExpr(f"DATEADD(day, -({b.sql}), {a.sql})", ta, agg, const)
            if ta == "date" and tb == "date":
                return TExpr(f"DATEDIFF(day, {b.sql}, {a.sql})", "int", agg, const)
            raise Refusal(f"unsupported expression: '-' on {ta}/{tb}")
        if op == "*" and {ta, tb} <= NUMERIC | {"unknown"}:
            return TExpr(f"({a.sql} * {b.sql})", num if "unknown" not in (ta, tb) else "unknown", agg, const)
        if op == "/":                       # float division; x / 0 is NULL in Tableau
            return TExpr(f"({a.sql} / NULLIF({b.sql}, 0))", "real", agg, const)
        if op == "%" and {ta, tb} <= {"int"}:
            return TExpr(f"MOD({a.sql}, {b.sql})", "int", agg, const)
        if op == "^":
            return TExpr(f"POWER({a.sql}, {b.sql})", "real", agg, const)
        raise Refusal(f"unsupported expression: operator {op} on {ta}/{tb}")

    # -- functions -----------------------------------------------------------

    def call(self, fn: str, raw: list, layer: Layer) -> TExpr:
        ctx = self.ctx
        if fn in TABLE_CALC_FUNCS:
            raise Refusal(f'table calculation {fn} in "{self.where()}"')
        if fn in USER_FUNCS:
            raise ViewerDependent(f'user function {fn} in "{self.where()}": row-level security, depends on the viewer')
        if fn in LOCALE_FUNCS:
            raise Refusal(f'unsupported function {fn} in "{self.where()}": locale-dependent')
        if fn.startswith(("REGEXP_", "SCRIPT_", "MODEL_", "RAWSQL")):
            raise Refusal(f'unsupported function {fn} in "{self.where()}": regex, script or pass-through SQL')
        if fn == "IIF" and len(raw) in (3, 4):
            # IIF returns its 4th argument (or NULL) when the test is NULL, not the
            # ELSE branch; a simple CASE on the boolean says exactly that.
            c, a, b = (self.tr(x, layer) for x in raw[:3])
            u = self.tr(raw[3], layer).sql if len(raw) == 4 else "NULL"
            return TExpr(f"CASE {c.sql} WHEN TRUE THEN {a.sql} WHEN FALSE THEN {b.sql} ELSE {u} END",
                         a.dtype if a.dtype != "null" else b.dtype, c.agg or a.agg or b.agg)
        args = [self.tr(x, layer) for x in raw]
        n, anyagg = len(args), any(a.agg for a in args)
        if fn in AGGREGATES and n == 1:
            return aggregate(fn, args[0], ctx, self.window, self.where())
        if (fn, n) in TEMPLATES:
            template, dtype = TEMPLATES[fn, n]
            return TExpr(template.format(*(a.sql for a in args)), dtype or args[0].dtype, anyagg)
        if fn == "SPLIT" and n == 3:
            if not (args[2].const and isinstance(args[2].lit, int) and args[2].lit > 0):
                raise Refusal(f'unsupported function SPLIT in "{self.where()}": only a positive literal token index has a rule')
            return TExpr(f"SPLIT_PART({args[0].sql}, {args[1].sql}, {args[2].sql})", "str", anyagg)
        if fn == "INT" and n == 1:
            x = args[0]
            if x.dtype == "bool":
                return TExpr(f"CASE {x.sql} WHEN TRUE THEN 1 WHEN FALSE THEN 0 END", "int", x.agg)
            if x.dtype in NUMERIC:          # truncates toward zero; a CAST would round
                return TExpr(f"CAST(TRUNC({x.sql}) AS BIGINT)", "int", x.agg)
            raise Refusal(f'unsupported function INT of {x.dtype} in "{self.where()}": string/date parsing differs')
        if fn == "STR" and n == 1:
            if args[0].dtype == "str":
                return args[0]
            if args[0].dtype == "int":
                return TExpr(f"CAST({args[0].sql} AS VARCHAR)", "str", anyagg)
            raise Refusal(f'unsupported function STR of {args[0].dtype} in "{self.where()}": number/date formatting differs')
        if fn == "DATE" and n == 1:
            if args[0].dtype == "date":
                return args[0]
            if args[0].dtype == "datetime":
                return TExpr(f"CAST({args[0].sql} AS DATE)", "date", anyagg)
            raise Refusal(f'unsupported function DATE of {args[0].dtype} in "{self.where()}": Tableau parses numbers/text its own way')
        if fn in ("TODAY", "NOW") and n == 0:
            ctx.flags.add("needs_as_of")
            return TExpr("CURRENT_DATE", "date") if fn == "TODAY" else TExpr("CURRENT_TIMESTAMP", "datetime")
        if fn in ("DATETRUNC", "DATEPART", "DATEADD", "DATEDIFF"):
            return self.date_fn(fn, args, anyagg)
        raise Refusal(f'unsupported function {fn}() in "{self.where()}": no translation rule')

    def date_fn(self, fn: str, args: list[TExpr], anyagg: bool) -> TExpr:
        u = args[0].lit.lower() if args and args[0].const and isinstance(args[0].lit, str) else None
        expected = 3 if fn in ("DATEADD", "DATEDIFF") else 2
        if u is None or len(args) != expected:
            raise Refusal(f'unsupported function {fn} in "{self.where()}": needs a literal date part and no start-of-week argument')
        if fn == "DATETRUNC" and u == "week":
            return TExpr(week_trunc(args[1].sql, self.ctx), args[1].dtype, anyagg)
        if fn == "DATEPART" and u == "weekday":
            return TExpr(weekday(args[1].sql, self.ctx), "int", anyagg)
        if u not in DATE_UNITS | ({"week"} if fn == "DATEADD" else set()):
            raise Refusal(f'unsupported function {fn}(\'{u}\') in "{self.where()}": no rule (week numbering, ISO parts)')
        if fn == "DATETRUNC":
            return TExpr(f"DATE_TRUNC('{u}', {args[1].sql})", args[1].dtype, anyagg)
        if fn == "DATEPART":
            return TExpr(f"DATE_PART({u}, {args[1].sql})", "int", anyagg)
        if fn == "DATEADD":
            return TExpr(f"DATEADD({u}, {args[1].sql}, {args[2].sql})", args[2].dtype, anyagg)
        return TExpr(f"DATEDIFF({u}, {args[1].sql}, {args[2].sql})", "int", anyagg)


DUPLICATE_SAFE = {"MIN", "MAX", "COUNTD", "ATTR"}     # the same over replicated rows


def aggregate(fn: str, x: TExpr, ctx: Ctx, window: list[str] | None = None, where: str = "?") -> TExpr:
    """One aggregation of a row-level expression (a derivation or a formula's
    own). Inside a FIXED body, `window` is its partition and the result is a
    window aggregate, row-level to the enclosing expression.

    A pure FIXED value (FIXED values and constants, nothing row-level) is held
    once per LOD-grain row in Tableau and once per row here. An aggregate that
    ignores duplicates reads the same either way. SUM and AVG are the value
    itself when every LOD dimension is one of the grain's (the view's
    dimensions, or the enclosing FIXED's), so they are emitted as MIN; at any
    other grain they are refused rather than summed per row."""
    if x.agg:
        raise Refusal(f"unsupported expression: {fn} over an aggregate")
    pure = pure_lod(x.sql)
    if pure and fn not in DUPLICATE_SAFE:
        used = [l for l in ctx.lods if l.name in pure]
        grain = window if window is not None else ctx.view_dims
        dims = " and ".join(", ".join(l.partition) or "the whole table" for l in used)
        if fn not in ("SUM", "AVG") or grain is None or not all(set(l.partition) <= set(grain) for l in used):
            raise Refusal(f'LOD FIXED in "{where}": {fn} over the FIXED value {used[0].text}: Tableau '
                          f"aggregates it at the LOD grain ({dims}), not per row; allowed under "
                          "MIN/MAX/COUNTD/ATTR, or as SUM/AVG when every LOD dimension is a view dimension")
        ctx.note(f"{fn} over a FIXED value whose dimensions are all view dimensions: one value per mark, "
                 "emitted as MIN")
        fn = "MIN"
    over, agg = "", window is None
    if window is not None:
        over = f" OVER (PARTITION BY {', '.join(window)})" if window else " OVER ()"
        if fn == "COUNTD" and ctx.dialect == "redshift":
            raise Refusal(f'LOD FIXED in "{where}": COUNTD inside a FIXED expression needs '
                          "COUNT(DISTINCT) OVER, which Redshift does not support")
    if x.dtype == "objectid":
        if fn == "COUNT":
            return TExpr(f"COUNT(*){over}", "int", agg)
        raise Refusal(f"unsupported expression: {fn} over a table's rows")
    if fn == "COUNTD":
        return TExpr(f"COUNT(DISTINCT {x.sql}){over}", "int", agg)
    if fn == "ATTR":
        # Tableau shows '*' when the values differ; SQL can only say NULL.
        ctx.note("ATTR(x) is written CASE WHEN MIN(x) = MAX(x) THEN MIN(x) END: NULL where Tableau shows '*'")
        return TExpr(f"CASE WHEN MIN({x.sql}){over} = MAX({x.sql}){over} THEN MIN({x.sql}){over} END", x.dtype, agg)
    dtype = "int" if fn == "COUNT" else "real" if fn == "AVG" else x.dtype
    return TExpr(f"{fn}({x.sql}){over}", dtype, agg)


def lod_refs(text: str) -> set[str]:
    """The FIXED columns a house-dialect fragment reads."""
    return set(_LOD_REF.findall(text))


def pure_lod(sql: str) -> set[str]:
    """The FIXED columns `sql` reads when it reads nothing row-level (no
    relation column), else the empty set."""
    if LOD_TABLE + "." not in sql:
        return set()
    cols = list(sqlglot.parse_one(sql, read=HOUSE).find_all(exp.Column))
    return {c.name for c in cols} if all(c.table == LOD_TABLE for c in cols) else set()


def inline_lods(sql: str, ctx: Ctx) -> str:
    """`__lod__.lod_n` written out as its window expression, for the element a
    reviewer reads; the tile SQL itself keeps the stages."""
    by = {l.name: l for l in ctx.lods}
    while True:
        out = _LOD_REF.sub(lambda m: f"({by[m.group(1)].sql})", sql)
        if out == sql:
            return out
        sql = out


def _week_offset(d: str, ctx: Ctx) -> str:
    """Days since the start of d's week, 0..6, from the datasource's declared
    week start. Counted from a date of known weekday (1900-01-01 was a Monday),
    so it does not depend on the warehouse session's week settings."""
    if (ctx.week_start or "").lower() not in WEEKDAYS:
        raise Refusal("unsupported function: week logic without a declared <date-options start-of-week>")
    anchor = f"1900-01-0{WEEKDAYS.index(ctx.week_start.lower()) + 1}"
    return f"MOD(MOD(DATEDIFF(day, DATE '{anchor}', {d}), 7) + 7, 7)"


def week_trunc(d: str, ctx: Ctx) -> str:
    return f"DATEADD(day, -{_week_offset(d, ctx)}, DATE_TRUNC('day', {d}))"


def weekday(d: str, ctx: Ctx) -> str:
    return f"({_week_offset(d, ctx)} + 1)"


def type_override(col: str, src: str, dst: str, name: str, ctx: Ctx) -> str:
    """`datatype-customized`: the user changed the field's type in Tableau, which
    casts the source value. Text to number is exact as TRY_CAST (unparseable text
    becomes NULL in both); other changes are refused."""
    if not (src == "str" and dst in NUMERIC):
        raise Refusal(f'unsupported expression: "{name}" type change {src} -> {dst} has no exact SQL rendering')
    ctx.note(f'"{name}": Tableau reads this text column as a number; emitted as TRY_CAST')
    return f"TRY_CAST({col} AS {'DOUBLE' if dst == 'real' else 'BIGINT'})"


# ---------------------------------------------------------------------------
# Column-instance derivations
# ---------------------------------------------------------------------------

DERIVATIONS = {"none": "None", "sum": "Sum", "avg": "Avg", "cnt": "Count", "ctd": "CountD",
               "min": "Min", "max": "Max", "attr": "Attribute", "usr": "User", "yr": "Year",
               "qr": "Quarter", "mn": "Month", "wk": "Week", "dy": "Day", "wd": "Weekday",
               "hr": "Hour", "tyr": "Year-Trunc", "tqr": "Quarter-Trunc", "tmn": "Month-Trunc",
               "twk": "Week-Trunc", "tdy": "Day-Trunc", "thr": "Hour-Trunc", "io": "InOut"}
QUICK_TABLE_CALCS = {"pcto", "pctd", "cum", "diff", "pdiff", "rank", "pctrank", "mavg", "msum",
                     "ytd", "ytdgr"}
MEASURE_AGG = {"Sum": "SUM", "Avg": "AVG", "Count": "COUNT", "CountD": "COUNTD", "Min": "MIN",
               "Max": "MAX", "Attribute": "ATTR"}
DATE_PARTS = {"Year": ("year", "year"), "Quarter": ("quarter", "quarter of year"),
              "Month": ("month", "month of year"), "Day": ("day", "day of month"),
              "Hour": ("hour", "hour of day")}
DATE_TRUNCS = {"Year-Trunc": "year", "Quarter-Trunc": "quarter", "Month-Trunc": "month",
               "Day-Trunc": "day", "Hour-Trunc": "hour"}


def parse_instance(inst: str) -> dict:
    """`[sum:amount:qk]` -> column `[amount]`, derivation Sum."""
    obj = inst.startswith(OBJ)
    s = inst[len(OBJ):] if obj else inst
    parts = (s[1:-1] if s.startswith("[") and s.endswith("]") else s).split(":")
    if len(parts) > 1 and parts[-1].isdigit():
        parts.pop()
    typekey = parts.pop() if len(parts) > 1 and parts[-1] in ("nk", "ok", "qk") else None
    prefixes = []
    while len(parts) > 1 and (parts[0] in DERIVATIONS or parts[0] in QUICK_TABLE_CALCS):
        prefixes.append(parts.pop(0))
    name = f"[{':'.join(parts)}]"
    deriv = next((DERIVATIONS[p] for p in prefixes if p in DERIVATIONS), None)
    return {"column": OBJ + name if obj else name, "derivation": deriv, "typekey": typekey,
            "table_calc": next((p for p in prefixes if p in QUICK_TABLE_CALCS), None),
            "decorated": bool(prefixes or typekey)}


def apply_derivation(t: TExpr, deriv: str | None, ctx: Ctx, where: str = "?") -> tuple[TExpr, str | None]:
    """A column-instance's derivation -> (SQL, bucket). Aggregations make a
    measure; date parts and truncations make a dimension at that grain.
    `where` names the field in a refusal."""
    if deriv in (None, "None"):
        return t, None
    if deriv == "User":                     # the formula carries its own aggregate
        if not (t.agg or t.const):
            raise Refusal("unsupported expression: derivation User on a row-level formula")
        return t, None
    if deriv in MEASURE_AGG:
        return aggregate(MEASURE_AGG[deriv], t, ctx, None, where), None
    if t.agg:
        raise Refusal(f"unsupported expression: date derivation {deriv} on an aggregate")
    if deriv in DATE_PARTS:
        u, bucket = DATE_PARTS[deriv]
        return TExpr(f"DATE_PART({u}, {t.sql})", "int"), bucket
    if deriv == "Weekday":
        return TExpr(weekday(t.sql, ctx), "int"), "weekday"
    if deriv in DATE_TRUNCS:
        u = DATE_TRUNCS[deriv]
        return TExpr(f"DATE_TRUNC('{u}', {t.sql})", t.dtype), u
    if deriv == "Week-Trunc":
        return TExpr(week_trunc(t.sql, ctx), t.dtype), "week"
    if deriv == "InOut":
        raise Refusal("set membership (IN/OUT) is computed by Tableau")
    raise Refusal(f"unsupported expression: derivation {deriv} has no rule (week numbers differ by convention)")
