# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A view's own calculations over its matrix (Power BI's visual calculations), as SQL.

A visual calculation is not a measure of the model. It is evaluated on the visual's MATRIX:
every cell the visual lays out, at every level of each axis (the leaf rows, each subtotal, the
grand total), the empty cells of a two-axis matrix included ("densified"). It reads the cell's
own values and other cells: the previous row along an axis, a running sum, the parent's total,
a cell found by its coordinates. So the statement is built the way the visual computes it:

1. MATRIX. The unit's measures are compiled at every level of the axes (each its own plain
   statement through the unit compiler: filters, joins and context as the leaf rows have
   them), and the levels are stacked into one table, `__vm`: a row per cell, its level on each
   axis (`__gr`, `__gc`, `__gp`), its key columns (NULL where the level totals them) and its
   values. A cell of a two-axis matrix with no rows is laid out too (every member of the rows
   at that level against every member of the columns), its values empty.
2. LAYERS. Each function that reads other cells becomes a column of a new layer over the one
   before: a window along the axis (running sum, moving average, previous, next, first, last,
   a range), or a join to the cell it navigates to (collapse, expand, lookup). Each of the
   visual's calculations is then a column, in the order they read one another.
3. STATEMENTS. The leaf statement reads the last layer at the leaf level, each total
   statement at its own; a row whose every shown value is empty is left out, as the visual
   hides it.

Semantics, from Microsoft's DAX reference for each function and checked live with
executeQueries' own visual-calculation engine (`WITH VISUAL SHAPE`, one probe per form):
* an axis is ordered by its columns ascending (an empty text member first; an empty number
  between the negatives and zero) unless ORDERBY says otherwise; ROWS COLUMNS runs down the
  rows, then on to the next column (ordered by the columns, then the rows);
* a window partitions by the level, the other axes' columns and the reset: an absolute reset
  (a positive number, HIGHESTPARENT, a column) by the hierarchy's columns down to that level,
  a relative one (a negative number, LOWESTPARENT) by those that many levels above the cell,
  clamped to the hierarchy's depth less one (on a one-level axis each cell restarts);
* COLLAPSE / EXPAND move N levels up / down on EACH axis they name (or to the level where the
  columns they name are absent / all present); EXPAND aggregates the cells it reaches;
* LOOKUP reads the cell at the current coordinates with the named columns replaced (BLANK
  where there is no such cell, or more than one); LOOKUPWITHTOTALS the cell of the named
  coordinates with every other column totalled; ISATLEVEL is whether the column is on the
  cell's level.
"""
from __future__ import annotations

import itertools
import re

from metrics_agent.plugs.readers import bicompile as B
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

AXES = ("rows", "columns", "pages")
_GRAIN = {"rows": "__gr", "columns": "__gc", "pages": "__gp"}
_AXIS_WORDS = {"ROWS": "rows", "COLUMNS": "columns", "ROWPAGES": "pages"}
_BLANKS = {"DEFAULT", "FIRST", "LAST", "KEEP"}
_RESET_WORDS = {"NONE": 0, "HIGHESTPARENT": 1, "LOWESTPARENT": -1}
# An aggregate over cells: over the one cell a calculation is evaluated on it is the cell's
# value; over the cells EXPAND or a RANGE reaches, the aggregate of theirs.
_AGG = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT",
        "COUNTA": "COUNT", "DISTINCTCOUNT": "COUNT_DISTINCT"}
_AGGX = {"SUMX": "SUM", "AVERAGEX": "AVG", "MINX": "MIN", "MAXX": "MAX", "COUNTX": "COUNT",
         "COUNTAX": "COUNT"}
# The functions that read other cells, each compiled by its handler below.
_NAV = ("RUNNINGSUM", "MOVINGAVERAGE", "PREVIOUS", "NEXT", "FIRST", "LAST", "COLLAPSE",
        "COLLAPSEALL", "EXPAND", "EXPANDALL", "LOOKUP", "LOOKUPWITHTOTALS", "ISATLEVEL")


class VisualCalcError(B.CompileError):
    """A visual calculation this compiler cannot write as SQL; the message says what."""


def _indent(sql: str, n: int = 4) -> str:
    return "\n".join((" " * n + ln) if ln.strip() else ln for ln in sql.splitlines())


def _split_args(toks: list) -> list[list]:
    """A call's argument token lists (the tokens between its parentheses)."""
    out, cur, depth = [], [], 0
    for k, v in toks:
        if k == "lparen":
            depth += 1
        elif k == "rparen":
            depth -= 1
        if k == "comma" and depth == 0:
            out.append(cur)
            cur = []
            continue
        cur.append((k, v))
    if cur or out:
        out.append(cur)
    return out


def _call_end(toks: list, i: int) -> int:
    """The index of the parenthesis closing the call whose `(` is at `i`."""
    depth = 0
    for j in range(i, len(toks)):
        if toks[j][0] == "lparen":
            depth += 1
        elif toks[j][0] == "rparen":
            depth -= 1
            if depth == 0:
                return j
    raise VisualCalcError("an unclosed call")


def _words(toks: list) -> list[str]:
    return [v.upper() for k, v in toks] if toks and all(k == "func" for k, _v in toks) else []


def _int_of(toks: list) -> int | None:
    text = "".join(v for _k, v in toks).replace(" ", "")
    return int(text) if re.fullmatch(r"[-+]?\d+", text) else None


def _normal(toks: list) -> list:
    """`'Table'[Column]` and `Table[Column]` name the matrix column `[Column]`: a visual
    calculation reads the visual's own fields, not the model's tables."""
    return [("col", "[" + B._column_ref(v) + "]") if k in ("qualified", "quoted_qualified")
            else (k, v) for k, v in toks]


