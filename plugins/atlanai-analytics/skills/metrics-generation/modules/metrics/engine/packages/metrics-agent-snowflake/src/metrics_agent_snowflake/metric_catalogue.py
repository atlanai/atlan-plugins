# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Read governed metric rows as model declarations."""
from __future__ import annotations

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlglot import exp

from metrics_agent import sockets as S
from metrics_agent.lib import card_sql, metric_validation, sqlshape
from metrics_agent.plug_runtime import statement_tables

SUFFIX = ".metric-catalogue.json"
COLUMNS = ("metric_name", "sql", "mandatory_filters", "expected_value",
           "expected_tolerance", "description")
DEFAULT_COLUMNS = {
    "metric_name": ("metric_name", "measure_name", "name"),
    "sql": ("sql", "metric_sql", "measure_sql", "exact_sql"),
    "mandatory_filters": ("mandatory_filters", "required_filters"),
    "expected_value": ("expected_value", "verified_value"),
    "expected_tolerance": ("expected_tolerance", "verified_tolerance"),
    "description": ("description", "measure_description"),
}


def is_catalogue_path(path: Path) -> bool:
    name = path.name.lower()
    return name == "dim_measure.csv" or name.endswith((SUFFIX, ".metric-catalogue.csv"))


def _load(path: Path) -> dict:
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        headers = {name.strip().lower(): name for name in reader.fieldnames or []}
        columns = {field: next((headers[a] for a in choices if a in headers), None)
                   for field, choices in DEFAULT_COLUMNS.items()}
        if not columns["metric_name"] or not columns["sql"]:
            raise ValueError("catalogue CSV needs metric name and saved SQL columns")
        return {"model_name": path.name.removesuffix(".csv").removesuffix(".metric-catalogue"),
                "rows": [{field: row[name] for field, name in columns.items() if name}
                         for row in reader]}


def _filters(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value) if value.lstrip().startswith("[") else [value]
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError("mandatory_filters must contain SQL predicates")
    return value


def _contains_filters(query: exp.Query, filters: list[str], dialect: str) -> bool:
    def conjuncts(node):
        for pred in node.flatten() if isinstance(node, exp.And) else [node]:
            yield pred.unnest()

    branches = [query]
    while any(isinstance(q, exp.SetOperation) for q in branches):
        branches = [part.unnest() for q in branches for part in
                    ([q.this, q.expression] if isinstance(q, exp.SetOperation) else [q])]
    for condition in filters:
        required = sqlshape.parse(condition, dialect)
        for branch in branches:
            where = branch.args.get("where")
            if not where or any(p not in list(conjuncts(where.this)) for p in conjuncts(required)):
                return False
    return True


def _measure(row: dict, dialect: str) -> tuple[dict, list[str]]:
    name = str(row.get("metric_name") or "").strip()
    sql = str(row.get("sql") or "").strip()
    tree = metric_validation.read_query(sql, dialect)
    if not name or tree is None:
        raise ValueError("a name and one read-only SQL statement are required")

    if len(tree.selects) != 1 or tree.selects[0].is_star:
        raise ValueError("the saved query must select exactly one named value")
    projection = tree.selects[0]
    alias = projection.args.get("alias") or (projection.this if isinstance(projection, exp.Column)
                                             else None)
    if not isinstance(alias, exp.Identifier):
        raise ValueError("give the saved query's value column an alias")
    tables = [table for _, table in statement_tables(sql, dialect)]
    if not tables or any(t.count(".") != 2 for t in tables):
        raise ValueError("the saved query must use fully qualified warehouse tables")
    filters = _filters(row.get("mandatory_filters"))
    if not _contains_filters(tree, filters, dialect):
        raise ValueError("a mandatory filter is missing from the saved query's WHERE clause")
    embedded = sql.rstrip().removesuffix(";").rstrip()
    if sqlshape.fragment_escape(embedded, dialect):
        embedded = sqlshape.strip_comments(tree).sql(pretty=True, dialect=dialect)
    column = exp.Column(this=alias.copy(), table=exp.to_identifier("metric_result"))
    declaration = {
        "entity": tables[0], "formula": column.sql(dialect=dialect),
        "grain": [], "source": f"(\n{embedded}\n) AS metric_result",
        "joins": [], "dimensions": [], "examples": [],
        "statement_sql": sql, "mandatory_filters": filters,
    }
    for key in ("expected_value", "expected_tolerance"):
        if row.get(key) is None or row[key] == "":
            continue
        try:
            number = Decimal(str(row[key]))
        except InvalidOperation:
            raise ValueError(f"{key} must be numeric") from None
        if not number.is_finite() or key == "expected_tolerance" and number < 0:
            raise ValueError(f"{key} must be a finite valid number")
        declaration[key] = int(number) if number == number.to_integral_value() else float(number)
    if "expected_tolerance" in declaration and "expected_value" not in declaration:
        raise ValueError("expected_tolerance requires expected_value")
    return {
        "name": name, "home_table": tables[0], "formula": declaration["formula"],
        "formula_language": "sql", "aggregation_at_use_site": False,
        **({"description": str(row["description"])} if row.get("description") else {}),
        "declared": declaration,
    }, tables


