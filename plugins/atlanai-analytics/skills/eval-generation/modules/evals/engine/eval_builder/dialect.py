"""Which SQL dialect a candidate is in, from the connector that produced it.

The dialect is never inferred from the SQL. Every source names its warehouse:

    Power BI       the Power Query connector (Databricks.Catalogs, Snowflake.Databases, …)
    Tableau        the datasource's <connection class>
    Sigma          the Atlan qualified name of the tables a tile reads
    Snowflake      the query-history miner reads Snowflake, so its rows are Snowflake
    files          a "dialect" field on the row, else `build --dialect`, else the
                   connectorName Atlan catalogues the row's tables under

and the candidate carries it from there through parsing, the sensitivity
scan, the Atlan column check, dedupe and execution, so the answer key stays in
the dialect its warehouse runs. `FALLBACK` is used only when no source can say,
and `build` reports how many candidates fell back.
"""

from __future__ import annotations

import re

# connector name (Atlan connectorName, Tableau class, a --dialect value) -> sqlglot dialect
CONNECTORS = {
    "snowflake": "snowflake", "databricks": "databricks", "spark": "databricks",
    "bigquery": "bigquery", "google-bigquery": "bigquery", "redshift": "redshift",
    "postgres": "postgres", "postgresql": "postgres", "mssql": "tsql", "sqlserver": "tsql",
    "synapse": "tsql", "tsql": "tsql", "oracle": "oracle", "mysql": "mysql",
    "teradata": "teradata", "athena": "athena", "trino": "trino", "presto": "presto",
    "hive": "hive", "duckdb": "duckdb", "sqlite": "sqlite",
}
FALLBACK = "snowflake"

_run: str | None = None


def normalize(name: str | None) -> str | None:
    """A connector or dialect name as a sqlglot dialect; None when unknown."""
    return CONNECTORS.get((name or "").strip().lower())


def from_qualified_name(qualified_name: str | None) -> str | None:
    """`default/databricks/1700000000/catalog/schema/table` -> databricks."""
    m = re.match(r"^[^/]+/([^/]+)/", qualified_name or "")
    return normalize(m.group(1)) if m else None


def use(name: str | None) -> None:
    """The run's dialect (`build --dialect`), for candidates that carry none."""
    global _run
    _run = normalize(name) if name else None
    if name and _run is None:
        raise ValueError(f"unknown dialect {name!r}; known: {', '.join(sorted(set(CONNECTORS.values())))}")


def run() -> str:
    return _run or FALLBACK


def of(candidate) -> str:
    """A candidate's dialect: its tile's, its own, the run's, or the fallback."""
    el = getattr(candidate, "element", None)
    if el is not None and getattr(el, "dialect", None):
        return el.dialect
    return getattr(candidate, "dialect", None) or run()
