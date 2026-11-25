# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""When statement prose is written, and what the metric gates read instead.

By default the metric gates read the SQL itself, with facts computed from it, and the prose is
written beside them and applied at the end (option C, 2026-11-23): the first metric comes
minutes sooner, and a blind review rated the metrics the same as with prose first. Two operator
switches restore the earlier order:

  METRICS_AGENT_DESCRIBE_AT=start  M2 runs before X1, and the metric gates read its prose.
  METRICS_AGENT_BI_DRAFT=model     C3 pauses for authored dashboard questions instead of
                                   writing a structural draft. Implied by DESCRIBE_AT=start.
"""
from __future__ import annotations

import os
import re


def describe_at_end() -> bool:
    return os.environ.get("METRICS_AGENT_DESCRIBE_AT", "").strip().lower() != "start"


def bi_draft_by_code() -> bool:
    if not describe_at_end():
        return False
    return os.environ.get("METRICS_AGENT_BI_DRAFT", "").strip().lower() != "model"


def prose_is_authored(o: dict) -> bool:
    """Whether the statement's question was written by somebody who read it. A history
    placeholder (`name_provisional`) is not, nor is C3's structural draft under option C."""
    if o.get("name_provisional"):
        return False
    if bi_draft_by_code() and str((o.get("provenance") or {}).get("lane") or "").startswith("bi"):
        return False
    return bool(str(o.get("question") or "").strip())


_EMPTY = re.compile(r"(?<![\w.])(1\s*=\s*0|0\s*=\s*1|FALSE)(?![\w.])", re.I)


def _root_select(tree):
    """The outermost select, seen through `SELECT *` wrappers and down a union's first branch:
    the same root `m2_describe._outer_select` reports."""
    from sqlglot import exp
    node = tree
    while isinstance(node, (exp.SetOperation, exp.Subquery, exp.Paren)):
        node = node.this
    root = node if isinstance(node, exp.Select) else next(
        (n for n in tree.walk() if isinstance(n, exp.Select)
         and n.find_ancestor(exp.Select) is None), None)
    hops = 0
    while root is not None and hops < 4 and len(root.expressions or []) == 1 \
            and isinstance(root.expressions[0], exp.Star):
        inner = next((n for n in root.walk() if isinstance(n, exp.Select) and n is not root
                      and n.find_ancestor(exp.CTE) is None), None)
        if inner is None:
            break
        root, hops = inner, hops + 1
    return root


