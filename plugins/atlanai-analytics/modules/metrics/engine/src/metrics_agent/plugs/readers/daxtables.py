# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A DAX CALCULATED TABLE as the statement that returns its rows.

A calculated table (`CALENDAR(...)`, `DATATABLE(...)`, `ADDCOLUMNS(...)`, `DISTINCT(T[c])`,
`SUMMARIZE(T, T[c], ...)`, `UNION(...)`, `GENERATESERIES(...)`) reads no warehouse object of
its own, so before this a unit grouped by one of its columns joined a table no warehouse has.
Each reader of a Power BI model hands the table's DAX here (`read_calculated_tables`); the
table then carries `source_lineage.statement` in the run's dialect, exactly as a table whose
Power Query changes its rows does, and the compiler reads it as that statement wherever it
joins it. A row series (the dates of a calendar, a what-if parameter's numbers) is dialect SQL
from the rule table (`dax.calendar`, `dax.generateseries`); every other form is plain SQL.

A table expression this does not read is a named refusal on the table
(`source_lineage.refusal`), never a guess.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from metrics_agent.lib import rules as _rules
from metrics_agent.plugs.readers import bicompile as _bc


class NotATable(_bc.CompileError):
    """A calculated table's DAX this module does not turn into a statement."""


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _out(name: str) -> str:
    """A column's name in the statement: the model's own name where it is a plain identifier
    (a formula naming `[Date]` then reads it as is), else a slug of it."""
    return name if _IDENT.match(name) else (re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
                                            or "column")


def _date(toks: list) -> dt.date:
    head, inner = _bc._call_head(toks)
    parts = _bc._split_top(inner) if head == "DATE" else []
    if len(parts) == 3 and all(len(p) == 1 and p[0][0] == "num" for p in parts):
        y, m, d = (int(float(p[0][1])) for p in parts)
        return dt.date(y, m, d)
    if len(toks) == 1 and toks[0][0] == "str":
        try:
            return dt.date.fromisoformat(toks[0][1].strip("\"'"))
        except ValueError:
            pass
    raise NotATable(f"a date that is not DATE(year, month, day): "
                    f"{_bc._text_of(toks)!r}")


def _number(toks: list) -> float:
    text = _bc._text_of(toks).replace(" ", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise NotATable(f"a number that is not a literal: {text!r}")
    return float(text)


def _literal(toks: list, dialect: str) -> str:
    if len(toks) == 1 and toks[0][0] == "str":
        return _bc._sql_string(toks[0][1], dialect)
    if len(toks) == 1 and toks[0][0] == "num":
        return toks[0][1]
    if len(toks) == 1 and toks[0][0] == "func" and toks[0][1].upper() in ("TRUE", "FALSE"):
        return toks[0][1].upper()
    if len(toks) == 2 and toks[0] == ("op", "-") and toks[1][0] == "num":
        return f"-{toks[1][1]}"
    head, _inner = _bc._call_head(toks)
    if head == "DATE":
        return f"CAST('{_date(toks).isoformat()}' AS DATE)"
    if head == "BLANK":
        return "NULL"
    raise NotATable(f"a DATATABLE value that is not a literal: {_bc._text_of(toks)!r}")


def _series_count(lo: float, hi: float, step: float) -> int:
    """How many values GENERATESERIES(lo, hi, step) holds. DAX adds the step to a running
    value in doubles and stops past the end (seen live: GENERATESERIES(0, 1, 0.1) has 11 rows,
    the last 0.9999999999999999; (0, 0.3, 0.1) has 3; (0.1, 0.7, 0.2) has 4; (0, 1, 0.05) has
    20), so a decimal step is counted the same way, never by dividing. Whole numbers add
    exactly. The values are written as start + i * step, which differ from DAX's running sum
    in the last binary digits only."""
    if all(float(x).is_integer() for x in (lo, hi, step)):
        return int((hi - lo) // step) + 1
    n, v = 0, lo
    while v <= hi:
        n += 1
        v += step
    return n


class _Reader:
    def __init__(self, rules, dialect: str, tables: dict[str, dict],
                 calculated: set[int] | frozenset = frozenset()) -> None:
        self.rules, self.dialect, self.tables = rules, dialect, tables
        self.reads: list[str] = []
        # The model's calculated tables (by id): CALENDARAUTO reads none of their dates.
        self.calculated = calculated

    def series(self, function: str, start: str, n: int, step: str, name: str) -> str:
        row = self.rules.function("dax", function, self.dialect) or {}
        emit = row.get("emit") or {}
        tmpl = (emit.get("dialect_templates") or {}).get(str(self.dialect).lower())
        if not tmpl:
            raise NotATable(f"{function} has no row series for the {self.dialect} dialect "
                            f"(the {row.get('id', 'dax.' + function.lower())} row)")
        # A dialect whose series is a fixed set of rows filtered to n (`max_rows`) holds no
        # more than that many: a longer series is refused rather than cut short.
        cap = (emit.get("max_rows") or {}).get(str(self.dialect).lower())
        if cap is not None and int(n) > int(cap):
            raise NotATable(f"{function} of {int(n)} rows: the {self.dialect} row series "
                            f"holds at most {int(cap)}")
        return (tmpl.replace("{0}", start).replace("{n}", str(int(n)))
                .replace("{step}", step).replace("{name}", name))

    def table(self, toks: list) -> tuple[str, list[str]]:
        """(the statement, its output columns in order)."""
        toks = _bc._strip_parens(toks)
        head, inner = _bc._call_head(toks)
        parts = _bc._split_top(inner) if head else []
        if head == "CALENDAR" and len(parts) == 2:
            try:
                lo, hi = _date(parts[0]), _date(parts[1])
            except NotATable:
                # Bounds the warehouse holds (MIN / MAX of a column, a DATE of their parts):
                # every day between them, read when the statement runs.
                return self.range_series(self.bounds(parts[0], parts[1]), "Date"), ["Date"]
            if hi < lo:
                raise NotATable("CALENDAR ends before it starts")
            sql = self.series("CALENDAR", f"CAST('{lo.isoformat()}' AS DATE)",
                              (hi - lo).days + 1, "1", "Date")
            return sql, ["Date"]
        if head == "CALENDARAUTO" and len(parts) <= 1:
            return self.range_series(self.auto_bounds(parts[0] if parts else None),
                                     "Date"), ["Date"]
        if head == "GENERATESERIES" and len(parts) in (2, 3):
            lo, hi = _number(parts[0]), _number(parts[1])
            step = _number(parts[2]) if len(parts) == 3 else 1.0
            if step <= 0 or hi < lo:
                raise NotATable("GENERATESERIES with an empty or a descending series")
            n = _series_count(lo, hi, step)

            def lit(v: float) -> str:
                return str(int(v)) if v == int(v) else repr(v)
            return self.series("GENERATESERIES", lit(lo), n, lit(step), "Value"), ["Value"]
        if head == "DATATABLE" and len(parts) >= 3 and len(parts) % 2 == 1:
            names = []
            for i in range(0, len(parts) - 1, 2):
                if len(parts[i]) != 1 or parts[i][0][0] != "str":
                    raise NotATable("DATATABLE's column names are strings")
                names.append(parts[i][0][1].strip("\"'"))
            rows_t = parts[-1]
            if not rows_t or rows_t[0] != ("brace", "{") or rows_t[-1] != ("brace", "}"):
                raise NotATable("DATATABLE's rows are a {{...}, ...} constructor")
            rows = []
            for r in _bc._split_top(rows_t[1:-1]):
                if not r or r[0] != ("brace", "{") or r[-1] != ("brace", "}"):
                    raise NotATable("each DATATABLE row is {value, ...}")
                vals = _bc._split_top(r[1:-1])
                if len(vals) != len(names):
                    raise NotATable("a DATATABLE row with another number of values")
                rows.append([_literal(v, self.dialect) for v in vals])
            if not rows:
                raise NotATable("an empty DATATABLE")
            sel = ["SELECT " + ", ".join(f"{v} AS {_out(n)}" for v, n in zip(rows[0], names))]
            sel += ["SELECT " + ", ".join(r) for r in rows[1:]]
            return "\nUNION ALL\n".join(sel), names
        if head in ("DISTINCT", "VALUES") and len(parts) == 1 and len(parts[0]) == 1 \
                and parts[0][0][0] in ("qualified", "quoted_qualified"):
            return self.distinct([parts[0][0][1]], _bc._table_ref(parts[0][0][1]))
        if head == "SUMMARIZE" and len(parts) >= 2 and all(
                len(p) == 1 and p[0][0] in ("qualified", "quoted_qualified") for p in parts[1:]):
            name = self.table_name(parts[0])
            return self.distinct([p[0][1] for p in parts[1:]], name)
        if head == "UNION" and len(parts) >= 2:
            subs = [self.table(p) for p in parts]
            cols = subs[0][1]
            if any(len(c) != len(cols) for _s, c in subs):
                raise NotATable("UNION of tables with another number of columns")
            # UNION keeps duplicates and takes the first table's column names (DAX).
            body = "\nUNION ALL\n".join(
                f"SELECT {', '.join(f'u{i}.{_out(c)} AS {_out(cols[k])}' for k, c in enumerate(cs))}"
                f"\nFROM   (\n{_indent(s)}\n) u{i}" for i, (s, cs) in enumerate(subs))
            return body, cols
        if head == "ADDCOLUMNS" and len(parts) >= 3 and len(parts) % 2 == 1:
            base, cols = self.table(parts[0])
            out = [f"b.{_out(c)} AS {_out(c)}" for c in cols]
            for i in range(1, len(parts), 2):
                if len(parts[i]) != 1 or parts[i][0][0] != "str":
                    raise NotATable("ADDCOLUMNS names each column with a string")
                name = parts[i][0][1].strip("\"'")
                expr = self.row_expression(parts[i + 1], cols)
                out.append(f"{expr} AS {_out(name)}")
                cols = cols + [name]
            return ("SELECT " + ",\n       ".join(out) + f"\nFROM   (\n{_indent(base)}\n) b",
                    cols)
        if head == "FILTER" and len(parts) == 2:
            # The rows of a table for which a row expression is TRUE (a BLANK is FALSE).
            base, cols = self.table(parts[0])
            pred = self.row_expression(parts[1], cols)
            return (f"SELECT " + ",\n       ".join(f"b.{_out(c)} AS {_out(c)}" for c in cols)
                    + f"\nFROM   (\n{_indent(base)}\n) b\nWHERE  {pred}", cols)
        if head == "SELECTCOLUMNS" and len(parts) >= 3 and len(parts) % 2 == 1:
            base, cols = self.table(parts[0])
            out, names = [], []
            for i in range(1, len(parts), 2):
                if len(parts[i]) != 1 or parts[i][0][0] != "str":
                    raise NotATable("SELECTCOLUMNS names each column with a string")
                name = parts[i][0][1].strip("\"'")
                out.append(f"{self.row_expression(parts[i + 1], cols)} AS {_out(name)}")
                names.append(name)
            return ("SELECT " + ",\n       ".join(out) + f"\nFROM   (\n{_indent(base)}\n) b",
                    names)
        if head is None and len(toks) == 1 and toks[0][0] in ("func", "str"):
            # A table of the model by its name: its rows, every column it reads.
            t = self.tables.get(self.table_name(toks).lower())
            if t is None or str(t.get("name") or "").startswith("calculated."):
                raise NotATable(f"{_bc._text_of(toks)!r} is not a table the warehouse holds")
            names = [c["name"] for c in t.get("columns") or [] if not c.get("is_calculated")]
            if not names:
                raise NotATable(f"{_bc._text_of(toks)!r} has no column the reader knows")
            if t["name"] not in self.reads:
                self.reads.append(t["name"])
            sel = [f"s.{c.get('source_column') or c['name']} AS {_out(c['name'])}"
                   for c in t.get("columns") or [] if not c.get("is_calculated")]
            return "SELECT " + ",\n       ".join(sel) + f"\nFROM   {t['name']} s", names
        raise NotATable(f"the calculated table {_bc._text_of(toks)[:80]!r} is not a "
                        f"CALENDAR, CALENDARAUTO, GENERATESERIES, DATATABLE, DISTINCT / VALUES, "
                        f"SUMMARIZE of one table's columns, UNION, ADDCOLUMNS, FILTER, "
                        f"SELECTCOLUMNS or a table")

    def range_series(self, bounds: str, name: str) -> str:
        row = self.rules.function("dax", "CALENDAR", self.dialect) or {}
        tmpl = ((row.get("emit") or {}).get("range_templates") or {}).get(
            str(self.dialect).lower())
        if not tmpl:
            raise NotATable(f"CALENDAR between bounds the warehouse holds has no row series "
                            f"for the {self.dialect} dialect (the dax.calendar row's "
                            f"range_templates)")
        return tmpl.replace("{bounds}", _indent(bounds)).replace("{name}", name)

    def column_sql(self, ref: str) -> tuple[str, str]:
        """(the warehouse table, its column) a `Table[Column]` of the model reads."""
        table = _bc._table_ref(ref)
        t = self.tables.get(str(table or "").lower())
        if t is None or str(t.get("name") or "").startswith("calculated."):
            raise NotATable(f"{ref.strip()} is not a column of a table the warehouse holds")
        col = _bc._column_ref(ref)
        src = {(c.get("name") or "").lower(): (c.get("source_column") or c["name"])
               for c in t.get("columns") or [] if not c.get("is_calculated")}
        if col.lower() not in src:
            raise NotATable(f"{table}[{col}] is not a column the table reads")
        if t["name"] not in self.reads:
            self.reads.append(t["name"])
        return t["name"], src[col.lower()]

    def bounds(self, lo: list, hi: list) -> str:
        """A one-row statement of `lo` and `hi`: each bound a DAX date expression whose MIN /
        MAX of a column is read from the warehouse table that holds it."""
        aggs: list[str] = []

        def swap(toks: list) -> str:
            out, i = [], 0
            while i < len(toks):
                k, v = toks[i]
                if k == "func" and v.upper() in ("MIN", "MAX", "FIRSTDATE", "LASTDATE") \
                        and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
                    end, args = _bc._call_args(toks, i + 1)
                    if end is None or len(args) != 1 or len(args[0]) != 1 \
                            or args[0][0][0] not in ("qualified", "quoted_qualified"):
                        raise NotATable(f"a calendar bound {v}(...) that is not of one column")
                    table, col = self.column_sql(args[0][0][1])
                    fn = "MIN" if v.upper() in ("MIN", "FIRSTDATE") else "MAX"
                    aggs.append(f"(SELECT {fn}(s.{col}) FROM {table} s) AS a{len(aggs)}")
                    out.append(("col", f"[a{len(aggs) - 1}]"))
                    i = end + 1
                    continue
                if k in ("qualified", "quoted_qualified"):
                    raise NotATable(f"a calendar bound reads {v.strip()} outside MIN / MAX")
                out.append((k, v))
                i += 1
            return _bc._text_of(out)
        texts = [swap(b) for b in (lo, hi)]
        if not aggs:
            raise NotATable("CALENDAR's bounds read no column and are not DATE literals")
        return self.bounds_of(texts[0], texts[1], "SELECT " + ",\n       ".join(aggs))

    def auto_bounds(self, fy_end: list | None) -> str:
        """CALENDARAUTO([fiscal year end month]): from the first day of the fiscal year of
        the model's earliest date to the last day of the fiscal year of its latest, the
        dates of every date column of a table the warehouse holds (not a calculated table or
        column), as the documentation states."""
        m = 12
        if fy_end:
            m = int(_number(fy_end))
            if not 1 <= m <= 12:
                raise NotATable("CALENDARAUTO's fiscal year end month is 1 to 12")
        refs = []
        for key, t in self.tables.items():
            if id(t) in self.calculated or str(t.get("name") or "").startswith("calculated.") \
                    or t.get("_dax_table") \
                    or key != str(t.get("logical_name") or t.get("name") or "").lower():
                continue
            for c in t.get("columns") or []:
                if not c.get("is_calculated") and str(c.get("data_type") or "").lower() in (
                        "datetime", "date", "datetimeoffset"):
                    name = str(t.get("logical_name") or t["name"]).replace("'", "''")
                    refs.append(f"'{name}'[{c['name']}]")
        if not refs:
            raise NotATable("CALENDARAUTO in a model with no date column of a warehouse table")
        ends = []
        for r in refs:
            table, col = self.column_sql(r)
            ends += [f"SELECT MIN(s.{col}) AS d FROM {table} s",
                     f"SELECT MAX(s.{col}) AS d FROM {table} s"]
        inner = ("SELECT MIN(u.d) AS a0, MAX(u.d) AS a1\nFROM   (\n"
                 + _indent("\nUNION ALL\n".join(ends)) + "\n) u")
        k = 12 - m
        return self.bounds_of(
            f"EDATE ( DATE ( YEAR ( EDATE ( [a0] , {k} ) ) , 1 , 1 ) , {-k} )",
            f"EOMONTH ( DATE ( YEAR ( EDATE ( [a1] , {k} ) ) , 12 , 31 ) , {-k} )", inner)

    def bounds_of(self, lo: str, hi: str, inner: str) -> str:
        """The one-row statement of `lo` and `hi`, two DAX date expressions over the columns
        of `inner` (a one-row statement)."""
        try:
            exprs = [_bc.compile_expression(b, "dax", self.rules, "x", self.dialect)[0]
                     for b in (lo, hi)]
        except _bc.CompileError as exc:
            raise NotATable(f"a calendar bound does not compile: {exc}") from exc
        return (f"SELECT {exprs[0]} AS lo,\n       {exprs[1]} AS hi\nFROM   (\n"
                + _indent(inner) + "\n) x")

    def table_name(self, toks: list) -> str:
        if len(toks) == 1 and toks[0][0] in ("func", "str"):
            v = toks[0][1]
            return v[1:-1].replace("''", "'") if v.startswith("'") else v
        raise NotATable(f"a table that is not named: {_bc._text_of(toks)!r}")

    def distinct(self, refs: list[str], table: str | None) -> tuple[str, list[str]]:
        t = self.tables.get(str(table or "").lower())
        if t is None or any(str(_bc._table_ref(r) or table).lower() != str(table).lower()
                            for r in refs):
            raise NotATable("DISTINCT / SUMMARIZE over columns of more than one table, or of "
                            "a table the model does not hold")
        src = {(c.get("name") or "").lower(): (c.get("source_column") or c["name"])
               for c in t.get("columns") or [] if not c.get("is_calculated")}
        names, sel = [], []
        for r in refs:
            col = _bc._column_ref(r)
            phys = src.get(col.lower())
            if phys is None:
                raise NotATable(f"{table}[{col}] is not a column the table reads")
            names.append(col)
            sel.append(f"s.{phys} AS {_out(col)}")
        self.reads.append(t["name"])
        return (f"SELECT DISTINCT {', '.join(sel)}\nFROM   {t['name']} s", names)

    def row_expression(self, toks: list, cols: list[str]) -> str:
        text = _bc._text_of(toks)
        for c in cols:
            # `[Date]` and `Calendar[Date]` both name the base table's column.
            text = re.sub(rf"(?:'[^']*'|\b\w+)?\[{re.escape(c)}\]", f"[{_out(c)}]", text)
        try:
            sql, _p = _bc.compile_expression(text, "dax", self.rules, "b", self.dialect)
        except _bc.CompileError as exc:
            raise NotATable(f"an ADDCOLUMNS expression does not compile: {exc}") from exc
        return sql


def _indent(sql: str) -> str:
    return "\n".join("    " + ln if ln.strip() else ln for ln in sql.splitlines())


def _json_literal(toks: list):
    """A DATATABLE value as the JSON value `fixed_rows` holds, or raises NotATable."""
    if len(toks) == 1 and toks[0][0] == "str":
        return toks[0][1][1:-1].replace(toks[0][1][0] * 2, toks[0][1][0])
    if len(toks) == 1 and toks[0][0] == "num":
        v = float(toks[0][1])
        return int(v) if v.is_integer() and "." not in toks[0][1] else v
    if len(toks) == 2 and toks[0] == ("op", "-") and toks[1][0] == "num":
        v = -float(toks[1][1])
        return int(v) if v.is_integer() and "." not in toks[1][1] else v
    if len(toks) == 1 and toks[0][0] == "func" and toks[0][1].upper() in ("TRUE", "FALSE"):
        return toks[0][1].upper() == "TRUE"
    head, _inner = _bc._call_head(toks)
    if head == "DATE":
        return _date(toks).isoformat()
    if head == "BLANK":
        return None
    raise NotATable(f"a DATATABLE value that is not a literal: {_bc._text_of(toks)!r}")


def fixed_rows(expression: str) -> dict | None:
    """The rows a calculated table's DAX fixes by itself, as `bi_facts` `fixed_rows`: a
    GENERATESERIES as its series (start, step, count, counted as DAX adds the step), a
    DATATABLE as its literal rows. None for a table whose rows depend on anything else (a
    CALENDAR is a series too, but of dates a filter is read on through its relationship)."""
    try:
        toks = _bc._strip_parens(_bc.tokenize(expression or ""))
    except _bc.CompileError:
        return None
    head, inner = _bc._call_head(toks)
    parts = _bc._split_top(inner) if head else []
    try:
        if head == "GENERATESERIES" and len(parts) in (2, 3):
            lo, hi = _number(parts[0]), _number(parts[1])
            step = _number(parts[2]) if len(parts) == 3 else 1.0
            if step <= 0 or hi < lo:
                return None

            def num(v: float):
                return int(v) if float(v).is_integer() else v
            return {"columns": ["Value"], "series": {
                "start": num(lo), "step": num(step), "count": _series_count(lo, hi, step)}}
        if head == "DATATABLE" and len(parts) >= 3 and len(parts) % 2 == 1:
            names = [p[0][1].strip("\"'") for p in parts[:-1:2]
                     if len(p) == 1 and p[0][0] == "str"]
            rows_t = parts[-1]
            if len(names) * 2 != len(parts) - 1 or not rows_t \
                    or rows_t[0] != ("brace", "{") or rows_t[-1] != ("brace", "}"):
                return None
            rows = []
            for r in _bc._split_top(rows_t[1:-1]):
                if not r or r[0] != ("brace", "{") or r[-1] != ("brace", "}"):
                    return None
                vals = _bc._split_top(r[1:-1])
                if len(vals) != len(names):
                    return None
                rows.append([_json_literal(v) for v in vals])
            return {"columns": names, "rows": rows}
    except NotATable:
        return None
    return None


def table_statement(expression: str, dialect: str | None, tables: dict[str, dict],
                    rules: Any = None, calculated: set[int] | frozenset = frozenset()
                    ) -> tuple[str, list[str], list[str]]:
    """(statement, output columns, the warehouse tables it reads) for one calculated table's
    DAX, or `NotATable` naming what it does not read."""
    if not dialect:
        raise NotATable("a calculated table is written in the run's SQL dialect, and none "
                        "was given")
    try:
        toks = _bc.tokenize(expression or "")
    except _bc.CompileError as exc:
        raise NotATable(f"its DAX does not parse: {exc}") from exc
    reader = _Reader(rules or _rules.load(_bc.RULES), dialect, tables, calculated)
    sql, cols = reader.table(toks)
    return sql, cols, reader.reads


def read_calculated_tables(tables: list[dict], dialect: str | None) -> list[str]:
    """Each table holding `_dax_table` (its calculated partition's DAX, left by the reader)
    gets the statement of its rows (`source_lineage.statement`, `object_kind: statement`), its
    columns read under the statement's names, and a name no warehouse table has; one this does
    not read gets `source_lineage.refusal`. A field switch (a table of NAMEOF rows) is the
    reader's own business and is left as it is. Returns notes, one per refused table."""
    notes: list[str] = []
    by_name: dict[str, dict] = {}
    for t in tables:
        for key in {str(t.get("logical_name") or ""), str(t.get("name") or "")} - {""}:
            by_name.setdefault(key.lower(), t)
    calculated = {id(t) for t in tables if t.get("_dax_table")}
    for t in tables:
        dax = t.pop("_dax_table", None)
        lin = t.get("source_lineage") or {}
        if not dax or t.get("field_choices") or lin.get("object") or lin.get("statement"):
            # A field switch, or a table another partition (Power QUERY) already reads.
            continue
        logical = t.get("logical_name") or t["name"]
        try:
            sql, cols, reads = table_statement(dax, dialect, by_name, calculated=calculated)
        except NotATable as exc:
            t["source_lineage"] = {"object": None, "object_kind": "statement",
                                   "refusal": f"calculated table {logical!r}: {exc}"}
            notes.append(f"calculated table {logical!r} has no statement: {exc}")
            continue
        outs = {c.lower(): _out(c) for c in cols}
        for c in t.get("columns") or []:
            if c.get("is_calculated"):
                continue
            src = str(c.get("source_column") or c["name"]).strip("[]")
            if src.lower() in outs:
                c["source_column"] = outs[src.lower()]
            elif c["name"].lower() in outs:
                c["source_column"] = outs[c["name"].lower()]
        t["source_lineage"] = {"object": None, "object_kind": "statement", "statement": sql,
                               "reads": reads, "raw_expression": None}
        fixed = fixed_rows(dax)
        if fixed:
            # Which rows a filter keeps is known from the file: a table no relationship
            # reaches is read as a parameter (bicompile `_fixed_tables`).
            t["fixed_rows"] = {**fixed, "columns": [_out(c) for c in fixed["columns"]]}
        t["logical_name"] = logical
        t["name"] = "calculated.dax." + (re.sub(r"[^a-z0-9_]+", "_", str(logical).lower())
                                         .strip("_") or "table") + "__calculated"
    return notes