class _Matrix:
    """The visual's matrix as SQL: its levels, its key and value columns, its layers."""

    def __init__(self, unit: dict, rules, dialect: str, compile_base, kw: dict):
        self.unit, self.rules, self.dialect = unit, rules, dialect
        vm = unit["visual_matrix"]
        self.vm = vm
        group = [str(g) for g in unit.get("group_by") or []]
        low = {g.lower(): g for g in group}
        self.axes: dict[str, list[list[str]]] = {}
        placed: set[str] = set()
        for a in AXES:
            levels = []
            for lvl in (vm.get("axes") or {}).get(a) or []:
                cols = [low[c.lower()] for c in lvl if c.lower() in low]
                if len(cols) != len(lvl):
                    raise VisualCalcError(f"the {a} axis names {lvl}, which the visual does "
                                          f"not group by")
                levels.append(cols)
                placed |= {c.lower() for c in cols}
            self.axes[a] = levels
        rest = [g for g in group if g.lower() not in placed]
        if rest:
            # A grouping column on no axis (a field in a role the reader did not place) is
            # one more level of the rows: the visual's rows are still per its members.
            self.axes["rows"].append(rest)
        self.slots: list[tuple[str, int, str]] = [
            (a, j, c) for a in AXES for j, lvl in enumerate(self.axes[a]) for c in lvl]
        self.slot_of = {c.lower(): i for i, (_a, _j, c) in enumerate(self.slots)}
        self.base = [m for m in unit.get("measures") or [] if not m.get("visual_calc")]
        self.res = Compiled()
        self.text_key = bool(kw.get("text_key")) and kw.get("text_key") != "source"
        types = kw.get("types") or {}
        self.kinds = []
        for _a, _j, c in self.slots:
            ty = next((str(t) for (_tb, col), t in types.items()
                       if str(col).lower() == c.lower() and t), "")
            self.kinds.append("num" if re.match(r"(?i)^(int|double|decimal|number|float|"
                                                r"currency|whole)", ty)
                              else "date" if re.match(r"(?i)^(date|time)", ty) else "text")
        nse_row = rules.operator("null_safe_eq", dialect) or {}
        emit = nse_row.get("emit") or {}
        self.nse = ((emit.get("dialect_templates") or {}).get(str(dialect).lower())
                    or emit.get("template"))
        if not self.nse:
            raise VisualCalcError(f"the matrix joins its cells NULL matching NULL, which has "
                                  f"no form for the {dialect} dialect")
        self._build(compile_base)

    # -- the matrix ---------------------------------------------------------------------
    def depth(self, a: str) -> int:
        return len(self.axes[a])

    def grains(self) -> list[dict]:
        out = [dict(zip(AXES, g)) for g in itertools.product(
            *(range(self.depth(a), -1, -1) for a in AXES))]
        return out           # the leaf first: its branch types the stacked columns

    def cols_at(self, g: dict) -> list[str]:
        return [c for a, j, c in self.slots if j < g[a]]

    def _build(self, compile_base) -> None:
        group = [str(x) for x in self.unit.get("group_by") or []]
        tables = list(self.unit.get("group_by_tables") or [])
        self.stmts: dict[tuple, dict] = {}
        for g in self.grains():
            keep = {c.lower() for c in self.cols_at(g)}
            sub = {k: v for k, v in self.unit.items()
                   if k not in ("totals", "visual_matrix", "measures", "group_by",
                                "group_by_tables")}
            sub["group_by"] = [x for x in group if x.lower() in keep]
            if tables and len(tables) == len(group):
                sub["group_by_tables"] = [tables[group.index(x)] for x in sub["group_by"]]
            sub["measures"] = [{k: v for k, v in m.items() if k != "hidden"} for m in self.base]
            if not self.base:
                sub["distinct_rows"] = True
            r = compile_base(sub)
            where = f"the matrix level grouped by {sub['group_by'] or 'nothing'}"
            if not r.sql or r.coverage == REFUSED:
                raise VisualCalcError(f"{where}: {r.reason or 'does not compile'}")
            if {"engine.top_n_by_sort", "engine.top_n_by_first_measure",
                    "engine.top_n_limit_after"} & set(r.rules_used) and sub["group_by"] != group:
                raise VisualCalcError(f"{where}: the Top N is kept by a LIMIT over the leaf "
                                      f"rows, which a level over fewer columns cannot reuse")
            if r.coverage == PARTIAL:
                self.res.coverage = PARTIAL
                self.res.untranslatable += r.untranslatable
                self.res.reason = self.res.reason or r.reason
            for x in r.rules_used:
                if x not in self.res.rules_used:
                    self.res.rules_used.append(x)
            self.res.provisional_rules += [x for x in r.provisional_rules
                                           if x not in self.res.provisional_rules]
            self.res.gotchas += [x for x in r.gotchas if x not in self.res.gotchas]
            names = self._outputs(r.sql)
            need = len(sub["group_by"]) + len(sub["measures"])
            if len(names) != need:
                raise VisualCalcError(f"{where}: its statement returns {len(names)} columns, "
                                      f"{need} expected")
            self.stmts[tuple(g[a] for a in AXES)] = {
                "sql": r.sql, "keys": dict(zip([x.lower() for x in sub["group_by"]], names)),
                "values": names[len(sub["group_by"]):]}

    def _outputs(self, sql: str) -> list[str]:
        """Each output column of a statement, as SQL that names it from outside."""
        import sqlglot  # noqa: PLC0415 - loaded by A3 already
        import sqlglot.expressions as E  # noqa: PLC0415
        tree = sqlglot.parse_one(sql, read=self.dialect)
        while isinstance(tree, E.Union):
            tree = tree.left
        if not isinstance(tree, E.Select):
            raise VisualCalcError("a matrix level is not one SELECT")
        out = []
        for p in tree.expressions:
            ident = p.args.get("alias") if isinstance(p, E.Alias) else (
                p.this if isinstance(p, E.Column) else None)
            if not isinstance(ident, E.Identifier):
                raise VisualCalcError(f"a matrix level returns an unnamed column {p.sql()!r}")
            out.append(ident.sql(dialect=self.dialect))
        return out

    def eq(self, a: str, b: str) -> str:
        return self.nse.replace("{0}", a).replace("{1}", b)

    def matrix_sql(self) -> list[tuple[str, str]]:
        """(name, statement) of each matrix CTE: a level's statement each, the stacked cells
        `__vm`, and the first layer `__l0` with a row id."""
        ctes: list[tuple[str, str]] = []
        name_of: dict[tuple, str] = {}
        for n, (g, st) in enumerate(self.stmts.items()):
            name_of[g] = f"__g{n}"
            ctes.append((f"__g{n}", st["sql"]))
        branches = []
        for g in self.grains():
            key = tuple(g[a] for a in AXES)
            st = self.stmts[key]
            on = [a for a in AXES if g[a] > 0]
            head = [f"{g[a]} AS {_GRAIN[a]}" for a in AXES]
            if len(on) <= 1:
                cols = [(f"s.{st['keys'][c.lower()]}" if j < g[a] else "NULL") + f" AS k{i}"
                        for i, (a, j, c) in enumerate(self.slots)]
                vals = [f"s.{v} AS v{i}" for i, v in enumerate(st["values"])]
                branches.append("SELECT " + ",\n       ".join(head + cols + vals)
                                + f"\nFROM   {name_of[key]} s")
                continue
            # A two-axis level: every member of each axis at its level, against each other,
            # the cell's values where it has rows (DENSIFY).
            src, joins = [], []
            for x, a in enumerate(on):
                dom = tuple(g[b] if b == a else 0 for b in AXES)
                src.append(f"{name_of[dom]} d{x}")
            cols, conds = [], []
            for i, (a, j, c) in enumerate(self.slots):
                if j >= g[a]:
                    cols.append(f"NULL AS k{i}")
                    continue
                x = on.index(a)
                dom = self.stmts[tuple(g[b] if b == a else 0 for b in AXES)]
                ref = f"d{x}.{dom['keys'][c.lower()]}"
                cols.append(f"{ref} AS k{i}")
                conds.append(self.eq(f"s.{st['keys'][c.lower()]}", ref))
            vals = [f"s.{v} AS v{i}" for i, v in enumerate(st["values"])]
            frm = src[0] + "".join(f"\nCROSS JOIN {s}" for s in src[1:])
            joins = (f"\nLEFT JOIN {name_of[key]} s\n    ON " + "\n   AND ".join(conds)
                     if conds else f"\nCROSS JOIN {name_of[key]} s")
            branches.append("SELECT " + ",\n       ".join(head + cols + vals)
                            + f"\nFROM   {frm}{joins}")
            if "engine.visual_calc_densify" not in self.res.rules_used:
                self.res.rules_used.append("engine.visual_calc_densify")
        ctes.append(("__vm", "\nUNION ALL\n".join(branches)))
        order = [f"x.{_GRAIN[a]}" for a in AXES] + [
            B._sort_term(f"x.k{i}", "ASC", "FIRST", self.dialect)
            for i in range(len(self.slots))]
        ctes.append(("__l0", "SELECT ROW_NUMBER() OVER (ORDER BY " + ", ".join(order)
                     + ") AS __rid,\n       x.*\nFROM   __vm x"))
        return ctes


