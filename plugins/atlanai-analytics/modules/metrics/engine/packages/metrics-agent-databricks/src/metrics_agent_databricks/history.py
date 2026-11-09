# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Workspace-scoped native history, with visible SQL and proven table qualification."""
from datetime import datetime, timedelta, timezone
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from metrics_agent import sockets as S
from metrics_agent.lib import access, policy
from metrics_agent.socket_gates import ReadOnlyRunner, plain_table_name

VIEW = "system.query.history"


def workspace_id(settings):
    value = str(settings.get("workspace_id") or "")
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Databricks native history needs the selected numeric workspace_id")
    return value


def bounds(window, now=None):
    config = policy.load()
    limits = [config.get("databricks_history." + key)
              for key in ("default_days", "max_days", "max_shapes")]
    if any(type(v) is not int or v <= 0 for v in limits) or limits[0] > limits[1]:
        raise ValueError("Databricks history policy needs positive, consistent integer limits")
    def instant(value):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Databricks history window needs ISO dates or timestamps") from None
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
    end = instant(window.end) if window.end else (now or datetime.now(timezone.utc))
    start = instant(window.start) if window.start else end - timedelta(days=limits[0])
    if start >= end or end - start > timedelta(days=limits[1]):
        raise ValueError("Databricks history needs a nonempty bounded window within its policy limit")
    return start, end, limits[2]


def literal(value):
    return exp.Literal.string(value).sql(dialect="databricks")


def statements(tables, window, settings):
    if not tables or any(not isinstance(t, str) or len(t.split('.')) != 3
                         or not plain_table_name(t) for t in tables):
        raise ValueError("Databricks history needs bounded CATALOG.SCHEMA.TABLE identifiers")
    workspace = workspace_id(settings)
    start, end, maximum = bounds(window)
    where = (f"workspace_id = {literal(workspace)}\n"
             "  AND UPPER(execution_status) = 'FINISHED' AND UPPER(statement_type) = 'SELECT'\n"
             f"  AND start_time >= CAST({literal(start.isoformat())} AS TIMESTAMP)\n"
             f"  AND start_time < CAST({literal(end.isoformat())} AS TIMESTAMP)")
    masked = ("SELECT COUNT(*) FROM " + VIEW + "\nWHERE " + where
              + "\n  AND (statement_text IS NULL OR TRIM(statement_text) = '' "
                "OR UPPER(TRIM(statement_text)) = '<REDACTED>')")
    wanted = " OR ".join("CONTAINS(UPPER(statement_text), " + literal(name) + ")"
                         for name in sorted({t.split('.')[-1].upper() for t in tables}))
    sql = ("SELECT statement_text, COUNT(*) AS exec_count, "
           "COUNT(DISTINCT executed_by_user_id) AS user_count, MIN(statement_id) AS representative_id\n"
           f"FROM {VIEW}\nWHERE {where}\n  AND ({wanted})\n"
           "GROUP BY statement_text\nORDER BY exec_count DESC, statement_text\n"
           f"LIMIT {maximum + 1}")
    return masked, sql, maximum


def qualified(sql):
    """CTE references need no namespace; physical source tables need all three parts.

    The native system table records no historical default catalog/schema. Assigning the
    connection's current defaults to past executions would invent source lineage.
    """
    try:
        parsed = sqlglot.parse(sql, read="databricks")
        if len(parsed) != 1 or not isinstance(parsed[0], exp.Query):
            return False
        tables = []
        for scope in traverse_scope(parsed[0]):
            for _node, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    tables.append(source)
        return bool(tables) and all(t.catalog and t.db and isinstance(t.this, exp.Identifier)
                                    for t in tables)
    except Exception:
        return False


def records(runner, tables, window, settings):
    masked, sql, maximum = statements(tables, window, settings)
    try:
        with ReadOnlyRunner(runner, "databricks") as ro:
            _columns, hidden = ro.fetch(masked, 1)
            if hidden and int(hidden[0][0] or 0):
                blocker = access.blocker(
                    "no_select_grant", source="databricks-direct", stage="h0",
                    item={"kind": "table", "name": VIEW},
                    not_read="visible native query text in the selected workspace/window",
                    ask="Ask the administrator for an approved source with visible SQL text or provide a scoped export; do not change grants automatically")
                raise S.AccessBlocked([blocker])
            _columns, rows = ro.fetch(sql, maximum + 1)
    except S.AccessBlocked as exc:
        blockers = [dict(b, stage="h0", not_read="native Databricks query history") for b in exc.blockers]
        raise S.AccessBlocked(blockers) from None
    if len(rows) > maximum:
        raise ValueError("Databricks history exceeded the group limit; narrow the window or table scope. No partial history was admitted")
    unresolved = sum(not qualified(row[0]) for row in rows)
    if unresolved:
        raise ValueError(f"Databricks history contains {unresolved} SQL groups without proven catalog/schema context; provide fully qualified SQL or a history export with session context. No partial history was admitted")
    for text, executions, users, query_id in rows:
        yield S.QueryRecord(query_text=text, exec_count=int(executions), user_count=int(users),
                            query_id=query_id, source="databricks-system-query-history")
