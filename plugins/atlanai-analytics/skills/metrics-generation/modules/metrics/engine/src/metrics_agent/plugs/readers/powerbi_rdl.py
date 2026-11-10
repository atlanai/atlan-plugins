# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Paginated reports (`.rdl`, Report Definition Language XML) -> the `bi_facts` contract.

A paginated report (Power BI Report Builder, SQL Server Reporting Services) carries its own
queries. Each `DataSet` has a `Query` naming a `DataSourceName` and a `CommandText`, the
`Fields` that query returns, and its query parameters; report items (`Tablix`, `Chart`) read one
dataset each and aggregate its fields with expressions such as `=Sum(Fields!Amount.Value)`,
grouped by their `GroupExpressions`.

THE ASSUMED SHAPE (local element names; the namespace is the 2008, 2010 or 2016 report
definition schema, matched by local name so all three read the same):

    Report
      DataSources/DataSource[@Name]   ConnectionProperties/{DataProvider, ConnectString}
                                      or DataSourceReference
      DataSets/DataSet[@Name]         Query/{DataSourceName, CommandType, CommandText,
                                             QueryParameters/QueryParameter[@Name]/Value}
                                      Fields/Field[@Name]/{DataField | Value}
                                      Filters/Filter                     (dataset filters)
                                      SharedDataSet                      (refused: no query)
      ReportParameters/ReportParameter[@Name]/{DataType, DefaultValue/Values/Value}
      Body ... Tablix[@Name]          DataSetName, Filters, TablixBody/TablixRows/TablixRow/
                                      TablixCells/TablixCell/CellContents/Textbox[@Name]
                                      (its value: Paragraphs/Paragraph/TextRuns/TextRun/Value;
                                      its format: Style/Format), TablixRowHierarchy and
                                      TablixColumnHierarchy (TablixMember/Group/GroupExpressions)
      Body ... Chart[@Name]           DataSetName, Filters, ChartTitles/ChartTitle/Caption,
                                      ChartCategoryHierarchy and ChartSeriesHierarchy
                                      (ChartMember/Group/GroupExpressions),
                                      ChartData/ChartSeriesCollection/ChartSeries[@Name]/
                                      ChartDataPoints/ChartDataPoint/ChartDataPointValues/Y

WHAT IT WRITES.

* A **SQL dataset** is a custom-SQL table: the statement is its source (`native_sql`,
  `statement_is_source`), its fields are the columns (`native_columns`: field name -> output
  column), and its lineage is the table the statement reads FROM, parsed from the SQL. A query
  parameter with a static default is fixed at that default in the statement, and the run says
  so; one with no static default is left as written and said out loud (the statement will not
  run until it is fixed).