class _Calcs:
    """The visual's calculations as layers over the matrix."""

    def __init__(self, mx: _Matrix):
        self.mx, self.rules, self.dialect = mx, mx.rules, mx.dialect
        self.ctes: list[tuple[str, str]] = []
        self.pending: list[tuple[str, str]] = []
        self.cur = "__l0"
        self.n = self.k = 0
        self.colmap: dict[str, str] = {}
        vm = mx.vm
        base_idx = {m["name"].lower(): i for i, m in enumerate(mx.base)}
        self.calcs = list(vm.get("calcs") or [])
        self.calc_idx = {c["name"].lower(): i for i, c in enumerate(self.calcs)}
        self.refs: dict[str, tuple[str, str]] = {}
        for name, i in base_idx.items():
            self.refs[name] = ("measure", f"v{i}")
        for c, i in mx.slot_of.items():
            self.refs.setdefault(c, ("column", f"k{i}"))
        for name, i in self.calc_idx.items():
            self.refs[name] = ("calc", self.calcs[i]["name"].lower())
        for ref, tgt in (vm.get("refs") or {}).items():
            if "measure" in tgt and tgt["measure"].lower() in base_idx:
                self.refs[ref.lower()] = ("measure", f"v{base_idx[tgt['measure'].lower()]}")
            elif "column" in tgt and tgt["column"].lower() in mx.slot_of:
                self.refs[ref.lower()] = ("column", f"k{mx.slot_of[tgt['column'].lower()]}")
            elif "calc" in tgt and tgt["calc"].lower() in self.calc_idx:
                self.refs[ref.lower()] = ("calc", tgt["calc"].lower())
        self.used: list[str] = []
        self.done: dict[str, str] = {}
        self.stack: list[str] = []
        self.gotchas: list[str] = []

    # -- layers -------------------------------------------------------------------------
    def col(self) -> str:
        self.k += 1
        return f"__x{self.k}"

    def layer(self, select: str) -> None:
        """Add a column (`<sql> AS <name>`) to the next layer. Columns that read none of the
        next layer's own go in together, so the layers are as deep as the calculations'
        dependencies, not as many as their functions (an engine that inlines a chain of
        CTEs pays for its depth)."""
        reads = set(re.findall(r"\bm\.(\w+)", select))
        if reads & {n for _s, n in self.pending}:
            self.flush()
        self.pending.append((select, select.rsplit(" AS ", 1)[1].strip()))

    def flush(self) -> str:
        """The layer every column added so far is in (the last one)."""
        if self.pending:
            self.n += 1
            name = f"__l{self.n}"
            cols = ",\n       ".join(s for s, _n in self.pending)
            self.ctes.append((name, f"SELECT m.*,\n       {cols}\nFROM   {self.cur} m"))
            self.cur = name
            self.pending = []
        return self.cur

    def materialize(self, sql: str) -> str:
        c = self.col()
        self.layer(f"{sql} AS {c}")
        self.colmap[c] = c
        return c

    # -- references ---------------------------------------------------------------------
    def column(self, name: str) -> tuple[str, str]:
        """(kind, layer column) a `[name]` reads; a calculation is compiled first."""
        hit = self.refs.get(name.lower())
        if hit is None:
            if name.lower() in self.colmap:
                return "lifted", self.colmap[name.lower()]
            raise VisualCalcError(f"[{name}] is not a field of the visual")
        kind, tgt = hit
        if kind == "calc":
            return "calc", self.calc(tgt)
        return kind, tgt

    def calc(self, name: str) -> str:
        """The layer column of the calculation `name`, compiled once, after the ones it
        reads (a calculation cannot read itself, directly or through another)."""
        if name in self.done:
            return self.done[name]
        if name in self.stack:
            raise VisualCalcError(f"the calculation [{name}] reads itself")
        self.stack.append(name)
        try:
            spec = self.calcs[self.calc_idx[name]]
            sql = self.expr(B.tokenize(spec["expression"]))
        finally:
            self.stack.pop()
        same = re.fullmatch(r"m\.(\w+)", sql.strip())
        if same:
            # The calculation IS a column already laid (a function's result, a field).
            self.done[name] = same.group(1)
            return same.group(1)
        c = f"c{self.calc_idx[name]}"
        self.layer(f"{sql} AS {c}")
        self.done[name] = c
        return c

    # -- expressions --------------------------------------------------------------------
    def expr(self, toks: list) -> str:
        """A calculation expression as SQL over the current layer (alias `m`)."""
        return self.scalar(self.lift(_normal(toks)))

    def scalar(self, toks: list) -> str:
        """Tokens with every cell-reading function lifted: the rest is a row-level DAX
        expression over the layer's columns, compiled by the DAX compiler."""
        cmap = {}
        for k, v in toks:
            if k == "col":
                name = B._column_ref(v)
                cmap[name.lower()] = self.column(name)[1]
        p = B._Parser(toks, self.rules, "dax", "m", self.dialect, colmap=cmap)
        try:
            sql = p.parse()
        finally:
            self.used += p.used
        return sql

    def lift(self, toks: list) -> list:
        """Each function that reads other cells, and each aggregate over the cell, replaced
        by a column of a new layer (`[__xN]`)."""
        toks = list(toks)
        i = 0
        while i < len(toks):
            k, v = toks[i]
            if k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
                up = v.upper()
                end = _call_end(toks, i + 1)
                args = _split_args(toks[i + 2:end])
                rep = None
                if up in _NAV:
                    rep = getattr(self, "_" + up.lower())(args)
                elif up == "CALCULATE":
                    rep = self._calculate(args)
                elif up in _AGGX and args and _words(args[0][:1]) == ["RANGE"]:
                    rep = self._range(_AGGX[up], args[1:], _split_args(args[0][2:-1]))
                elif up in _AGG and len(args) == 1:
                    inner = self.lift(args[0])
                    if _AGG[up] in ("COUNT", "COUNT_DISTINCT"):
                        rep_toks = ([("func", "IF"), ("lparen", "("), ("func", "ISBLANK"),
                                     ("lparen", "(")] + inner + [("rparen", ")"), ("comma", ","),
                                    ("func", "BLANK"), ("lparen", "("), ("rparen", ")"),
                                    ("comma", ","), ("num", "1"), ("rparen", ")")])
                    else:
                        rep_toks = [("lparen", "(")] + inner + [("rparen", ")")]
                    toks[i:end + 1] = rep_toks
                    i += len(rep_toks)
                    continue
                if rep is not None:
                    self.colmap[rep] = rep
                    toks[i:end + 1] = [("col", f"[{rep}]")]
                    i += 1
                    continue
            i += 1
        return toks

    def value(self, toks: list) -> str:
        """An argument's value on the cell, as SQL over the current layer."""
        return self.expr(toks)

    def value_col(self, toks: list) -> str:
        """An argument's value on every cell, as a column of the current layer."""
        sql = self.expr(toks)
        m = re.fullmatch(r"m\.(\w+)", sql.strip())
        return m.group(1) if m else self.materialize(sql)

    # -- axes, order, reset ---------------------------------------------------------------
    def default_axis(self) -> list[str]:
        return next(([a] for a in AXES if self.mx.depth(a)), [])

    def options(self, args: list[list], fn: str) -> dict:
        """The optional axis, ORDERBY, blanks and reset arguments of a window function."""
        out = {"axis": None, "order": None, "blanks": "DEFAULT", "reset": 0}
        for a in args:
            words = _words(a)
            if words and all(w in _AXIS_WORDS for w in words):
                out["axis"] = [_AXIS_WORDS[w] for w in words]
            elif a and a[0][0] == "func" and a[0][1].upper() == "ORDERBY":
                out["order"] = _split_args(a[2:-1])
            elif words and len(words) == 1 and words[0] in _BLANKS:
                out["blanks"] = "DEFAULT" if words[0] == "KEEP" else words[0]
            elif words and len(words) == 1 and words[0] in _RESET_WORDS:
                out["reset"] = _RESET_WORDS[words[0]]
            elif _int_of(a) is not None:
                out["reset"] = _int_of(a)
            elif len(a) == 1 and a[0][0] in ("col", "qualified", "quoted_qualified"):
                out["reset"] = ("column", B._column_ref(a[0][1]))
            else:
                raise VisualCalcError(f"{fn}: an argument {''.join(v for _k, v in a)!r} is "
                                      f"not an axis, ORDERBY, blanks or reset")
        axis = [x for x in (out["axis"] or self.default_axis()) if self.mx.depth(x)]
        out["axis"] = axis
        return out

    def key_term(self, i: int, direction: str, blanks: str) -> str:
        x = f"m.k{i}"
        kind = self.mx.kinds[i]
        return self.sort_term(x, kind, direction, blanks)

    def sort_term(self, x: str, kind: str, direction: str, blanks: str) -> str:
        d = direction.upper()
        if blanks in ("FIRST", "LAST"):
            key = f"UPPER({x})" if kind == "text" and self.mx.text_key else x
            return B._sort_term(key, d, blanks, self.dialect)
        if kind == "num":
            # DEFAULT: an empty number sorts between the negatives and zero.
            return (f"COALESCE({x}, 0) {d}, CASE WHEN {x} IS NULL THEN 0 ELSE 1 END {d}")
        key = f"UPPER({x})" if kind == "text" and self.mx.text_key else x
        # DEFAULT: an empty text (or date) before every value.
        term = B._sort_term(key, d, "FIRST" if d == "ASC" else "LAST", self.dialect)
        return term + (f", {x} {d}" if key != x else "")

    def over(self, opt: dict, fn: str) -> str:
        """PARTITION BY and ORDER BY of a window along `opt`'s axis."""
        mx, axis = self.mx, opt["axis"]
        hier = [(a, j) for a in reversed(axis) for j in range(mx.depth(a))]
        part = [f"m.{_GRAIN[a]}" for a in AXES]
        part += [f"m.k{i}" for i, (a, _j, _c) in enumerate(mx.slots) if a not in axis]
        reset = opt["reset"]
        if isinstance(reset, tuple):
            col = reset[1].lower()
            if col not in mx.slot_of:
                raise VisualCalcError(f"{fn}: the reset column [{reset[1]}] is not on the "
                                      f"visual")
            a, j, _c = mx.slots[mx.slot_of[col]]
            if (a, j) not in hier:
                raise VisualCalcError(f"{fn}: the reset column [{reset[1]}] is not on the "
                                      f"axis it runs along")
            reset = hier.index((a, j)) + 1
        n = len(hier)
        if isinstance(reset, int) and reset > 0:
            for a, j in hier[:min(reset, n)]:
                part += [f"m.k{i}" for i, (b, l, _c) in enumerate(mx.slots) if (b, l) == (a, j)]
            self.used.append("engine.visual_calc_reset")
        elif isinstance(reset, int) and reset < 0 and n:
            x = min(-reset, n - 1)
            total = " + ".join(f"m.{_GRAIN[a]}" for a in reversed(axis)) or "0"
            for p, (a, j) in enumerate(hier):
                before = [b for b in reversed(axis)][:list(reversed(axis)).index(a)]
                pos = " + ".join([f"m.{_GRAIN[b]}" for b in before] + [str(j + 1)])
                cond = f"m.{_GRAIN[a]} >= {j + 1} AND {pos} <= {total} - {x}"
                part += [f"CASE WHEN {cond} THEN m.k{i} END"
                         for i, (b, l, _c) in enumerate(mx.slots) if (b, l) == (a, j)]
            self.used.append("engine.visual_calc_reset")
        if opt["order"]:
            terms = []
            parts = opt["order"]
            i = 0
            while i < len(parts):
                e = parts[i]
                d = "ASC"
                if i + 1 < len(parts) and _words(parts[i + 1]) and _words(parts[i + 1])[0] in (
                        "ASC", "DESC"):
                    d = _words(parts[i + 1])[0]
                    i += 1
                sql = self.value(e)
                kind = "num"
                one = re.fullmatch(r"m\.k(\d+)", sql.strip())
                if one:
                    kind = mx.kinds[int(one.group(1))]
                terms.append(self.sort_term(sql, kind, d, opt["blanks"]))
                i += 1
            self.used.append("engine.visual_calc_orderby")
        else:
            terms = []
        # The axis' own columns ascending: the order without an ORDERBY, and the order of
        # the cells an ORDERBY ties (a column it names that is not on the cell's level).
        terms += [self.key_term(i, "ASC", opt["blanks"])
                  for a in reversed(axis) for j in range(mx.depth(a))
                  for i, (b, l, _c) in enumerate(mx.slots) if (b, l) == (a, j)]
        oby = "ORDER BY " + ", ".join(terms or ["m.__rid"])
        return f"PARTITION BY {', '.join(part)} {oby}"

    def template(self, fn: str) -> dict:
        rule = self.rules.function("dax", fn, self.dialect)
        if rule is None:
            raise VisualCalcError(f"no rule for the visual calculation function {fn} in the "
                                  f"{self.dialect} dialect")
        self.used.append(rule["id"])
        return B._emit_for(rule, self.dialect)

    def window(self, fn: str, value: str, opt: dict, **fill) -> str:
        emit = self.template(fn)
        t = emit["template"].replace("{over}", self.over(opt, fn))
        for k, v in fill.items():
            t = t.replace("{" + k + "}", str(v))
        return self.materialize(t.replace("{0}", value))

    # -- window functions ---------------------------------------------------------------
    def _runningsum(self, args):
        if not args:
            raise VisualCalcError("RUNNINGSUM takes a column")
        v = self.value(args[0])
        return self.window("RUNNINGSUM", v, self.options(args[1:], "RUNNINGSUM"))

    def _movingaverage(self, args):
        if len(args) < 2 or _int_of(args[1]) is None or _int_of(args[1]) < 1:
            raise VisualCalcError("MOVINGAVERAGE takes a column and a whole window size")
        size = _int_of(args[1])
        rest = args[2:]
        include = True
        if rest and _words(rest[0][:1]) in (["TRUE"], ["FALSE"]):
            include = _words(rest[0][:1]) == ["TRUE"]
            rest = rest[1:]
        frame = (f"ROWS BETWEEN {size - 1} PRECEDING AND CURRENT ROW" if include
                 else f"ROWS BETWEEN {size} PRECEDING AND 1 PRECEDING")
        v = self.value(args[0])
        return self.window("MOVINGAVERAGE", v, self.options(rest, "MOVINGAVERAGE"), frame=frame)

    def _shift(self, fn: str, args):
        if not args:
            raise VisualCalcError(f"{fn} takes a column")
        rest = args[1:]
        steps = 1
        if rest and _int_of(rest[0]) is not None:
            steps = _int_of(rest[0])
            rest = rest[1:]
        if steps < 1:
            raise VisualCalcError(f"{fn}'s steps is {steps}; a whole number of rows from 1")
        v = self.value(args[0])
        return self.window(fn, v, self.options(rest, fn), n=steps)

    def _previous(self, args):
        return self._shift("PREVIOUS", args)

    def _next(self, args):
        return self._shift("NEXT", args)

    def _first(self, args):
        if not args:
            raise VisualCalcError("FIRST takes a column")
        return self.window("FIRST", self.value(args[0]), self.options(args[1:], "FIRST"))

    def _last(self, args):
        if not args:
            raise VisualCalcError("LAST takes a column")
        return self.window("LAST", self.value(args[0]), self.options(args[1:], "LAST"))

    def _range(self, agg: str, value_args: list, range_args: list) -> str:
        """An aggregate over the cells RANGE(step[, includeCurrent][, axis]...) reaches."""
        if not range_args or _int_of(range_args[0]) is None:
            raise VisualCalcError("RANGE takes a whole number of rows")
        step = _int_of(range_args[0])
        rest = range_args[1:]
        include = True
        if rest and _words(rest[0][:1]) in (["TRUE"], ["FALSE"]):
            include = _words(rest[0][:1]) == ["TRUE"]
            rest = rest[1:]
        if step < 0:
            frame = (f"{-step} PRECEDING AND CURRENT ROW" if include
                     else f"{-step} PRECEDING AND 1 PRECEDING")
        elif step > 0:
            frame = (f"CURRENT ROW AND {step} FOLLOWING" if include
                     else f"1 FOLLOWING AND {step} FOLLOWING")
        else:
            frame = "CURRENT ROW AND CURRENT ROW"
        if len(value_args) != 1:
            raise VisualCalcError("an aggregate over a RANGE takes one expression")
        v = self.value(value_args[0])
        call = self._agg_call(agg, v)
        return self.window("RANGE", call, self.options(rest, "RANGE"),
                           frame=f"ROWS BETWEEN {frame}")

    def _agg_call(self, agg: str, v: str) -> str:
        if agg == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {v})"
        return f"{agg}({v})"

    # -- navigation ---------------------------------------------------------------------
    def _axis_of(self, toks: list, fn: str) -> list[str] | None:
        words = _words(toks)
        if words and all(w in _AXIS_WORDS for w in words):
            return [a for a in (_AXIS_WORDS[w] for w in words) if self.mx.depth(a)]
        return None

    def _level_target(self, fn: str, args: list[list], up: bool) -> dict:
        """The level each axis moves to, as a function of the cell's level on it: N levels
        up (COLLAPSE) or down (EXPAND) on each axis named, or to where the columns named are
        all absent (up) / all present (down). Axes it does not move are left out."""
        mx = self.mx
        axis = self._axis_of(args[0], fn) if args else None
        if axis is not None:
            n = _int_of(args[1]) if len(args) > 1 else 1
            if n is None or n < 1 or len(args) > 2:
                raise VisualCalcError(f"{fn} takes an axis and a whole number of levels")
            return {a: ((lambda i, n=n: max(i - n, 0)) if up else
                        (lambda i, n=n, d=mx.depth(a): min(i + n, d))) for a in axis}
        by_axis: dict[str, list[int]] = {}
        for a_toks in args:
            if len(a_toks) != 1 or a_toks[0][0] not in ("col", "qualified", "quoted_qualified"):
                raise VisualCalcError(f"{fn} takes an axis or the visual's columns")
            c = B._column_ref(a_toks[0][1]).lower()
            if c not in mx.slot_of:
                raise VisualCalcError(f"{fn}: [{c}] is not a column of the visual")
            a, j, _c = mx.slots[mx.slot_of[c]]
            by_axis.setdefault(a, []).append(j)
        return {a: ((lambda i, lvl=min(js): min(i, lvl)) if up else
                    (lambda i, lvl=max(js) + 1: max(i, lvl))) for a, js in by_axis.items()}

    def _combos(self, axes: list[str]) -> list[dict]:
        return [dict(zip(axes, c)) for c in itertools.product(
            *(range(self.mx.depth(a) + 1) for a in axes))]

    def _per_level(self, axes: list[str], body) -> str:
        """`body(levels)` for each combination of the cell's levels on `axes`, as one CASE:
        each a window over the layer, so a cell reads another cell without a self-join."""
        whens = []
        for c in self._combos(axes):
            cond = " AND ".join(f"m.{_GRAIN[a]} = {i}" for a, i in c.items()) or "1 = 1"
            whens.append(f"WHEN {cond} THEN {body(c)}")
        return "CASE " + "\n                ".join(whens) + " END"

    def _window_of(self, part: list[str]) -> str:
        return f"OVER (PARTITION BY {', '.join(part)})" if part else "OVER ()"

    def _nav_part(self, c: dict, tgt: dict) -> list[str]:
        """The partition holding a cell and the cells it navigates to: every axis it does not
        move (its level and columns), and on each it moves the columns both levels share."""
        mx = self.mx
        part = [f"m.{_GRAIN[a]}" for a in AXES if a not in tgt]
        for i, (a, j, _c) in enumerate(mx.slots):
            if a not in tgt or j < min(c[a], tgt[a](c[a])):
                part.append(f"m.k{i}")
        return part

    def _value_at(self, col: str, tgt: dict) -> str:
        """The value of layer column `col` at the cell `tgt` moves to (one cell)."""
        axes = list(tgt)

        def body(c):
            cond = " AND ".join(f"m.{_GRAIN[a]} = {tgt[a](c[a])}" for a in axes)
            return (f"MAX(CASE WHEN {cond} THEN m.{col} END) "
                    + self._window_of(self._nav_part(c, tgt)))
        self.used.append("engine.visual_calc_navigate")
        return self.materialize(self._per_level(axes, body))

    def _set_at(self, toks: list, tgt: dict, fn: str) -> str:
        """An expression of aggregates over the cells `tgt` moves to, within the cell's own
        members."""
        aggs: list[tuple[str, str, str]] = []
        toks = _normal(toks)

        def walk(ts: list) -> list:
            out, i = [], 0
            while i < len(ts):
                k, v = ts[i]
                if k == "func" and i + 1 < len(ts) and ts[i + 1][0] == "lparen" and (
                        v.upper() in _AGG):
                    end = _call_end(ts, i + 1)
                    a = _split_args(ts[i + 2:end])
                    if len(a) != 1:
                        raise VisualCalcError(f"{v.upper()} takes one expression")
                    aggs.append((f"__agg_{len(aggs)}", _AGG[v.upper()], self.value_col(a[0])))
                    out.append(("col", f"[{aggs[-1][0]}]"))
                    i = end + 1
                    continue
                out.append((k, v))
                i += 1
            return out
        rest = walk(toks)
        if not aggs:
            raise VisualCalcError(f"{fn} reaches several cells; its expression aggregates "
                                  f"them (SUM, AVERAGE, MIN, MAX, COUNT)")
        axes = list(tgt)
        for name, agg, y in aggs:
            def body(c, agg=agg, y=y):
                cond = " AND ".join(f"m.{_GRAIN[a]} = {tgt[a](c[a])}" for a in axes)
                call = (self._agg_call(agg, f"CASE WHEN {cond} THEN m.{y} END") + " "
                        + self._window_of(self._nav_part(c, tgt)))
                # A count of no cell is BLANK, not 0.
                return f"NULLIF({call}, 0)" if agg.startswith("COUNT") else call
            col = self.materialize(self._per_level(axes, body))
            self.refs[name] = ("lifted", col)
        try:
            sql = self.scalar(rest)
        finally:
            for name, _a, _y in aggs:
                self.refs.pop(name, None)
        self.used.append("engine.visual_calc_expand")
        return self.materialize(sql)

    def _collapse(self, args, nav_only=None):
        if len(args) < 2 and nav_only is None:
            raise VisualCalcError("COLLAPSE takes an expression and an axis or columns")
        expr, rest = (nav_only, args) if nav_only is not None else (args[0], args[1:])
        tgt = self._level_target("COLLAPSE", rest, up=True)
        self.template("COLLAPSE")
        return self._value_at(self.value_col(expr), tgt)

    def _collapseall(self, args, nav_only=None):
        expr, rest = (nav_only, args) if nav_only is not None else (args[0], args[1:])
        axis = self._axis_of(rest[0], "COLLAPSEALL") if len(rest) == 1 else None
        if axis is None:
            raise VisualCalcError("COLLAPSEALL takes an expression and an axis")
        self.template("COLLAPSEALL")
        return self._value_at(self.value_col(expr), {a: (lambda i: 0) for a in axis})

    def _expand(self, args, nav_only=None):
        if len(args) < 2 and nav_only is None:
            raise VisualCalcError("EXPAND takes an expression and an axis or columns")
        expr, rest = (nav_only, args) if nav_only is not None else (args[0], args[1:])
        tgt = self._level_target("EXPAND", rest, up=False)
        self.template("EXPAND")
        return self._set_at(expr, tgt, "EXPAND")

    def _expandall(self, args, nav_only=None):
        expr, rest = (nav_only, args) if nav_only is not None else (args[0], args[1:])
        axis = self._axis_of(rest[0], "EXPANDALL") if len(rest) == 1 else None
        if axis is None:
            raise VisualCalcError("EXPANDALL takes an expression and an axis")
        self.template("EXPANDALL")
        return self._set_at(expr, {a: (lambda i, d=self.mx.depth(a): d) for a in axis},
                            "EXPANDALL")

    def _lookup_cell(self, fn: str, args, totals: bool) -> str:
        """The value at the cell of the coordinates given: LOOKUP keeps the cell's own other
        coordinates (EXPLICIT) or those the given ones do not determine (INFERRED: on an axis
        given a coordinate, the others of that axis are free); LOOKUPWITHTOTALS totals every
        column not given. BLANK where no cell, or more than one, matches."""
        mx = self.mx
        if not args:
            raise VisualCalcError(f"{fn} takes an expression")
        expr, pairs = args[0], args[1:]
        mode = "EXPLICIT"
        if pairs and len(pairs) % 2 == 1 and _words(pairs[-1]) in (["EXPLICIT"],
                                                                   ["INFERRED"]):
            mode = _words(pairs[-1])[0]
            pairs = pairs[:-1]
        if len(pairs) % 2:
            raise VisualCalcError(f"{fn} takes pairs of a column and its value")
        y = self.value_col(expr)
        given: dict[int, str] = {}
        for c_toks, v_toks in zip(pairs[0::2], pairs[1::2]):
            if len(c_toks) != 1 or c_toks[0][0] not in ("col", "qualified", "quoted_qualified"):
                raise VisualCalcError(f"{fn}: a coordinate is a column of the visual")
            c = B._column_ref(c_toks[0][1]).lower()
            if c not in mx.slot_of:
                raise VisualCalcError(f"{fn}: [{c}] is not a column of the visual")
            given[mx.slot_of[c]] = self.value(v_toks)
        deepest = {a: max([j + 1 for i, (b, j, _c) in enumerate(mx.slots)
                           if b == a and i in given] or [0]) for a in AXES}
        match = []
        for i, lit in given.items():
            key = f"m.k{i}"
            if mx.kinds[i] == "text" and mx.text_key:
                match.append(f"UPPER(RTRIM({key})) = UPPER(RTRIM({lit}))")
            else:
                match.append(f"{key} = {lit}")
        free = {a for a in AXES if totals or (mode == "INFERRED" and deepest[a])}

        def body(c):
            lvl = {a: (deepest[a] if a in free else max(c.get(a, 0), deepest[a]))
                   for a in AXES}
            cond = " AND ".join([f"m.{_GRAIN[a]} = {lvl[a]}" for a in AXES] + match)
            part = [f"m.k{i}" for i, (a, j, _c) in enumerate(mx.slots)
                    if a not in free and i not in given and j < c.get(a, 0)]
            w = self._window_of(part)
            return (f"CASE WHEN COUNT(CASE WHEN {cond} THEN 1 END) {w} = 1 "
                    f"THEN MAX(CASE WHEN {cond} THEN m.{y} END) {w} END")
        varying = [a for a in AXES if a not in free and mx.depth(a)]
        # A coordinate set that leaves a column of the cell it reaches unnamed on some level
        # can match several cells, where Power BI raises an error (seen live) and the
        # statement shows BLANK.
        open_ = any(i not in given and j < (deepest[a] if a in free else
                                            max(c.get(a, 0), deepest[a]))
                    and (a in free or j >= c.get(a, 0))
                    for c in (self._combos(varying) or [{}])
                    for i, (a, j, _c) in enumerate(mx.slots))
        if open_:
            self.gotchas.append(
                f"{fn} names coordinates that can match several cells of the visual; there "
                f"Power BI shows an error, and this statement BLANK.")
        self.used.append("engine.visual_calc_lookup")
        return self.materialize(self._per_level(varying, body) if varying
                                else body({a: 0 for a in AXES}))

    def _lookup(self, args):
        self.template("LOOKUP")
        return self._lookup_cell("LOOKUP", args, totals=False)

    def _lookupwithtotals(self, args):
        self.template("LOOKUPWITHTOTALS")
        return self._lookup_cell("LOOKUPWITHTOTALS", args, totals=True)

    def _isatlevel(self, args):
        if len(args) != 1 or len(args[0]) != 1 or args[0][0][0] not in (
                "col", "qualified", "quoted_qualified"):
            raise VisualCalcError("ISATLEVEL takes one column of the visual")
        c = B._column_ref(args[0][0][1]).lower()
        if c not in self.mx.slot_of:
            raise VisualCalcError(f"ISATLEVEL: [{c}] is not a column of the visual")
        a, j, _c = self.mx.slots[self.mx.slot_of[c]]
        emit = self.template("ISATLEVEL")
        return self.materialize(emit["template"].replace("{0}", f"m.{_GRAIN[a]}")
                                .replace("{1}", str(j + 1)))

    def _calculate(self, args):
        """CALCULATE(expression, navigation...): the expression evaluated after each
        navigation (COLLAPSE / EXPAND / COLLAPSEALL / EXPANDALL with no expression), or an
        aggregate over a RANGE of cells."""
        if not args:
            raise VisualCalcError("CALCULATE takes an expression")
        expr, mods = args[0], args[1:]
        if not mods:
            return self.value_col(expr)
        if len(mods) == 1 and mods[0] and _words(mods[0][:1]) == ["RANGE"]:
            e = _normal(expr)
            if not (e and e[0][0] == "func" and e[0][1].upper() in _AGG
                    and _call_end(e, 1) == len(e) - 1):
                raise VisualCalcError("CALCULATE over a RANGE aggregates one expression "
                                      "(SUM, AVERAGE, MIN, MAX, COUNT)")
            inner = _split_args(e[2:-1])
            return self._range(_AGG[e[0][1].upper()], inner, _split_args(mods[0][2:-1]))
        out = expr
        for m in reversed(mods):
            head = _words(m[:1])
            nav = head[0] if head else ""
            if nav not in ("COLLAPSE", "COLLAPSEALL", "EXPAND", "EXPANDALL") or len(m) < 3:
                raise VisualCalcError(f"CALCULATE in a visual calculation takes COLLAPSE, "
                                      f"EXPAND, COLLAPSEALL, EXPANDALL or RANGE, not "
                                      f"{''.join(v for _k, v in m)!r}")
            col = getattr(self, "_" + nav.lower())(_split_args(m[2:-1]), nav_only=out)
            out = [("col", f"[{col}]")]
            self.colmap[col] = col
        return out[0][1][1:-1]