def columns(sql: str, dialect: str, limit: int = 60) -> list[dict]:
    """EACH OUTPUT COLUMN, AND THE EXPRESSION THAT ACTUALLY PRODUCES IT.

    A column named `c5` says nothing; the scalar subquery or CTE column behind it says
    everything. Resolved through scalar subqueries, CTEs and derived tables, up to three
    levels. `always_empty` marks an output whose own WHERE is forced false (`1 = 0`): an
    analyst's empty twin of another column, never a number of its own.
    """
    from sqlglot import exp
    from metrics_agent.lib import sqlshape as S
    try:
        tree = S.parse(sql, dialect)
    except Exception:
        return []
    root = _root_select(tree)
    if root is None:
        return []
    sources: dict[str, exp.Select] = {}
    for cte in tree.find_all(exp.CTE):
        if isinstance(cte.this, exp.Select) and cte.alias:
            sources[cte.alias.lower()] = cte.this
    for sq in tree.find_all(exp.Subquery):
        if sq.alias and isinstance(sq.this, exp.Select):
            sources[sq.alias.lower()] = sq.this
    # `FROM engagement eg`: the alias reads the CTE.
    for t in tree.find_all(exp.Table):
        if t.alias and not t.db and t.name.lower() in sources:
            sources.setdefault(t.alias.lower(), sources[t.name.lower()])

    def _tables(sel):
        return sorted({".".join(p for p in (t.catalog, t.db, t.name) if p)
                       for t in sel.find_all(exp.Table)})[:6]

    def _where(sel):
        w = sel.args.get("where")
        return w.this.sql(dialect=dialect) if w is not None else ""

    def _forced_false(sel) -> bool:
        """A conjunct of the WHERE that can never hold: `1 = 0`, `FALSE`. Not a comparison
        that merely mentions FALSE (`IS_DELETED = FALSE` is an ordinary filter)."""
        w = sel.args.get("where")
        if w is None:
            return False
        cond = w.this
        while isinstance(cond, exp.Paren):
            cond = cond.this
        parts = list(cond.flatten()) if isinstance(cond, exp.And) else [cond]
        for c in parts:
            while isinstance(c, exp.Paren):
                c = c.this
            if isinstance(c, exp.Boolean) and not c.this:
                return True
            if isinstance(c, exp.EQ) and isinstance(c.this, exp.Literal) \
                    and isinstance(c.expression, exp.Literal) \
                    and c.this.this != c.expression.this:
                return True
        return False

    def _by_name(sel, name):
        for p in sel.expressions or []:
            if (p.alias_or_name or "").lower() == name.lower():
                return p.this if isinstance(p, exp.Alias) else p
        return None

    def resolve(e, depth=0):
        info = {"computes": e.sql(dialect=dialect)}
        if depth >= 3:
            return info
        if isinstance(e, exp.Subquery) and isinstance(e.this, exp.Select) and e.this.expressions:
            inner = e.this
            first = inner.expressions[0]
            first = first.this if isinstance(first, exp.Alias) else first
            out = resolve(first, depth + 1)
            out["from"] = _tables(inner)
            w = _where(inner)
            if w:
                out["where"] = w[:400]
            if _forced_false(inner):
                out["always_empty"] = True
            return out
        if isinstance(e, exp.Column):
            src = (e.table or "").lower()
            if not src:
                froms = [t.alias_or_name.lower() for t in
                         ((root.args.get("from") or root.args.get("from_")).find_all(exp.Table, exp.Subquery)
                          if (root.args.get("from") or root.args.get("from_")) else [])]
                src = froms[0] if len(froms) == 1 else ""
            sel = sources.get(src)
            inner = _by_name(sel, e.name) if sel is not None else None
            if inner is not None:
                out = resolve(inner, depth + 1)
                out["via"] = f"{src}.{e.name}"
                w = _where(sel)
                if w and "where" not in out:
                    out["where"] = w[:400]
                if _forced_false(sel):
                    out["always_empty"] = True
                return out
        # An expression over CTE columns (`COALESCE(eg.FORMS, 0)`): resolve the columns it reads.
        inputs = []
        for c in e.find_all(exp.Column):
            if c is e or (c.table or "").lower() not in sources:
                continue
            r = resolve(c, depth + 1)
            if r.get("computes") != c.sql(dialect=dialect):
                inputs.append({"column": c.sql(dialect=dialect),
                               **{k: v for k, v in r.items() if k != "via"}})
            if len(inputs) >= 4:
                break
        if inputs:
            info["inputs"] = inputs
        return info

    rows = []
    for p in (root.expressions or [])[:limit]:
        name = p.alias_or_name or p.sql(dialect=dialect)
        e = p.this if isinstance(p, exp.Alias) else p
        r = resolve(e)
        r["computes"] = r["computes"][:500]
        rows.append({"column": name, **r})
    return rows


def _resolved(rows: list[dict]) -> list[dict]:
    """Only the columns the SQL hides: one produced by a scalar subquery, a CTE or a derived
    table, or forced empty. A column read straight off a table says nothing the SQL does not."""
    return [r for r in rows if r.get("via") or r.get("from") or r.get("inputs")
            or r.get("always_empty")]


def facts(o: dict, dialect: str, light: bool = False) -> dict:
    """What the describe stage reads, computed by code, for gates that run before it.

    `light` is for the gates after the proposals (merge, cards): the filters already travel on
    each proposal, so only the hidden columns and the dashboard's own words are added."""
    from metrics_agent.stages import m2_describe as _m2
    out: dict = {} if light else {
        "outer_select": _m2._outer_select(o["sql"], dialect),
        "restrictions": _m2._restrictions_of(o, dialect)[:25],
        "aliases_seen": o.get("aliases") or [],
    }
    hidden = _resolved(columns(o["sql"], dialect))
    if hidden:
        out["columns"] = hidden
    if bi_draft_by_code():
        ctx = ((o.get("provenance") or {}).get("bi") or {}).get("bi_context")
        if ctx:
            out["dashboard_context"] = ctx
    return out


def prose(o: dict) -> dict:
    """name / question / description as the gates should see them: authored prose, or none."""
    if prose_is_authored(o):
        return {"name": o.get("name"), "question": o.get("question"),
                "description": o.get("description")}
    return {"name": None, "question": None, "description": None}


NOTE = ("Statement prose is written LAST in this run. `question` and `description` are null "
        "where nobody has written them yet: work out what each statement answers from "
        "`facts.outer_select` (what the outermost select returns and groups by), "
        "`facts.restrictions` (every filter, CTEs included), "
        "`facts.columns` (each output column the SQL hides behind a subquery or CTE, "
        "resolved), `facts.aliases_seen` and, where "
        "present, `facts.dashboard_context` (the dashboard's own titles, tooltips and measure "
        "descriptions), before reading the SQL.")
