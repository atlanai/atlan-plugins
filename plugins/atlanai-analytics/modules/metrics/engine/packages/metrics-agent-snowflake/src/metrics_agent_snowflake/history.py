# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Bounded Account Usage history converted to the common pre-aggregated records."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlglot import exp

from metrics_agent import sockets as S
from metrics_agent.lib import policy
from metrics_agent.socket_gates import ReadOnlyRunner, plain_table_name

VIEW = "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY"


def source(settings: dict) -> str:
    selected = settings.get("history_source") or ("shape_table" if settings.get("shape_table")
                                                  else "account_usage")
    if selected not in ("account_usage", "shape_table"):
        raise ValueError("snowflake-direct.history_source must be account_usage or shape_table")
    if selected == "shape_table" and not settings.get("shape_table"):
        raise ValueError("snowflake-direct: history_source shape_table requires shape_table")
    if selected == "account_usage" and (settings.get("shape_table") or settings.get("columns")):
        raise ValueError("snowflake-direct: account_usage does not use shape_table or columns")
    return selected


def _instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Snowflake history window needs ISO dates or timestamps") from None
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)


def bounds(window: S.Window, *, now: datetime | None = None) -> tuple[datetime, datetime, int]:
    config = policy.load()
    values = [config.get("snowflake_history." + key)
              for key in ("default_days", "max_days", "max_shapes")]
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("snowflake_history policy limits must be positive integers")
    days, maximum, shapes = values
    if days > maximum:
        raise ValueError("snowflake_history.default_days exceeds max_days")
    end = _instant(window.end) if window.end else (now or datetime.now(timezone.utc))
    start = _instant(window.start) if window.start else end - timedelta(days=days)
    if start >= end or end - start > timedelta(days=maximum):
        raise ValueError("Snowflake history needs a nonempty window no longer than "
                         f"{maximum} days; set run.yml window.start and window.end")
    return start, end, shapes


def statement(tables: list[str], window: S.Window) -> tuple[str, int]:
    """Only validated identifiers, typed bounds and encoded literals enter this SELECT.

    Bare table names are a broad prefilter, including quoted and cross-database SQL.
    H0's SQL lineage remains the authoritative scope check, never a text match.
    """
    if not tables or any(not isinstance(t, str) or len(t.split('.')) != 3
                         or not plain_table_name(t) for t in tables):
        raise ValueError("Snowflake native history requires bounded DB.SCHEMA.TABLE names")
    start, end, maximum = bounds(window)
    def literal(value: str) -> str:
        return exp.Literal.string(value).sql(dialect="snowflake")

    wanted = " OR ".join("CONTAINS(UPPER(query_text), " + literal(t.split('.')[-1].upper()) + ")"
                         for t in sorted(set(tables)))
    sql = f"""SELECT query_text, COUNT(*) AS exec_count, COUNT(DISTINCT user_name) AS user_count,
       MIN(query_id) AS representative_query_id, database_name, schema_name
FROM {VIEW}
WHERE UPPER(execution_status) = 'SUCCESS' AND UPPER(query_type) = 'SELECT'
  AND query_text IS NOT NULL AND query_text <> ''
  AND start_time >= TO_TIMESTAMP_TZ({literal(start.isoformat())})
  AND start_time < TO_TIMESTAMP_TZ({literal(end.isoformat())})
  AND ({wanted})
GROUP BY query_text, database_name, schema_name
ORDER BY exec_count DESC, query_text, database_name, schema_name
LIMIT {maximum + 1}"""
    return sql, maximum


def records(runner: S.SqlRunner, tables: list[str], window: S.Window) -> Iterator[S.QueryRecord]:
    sql, maximum = statement(tables, window)
    try:
        with ReadOnlyRunner(runner, "snowflake") as ro:
            _columns, rows = ro.fetch(sql, maximum + 1)
    except S.AccessBlocked as exc:
        # Keep the common access code, but name the history read and grant accurately.
        blockers = []
        for original in exc.blockers:
            item = dict(original, stage="h0", not_read="native Snowflake query history",
                        item={"kind": "table", "name": VIEW})
            if item["code"] in ("no_select_grant", "object_not_visible", "database_not_visible"):
                item["ask"] = dict(
                    original["ask"], what=("Ask the Snowflake administrator for read access to "
                                           + VIEW + "; alternatively select an accessible "
                                           "shape_table or local export."))
            blockers.append(item)
        raise S.AccessBlocked(blockers) from None
    if len(rows) > maximum:
        raise ValueError(f"Snowflake history exceeds {maximum} SQL/context groups; narrow the "
                         "time window or table scope. No partial history was admitted.")
    for text, executions, users, query_id, database, schema in rows:
        yield S.QueryRecord(query_text=text, exec_count=int(executions), user_count=int(users),
                            query_id=query_id, catalog=database, schema=schema,
                            source="snowflake-account-usage")