def read(path: Path, *, vendor: str, dialect: str) -> dict:
    try:
        document = _load(path)
    except (OSError, ValueError, csv.Error):
        raise S.ReaderRefused("metric catalogue must be valid JSON or CSV with a name and SQL column") from None
    if not isinstance(document, dict) or not isinstance(document.get("model_name"), str) \
            or not document["model_name"].strip() or not isinstance(document.get("rows"), list) \
            or not document["rows"]:
        raise S.ReaderRefused("metric catalogue requires a model_name and a non-empty rows list")
    model = document["model_name"]
    measures, usage, tables = [], [], {}
    names: set[str] = set()
    for index, row in enumerate(document["rows"], 1):
        if not isinstance(row, dict):
            raise S.ReaderRefused(f"metric catalogue row {index} must be an object")
        try:
            measure, sources = _measure(row, dialect)
        except (ValueError, TypeError, sqlshape.ParseError) as exc:
            message = str(exc) if type(exc) is ValueError else "invalid SQL or field type"
            raise S.ReaderRefused(f"metric catalogue row {index}: {message}") from None
        name = measure["name"]
        if name.casefold() in names:
            raise S.ReaderRefused(f"metric catalogue row {index} repeats a metric name")
        names.add(name.casefold())
        measures.append(measure)
        usage.append({
            "unit_id": f"catalogue::{model}::{index}", "container": model,
            "container_kind": "perspective", "dashboards": [], "visual_id": name,
            "visual_type": "catalogue_metric", "title": name,
            "description": measure.get("description"), "measures": [name], "columns": [],
            "filters": [], "sort": None, "top_n": None, "parameters": [],
            "source_fragments": {"widget": None, "dashboard": None, "prose": [model]},
        })
        for table in sources:
            db, schema, obj = table.split(".")
            tables.setdefault(table.casefold(), {
                "name": table, "columns": [],
                "source_lineage": {"connection": None, "database": db, "schema": schema,
                                   "object": obj.lower(), "object_kind": "table",
                                   "raw_expression": table, "published_datasource": None},
            })
    return {
        "contract_version": "1.0", "vendor": vendor, "source_file": str(path),
        "model_name": model, "tables": list(tables.values()), "measures": measures,
        "relationships": [], "usage": usage, "parameters": [],
    }


def compile_declaration(declaration: dict, dialect: str) -> S.Compiled:
    statement = declaration["statement_sql"]
    if "<redacted:" in statement:
        return S.Compiled(reason="the catalogue statement depends on a withheld literal")
    sql = card_sql.canonical_statement({
        "formula": declaration["formula"], "sql": declaration["source"],
    })
    if sqlshape.has_dml(statement, dialect) or sqlshape.has_dml(sql, dialect):
        return S.Compiled(reason="the catalogue statement must be read-only")
    return S.Compiled(sql=sql, coverage=S.TRANSLATED)