* Each **Tablix or Chart** is one question unit: its aggregate expressions are measures, in the
  core's own `sql` (`=Sum(Fields!Amount.Value)` is `SUM(amount)` over the dataset's output),
  grouped by the fields of its group expressions, filtered by its data-region filters. A
  measure is named by the header text above its cell (a chart's: its series label), else by
  its textbox's or series' own name when the author gave one, else by the aggregate
  (`Sum of Amount`); the unit's title is
  the chart title or the data region's name; its number format is the cell's `Format`.
* An expression this reader cannot translate exactly (`IIF`, `Lookup`, a scope argument such as
  `Sum(x, "DataSet1")`, a running value, a group filter, a computed group) is NAMED in the run's
  notes and its measure or unit is left out — never approximated.
* A dataset whose command is **DAX or MDX against a Power BI model** (or Analysis Services) is
  refused BY NAME: its numbers are the model's, so supply the model instead. A shared dataset
  (`.rsd`), a stored procedure or an expression-built command is refused by name too. A
  report with no dataset left to read is refused as a whole.

XML SAFETY. A report never declares a DTD, so one that does (entity expansion, external
entities) is refused before the tree parser sees it, as the Tableau reader does.
"""
from __future__ import annotations

import pathlib
import re
import xml.etree.ElementTree as ET
from typing import Any
from xml.parsers import expat

from metrics_agent.plugs.readers import mquery as _mquery
from metrics_agent.plugs.readers.bicompile import FormulaCompiler, _ident

NAMESPACES = (
    "http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2010/01/reportdefinition",
    "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition",
)
# The data providers and connection strings of a dataset whose numbers come from a model,
# not from a SQL statement: a Power BI semantic model (XMLA) or Analysis Services.
_MODEL_PROVIDERS = {"pbidataset", "msolap", "ssas", "olap", "analysisservices"}
_MODEL_CONNECT = re.compile(r"pbiazure://|powerbi://|\bprovider\s*=\s*msolap", re.I)
_DAX = re.compile(r"^\s*(EVALUATE|DEFINE)\b", re.I)
_MDX = re.compile(r"\bON\s+(COLUMNS|ROWS|AXIS\s*\()", re.I)
_DEFAULT_NAME = re.compile(r"^(Textbox|Series|Chart|Tablix)\d*$", re.I)
_FIELD = re.compile(r"^=\s*Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value\s*$", re.I)
_PARAM_REF = re.compile(r"^=\s*Parameters!([A-Za-z_][A-Za-z0-9_]*)\.Value\s*$", re.I)
_AGG = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
        "countdistinct": "COUNT", "countrows": "COUNT"}
# RDL filter operators -> the rule table's operators (`bi_compiler_rules.yml`). A bound is a
# range, as the other readers write one.
_OPS = {"equal": "eq", "notequal": "neq", "greaterthan": "gt", "lessthan": "lt", "in": "in"}


class RDLError(ValueError):
    pass


class _Untranslatable(Exception):
    """An expression construct with no exact SQL form here: named, never approximated."""


# --------------------------------------------------------------------------- XML
def _refuse_declarations(label: str, data: bytes) -> None:
    """A report never carries a DTD; one that does is refused before parsing (a copy of the
    Tableau reader's check: entity expansion and external entities start there)."""
    def refuse(what: str):
        def handler(*_args) -> None:
            raise RDLError(f"{label}: declares {what}. A report definition never carries one; "
                           "refused before parsing.")
        return handler

    p = expat.ParserCreate()
    p.StartDoctypeDeclHandler = refuse("a DOCTYPE")
    p.EntityDeclHandler = refuse("an ENTITY")
    p.ExternalEntityRefHandler = refuse("an external entity reference")
    try:
        p.Parse(data, True)
    except expat.ExpatError:
        return


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _ns(tag: Any) -> str:
    t = str(tag)
    return t[1:].split("}", 1)[0] if t.startswith("{") else ""


def _kids(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [c for c in (el if el is not None else []) if _local(c.tag) == name]


def _kid(el: ET.Element | None, name: str) -> ET.Element | None:
    found = _kids(el, name)
    return found[0] if found else None


def _path(el: ET.Element | None, *names: str) -> ET.Element | None:
    for n in names:
        el = _kid(el, n)
        if el is None:
            return None
    return el


def _txt(el: ET.Element | None, *names: str) -> str | None:
    el = _path(el, *names) if names else el
    return (el.text or "").strip() if el is not None and el.text is not None else None


def _all(el: ET.Element, name: str) -> list[ET.Element]:
    return [x for x in el.iter() if _local(x.tag) == name]


def read_report(path: pathlib.Path) -> ET.Element:
    data = pathlib.Path(path).read_bytes()
    _refuse_declarations(pathlib.Path(path).name, data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise RDLError(f"{pathlib.Path(path).name} is not readable XML: {exc}") from None
    if _local(root.tag) != "Report":
        raise RDLError(f"{pathlib.Path(path).name} is not a report definition: its root is "
                       f"<{_local(root.tag)}>, not <Report>.")
    return root


# --------------------------------------------------------------------------- expressions
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<field>Fields!(?P<fname>[A-Za-z_][A-Za-z0-9_]*)\.Value)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_!.]*)
  | (?P<op>[-+*/(),]|[<>=&]+)
""", re.X | re.I)


def _tokens(expr: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(expr):
        m = _TOKEN.match(expr, i)
        if not m:
            raise _Untranslatable(f"the character {expr[i]!r}")
        i = m.end()
        if m.group("ws"):
            continue
        if m.group("field"):
            out.append(("field", m.group("fname")))
        elif m.group("num"):
            out.append(("num", m.group("num")))
        elif m.group("str"):
            out.append(("str", m.group("str")))
        elif m.group("ident"):
            out.append(("ident", m.group("ident")))
        else:
            out.append(("op", m.group("op")))
    return out


class _Expr:
    """A small, exact translator: aggregates of field arithmetic, and arithmetic of those.
    Anything else raises `_Untranslatable` with the construct named."""

    def __init__(self, expr: str, column_of: dict[str, str]):
        self.toks = _tokens(expr)
        self.i = 0
        self.column_of = column_of
        self.in_agg = False
        self.aggs: list[tuple[str, str | None]] = []

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise _Untranslatable("an incomplete expression")
        self.i += 1
        return tok

    def parse(self) -> str:
        out = self.expr()
        if self.peek() is not None:
            raise _Untranslatable(f"{self.peek()[1]!r} after a complete expression")
        return out

    def expr(self) -> str:
        out = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            out = f"{out} {self.take()[1]} {self.term()}"
        return out

    def term(self) -> str:
        out = self.factor()
        while self.peek() in (("op", "*"), ("op", "/")):
            out = f"{out} {self.take()[1]} {self.factor()}"
        return out

    def factor(self) -> str:
        kind, val = self.take()
        if (kind, val) == ("op", "("):
            inner = self.expr()
            if self.take() != ("op", ")"):
                raise _Untranslatable("an unclosed parenthesis")
            return f"({inner})"
        if (kind, val) == ("op", "-"):
            return f"-{self.factor()}"
        if kind == "num":
            return val
        if kind == "field":
            if not self.in_agg:
                raise _Untranslatable(f"Fields!{val}.Value outside an aggregate")
            col = self.column_of.get(val.lower())
            if col is None:
                raise _Untranslatable(f"Fields!{val}.Value, which is not a column of the "
                                      "dataset's query")
            return _ident(col)
        if kind == "ident" and self.peek() == ("op", "("):
            return self.call(val)
        raise _Untranslatable(f"{val!r}")

    def call(self, fn: str) -> str:
        self.take()                                     # (
        agg = _AGG.get(fn.lower())
        if agg is None:
            raise _Untranslatable(f"the function {fn}")
        if self.in_agg:
            raise _Untranslatable(f"{fn} inside another aggregate")
        if fn.lower() == "countrows":
            if self.take() != ("op", ")"):
                raise _Untranslatable("CountRows with a scope argument")
            self.aggs.append((fn, None))
            return "COUNT(*)"
        self.in_agg = True
        inner = self.expr()
        self.in_agg = False
        nxt = self.take()
        if nxt == ("op", ","):
            raise _Untranslatable(f"{fn} with a scope argument")
        if nxt != ("op", ")"):
            raise _Untranslatable(f"{fn}(...) is not closed")
        self.aggs.append((fn, inner))
        if fn.lower() == "countdistinct":
            return f"COUNT(DISTINCT {inner})"
        return f"{agg}({inner})"


def translate(expr: str, column_of: dict[str, str]) -> tuple[str, list] | None:
    """`=Sum(Fields!X.Value)` -> (`SUM(x)`, its aggregates), None when the expression holds no
    aggregate (a label, a detail field); raises `_Untranslatable` naming what it cannot do."""
    text = (expr or "").strip()
    if not text.startswith("="):
        return None
    body = text[1:].strip()
    p = _Expr(body, column_of)
    has_agg = any(k == "ident" and v.lower() in _AGG and i + 1 < len(p.toks)
                  and p.toks[i + 1] == ("op", "(") for i, (k, v) in enumerate(p.toks))
    if has_agg:
        for k, v in p.toks:
            if k == "ident" and v.lower() not in _AGG:
                # IIF, Lookup, RunningValue, Format...: named before any character trips the
                # tokenizer, so the note says which construct it was.
                raise _Untranslatable(f"the function or name {v}")
    calls = [v for i, (k, v) in enumerate(p.toks)
             if k == "ident" and i + 1 < len(p.toks) and p.toks[i + 1] == ("op", "(")]
    if not has_agg and calls and any(k == "field" for k, _v in p.toks) \
            and not all(c.lower() in ("format", "formatnumber", "cstr", "trim", "ucase",
                                      "lcase") for c in calls):
        # RunningValue, Lookup, Previous, IIF over a field...: may well be the number the
        # region shows, and it has no exact SQL form here. Named, never dropped quietly.
        raise _Untranslatable(f"the function {calls[0]}")
    if not has_agg:
        # A label, a detail field or a computed text (IIF, Format...) with no aggregate in
        # it: not a number the region computes.
        return None
    return p.parse(), p.aggs


def _derived_name(aggs: list, expr: str) -> str:
    if len(aggs) == 1:
        fn, _inner = aggs[0]
        fields = re.findall(r"Fields!([A-Za-z_][A-Za-z0-9_]*)\.Value", expr, re.I)
        if fn.lower() == "countrows":
            return "Count of Rows"
        if len(set(fields)) == 1:
            return f"{fn[0].upper()}{fn[1:]} of {fields[0]}"
    return re.sub(r"\s+", " ", expr.lstrip("= ").strip())


# --------------------------------------------------------------------------- datasets
def _sql_param_default(rp: ET.Element) -> tuple[str | None, str | None]:
    """A report parameter's STATIC default as a SQL literal, and as written; (None, None) when
    it has none or it is an expression."""
    values = _all(_kid(rp, "DefaultValue") or ET.Element("x"), "Value")
    if len(values) != 1 or values[0].text is None:
        return None, None
    raw = values[0].text.strip()
    if raw.startswith("="):
        lit = re.match(r'^=\s*"((?:[^"]|"")*)"\s*$', raw)
        if lit:
            raw = lit.group(1).replace('""', '"')
        elif re.match(r"^=\s*-?\d+(\.\d+)?\s*$", raw):
            raw = raw.lstrip("= ").strip()
        else:
            return None, None
    dtype = (_txt(rp, "DataType") or "String").lower()
    if dtype in ("integer", "float"):
        if not re.match(r"^-?\d+(\.\d+)?$", raw):
            return None, None
        return raw, raw
    if dtype == "boolean":
        if raw.lower() not in ("true", "false"):
            return None, None
        return raw.upper(), raw
    return "'" + raw.replace("'", "''") + "'", raw


def _bind_parameters(sql: str, query: ET.Element, defaults: dict[str, tuple]) -> tuple[
        str, list[dict], list[str]]:
    """The statement with each query parameter fixed at its report parameter's static default;
    the parameters (for the units) and the names left unbound."""
    params, unbound = [], []
    for qp in _all(query, "QueryParameter"):
        name = (qp.get("Name") or "").strip()
        if not name:
            continue
        bare = name.lstrip("@:")
        value = _txt(qp, "Value") or ""
        ref = _PARAM_REF.match(value)
        literal, shown = defaults.get(ref.group(1).lower(), (None, None)) if ref else (None, None)
        if literal is None and value and not value.startswith("="):
            literal, shown = "'" + value.replace("'", "''") + "'", value
        params.append({"name": bare, "default": shown})
        if literal is None:
            unbound.append(bare)
            continue
        noise = _mquery._strip_sql_noise(sql)
        pat = re.compile(rf"[@:]{re.escape(bare)}(?![A-Za-z0-9_])", re.I)
        # Only where the blanked statement still holds it: never inside a string or comment.
        spans = [m.span() for m in pat.finditer(noise)]
        for start, end in reversed(spans):
            sql = sql[:start] + literal + sql[end:]
    return sql, params, unbound


def _output_column(data_field: str, sql: str) -> str:
    """The name the statement's output gives a field: its quoted spelling where the query
    quotes it (a quoted alias keeps its case), else lower-cased, as the other readers write a
    source column (bare, the warehouse resolves it case-insensitively)."""
    for q in re.findall(r'"([^"]+)"|\[([^\]]+)\]', sql):
        name = q[0] or q[1]
        if name.lower() == data_field.lower() and name != name.lower():
            return name
    return data_field.lower()


def _filters(el: ET.Element | None, field_of: dict[str, str], defaults: dict[str, tuple],
             scope: str) -> tuple[list[dict], int | None]:
    """A data region's (or dataset's) filters -> the common filter shape, and a Top N."""
    out: list[dict] = []
    top_n = None
    for f in _all(el, "Filter") if el is not None else []:
        expr = _txt(f, "FilterExpression") or ""
        op = (_txt(f, "Operator") or "Equal").lower()
        vals = []
        for v in _all(_kid(f, "FilterValues") or ET.Element("x"), "FilterValue"):
            raw = (v.text or "").strip()
            ref = _PARAM_REF.match(raw)
            if ref:
                _lit, shown = defaults.get(ref.group(1).lower(), (None, None))
                if shown is None:
                    raise _Untranslatable(f"a filter on a parameter with no static default "
                                          f"({ref.group(1)})")
                raw = shown
            elif raw.startswith("="):
                lit = re.match(r'^=\s*"((?:[^"]|"")*)"\s*$', raw)
                num = re.match(r"^=\s*(-?\d+(?:\.\d+)?)\s*$", raw)
                if lit:
                    raw = lit.group(1).replace('""', '"')
                elif num:
                    raw = num.group(1)
                else:
                    raise _Untranslatable(f"a filter value written as an expression ({raw})")
            dtype = (v.get("DataType") or "").lower()
            vals.append(float(raw) if dtype == "float" else int(raw) if dtype == "integer"
                        else raw)
        if op in ("topn",):
            if len(vals) != 1:
                raise _Untranslatable("a Top N with no count")
            top_n = int(vals[0])
            continue
        m = _FIELD.match(expr)
        if not m or m.group(1).lower() not in field_of:
            raise _Untranslatable(f"a filter on the expression {expr or '(empty)'}")
        column = field_of[m.group(1).lower()]
        if op in _OPS:
            value: Any = vals if op == "in" else (vals[0] if len(vals) == 1 else None)
            if value is None:
                raise _Untranslatable(f"a {op} filter with {len(vals)} values")
            out.append({"scope": scope, "stage": "dimension", "column": column,
                        "op": _OPS[op], "value": value, "evidence": f"rdl {scope} filter"})
        elif op in ("greaterthanorequal", "lessthanorequal", "between"):
            bounds = ({"min": vals[0]} if op == "greaterthanorequal" else
                      {"max": vals[0]} if op == "lessthanorequal" else
                      {"min": vals[0], "max": vals[1]} if len(vals) == 2 else None)
            if not bounds or (op != "between" and len(vals) != 1):
                raise _Untranslatable(f"a {op} filter with {len(vals)} values")
            out.append({"scope": scope, "stage": "dimension", "column": column,
                        "op": "range", "value": bounds, "evidence": f"rdl {scope} filter"})
        else:
            raise _Untranslatable(f"the filter operator {_txt(f, 'Operator')}")
    return out, top_n


def _datasets(root: ET.Element, defaults: dict[str, tuple], notes: list[str]) -> tuple[
        dict[str, dict], list[str]]:
    """{dataset name: what a unit over it needs}, and the datasets refused, each by name."""
    sources = {}
    for ds in _all(root, "DataSource"):
        if _local(ds.tag) != "DataSource" or not ds.get("Name"):
            continue
        sources[ds.get("Name")] = {
            "provider": (_txt(ds, "ConnectionProperties", "DataProvider") or "").strip(),
            "connect": _txt(ds, "ConnectionProperties", "ConnectString") or "",
            "reference": _txt(ds, "DataSourceReference")}
    out: dict[str, dict] = {}
    refused: list[str] = []
    for ds in _all(root, "DataSet"):
        name = ds.get("Name")
        if not name:
            continue
        query = _kid(ds, "Query")
        if query is None:
            refused.append(f"dataset {name!r} is a shared dataset (.rsd) or has no query: "
                           "its command is not in this file")
            continue
        command = _txt(query, "CommandText") or ""
        source = sources.get(_txt(query, "DataSourceName") or "", {})
        provider = source.get("provider", "").lower()
        if provider in _MODEL_PROVIDERS or _MODEL_CONNECT.search(source.get("connect", "")) \
                or _DAX.match(command) or (re.search(r"\bSELECT\b", command, re.I)
                                           and _MDX.search(command)):
            refused.append(f"dataset {name!r} queries a Power BI or Analysis Services MODEL "
                           "in DAX or MDX: its numbers are the model's. Supply the model "
                           "instead (its TMDL folder, .pbix, .bim or a scan result)")
            continue
        if (_txt(query, "CommandType") or "Text").lower() == "storedprocedure":
            refused.append(f"dataset {name!r} calls the stored procedure {command!r}: there "
                           "is no statement to read")
            continue
        if not command.strip() or command.strip().startswith("="):
            refused.append(f"dataset {name!r} builds its command from an expression: there "
                           "is no fixed statement to read")
            continue
        sql, params, unbound = _bind_parameters(command, query, defaults)
        if unbound:
            notes.append(f"dataset {name!r}: query parameter(s) {', '.join(unbound)} have no "
                         "static default, so the statement keeps them as written and will "
                         "not run until they are fixed.")
        fixed = [p["name"] for p in params if p["name"] not in unbound]
        if fixed:
            notes.append(f"dataset {name!r}: query parameter(s) {', '.join(fixed)} are fixed "
                         "at their report parameter's DEFAULT in the statement.")
        field_of, column_of, cols, calcs = {}, {}, [], []
        for f in _all(ds, "Field"):
            fname = f.get("Name")
            data_field = _txt(f, "DataField")
            if not fname:
                continue
            if not data_field:
                calcs.append(fname)
                continue
            out_col = _output_column(data_field, sql)
            field_of[fname.lower()] = fname.lower()
            column_of[fname.lower()] = out_col
            cols.append({"name": fname, "data_type": _txt(f, "TypeName"),
                         "is_calculated": False, "source_column": data_field.lower()})
        if calcs:
            notes.append(f"dataset {name!r}: calculated field(s) {', '.join(calcs)} are report "
                         "expressions, not columns of the query, and are not read.")
        target = _mquery._target_from_sql(sql)
        db, sch, obj = target if target else ("", "", "")
        qualified = ".".join(p.lower() for p in (db, sch, obj) if p) if target else None
        pairs = [(c["name"], column_of[c["name"].lower()]) for c in cols]
        pairs += [(column_of[c["name"].lower()], column_of[c["name"].lower()]) for c in cols]
        try:
            ds_filters, _top = _filters(_kid(ds, "Filters"), field_of, defaults, "datasource")
            ds_problem = None
        except _Untranslatable as exc:
            ds_filters, ds_problem = [], str(exc)
        out[name] = {
            "table": {
                "name": qualified or name, "logical_name": name, "columns": cols,
                "source_lineage": {
                    "connection": source.get("provider") or source.get("reference") or None,
                    "database": db.lower() or None, "schema": sch.lower() or None,
                    "object": obj.lower() or None, "object_kind": "custom-sql",
                    "raw_expression": sql, "published_datasource":
                        bool(source.get("reference")) or None,
                    "native_sql": sql, "statement_is_source": True,
                    "native_columns": [{"name": n, "column": c}
                                       for n, c in dict.fromkeys(pairs)]}},
            "column_of": column_of, "field_of": field_of, "parameters": params,
            "filters": ds_filters, "filter_problem": ds_problem,
        }
    return out, refused


# --------------------------------------------------------------------------- report items
def _groups(el: ET.Element, field_of: dict[str, str]) -> list[str]:
    """The fields a data region groups by, outermost first. Raises for a computed group or a
    group filter (a HAVING the statement could not state)."""
    cols: list[str] = []
    for g in _all(el, "Group"):
        if _kid(g, "Filters") is not None:
            raise _Untranslatable(f"a filter on the group {g.get('Name')!r}")
        for ge in _all(g, "GroupExpression"):
            text = (ge.text or "").strip()
            if not text:
                continue
            m = _FIELD.match(text)
            if not m or m.group(1).lower() not in field_of:
                raise _Untranslatable(f"the computed group {text}")
            col = field_of[m.group(1).lower()]
            if col not in cols:
                cols.append(col)
    return cols


def _textbox_value(tb: ET.Element) -> str:
    return "".join((v.text or "") for v in _all(tb, "Value")
                   if _local(v.tag) == "Value").strip()


def _format(tb: ET.Element) -> str | None:
    for f in _all(tb, "Format"):
        if f.text and f.text.strip() and not f.text.strip().startswith("="):
            return f.text.strip()
    return None


def _tablix_cells(tablix: ET.Element) -> list[tuple[int, int, ET.Element]]:
    """(row, column, textbox) for each body cell of THIS tablix (not a nested one)."""
    out = []
    rows = _path(tablix, "TablixBody", "TablixRows")
    for r, row in enumerate(_kids(rows, "TablixRow")):
        for c, cell in enumerate(_kids(_kid(row, "TablixCells"), "TablixCell")):
            tb = _path(cell, "CellContents", "Textbox")
            if tb is not None:
                out.append((r, c, tb))
    return out


def _measures_of_tablix(tablix: ET.Element, column_of: dict[str, str],
                        problems: list[str]) -> list[dict]:
    cells = _tablix_cells(tablix)
    labels = {(r, c): _textbox_value(tb) for r, c, tb in cells}
    out = []
    for r, c, tb in cells:
        expr = _textbox_value(tb)
        try:
            got = translate(expr, column_of)
        except _Untranslatable as exc:
            problems.append(f"{tb.get('Name') or 'a cell'}: {exc}")
            continue
        if got is None:
            continue
        sql, aggs = got
        header = next((labels[(rr, c)] for rr in range(r - 1, -1, -1)
                       if labels.get((rr, c)) and not labels[(rr, c)].startswith("=")), None)
        own = tb.get("Name") or ""
        name = header or (own if own and not _DEFAULT_NAME.match(own) else None) \
            or _derived_name(aggs, expr)
        out.append({"name": name, "formula": sql, "format": _format(tb)})
    return out


def _measures_of_chart(chart: ET.Element, column_of: dict[str, str],
                       problems: list[str]) -> list[dict]:
    out = []
    all_series = _all(chart, "ChartSeries")
    # The label a reader SEES for each series: the series hierarchy's static members, in
    # order, when there is one per series and each is literal text.
    static = [_txt(m, "Label") for m in _all(_kid(chart, "ChartSeriesHierarchy")
                                             or ET.Element("x"), "ChartMember")
              if _kid(m, "Group") is None]
    labels = static if len(static) == len(all_series) and all(
        x and not x.startswith("=") for x in static) else [None] * len(all_series)
    for series, label in zip(all_series, labels):
        for y in _all(series, "Y"):
            expr = (y.text or "").strip()
            try:
                got = translate(expr, column_of)
            except _Untranslatable as exc:
                problems.append(f"{series.get('Name') or 'a series'}: {exc}")
                continue
            if got is None:
                continue
            sql, aggs = got
            own = series.get("Name") or ""
            name = label or (own if own and not _DEFAULT_NAME.match(own) else None) \
                or _derived_name(aggs, expr)
            fmt = next((f.text.strip() for f in _all(series, "Format")
                        if f.text and not f.text.strip().startswith("=")), None)
            out.append({"name": name, "formula": sql, "format": fmt})
    return out


def parse(path: pathlib.Path) -> dict[str, Any]:
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    path = pathlib.Path(path)
    try:
        root = read_report(path)
    except RDLError as exc:
        raise ReaderRefused(str(exc)) from None
    notes: list[str] = []
    ns = _ns(root.tag)
    if ns and ns not in NAMESPACES:
        notes.append(f"{path.name} uses the report definition namespace {ns}, which is not "
                     "one of the 2008, 2010 or 2016 schemas; read by element name.")
    report = path.stem
    defaults = {}
    for rp in _all(root, "ReportParameter"):
        if rp.get("Name"):
            defaults[rp.get("Name").lower()] = _sql_param_default(rp)
    datasets, refused = _datasets(root, defaults, notes)
    if not datasets:
        raise ReaderRefused(
            f"{path.name}: no dataset this reader can read.\n"
            + "\n".join(f"  {r}." for r in refused or ["the report declares no dataset"]))
    for r in refused:
        notes.append(f"REFUSED {r}.")

    measures: dict[tuple[str, str], dict] = {}
    names_taken: dict[str, tuple[str, str]] = {}
    usage: list[dict] = []
    skipped: list[str] = []
    untranslated: list[str] = []
    regions = [el for el in root.iter() if _local(el.tag) in ("Tablix", "Chart")]
    for pos, el in enumerate(regions):
        kind = _local(el.tag).lower()
        rname = el.get("Name") or f"{kind}{pos + 1}"
        ds_name = _txt(el, "DataSetName") or (next(iter(datasets)) if len(datasets) == 1
                                              else None)
        if ds_name not in datasets:
            if ds_name:
                skipped.append(f"{rname} (reads the refused dataset {ds_name!r})")
            continue
        ds = datasets[ds_name]
        try:
            if ds["filter_problem"]:
                raise _Untranslatable(ds["filter_problem"])
            cols = _groups(el, ds["field_of"])
            filters, top_n = _filters(_kid(el, "Filters"), ds["field_of"], defaults, "visual")
            problems: list[str] = []
            found = (_measures_of_tablix(el, ds["column_of"], problems) if kind == "tablix"
                     else _measures_of_chart(el, ds["column_of"], problems))
        except _Untranslatable as exc:
            skipped.append(f"{rname} ({exc})")
            continue
        untranslated += [f"{rname}/{x}" for x in problems]
        if not found:
            continue
        unit_measures, fmt = [], None
        for m in found:
            key = (ds_name, m["formula"])
            if key not in measures:
                # One name, one formula: a second formula under a name already taken is
                # told apart by its dataset, then by a counter — never merged into the first.
                name, n = m["name"], 1
                while names_taken.get(name.lower(), key) != key:
                    n += 1
                    name = f"{m['name']} ({ds_name})" if n == 2 else f"{m['name']} ({n})"
                names_taken[name.lower()] = key
                measures[key] = {"name": name, "home_table": ds_name, "formula": m["formula"],
                                 "formula_language": "sql", "aggregation_at_use_site": False,
                                 **({"format_string": m["format"]} if m["format"] else {})}
            if measures[key]["name"] not in unit_measures:
                unit_measures.append(measures[key]["name"])
            fmt = fmt or m["format"]
        title = (_txt(el, "ChartTitles", "ChartTitle", "Caption") if kind == "chart" else None)
        usage.append({
            "unit_id": f"rdl::{report}::{rname}",
            "container": report,
            "container_kind": "report",
            "dashboards": [{"name": report, "title": report, "position": pos}],
            "visual_id": rname,
            "visual_type": kind,
            "title": title or rname,
            "measures": unit_measures,
            "columns": cols,
            "filters": ds["filters"] + filters,
            "sort": None,
            "top_n": top_n,
            "parameters": [{"name": p["name"], "default": p["default"]}
                           for p in ds["parameters"]],
            "number_format": fmt,
            "source_fragments": {"widget": None, "dashboard": None,
                                 "prose": [p for p in (_txt(root, "Description"), report) if p]},
            "_aggregations": {},
            "_siblings": [],
        })
    if untranslated:
        notes.append(f"{len(untranslated)} aggregate expression(s) not read, each NAMED for "
                     f"a construct with no exact SQL form here: {'; '.join(untranslated[:10])}.")
    if skipped:
        notes.append(f"{len(skipped)} data region(s) not read, each for a construct with no "
                     f"exact SQL form here: {'; '.join(skipped[:10])}.")
    return {
        "contract_version": "1.0",
        "vendor": PowerBIPaginatedReader.name,
        "source_file": str(path),
        "model_name": report,
        "tables": [d["table"] for d in datasets.values()],
        "measures": list(measures.values()),
        "relationships": [],
        "usage": usage,
        "assets": [{"role": "report", "name": report, "vendor_id": None, "path": None,
                    "site": None, "catalog_id": None, "catalog_qualified_name": None}],
        "parameters": [{"name": n, "value": shown, "kind": "parameter"}
                       for n, (_lit, shown) in sorted(defaults.items())],
        "_reader_notes": notes,
        "_unresolved_published": [],
    }


class PowerBIPaginatedReader(FormulaCompiler):
    """The `powerbi-paginated` BI reader (`sockets.BIReader`): one paginated report (`.rdl`).
    Its measures are written in the core's own `sql`, so it declares no language of its own;
    the shared engine compiles them over each dataset's statement."""

    name = "powerbi-paginated"
    languages: tuple[str, ...] = ()
    suffixes = (".rdl",)

    def detect(self, path: pathlib.Path) -> bool:
        path = pathlib.Path(path)
        return path.is_file() and path.suffix.lower() == ".rdl"

    def form(self, path: pathlib.Path) -> str:
        from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

        path = pathlib.Path(path)
        if path.is_dir() and len(self.models(path)) == 1:
            return "rdl"
        if path.is_dir() or path.suffix.lower() != ".rdl":
            raise ReaderRefused(f"the powerbi-paginated reader reads a paginated report "
                                f"(.rdl), or a folder of them; {path.name} is not one.")
        return "rdl"

    def models(self, path: pathlib.Path) -> list[pathlib.Path]:
        """One model per report: a folder of `.rdl` files is several reports, each read on
        its own (their datasets and measures are their own) and merged like two BI plugs."""
        path = pathlib.Path(path)
        if not path.is_dir():
            return []
        return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".rdl")

    def parse(self, path: pathlib.Path, **_opts: Any) -> dict[str, Any]:
        path = pathlib.Path(path)
        if path.is_dir():
            found = self.models(path)
            if len(found) != 1:
                from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

                raise ReaderRefused(f"{path.name} holds {len(found)} .rdl file(s); each is "
                                    "read on its own")
            path = found[0]
        return parse(path)