def compile_matrix(unit: dict, rules, dialect: str, compile_base, kw: dict) -> Compiled:
    """The unit's statements read off its visual's matrix: the leaf rows (`sql`) and each
    total the visual shows (`totals`), every one of the visual's calculations a column."""
    res = Compiled()
    try:
        mx = _Matrix(unit, rules, dialect, compile_base, kw)
        ctes = mx.matrix_sql()
        calcs = _Calcs(mx)
        for c in calcs.calcs:
            calcs.calc(c["name"].lower())
    except B.CompileError as exc:
        res.coverage = REFUSED
        res.reason = f"visual calculation: {exc}"
        res.untranslatable.append({"construct": "visual calculation", "why": str(exc)})
        return res
    res = mx.res
    res.coverage = res.coverage if res.coverage == PARTIAL else TRANSLATED
    res.rules_used += [x for x in dict.fromkeys(calcs.used + ["engine.visual_calc"])
                       if x not in res.rules_used]
    last = calcs.flush()
    with_ = "WITH " + ",\n".join(f"{n} AS (\n{_indent(s)}\n)" for n, s in ctes + calcs.ctes)
    leaf = mx.stmts[tuple(mx.depth(a) for a in AXES)]
    group = [str(g) for g in unit.get("group_by") or []]
    shown = [m for m in unit.get("measures") or [] if not m.get("hidden")]
    base_alias = {m["name"].lower(): leaf["values"][i] for i, m in enumerate(mx.base)}

    def value_of(m: dict) -> tuple[str, str]:
        if m.get("visual_calc"):
            return f"m.{calcs.done[m['name'].lower()]}", B._measure_alias(m["name"])
        i = next(i for i, b in enumerate(mx.base) if b["name"] == m["name"])
        return f"m.v{i}", base_alias[m["name"].lower()]

    def statement(g: dict) -> str:
        keep = {c.lower() for c in mx.cols_at(g)}
        sel = [f"m.k{mx.slot_of[x.lower()]} AS {leaf['keys'][x.lower()]}" for x in group
               if x.lower() in keep]
        vals = [value_of(m) for m in shown]
        sel += [f"{v} AS {a}" for v, a in vals]
        where = [f"m.{_GRAIN[a]} = {g[a]}" for a in AXES]
        if vals:
            where.append("(" + " OR ".join(f"{v} IS NOT NULL" for v, _a in vals) + ")")
        return (with_ + "\nSELECT " + ",\n       ".join(sel) + f"\nFROM   {last} m\nWHERE  "
                + "\n  AND  ".join(where))

    res.sql = statement({a: mx.depth(a) for a in AXES})
    for t in unit.get("totals") or []:
        keep = {str(x).lower() for x in t.get("group_by") or []}
        g = {}
        for a in AXES:
            n = 0
            while n < mx.depth(a) and all(c.lower() in keep for c in mx.axes[a][n]):
                n += 1
            g[a] = n
        kept = [x for x in group if x.lower() in {c.lower() for c in mx.cols_at(g)}]
        res.totals.append({"kind": t.get("kind") or ("subtotal" if kept else "grand_total"),
                           "group_by": kept, "sql": statement(g), "reason": None})
    res.gotchas += [g for g in dict.fromkeys(calcs.gotchas) if g not in res.gotchas]
    if unit.get("sort"):
        res.gotchas.append("The visual's calculations run along its axes in the order of the "
                           "axes' columns (or their own ORDERBY), not in the order the visual "
                           "is sorted by.")
    return res
