# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Build and check metric SQL against the evidence that defines it."""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from metrics_agent.lib import sqlshape


def read_query(sql: str, dialect: str) -> exp.Query | None:
    """A single, read-only query that can be embedded as a metric's source."""
    if sqlshape.has_dml(sql, dialect):
        return None
    expressions = [tree for tree in sqlglot.parse(sql, read=dialect) if tree is not None]
    if len(expressions) == 1 and isinstance(expressions[0], exp.Query):
        return expressions[0]
    return None


def sum_over_output(formula: str, sql: str, dialect: str) -> tuple[str, str] | None:
    """A proposed SUM may wrap a uniquely named column returned by its source."""
    try:
        aggregate = sqlshape.parse(formula, dialect)
        query = read_query(sql, dialect)
    except sqlshape.ParseError:
        return None
    if not isinstance(aggregate, exp.Sum) or not isinstance(aggregate.this, exp.Column):
        return None
    if aggregate.this.table or query is None:
        return None
    column = aggregate.this.this

    sql_dialect = sqlglot.Dialect.get_or_raise(dialect)

    def output_key(identifier: exp.Identifier) -> str:
        return sql_dialect.normalize_identifier(identifier.copy()).name

    matches = [p for p in query.selects if isinstance(p, (exp.Column, exp.Alias))
               and not p.is_star and output_key(p.args.get("alias") or p.this) == output_key(column)]
    if len(matches) != 1 or any(p.is_star for p in query.selects):
        return None
    result = exp.column(column.name, table="metric_rows", quoted=column.quoted)
    source = sqlshape.strip_comments(query).subquery("metric_rows")
    return exp.Sum(this=result).sql(dialect=dialect), source.sql(dialect=dialect, pretty=True)


def source_relations(source: str, dialect: str) -> tuple[set[str], list[exp.Table], list[str]]:
    """Return visible aliases, aliased physical tables, and unresolved outer references."""
    if not source:
        return set(), [], []
    tree = sqlshape.parse(f"SELECT 1 FROM {source}", dialect)
    if not isinstance(tree, exp.Select) or not tree.args.get("from_"):
        raise sqlshape.ParseError("expected a FROM clause")
    aliases: set[str] = set()
    top_tables: list[exp.Table] = []

    def visit(node: exp.Expression, *, grouped: bool = False) -> None:
        if isinstance(node, exp.Subquery):
            if node.alias:
                aliases.add(node.alias.lower())
            elif isinstance(node.this, (exp.Subquery, exp.Table)):
                visit(node.this, grouped=True)
        elif isinstance(node, exp.Table) and node.alias:
            if grouped:
                aliases.add(node.alias.lower())
            else:
                top_tables.append(node)
        for join in node.args.get("joins") or []:
            visit(join.this, grouped=grouped)

    visit(tree.args["from_"].this)
    for join in tree.args.get("joins") or []:
        visit(join.this)
    unresolved = []
    for column in tree.find_all(exp.Column):
        if (column.find_ancestor(exp.Select, exp.SetOperation) is not tree
                or not column.table or column.db or column.table.lower() in aliases):
            continue
        enclosure = column.find_ancestor(exp.Subquery)
        if enclosure is None or not enclosure.alias:
            unresolved.append(column.sql(dialect=dialect))
    return aliases, top_tables, list(dict.fromkeys(unresolved))


def _conjuncts(condition: exp.Expression):
    condition = condition.unnest()
    if isinstance(condition, exp.And):
        yield from _conjuncts(condition.this)
        yield from _conjuncts(condition.expression)
    else:
        yield condition


def _same_column(node: exp.Expression, column: exp.Column) -> bool:
    return (isinstance(node, exp.Column) and node.name.casefold() == column.name.casefold()
            and all(not left or not right or left.casefold() == right.casefold()
                    for left, right in ((node.table, column.table), (node.db, column.db),
                                        (node.catalog, column.catalog))))


def _fixed_period(predicate: exp.Expression, column: exp.Column) -> bool:
    if not isinstance(predicate, exp.EQ):
        return False
    return any(_same_column(left, column)
               and (isinstance(right, (exp.Literal, exp.Placeholder, exp.Parameter))
                    or isinstance(right, exp.Cast) and isinstance(right.this, exp.Literal))
               for left, right in ((predicate.this, predicate.expression),
                                   (predicate.expression, predicate.this)))


def snapshot_problem(grain: str | None, sql: str, dialect: str, *,
                     period: str | None = None) -> str | None:
    """A stock can be summed across entities once per period."""
    period = period or next(
        (key.strip() for key in (grain or "").split(",")
         if key.rsplit(".", 1)[-1].strip().lower() in
         {"snapshot_date", "snapshot_day", "snapshot_week", "snapshot_month", "snapshot_period"}),
        None)
    if not period:
        return None
    try:
        column = sqlshape.parse(period, dialect)
        tree = sqlshape.parse(sql, dialect)
    except sqlshape.ParseError:
        return "snapshot_period_column and the metric SQL must parse"
    if not isinstance(column, exp.Column) or not isinstance(tree, exp.Select):
        return "snapshot_period_column must be a column in the metric source"
    if not any(isinstance(node, exp.Sum) and node.find_ancestor(exp.Select) is tree
               for node in tree.find_all(exp.Sum)):
        return None
    group = tree.args.get("group")
    if group and any(_same_column(item, column) for item in group.expressions):
        return None
    where = tree.args.get("where")
    if where and any(_fixed_period(p, column) for p in _conjuncts(where.this)):
        return None
    # A direct derived source may already restrict its input to one snapshot.
    source = tree.args["from_"].this
    if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
        inner_where = source.this.args.get("where")
        for projection in source.this.selects:
            if projection.alias_or_name.casefold() != column.name.casefold():
                continue
            inner_column = projection.this if isinstance(projection, exp.Alias) else projection
            if (isinstance(inner_column, exp.Column) and inner_where and
                    any(_fixed_period(p, inner_column) for p in _conjuncts(inner_where.this))):
                return None
    return "sums a snapshot across periods. Select one period or group by the snapshot period."
