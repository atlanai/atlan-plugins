# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Deterministic SQL analysis: parse, canonicalise, featurize, classify, project.

Every function here is pure and reproducible — no LLM anywhere. That is a measured choice:
sqlglot parses essentially all real warehouse query text, the classification is rule-based,
and putting a model in front of a hundred-thousand-shape corpus makes it both expensive and
irreproducible.

PROVENANCE. The shape/featurize design is copied (not imported) from an earlier benchmark
tool's query-history pipeline — its canonicalise, featurize, filter, enrich and dedup steps
— and from the query-shape module of an earlier project.
Deliberately left behind: the DuckDB bronze reader (ours is `h0_corpus.py` and supports both
source modes), the Snowflake witness substrate (ours is `lib/engine.py`), and the repo-root
path constants that made the original unimportable.

RULE 11, load-bearing: comments are stripped with the PARSER, never a regex, and SQL is never
single-line-flattened. A prior corpus pass flattened statements while leaving inline `--`
comments, commenting out the rest of every affected statement: 223 of 296 witness failures.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope as _traverse_scope

AGG_FUNCS = {
    "SUM": "sum", "COUNT": "count", "AVG": "avg", "MIN": "min", "MAX": "max",
    "MEDIAN": "median", "STDDEV": "stddev", "VARIANCE": "variance",
    "APPROXDISTINCT": "count_distinct", "COUNTIF": "count",
    "ARRAYAGG": "custom", "LISTAGG": "custom", "STRINGAGG": "custom",
}
_WS = re.compile(r"\s+")


class ParseError(ValueError):
    """Raised when a statement cannot be parsed in the declared dialect."""


# --------------------------------------------------------------------------- parse
def parse(sql: str, dialect: str) -> exp.Expression:
    """Parse in the DECLARED dialect. Never parse generically: generic parsing of a
    Snowflake statement mis-reads QUALIFY, semi-structured access and `::` casts silently,
    and a mis-parse at B3 drops a query as out-of-scope — a scope finding, not a bug report."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as exc:  # sqlglot raises several types
        raise ParseError(f"{type(exc).__name__}: {exc}") from None
    if tree is None:
        raise ParseError("statement parsed to nothing")
    return tree


def string_literal(value: str, dialect: str) -> str:
    """`value` as a string literal of `dialect`, quoted and escaped as that dialect reads one
    (a backslash is an escape in some). For binding a value into a statement in memory."""
    return exp.Literal.string(str(value)).sql(dialect=dialect)


def string_literals(tree: exp.Expression) -> list[str]:
    """Every string literal `tree` holds, read through the AST (never a regex over text)."""
    return [str(lit.this) for lit in tree.find_all(exp.Literal) if lit.is_string]


def strip_comments(tree: exp.Expression) -> exp.Expression:
    """Remove comments through the AST. Rule 11: never a regex, never a flatten."""
    out = tree.copy()
    for node in out.walk():
        if isinstance(node, exp.Expression) and node.comments:
            node.comments = None
    return out


def render(tree: exp.Expression, dialect: str, *, pretty: bool = True) -> str:
    """Render back to SQL. `pretty=True` by default and never overridden to produce one line:
    multi-line output is what keeps a trailing `--` comment from eating the statement."""
    return tree.sql(dialect=dialect, pretty=pretty)


def comment_vocabulary(tree: exp.Expression) -> list[str]:
    """Tokens from SQL comments. Analysts write the business word in a comment far more often
    than they name a column after it, so this is real retrieval vocabulary — collected before
    comments are stripped."""
    words: set[str] = set()
    for node in tree.walk():
        for c in (getattr(node, "comments", None) or []):
            for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", c):
                words.add(w.lower())
    return sorted(words)


# --------------------------------------------------------------------- canonicalise
def canonicalise(tree: exp.Expression, dialect: str) -> str:
    """The SHAPE: every literal replaced by a placeholder, identifiers normalised.

    Deliberately NOT executable — it is the unit of deduplication. Two queries differing only
    in their literal values (or in whitespace / identifier casing) collapse to one shape_key.
    """
    out = strip_comments(tree)
    for lit in list(out.find_all(exp.Literal)):
        lit.replace(exp.Placeholder())
    for node in list(out.find_all(exp.Identifier)):
        node.set("this", str(node.this).lower())
        node.set("quoted", False)
    sql = out.sql(dialect=dialect, pretty=False, normalize=True)
    return _WS.sub(" ", sql).strip().lower()


def shape_key(shape_sql: str) -> str:
    return hashlib.sha256(shape_sql.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------- tables
def _subject_of(pred: exp.Expression, col: exp.Column) -> str:
    """What a comparison restricts: the column, or, when the column's side computes something
    (`year * 100 + month >= 202501`), that expression. Naming the first column inside it
    would claim `month >= 202501`, and record 202501 as an observed month."""
    left, right = pred.this, pred.args.get("expression")
    side = left if (left is col or col in list(left.find_all(exp.Column))) else right
    while isinstance(side, exp.Paren):
        side = side.this
    if side is None or isinstance(side, exp.Column):
        return _col_name(col)
    if any(not isinstance(n, exp.Predicate) for n in side.find_all(exp.Binary)):
        return _WS.sub(" ", side.sql()).strip().lower()
    return _col_name(col)


def _operand_of(pred: exp.Expression, col: exp.Column) -> tuple[Any, str]:
    """What this predicate compares the column TO, and whether that is a literal.

    THE OPERAND USED TO BE DROPPED WHENEVER IT WAS NOT A LITERAL. Only `exp.Literal` was
    read, so `close_date_as_of >= r.close_quarter_start` recorded `value: None` and the
    metric card published `close_date_as_of >= NULL` — a restriction no reader can act on
    and no engine can run. Measured on this estate: 8,664 of 25,065 filters, 34.6%, carried
    no value at all.

    So take the side the column is not on, and say what it is. A literal keeps its raw value
    exactly as before, so nothing that already worked changes; a column or expression is kept
    as its own SQL text, which is the only faithful thing to record.
    """
    left, right = pred.this, pred.args.get("expression")
    other = right if (left is col or col in list(left.find_all(exp.Column))) else left
    if other is None:
        return None, "absent"
    lits = list(other.find_all(exp.Literal)) if not isinstance(other, exp.Literal) else [other]
    if isinstance(other, exp.Literal):
        return other.this, "literal"
    if isinstance(other, exp.Column):
        return _col_name(other), "column"
    if len(lits) == 1 and not list(other.find_all(exp.Column)):
        # a cast or a simple wrapper around one literal: `'2026-01-01'::DATE`
        return lits[0].this, "literal"
    return _WS.sub(" ", other.sql()).strip().lower() or None, "expression"


# The function calls that ARE a restriction rather than part of one. Shared with D3, which
# must render such a predicate as the call itself: it has no operand to place after an
# operator, and the column-op-value shape prints it twice.
PREDICATE_FUNCTIONS = ("contains", "startswith", "endswith", "array_contains", "regexp_like",
                       "rlike", "like_any", "ilike_any")


def _folded_column(e: exp.Expression) -> exp.Column | None:
    """The column a FOLDED text key reads: `UPPER(RTRIM(col))`, `UPPER(col)` (a model that
    groups text without case or trailing spaces groups by that key). None for anything else."""
    cur = e
    seen = False
    while isinstance(cur, (exp.Upper, exp.Trim)) or (
            isinstance(cur, exp.Anonymous) and str(cur.name).upper() == "RTRIM"):
        seen = True
        cur = cur.this if not isinstance(cur, exp.Anonymous) else (cur.expressions or [None])[0]
    return cur if seen and isinstance(cur, exp.Column) else None


def key_displays(select: exp.Select | None) -> set[int]:
    """The projections that SHOW a grouping column rather than compute a number: `MIN(col) AS
    c` beside `GROUP BY UPPER(RTRIM(col))` (one spelling of a folded text key), and
    `MIN(col) OVER (PARTITION BY UPPER(col))` beside `GROUP BY col` (one spelling per value
    equal without case). By id()."""
    if select is None:
        return set()
    group = select.args.get("group")
    if group is None:
        return set()
    folded = {(_folded_column(g).sql().lower()) for g in group.expressions
              if _folded_column(g) is not None}
    plain = {g.sql().lower() for g in group.expressions if isinstance(g, exp.Column)}
    out: set[int] = set()
    for p in select.expressions:
        inner = p.this if isinstance(p, exp.Alias) else p
        if isinstance(inner, exp.Min) and isinstance(inner.this, exp.Column) \
                and inner.this.sql().lower() in folded:
            out.add(id(p))
        elif isinstance(inner, exp.Window) and isinstance(inner.this, exp.Min) \
                and isinstance(inner.this.this, exp.Column) \
                and inner.this.this.sql().lower() in plain:
            out.add(id(p))
    return out


def resolve_group_expressions(group: exp.Expression) -> list[exp.Expression]:
    """The GROUP BY keys as EXPRESSIONS, with positional references resolved.

    `GROUP BY 1` is the first projection of the select the GROUP BY belongs to. Two places
    read a GROUP BY — the shape extractor and the dimension observer — and only fixing one
    left the other publishing dimensions literally named `1`, which then failed D4's
    reference-integrity gate because an example grouped by `week_of` while the metric declared
    a dimension called `1`. One resolver, so the two cannot drift again.
    """
    sel = group.parent if isinstance(group.parent, exp.Select) else None
    proj = list(sel.expressions) if sel is not None else []
    out: list[exp.Expression] = []
    for e in group.expressions:
        if isinstance(e, exp.Literal) and str(e.this).isdigit():
            idx = int(e.this) - 1
            if 0 <= idx < len(proj):
                out.append(proj[idx])
                continue
        # A folded text key groups by its column (`key_displays`).
        out.append(_folded_column(e) or e)
    return out


def _ancestors(node: exp.Expression, stop: exp.Expression):
    """Every node between this one and its clause, so a predicate already captured as part of
    an enclosing function call is not counted a second time as a bare comparison."""
    cur = node.parent
    while cur is not None and cur is not stop:
        yield cur
        cur = cur.parent


def _under_not(node: exp.Expression, stop: exp.Expression) -> bool:
    """Is this predicate negated by an enclosing NOT, anywhere up to its clause?

    Counted rather than flagged, because `NOT (NOT x)` is x — and because the negation may
    sit several levels up: `NOT (a ILIKE '..' OR b ILIKE '..')` puts one `Not` above a
    `Paren` above the `Or` above each comparison, and every comparison under it is excluded.
    """
    seen = 0
    cur = node
    while cur is not None and cur is not stop:
        if isinstance(cur, exp.Not):
            seen += 1
        cur = cur.parent
    return seen % 2 == 1


def _qualified(t: exp.Table, *, preserve_case: bool = False) -> str:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    name = ".".join(parts)
    return name if preserve_case else name.lower()


def extract_tables(tree: exp.Expression, *, preserve_case: bool = False) -> list[str]:
    """Fully-qualified REAL tables. CTE names are excluded — a CTE is a name for a subquery,
    not an object in the estate, and counting it makes B3's scope check reject valid work.

    Folded to lower case by DEFAULT, because every caller here uses the result as a key: a
    shape signature, a support count, a scope check. `preserve_case=True` returns the names
    exactly as the statement spells them, which is what a caller must use when the names will
    be RESOLVED OR EXECUTED against the warehouse — Snowflake quoted identifiers, Databricks
    and Postgres are case-sensitive, and a folded name is a different object there. Normalise
    at the point of comparison, never in the artifact.
    """
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    found: dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        name = _qualified(t, preserve_case=preserve_case)
        if not name:
            continue
        if t.name and t.name.lower() in ctes and not t.db:
            continue
        found.setdefault(name.lower(), name)
    return [found[k] for k in sorted(found)]


# ------------------------------------------------------------------------ features
def _col_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Column):
        return node.name.lower()
    return node.sql().lower()


def is_identity_column(name: str, patterns: list[re.Pattern[str]]) -> bool:
    """A predicate counts as business logic only if at least one filtered column is NOT an
    id/key column. `WHERE customer_id = ?` is a lookup; `WHERE status = 'churned'` is a
    decision. Without this rule the corpus fills with point lookups carrying no reusable
    logic."""
    bare = name.split(".")[-1]
    return any(p.search(bare) for p in patterns)


# Navigation functions sqlglot types as aggregates. They read a neighbouring row; they do not
# aggregate anything, and treating `LAG(spend)` as a measure published the previous row as one.
_NOT_AGGREGATES = tuple(c for c in (getattr(exp, n, None) for n in
                                    ("Lag", "Lead", "FirstValue", "LastValue", "NthValue"))
                        if c is not None)


def _aggregates_in(node: exp.Expression) -> list[exp.Expression]:
    return [a for a in node.find_all(exp.AggFunc) if not isinstance(a, _NOT_AGGREGATES)]


# Wrappers that hand on ONE value unchanged in meaning: a pass-through, not a computation.
_PASS_THROUGH = (exp.Coalesce, exp.Round, exp.Cast, exp.TryCast, exp.Paren)


def _aggregate_behind(node: exp.Expression, select: exp.Select, tree: exp.Expression,
                      hops: int = 3) -> exp.Expression | None:
    """The aggregate a pass-through projection hands on, from the CTE, derived table or
    scalar subquery that computes it; None when it is not a pass-through of one."""
    while isinstance(node, _PASS_THROUGH):
        node = node.this
    if hops <= 0 or node is None:
        return None
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select):
        inner_sel = node.this
        if len(inner_sel.expressions) != 1:
            return None
        proj = inner_sel.expressions[0]
        body = proj.this if isinstance(proj, exp.Alias) else proj
        if _aggregates_in(body):
            return body
        return _aggregate_behind(body, inner_sel, tree, hops - 1)
    if not isinstance(node, exp.Column):
        return None
    sources: dict[str, exp.Expression] = {}
    frm = select.args.get("from_") or select.args.get("from")
    for src in ([frm.this] if frm is not None else []) + \
               [j.this for j in (select.args.get("joins") or [])]:
        key = (src.alias_or_name or "").lower()
        if isinstance(src, exp.Table):
            sources[key] = src
            sources.setdefault(src.name.lower(), src)
        elif isinstance(src, exp.Subquery):
            sources[key] = src.this
    qual = (node.table or "").lower()
    src = sources.get(qual) if qual else (next(iter(sources.values())) if len(sources) == 1
                                         else None)
    if isinstance(src, exp.Table):
        cte = next((c for c in tree.find_all(exp.CTE)
                    if c.alias_or_name.lower() == src.name.lower()), None)
        src = cte.this if cte is not None else None
    if not isinstance(src, exp.Select):
        return None
    for proj in src.expressions:
        if proj.alias_or_name.lower() != node.name.lower():
            continue
        body = proj.this if isinstance(proj, exp.Alias) else proj
        if _aggregates_in(body):
            return body
        return _aggregate_behind(body, src, tree, hops - 1)
    return None


def set_operation_branches(tree: exp.Expression) -> list[exp.Select]:
    """The SELECT of every branch of the statement's outermost set operation, left to right;
    [] when the statement is not a set operation.

    Set operations nest to the left (`a UNION b UNION c` is `(a UNION b) UNION c`) and a
    branch may be parenthesised, so the walk unwraps both. A branch that is not a plain
    SELECT (a VALUES list, say) contributes nothing.
    """
    root = tree
    while isinstance(root, (exp.Subquery, exp.Paren)):
        root = root.this
    if not isinstance(root, exp.SetOperation):
        return []
    out: list[exp.Select] = []

    def walk(node: exp.Expression | None) -> None:
        while isinstance(node, (exp.Subquery, exp.Paren)):
            node = node.this
        if isinstance(node, exp.SetOperation):
            walk(node.this)
            walk(node.expression)
        elif isinstance(node, exp.Select):
            out.append(node)

    walk(root)
    return out


def _branch_scope_sql(branch: exp.Select, tree: exp.Expression, dialect: str | None) -> str:
    """One branch as a statement of its own, carrying the statement's CTEs so a name the
    branch reads from one still resolves. What the branch's FROM reads is where ITS rows come
    from, which is not necessarily where the first branch's do."""
    node = branch.copy()
    with_ = tree.args.get("with_") or tree.args.get("with")
    if with_ is not None and not (node.args.get("with_") or node.args.get("with")):
        node.set("with_" if "with_" in tree.args else "with", with_.copy())
    return node.sql(dialect=dialect)


def extract_aggregations(tree: exp.Expression, dialect: str | None = None, *,
                         every_branch: bool = False) -> list[dict[str, Any]]:
    """Every aggregate in the projection, with its alias when it has one.

    An UNALIASED aggregate is still reported here (B5 wants to know it exists) but D1 refuses
    to lift it into a metric: a generated name is a business claim nobody made.

    `every_branch`: A SET OPERATION COMPUTES IN EVERY BRANCH. By default only the first
    branch's projection is read, which is what names the statement's output columns and
    what the shape key is built from. Measure lifting passes True: `SELECT SUM(a) FROM x
    UNION ALL SELECT COUNT(*) FROM y` computes two numbers, and reading the left branch alone
    lost the second. Each aggregate found in a later branch carries `branch` (its 0-based
    position) and `scope_sql` (that branch as a statement, with the CTEs), so its entity is
    resolved from the branch that computes it; a branch's unaliased column takes the name the
    first branch gives that position, which is the name the statement returns it under.
    """
    out: list[dict[str, Any]] = []
    branches = set_operation_branches(tree) if every_branch else []
    if branches:
        first_names = [p.alias_or_name if isinstance(p, exp.Alias) else None
                       for p in branches[0].expressions]
        for i, sel in enumerate(branches):
            found = _aggregations_of(sel, tree, dialect)
            for a in found:
                pos = a.pop("_position")
                if not a["alias"] and pos < len(first_names) and first_names[pos]:
                    a["alias"] = first_names[pos].lower()
                if i:
                    a["branch"] = i
                    a["scope_sql"] = _branch_scope_sql(sel, tree, dialect)
                out.append(a)
        return out
    select = tree.find(exp.Select)
    if select is None:
        return out
    for a in _aggregations_of(select, tree, dialect):
        a.pop("_position")
        out.append(a)
    return out


def _aggregations_of(select: exp.Select, tree: exp.Expression,
                     dialect: str | None) -> list[dict[str, Any]]:
    """The aggregates one SELECT's projection computes (see `extract_aggregations`)."""
    out: list[dict[str, Any]] = []
    shown = key_displays(select)
    for position, proj in enumerate(select.expressions):
        if id(proj) in shown:
            continue                    # a grouping column's spelling, not a number
        alias = proj.alias_or_name if isinstance(proj, exp.Alias) else None
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        aggs = _aggregates_in(inner)
        if not aggs:
            # THE NUMBER MAY BE COMPUTED ONE LEVEL IN. `SELECT c.current_spend FROM
            # supplier_current c` and `(SELECT total FROM total_current)` hand on an aggregate
            # a CTE computed; reading only the outer projection found nothing, so a statement
            # written with CTEs offered no metric at all. Follow a pass-through to its source.
            behind = _aggregate_behind(inner, select, tree)
            if behind is None:
                continue
            inner = behind
            aggs = _aggregates_in(inner)
            # A bare column's name IS its output name: `SELECT monthly_spend FROM monthly`.
            alias = alias or proj.alias_or_name or None
        top = aggs[0]
        fn = type(top).__name__.upper()
        kind = AGG_FUNCS.get(fn, "custom")
        if fn == "COUNT" and top.args.get("distinct"):
            kind = "count_distinct"
        if isinstance(inner, exp.Div) or len(aggs) > 1:
            kind = "ratio" if isinstance(inner, exp.Div) else kind
        out.append({
            "fn": kind,
            "expr": inner.sql(dialect=dialect).strip(),
            "alias": alias.lower() if alias else None,
            "n_aggs": len(aggs),
            "_position": position,
        })
    return out


def _defines_source(clause: exp.Expression) -> bool:
    """Is this clause the WHERE of a derived table the statement reads FROM (or JOINs)?

    Such a predicate is not a choice of the question: it defines the rows of the table the
    statement reads (a BI tool's own query steps, compiled as the table's statement, keep only
    some rows), so a metric over the statement keeps it whatever else it varies."""
    if not isinstance(clause, exp.Where):
        return False
    sel = clause.parent
    if not isinstance(sel, exp.Select):
        return False
    sub = sel.parent
    return isinstance(sub, exp.Subquery) and isinstance(sub.parent, (exp.From, exp.Join))


def extract_feat(tree: exp.Expression, identity_patterns: list[re.Pattern[str]]) -> dict[str, Any]:
    """Structured retrieval features: what the query filters, groups, aggregates and joins on.

    A superset-compatible copy of an earlier benchmark tool's extractor. These are the fields a
    consumer's BM25F index reads, which is why aliases and comment vocabulary are collected
    rather than discarded: colloquial question words never match column names.
    """
    select = tree.find(exp.Select)

    # The left side of a join is whatever the query selected FROM. Resolving it (rather than
    # leaving it blank) is what lets C2's shape key and D1b's join analysis see a table PAIR
    # rather than a dangling right-hand side.
    from_expr = tree.find(exp.From)
    base = ""
    if from_expr is not None:
        ft = from_expr.find(exp.Table)
        if ft is not None:
            base = _qualified(ft)

    # A CTE reference parses as exp.Table whose name is the CTE ALIAS, so `_qualified` yields
    # `__users`, not the table `__users` reads. That name matches no real table anywhere, so a
    # consumer building a reachability graph from these endpoints gets edges between phantom
    # nodes: measured on this estate, 684 recorded edges connected nothing and 38% of
    # multi-table metric candidates came out unjoinable, which made D2's rule 3 unsatisfiable
    # for them by construction. Resolve the alias to the base tables it reads — transitively,
    # because a CTE may select from another CTE — and emit one entry per real table pair. That
    # is what this field has always claimed to hold.
    cte_base: dict[str, list[str]] = {}
    for w in tree.find_all(exp.With):
        for cte in w.expressions:
            nm = (cte.alias_or_name or "").lower()
            if nm:
                cte_base[nm] = [_qualified(t) for t in cte.this.find_all(exp.Table)]
    for _ in range(len(cte_base)):          # transitive closure, bounded by the CTE count
        for nm, refs in cte_base.items():
            out: list[str] = []
            for r in refs:
                out.extend(cte_base.get(r.lower(), [r]))
            cte_base[nm] = out

    def _resolve(name: str) -> list[str]:
        """-> the REAL base tables an endpoint stands for. A non-CTE name stands for itself."""
        return [t for t in dict.fromkeys(cte_base.get(name.lower(), [name])) if t]

    joins: list[dict[str, Any]] = []
    prev = base
    for j in tree.find_all(exp.Join):
        right = j.this
        rname = _qualified(right) if isinstance(right, exp.Table) else right.sql().lower()
        on = j.args.get("on")
        cols = sorted({_col_name(c) for c in on.find_all(exp.Column)}) if on else []
        jtype = (j.side or j.kind or "inner").lower()
        for lt in _resolve(prev or rname):
            for rt in _resolve(rname):
                if lt != rt:
                    joins.append({"left": lt, "right": rt, "on": cols, "type": jtype})
        prev = rname

    def _scope_of(clause: exp.Expression) -> list[str]:
        """The REAL tables the select owning this clause restricts.

        A PREDICATE RESTRICTS THE SELECT IT SITS IN, NOT THE STATEMENT. Filters were
        harvested from every WHERE in the statement and attributed flat to the metric's one
        `entity`, and 79% of these statements have more than one. So a card for a metric over
        STG_CRM_ACCOUNTS published "All rows of STG_CRM_ACCOUNTS, restricted by
        contacts_reached > 0, campaign = 'Spring Launch 2026'" — columns that table does not
        have, taken from two other CTEs over two other tables. Measured here: 381 of 787
        cards, 48%, described rows their own entity never had.

        Only the select's OWN sources count (its FROM and its JOINs); a nested subquery has
        its own scope and its predicates carry their own. CTE names resolve to the base tables
        they stand for, which is what the rest of this function already means by a table.
        """
        sel = clause.parent
        while sel is not None and not isinstance(sel, exp.Select):
            sel = sel.parent
        if sel is None:
            return []
        srcs: list[str] = []
        # sqlglot names this arg `from_` from 30.x and `from` before it; read both rather
        # than pin a version, because an unrecognised key silently yields an empty scope and
        # an empty scope is indistinguishable from "no scope recorded".
        frm = sel.args.get("from_") or sel.args.get("from")
        if frm is not None:
            srcs += [_qualified(t) for t in frm.find_all(exp.Table)]
        for j in (sel.args.get("joins") or []):
            srcs += [_qualified(t) for t in j.find_all(exp.Table)]
        out: list[str] = []
        for r in srcs:
            out.extend(_resolve(r))
        return sorted(dict.fromkeys(t for t in out if t))

    # A RESTRICTION IS NOT ALWAYS A COMPARISON. Only `exp.Binary` was read, so
    # `CONTAINS(LOWER(TAGS_C), LOWER('AI Ready'))` — the single predicate that defines an
    # AI-Ready account on this estate — was dropped entirely and the card published the
    # unrelated predicates from other CTEs in its place. 45% of these statements restrict
    # through a function. The call is recorded verbatim as its own SQL, because that is the
    # only faithful rendering of a predicate with no operand to name.
    _PRED_FUNCS = PREDICATE_FUNCTIONS

    filters: list[dict[str, Any]] = []
    for clause_type in (exp.Where, exp.Having, exp.Qualify):
        for clause in tree.find_all(clause_type):
            scope = _scope_of(clause)
            source_rows = _defines_source(clause)
            hidden = hides_empty_rows(clause)
            for pred in clause.find_all(exp.Func):
                if id(pred) in hidden:
                    continue
                name = (pred.sql_name() or "").lower() if hasattr(pred, "sql_name") else ""
                if name not in _PRED_FUNCS:
                    continue
                fcols = list(pred.find_all(exp.Column))
                if not fcols:
                    continue
                filters.append({
                    "column": _col_name(fcols[0]),
                    "op": name,
                    "value": _WS.sub(" ", pred.sql()).strip(),
                    "value_kind": "expression",
                    "is_identity": is_identity_column(_col_name(fcols[0]), identity_patterns),
                    "stage": clause_type.__name__.lower(),
                    "negated": _under_not(pred, clause),
                    "scope_tables": scope,
                    **({"in_source": True} if source_rows else {}),
                })
            # MEMBERSHIP AND RANGE ARE RESTRICTIONS TOO. `type IN ('Direct', 'Indirect')` and
            # `year BETWEEN 2025 AND 2026` are not `exp.Binary`, so they were never recorded,
            # and every consumer believed the statement covered all types and all years.
            for pred in clause.find_all(exp.In, exp.Between):
                if id(pred) in hidden:
                    continue
                if any(isinstance(a, exp.Func) and (a.sql_name() or "").lower() in _PRED_FUNCS
                       for a in _ancestors(pred, clause)):
                    continue
                subject = pred.this
                cols = list(subject.find_all(exp.Column)) if subject is not None else []
                if not cols:
                    continue
                if isinstance(pred, exp.In):
                    members = pred.args.get("query") or pred.args.get("unnest")
                    value = (members.sql() if members is not None else
                             "(" + ", ".join(e.sql() for e in pred.expressions) + ")")
                else:
                    value = f"{pred.args['low'].sql()} AND {pred.args['high'].sql()}"
                filters.append({
                    "scope_tables": scope,
                    **({"in_source": True} if source_rows else {}),
                    "column": _col_name(cols[0]),
                    "op": "in" if isinstance(pred, exp.In) else "between",
                    "value": _WS.sub(" ", value).strip(),
                    # A set or a range is not ONE tunable value.
                    "value_kind": "expression",
                    "is_identity": is_identity_column(_col_name(cols[0]), identity_patterns),
                    "stage": clause_type.__name__.lower(),
                    "negated": _under_not(pred, clause),
                })
            for pred in clause.find_all(exp.Binary):
                if isinstance(pred, (exp.And, exp.Or)) or id(pred) in hidden:
                    continue
                # ARITHMETIC IS NOT A RESTRICTION. `year * 100 + month >= …` is ONE comparison;
                # its `*` and `+` are also `exp.Binary`, and each was recorded as a filter of
                # its own (`year mul 100`, `month add …`).
                if not isinstance(pred, exp.Predicate):
                    continue
                cols = list(pred.find_all(exp.Column))
                if not cols:
                    continue
                # Some of these are BOTH a Func and a Binary (`RegexpLike`), so the node
                # itself counts, not only its ancestors: without this the same restriction
                # was recorded twice, once whole and once as its inner comparison.
                if any(isinstance(a, exp.Func) and (a.sql_name() or "").lower() in _PRED_FUNCS
                       for a in (pred, *_ancestors(pred, clause))):
                    continue        # already recorded as the function predicate
                col = _subject_of(pred, cols[0])
                value, kind = _operand_of(pred, cols[0])
                filters.append({
                    "scope_tables": scope,
                    **({"in_source": True} if source_rows else {}),
                    "column": col,
                    "op": type(pred).__name__.lower(),
                    "value": value,
                    # A LITERAL CAN BE TUNED; A COLUMN CANNOT. D1 decides definitional-vs-
                    # parameter by whether a column/op repeats with varying values, and a
                    # correlated comparison has no value to vary — calling one a knob invites
                    # a caller to set `close_date_as_of` to a date when the statement means
                    # "within the quarter this row belongs to".
                    "value_kind": kind,
                    "is_identity": is_identity_column(col, identity_patterns),
                    "stage": clause_type.__name__.lower(),
                    # WITHOUT POLARITY AN EXCLUSION READS AS AN INCLUSION, and everything
                    # downstream believes it. `NOT (stage ILIKE '1 - %')` was recorded as
                    # `stage ilike '1 - %'`, so D1 saw one column carrying four different
                    # values, applied its "same question asked of different segments" rule,
                    # and published the whole set as a tunable parameter instead of part of
                    # the definition. The metric that resulted defined open pipeline with no
                    # stage exclusions at all and answered three benchmark questions ~19% over.
                    "negated": _under_not(pred, clause),
                })

    selects = []
    if select is not None:
        for p in select.expressions:
            selects.append((p.alias_or_name or p.sql()).lower())

    group_by = []
    g = tree.find(exp.Group)
    if g is not None:
        # A POSITIONAL GROUP BY NAMES A COLUMN, NOT A NUMBER. `GROUP BY 1, 2` refers to the
        # first and second projections, and recording the ordinal published a metric grained
        # "one row per 1 x 2" and declared dimensions literally called `1` and `2` — measured
        # on this estate, 37 objects carried a dimension named `1`. The ordinal is resolved
        # against the projections of the SELECT the GROUP BY belongs to, which is the same
        # list the engine resolves it against.
        for e in resolve_group_expressions(g):
            group_by.append((e.alias_or_name or e.sql()).lower()
                            if isinstance(e, exp.Alias) else _col_name(e))

    window_keys: set[str] = set()
    for w in tree.find_all(exp.Window):
        for part in (w.args.get("partition_by") or []):
            window_keys.add(_col_name(part))

    return {
        "joins": joins,
        "filters": filters,
        "select": selects,
        "group_by": group_by,
        "aggregations": [
            {"fn": a["fn"], "expr": a["expr"], "alias": a["alias"]}
            for a in extract_aggregations(tree)
        ],
        "window_keys": sorted(window_keys),
        "comment_vocab": comment_vocabulary(tree),
    }


def hides_empty_rows(clause: exp.Expression) -> set[int]:
    """The nodes of a HAVING that only HIDE EMPTY ROWS: a term `<value> IS NOT NULL` (or an
    OR of such terms) whose value is one of the SELECT's own projections. A statement that
    leaves out the rows whose every value is empty (as a BI view does) says so this way; it
    restricts no row's population, so no part of it is a filter. -> the ids of every node of
    those terms (empty for any other clause)."""
    if not isinstance(clause, exp.Having):
        return set()
    sel = clause.parent
    while sel is not None and not isinstance(sel, exp.Select):
        sel = sel.parent
    if sel is None:
        return set()
    shown = {(p.this if isinstance(p, exp.Alias) else p).sql() for p in sel.expressions}

    def empty_test(node) -> bool:
        if isinstance(node, exp.Paren):
            return empty_test(node.this)
        if isinstance(node, exp.Or):
            return empty_test(node.left) and empty_test(node.right)
        if isinstance(node, exp.Not) and isinstance(node.this, exp.Is) \
                and isinstance(node.this.expression, exp.Null):
            return node.this.this.sql() in shown
        return False

    out: set[int] = set()
    terms = [clause.this]
    while terms:
        t = terms.pop()
        if isinstance(t, exp.And):
            terms += [t.left, t.right]
        elif isinstance(t, exp.Paren) and isinstance(t.this, exp.And):
            terms.append(t.this)
        elif empty_test(t):
            out |= {id(n) for n in t.walk()}
    return out


def extract_filter_values(tree: exp.Expression) -> list[tuple[str, str]]:
    """(column, literal) for every comparison in a WHERE/HAVING/QUALIFY.

    Deliberately lighter than `extract_feat`: H1 runs this per ROW, not per shape, so it walks
    only the predicate clauses and skips joins, projections, grouping and comments.
    """
    out: list[tuple[str, str]] = []
    for clause_type in (exp.Where, exp.Having, exp.Qualify):
        for clause in tree.find_all(clause_type):
            hidden = hides_empty_rows(clause)
            for pred in clause.find_all(exp.Binary):
                if isinstance(pred, (exp.And, exp.Or)) or not isinstance(pred, exp.Predicate) \
                        or id(pred) in hidden:
                    continue        # arithmetic's operands are not values of the column
                cols = list(pred.find_all(exp.Column))
                if not cols:
                    continue
                col = _subject_of(pred, cols[0])
                value, kind = _operand_of(pred, cols[0])
                if kind == "literal" and value not in (None, ""):
                    out.append((col, str(value)))
    # An IN list is the case this exists for: one predicate, many values.
    for node in tree.find_all(exp.In):
        cols = list(node.find_all(exp.Column))
        if not cols:
            continue
        col = _col_name(cols[0])
        for lit in node.find_all(exp.Literal):
            val = str(lit.this)
            if val:
                out.append((col, val))
    return out


# The comparisons whose literal is a value the column TAKES. `<>` names a value the statement
# excludes, and a LIKE/regex pattern is not a value at all.
_VALUE_COMPARISONS = (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def observed_literals(tree: exp.Expression) -> list[tuple[str, str]]:
    """(column, value) for every literal a statement restricts a column TO: each member of an
    IN list, each bound of a BETWEEN, the literal of a comparison. Never a column compared
    to a column (`a.x = b.y` has no value), never a negated predicate (`NOT IN`, `<>`): those
    name what the population is not."""
    out: list[tuple[str, str]] = []

    def _lit(node) -> str | None:
        return str(node.this) if isinstance(node, exp.Literal) and str(node.this) else None

    for clause_type in (exp.Where, exp.Having, exp.Qualify):
        for clause in tree.find_all(clause_type):
            hidden = hides_empty_rows(clause)
            for pred in clause.find_all(exp.In, exp.Between, *_VALUE_COMPARISONS):
                if _under_not(pred, clause) or id(pred) in hidden:
                    continue
                if isinstance(pred, (exp.In, exp.Between)):
                    subject = pred.this
                    if not isinstance(subject, exp.Column):
                        continue
                    col = _col_name(subject)
                    vals = (pred.expressions if isinstance(pred, exp.In)
                            else [pred.args.get("low"), pred.args.get("high")])
                    out += [(col, v) for v in (_lit(x) for x in vals) if v]
                    continue
                cols = list(pred.find_all(exp.Column))
                if not cols:
                    continue
                value, kind = _operand_of(pred, cols[0])
                if kind == "literal" and value not in (None, ""):
                    out.append((_subject_of(pred, cols[0]), str(value)))
    return out


def compute_constructs(tree: exp.Expression, feat: dict[str, Any]) -> dict[str, bool]:
    """Boolean presence of each business-logic construct. B2 scores on these."""
    has_business_predicate = any(
        not f["is_identity"] for f in feat["filters"]
    )
    return {
        "aggregate": bool(feat["aggregations"]),
        "join": bool(feat["joins"]),
        "business_predicate": has_business_predicate,
        "any_predicate": bool(feat["filters"]),
        "group_by": bool(feat["group_by"]),
        "window_function": bool(list(tree.find_all(exp.Window))),
        "cte": bool(list(tree.find_all(exp.CTE))),
        "having": bool(list(tree.find_all(exp.Having))),
        "set_operation": bool(list(tree.find_all(exp.Union))
                              or list(tree.find_all(exp.Intersect))
                              or list(tree.find_all(exp.Except))),
        "distinct": bool(tree.find(exp.Distinct)),
        "subquery": bool(list(tree.find_all(exp.Subquery))),
    }


def business_score(constructs: dict[str, bool], weights: dict[str, int]) -> tuple[int, list[str]]:
    """B2's content-based admission score, with the reasons it scored.

    `select * from t` and bare projections score 0 and are dropped. The reasons are kept so a
    drop ledger can say exactly why a query did not reach silver.
    """
    score = 0
    why: list[str] = []
    for key, weight in weights.items():
        if constructs.get(key):
            score += int(weight)
            why.append(key)
    return score, why


def family_key(tree: exp.Expression, feat: dict[str, Any]) -> str:
    """Coarse structural signature for grouping near-identical shapes into families."""
    tables = ",".join(extract_tables(tree))
    aggs = ",".join(sorted(a["fn"] for a in feat["aggregations"]))
    grp = ",".join(sorted(feat["group_by"]))
    return hashlib.sha256(f"{tables}|{aggs}|{grp}".encode()).hexdigest()[:12]


def classify(feat: dict[str, Any], constructs: dict[str, bool]) -> str:
    """Route the object: metric | segmentation | cohort | lookup | trend.

    Rule-based and ordered most-specific first. Classification decides what happens next — a
    `cohort` becomes a population a metric can reference, a `metric` is a D1 candidate, a
    `lookup` becomes a verified query and nothing more.
    """
    has_agg = constructs["aggregate"]
    has_time_group = any(
        re.search(r"(date|day|week|month|quarter|year|ts|time)", g)
        for g in feat["group_by"]
    )
    if has_agg and has_time_group:
        return "trend"
    if has_agg and feat["group_by"]:
        return "segmentation"
    if has_agg:
        return "metric"
    if constructs["business_predicate"] and not has_agg:
        return "cohort"
    return "lookup"


def derive_grain(feat: dict[str, Any]) -> str:
    """State the row grain of the output so a consumer knows COUNT(*) from COUNT(DISTINCT)
    without re-reading the SQL."""
    if feat["group_by"]:
        return "one row per " + " x ".join(feat["group_by"])
    if feat["aggregations"]:
        return "single scalar row"
    return "one row per source row"


def normalised_predicates(feat: dict[str, Any]) -> list[str]:
    """Predicate set with literals dropped, for the semantic shape key. Two questions that
    differ only in a filter VALUE are the same question."""
    return sorted({f"{f['column']} {f['op']}" for f in feat["filters"]})




def columns_used(tree: exp.Expression) -> set[str]:
    """Every bare column name referenced. Feeds the column-grounding gates.

    A STAR IS NOT A COLUMN. `t.*` parses as a Column whose body is a Star, so its `.name` is
    the literal `*` — and no catalogue has ever held a column called that, so grounding it
    could only ever fail. Excluded here rather than special-cased in each gate.
    """
    return {c.name.lower() for c in tree.find_all(exp.Column)
            if c.name and not isinstance(c.this, exp.Star)}


def projected_names(tree: exp.Expression) -> set[str]:
    """Names the STATEMENT ITSELF defines, so a grounding check does not call them invented.

    Such a name is grounded BY CONSTRUCTION — it is computed here, not read from a base
    table, and a check that only knows the estate's catalogue would block a statement that is
    exactly right. This matters now that a canonical statement is derived from its parent and
    keeps the parent's CTEs.

    Three sources, all of them seen on this estate:
      * every EXPLICIT alias, in every SELECT — including each branch of a set operator. A CTE
        whose body is a `UNION ALL` has a Union, not a Select, at its root, so looking only at
        the root skips the whole thing and every name it defines. The OUTER select counts too:
        `COUNT(*) AS requests ... ORDER BY requests` is legal SQL and `requests` is defined by
        the statement, not read from a table;
      * an explicit alias column list, `AS t(pid, plabel)`;
      * the implicit `column1..columnN` a bare `VALUES` relation is given.

    EXPLICIT ALIASES ONLY, which is what keeps this from grounding anything it likes: a bare
    `SELECT d.invented_col` defines nothing and is still checked against the catalogue. An
    invented column is referenced, never aliased into existence.
    """
    out: set[str] = set()
    for sel in tree.find_all(exp.Select):
        for proj in sel.expressions:
            if isinstance(proj, exp.Alias) and proj.alias:
                out.add(proj.alias.lower())
    for ta in tree.find_all(exp.TableAlias):
        for col in ta.columns:
            if col.name:
                out.add(col.name.lower())
    # A TABLE FUNCTION DEFINES ITS OWN OUTPUT COLUMNS. Snowflake's FLATTEN emits
    # SEQ/KEY/PATH/INDEX/VALUE/THIS, and a statement that unnests a VARIANT reads `f.value`
    # constantly. No base table has a column called `value`, so grounding it against the
    # catalogue blocks a correct statement on a name the engine itself supplies.
    # Detected on the AST, never on rendered text, and in no particular dialect: read as
    # Snowflake, FLATTEN is an `Explode` node; read as anything else it is an anonymous
    # function called FLATTEN.
    if tree.find(exp.Explode) is not None or any(
            str(f.this).upper() == "FLATTEN" for f in tree.find_all(exp.Anonymous)):
        out.update({"seq", "key", "path", "index", "value", "this"})
    for vals in tree.find_all(exp.Values):
        if vals.args.get("alias") or not vals.expressions:
            continue
        width = max(len(row.expressions) for row in vals.expressions)
        out.update(f"column{i}" for i in range(1, width + 1))
    return out


def _own_columns(root: exp.Expression) -> list[exp.Column]:
    """The columns written IN this select, not in a select nested inside it.

    `Scope.columns` is not that: it hands a scope the columns of its nested subqueries too, so
    a scalar subquery `(SELECT dt FROM lb)` puts `dt` into the enclosing scope as well — where
    the source IS a base table, and `dt` is then grounded against a catalogue that was never
    going to hold it. Eight candidates blocked on exactly that. A column belongs to the nearest
    enclosing select, and nothing else decides it.
    """
    out: list[exp.Column] = []
    for col in root.find_all(exp.Column):
        node = col.parent
        while node is not None and node is not root:
            if isinstance(node, exp.Select):
                break
            node = node.parent
        else:
            out.append(col)
            continue
        if node is root:
            out.append(col)
    return out


def _scope_aliases(expression: exp.Expression) -> set[str]:
    """The names ONE select defines for itself — its own explicit projection aliases, and
    nothing from a nested select. `COUNT(*) AS requests ... ORDER BY requests` is the case."""
    if not isinstance(expression, exp.Select):
        return set()
    return {p.alias.lower() for p in expression.expressions
            if isinstance(p, exp.Alias) and p.alias}


def columns_to_ground(tree: exp.Expression) -> set[str]:
    """The columns a grounding gate must find in the catalogue.

    `columns_used() - projected_names()` is the OBVIOUS pairing and it is WRONG, because
    `SUM(f.units_teleported) AS units_teleported` is one of the most common shapes in SQL
    there is. The alias equals the column, so subtracting the statement's own names hides the
    only reference to a column that does not exist — and the gate reports a clean pass on a
    statement it was built to refuse. Caught by the fixture that invents a column; the shape is
    frequent enough that the gate was effectively off wherever it appeared.

    What actually decides it is WHERE THE NAME IS READ FROM, and the qualifier says so:

      * qualified by a handle bound IN THIS SCOPE to a BASE TABLE (`f.units_sold`) — a read
        from that table. Always grounded, and no projection alias can excuse it: a projection
        is not addressable through a table qualifier in any dialect.
      * qualified by a handle bound to a CTE, a derived table or a table function
        (`c.arr_or_amount`, Snowflake's `f.value`) — supplied by that relation, which the
        catalogue does not describe and never will.
      * UNQUALIFIED — grounded only where every source in the scope is a base table, and the
        scope does not define the name itself.

    RESOLVED PER SCOPE, WHICH IS THE WHOLE DIFFICULTY. A tree-wide handle map is wrong on real
    SQL: seen here, one statement writes `FROM fct_opportunity o` in one CTE and `FROM opps o`
    in the next, so `o` names a base table in one scope and a CTE in the other. Reading the map
    tree-wide makes every `o.arr_or_amount` look like a base-table read and blocks a statement
    that is exactly right — which is how the first version of this failed, on 66 candidates.
    `traverse_scope` does the binding properly, including alias shadowing; it resolved all
    4,886 candidates of this corpus without raising.

    Where scope resolution is unavailable the function DEGRADES to the old subtraction and says
    so here rather than silently: that reading under-reports invented columns, which is the
    direction that does not block a correct statement on a limit of ours.
    """
    try:
        scopes = list(_traverse_scope(tree))
    except Exception:
        scopes = []
    if not scopes:
        return columns_used(tree) - projected_names(tree)

    out: set[str] = set()
    for scope in scopes:
        # `selected_sources`, NOT `sources`: the latter carries every CTE that is lexically
        # visible, so a CTE body reading one table reports several sources, `all_base` is
        # false, and every unqualified read in it escapes grounding. Measured on this corpus
        # that is most of them — the gate would have looked strict and checked almost nothing.
        sources = {h: src for h, (_, src) in (scope.selected_sources or {}).items()}
        base = {h.lower() for h, src in sources.items() if isinstance(src, exp.Table)}
        # An unqualified name is only attributable when there is nothing else it could have
        # come from. One derived source, one table function, one CTE — and it is ambiguous.
        all_base = bool(sources) and len(base) == len(sources)
        defined = _scope_aliases(scope.expression)
        for col in _own_columns(scope.expression):
            if not col.name or isinstance(col.this, exp.Star):
                continue
            name = col.name.lower()
            qualifier = (col.table or "").lower()
            if qualifier:
                if qualifier in base:
                    out.add(name)
                continue
            if all_base and name not in defined:
                out.add(name)
    return out


def is_answer_key_shape(tree: exp.Expression) -> bool:
    """True for a statement that answers ONE PRE-REGISTERED ITEM rather than a question.

    The shape, seen 43 times in this estate's query log:

        SELECT 'qbr-04' AS id, (WITH ... SELECT COUNT(*) ... )::VARCHAR AS value

    An outer SELECT WITH NO FROM whose projection is a constant label beside a scalar
    subquery. That is an evaluation fixture: the label names the item, the subquery computes
    its expected answer, and the whole recipe is sealed inside a scalar so nothing downstream
    can read it at the layer's grain anyway.

    IT MUST NOT ENTER A GOVERNED LAYER. Publishing it would hand a consumer the answer to a
    question it was supposed to work out, and on an evaluated estate that is the difference
    between a layer that helps and a layer that cheats. It is dropped on SHAPE — never by
    matching a list of item names, which would only work on the estate the list came from and
    would rot the moment someone renamed one.

    The no-FROM test is what makes this safe to apply. A genuine analytical query selects from
    something at its outermost level; one that selects from nothing and reads its answer out of
    a subquery is not describing a population at all.
    """
    root = tree
    if isinstance(root, exp.Subquery):
        root = root.this
    # A fixture usually answers SEVERAL items in one statement, one UNION ALL branch each.
    # Every branch must be a fixture, or a genuine union with one odd branch would be dropped.
    if isinstance(root, (exp.Union, exp.Except, exp.Intersect)):
        branches = [root.this, root.expression]
        return all(b is not None and is_answer_key_shape(b) for b in branches)
    if not isinstance(root, exp.Select):
        return False
    # sqlglot names the FROM clause `from_` (`from` before v26); either is a FROM.
    if root.args.get("from_") or root.args.get("from") or root.args.get("joins"):
        return False
    projections = root.expressions
    if not projections:
        return False
    has_literal_label = False
    has_scalar_subquery = False
    for proj in projections:
        node = proj.this if isinstance(proj, exp.Alias) else proj
        # `(SELECT ...)::VARCHAR` — the cast is the common form and must not hide the subquery.
        while isinstance(node, (exp.Cast, exp.Paren)):
            node = node.this
        if isinstance(node, exp.Literal) and node.is_string:
            has_literal_label = True
        elif isinstance(node, (exp.Subquery, exp.Select)):
            has_scalar_subquery = True
        else:
            return False
    return has_literal_label and has_scalar_subquery


def has_dml(sql: str, dialect: str) -> bool:
    """The read-only gate. Anything that is not a pure SELECT/CTE read is refused before
    transport — the warehouse role is read-only and this is the second lock.

    An ALLOWLIST, not a list of writes: every statement must be a query (SELECT, a set
    operation, VALUES), and nothing inside it may write. A list of forbidden statements
    misses whatever it forgot to name — `CALL`, `COPY … TO`, `ATTACH`, `INSTALL`, `SET`,
    `SELECT … INTO` — and each of those changes something.
    """
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception:
        return True  # unparseable never reaches an engine
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
                 exp.TruncateTable, exp.Merge, exp.Grant, exp.Command, exp.Copy, exp.Set,
                 exp.Use, exp.Pragma, exp.Attach, exp.Install, exp.Comment)
    trees = [t for t in trees if t is not None]
    if not trees:
        return True
    for tree in trees:
        if not isinstance(tree, (exp.Query, exp.Values)):
            return True
        if any(tree.find_all(*forbidden)):
            return True
        if any(s.args.get("into") for s in tree.find_all(exp.Select)):
            return True   # SELECT … INTO creates a table
    return False


def fragment_escape(text: str, dialect: str) -> str | None:
    """Why a SQL FRAGMENT could reach past its own field, or None when it cannot.

    A metric card's `formula`, `source.sql`, filters, join and dimension SQL are each a
    fragment, and every consumer glues them into one statement. `has_dml` on the glued
    statement is not enough: a block comment opened in one field and closed in the next
    hides a `; DELETE …` from the composed check while the field on its own still carries
    it. So each fragment must stand alone: it tokenizes by itself (no string or comment left
    open for the next field to close), and it holds no comment and no statement separator.
    """
    from sqlglot.tokens import TokenType
    if not str(text or "").strip():
        return None
    try:
        tokens = sqlglot.tokenize(str(text), read=dialect)
    except Exception:
        return "does not tokenize on its own (a string, identifier or comment is left open)"
    if not tokens:
        return "is only a comment"
    if any(t.comments for t in tokens):
        return "carries a comment"
    if any(t.token_type == TokenType.SEMICOLON for t in tokens):
        return "carries a statement separator `;`"
    return None


# ------------------------------------------------------------------ requalification
def alias_map(tree: exp.Expression) -> dict[str, str]:
    """{table alias (or bare name) -> fully-qualified table}.

    D1/D3 need it because an aggregate expression lifted out of a query carries the PARENT's
    alias (`f.units_sold`). A canonical statement that selects `FROM <entity> m` while the
    expression still says `f.` does not run — and it fails at execution, not at authoring,
    which is the expensive place to find it.
    """
    out: dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        q = _qualified(t)
        if not q:
            continue
        out[(t.alias or t.name).lower()] = q
        out[t.name.lower()] = q

    # A QUALIFIER MAY NAME A CTE, AND A CTE IS NOT A TABLE. These statements build a CTE per
    # subject and aggregate over it — `SUM(CASE WHEN clc.S1_STATUS = 'Booked' ...)` where
    # `clc` is `latest_clc`, which reads FCT_ONBOARDING_ACTIVITY. Without the CTE here,
    # `source_table_of` resolves nothing, falls through to `sorted(fallback)[0]` and files the
    # measure under the ALPHABETICALLY FIRST table the query touches — the very failure its
    # docstring says it fixed, still live for the CTE half. Measured: 8 of the 9 measures
    # mined from the eight statements reading that tracker were filed under DIM_ACCOUNTS, so
    # the tracker published one generic `COUNT(DISTINCT account_id)` and nothing a question
    # about booked or completed S1s could ever retrieve.
    #
    # A CTE resolves to the base table it reads, transitively, and only when that is
    # UNAMBIGUOUS: a CTE joining three tables names no single source, and guessing one is how
    # a measure ends up filed under a dimension it merely joined to.
    cte_tables: dict[str, list[str]] = {}
    for w in tree.find_all(exp.With):
        for cte in w.expressions:
            nm = (cte.alias_or_name or "").lower()
            if nm:
                cte_tables[nm] = [_qualified(t) for t in cte.this.find_all(exp.Table)]
    for _ in range(len(cte_tables)):           # transitive: a CTE may select from a CTE
        for nm, refs in cte_tables.items():
            flat: list[str] = []
            for r in refs:
                flat.extend(cte_tables.get(str(r).lower(), [r]))
            cte_tables[nm] = flat
    resolved_cte: dict[str, str] = {}
    for nm, refs in cte_tables.items():
        real = [r for r in dict.fromkeys(refs) if r and str(r).lower() not in cte_tables]
        if len(real) == 1:
            resolved_cte[nm] = real[0]

    # OVERWRITE, never setdefault. `FROM latest_clc clc` parses as a Table, so the walk above
    # has already mapped both `clc` and `latest_clc` to the CTE's own NAME — a name that is
    # not a table and resolves to nothing downstream. The CTE's base table is the better
    # answer and has to replace it.
    for alias, target in list(out.items()):
        t = str(target).lower()
        if t in resolved_cte:
            out[alias] = resolved_cte[t]
    for nm, real in resolved_cte.items():
        out[nm] = real
    # A DERIVED TABLE IS READ LIKE A CTE. `FROM (SELECT … FROM fact q1 WHERE …) t` (a BI
    # tool's query step, compiled as a statement) leaves `t` naming no table; it resolves to
    # the one base table the subquery reads, and only when there is exactly one.
    for sub in tree.find_all(exp.Subquery):
        nm = (sub.alias or "").lower()
        if not nm or nm in out:
            continue
        refs = [_qualified(t) for t in sub.this.find_all(exp.Table)]
        flat = [resolved_cte.get(str(r).lower(), r) for r in refs]
        real = [r for r in dict.fromkeys(flat) if r and str(r).lower() not in cte_tables]
        if len(real) == 1:
            out[nm] = real[0]
    return out


def tables_in_source_order(sql: str, dialect: str) -> list[str]:
    """The base tables a statement reads, in the order its text reads them, CTE names left
    out. The first is the base of the first FROM: for a statement built on a derived table
    (a BI tool's custom SQL, say) that is the table the rows come from."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return []
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out: list[str] = []
    # Depth first, which is the text's order: the default breadth-first walk reads a JOIN's
    # table before the table inside a derived `FROM (…) t`, and filed the measure under it.
    for t in tree.find_all(exp.Table, bfs=False):
        if not t.name or (t.name.lower() in ctes and not t.db):
            continue
        name = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        if name.lower() not in {o.lower() for o in out}:
            out.append(name)
    return out


def source_table_of(expr_sql: str, amap: dict[str, str], dialect: str,
                    fallback: list[str], statement_sql: str | None = None) -> str:
    """Which table an expression actually reads.

    Resolved from the expression's own column qualifiers rather than by sorting the candidate
    tables: alphabetical order picked `dim_department` for a metric computed entirely over
    `fct_season_performance`, which is wrong in a way that only shows up at execution.
    """
    try:
        node = sqlglot.parse_one(expr_sql, read=dialect)
    except Exception:
        return sorted(fallback)[0] if fallback else "unknown"
    # The alias map resolves a CTE alias to the CTE's NAME, which is not a table: `ap` ->
    # `attendee_persona`. Returned as the source, D3 builds `FROM attendee_persona m` and the
    # statement cannot compile — measured here, 117 of 1169 candidates and the single largest
    # cause of uncompilable metrics. So the answer must be one of the tables this object
    # actually reads; anything else is a name that exists only inside the parent statement.
    known = {str(t).lower(): t for t in (fallback or [])}
    seen: list[str] = []
    for col in node.find_all(exp.Column):
        tbl = (col.table or "").lower()
        if not tbl or tbl not in amap:
            continue
        resolved = amap[tbl]
        real = known.get(str(resolved).lower())
        if real and real not in seen:
            seen.append(real)
    if seen:
        return seen[0]
    known_lower = {str(t).lower(): t for t in (fallback or [])}

    def _match(name: str):
        return known_lower.get(name.lower()) or next(
            (t for k, t in known_lower.items() if k.endswith("." + name.lower())), None)
    # A SCALAR SUBQUERY NAMES ITS OWN TABLE. `(SELECT COUNT(*) FROM X) AS x_rows` qualifies
    # no column, so the fallback below filed it under the statement's FIRST FROM — another
    # subquery's table: on a live health-check statement all seven aggregates were filed
    # under the first one's INFORMATION_SCHEMA.TABLES.
    if node.find(exp.Select):
        for name in tables_in_source_order(expr_sql, dialect):
            hit = _match(name)
            if hit:
                return hit
    # UNRESOLVED IS NOT ALPHABETICAL. When the expression's columns sit on a derived table
    # (a BI tool's custom SQL wrapped as `FROM (…) t`), no qualifier names a base table, and
    # the first table by name was a small mapping table the custom SQL joins in. The base of
    # the statement's first FROM is where its rows come from.
    if statement_sql and fallback:
        for name in tables_in_source_order(statement_sql, dialect):
            hit = _match(name)
            if hit:
                return hit
    return sorted(fallback)[0] if fallback else "unknown"


def requalify(expr_sql: str, new_alias: str, dialect: str) -> str:
    """Rewrite every column qualifier in an expression onto one canonical alias."""
    try:
        node = sqlglot.parse_one(expr_sql, read=dialect)
    except Exception:
        return expr_sql
    for col in node.find_all(exp.Column):
        if col.table:
            col.set("table", exp.to_identifier(new_alias))
    return node.sql(dialect=dialect)


# --------------------------------------------------------------------------- dimensions
# `extract_feat` strips qualifiers on purpose: its `group_by` and `select` feed the SHAPE
# KEY, where two queries aliasing the same table `d` and `dim` MUST collapse to one shape.
# Reusing those value-free strings as the source for registry dimensions is what made every
# dimension unpublishable — `d.department` had already become `department`, so the expression
# the registry needs was gone before D3 ever looked. These functions read the tree again and
# keep the qualifier.

# A dimension is not only a GROUP BY. Each of these roles independently evidences one.
DIM_ROLES = ("grouped", "projected", "window_partition", "filtered", "bi_declared")

# Temporal detection is by CLASS NAME, not an isinstance list, because the same source text
# parses to a different node per dialect: `DATE_TRUNC('week', x)` is `exp.DateTrunc` in
# Snowflake and `exp.TimestampTrunc` in DuckDB. An isinstance tuple silently answered "not
# temporal" on one of the two dialects we actually run, which would have published a date
# dimension as `categorical`.
_TEMPORAL_NAME = re.compile(r"(date|time|timestamp|epoch|year|month|week|day|quarter|hour)",
                            re.I)
_TEMPORAL_EXTRA = {"Extract", "CurrentDate", "CurrentTimestamp", "StrToDate", "TsOrDsToDate"}
_TEMPORAL_DATATYPES = {"date", "time", "timestamp", "timestamp_ntz", "timestamp_tz",
                       "timestamp_ltz", "datetime", "datetime2", "timestamptz",
                       "timestamp_with_time_zone", "timestamp without time zone"}


def _dim_entry(node: exp.Expression, dialect: str, role: str,
               alias: str | None = None, rename: dict[str, str] | None = None) -> dict[str, Any]:
    """One dimension observation, with the qualifier INTACT.

    `sql` is the expression exactly as the author wrote it, because that is the only text
    guaranteed to compute the value the verified query actually showed. `name` prefers the
    author's SELECT alias — that is the business word a person searches for — and falls back
    to the bare column.
    """
    # TWO spellings, deliberately. `sql` is what the registry gets, rebound onto the metric's
    # own alias so it composes with the canonical statement. `raw_sql` is what the parent
    # actually wrote, and it is what D1b's recorded GROUP BY is matched against — matching on
    # the rebound text would miss every entity-side dimension, because D1b runs before the
    # entity (and therefore the rename) is known.
    raw_sql = node.sql(dialect=dialect)
    sql = _renamed(node, rename or {}).sql(dialect=dialect)
    if isinstance(node, exp.Column):
        bare, qualifier = node.name.lower(), (node.table or "").lower()
    else:
        bare, qualifier = "", ""
        cols = list(node.find_all(exp.Column))
        if cols:
            bare, qualifier = cols[0].name.lower(), (cols[0].table or "").lower()
    name = (alias or bare or re.sub(r"\W+", "_", sql.lower()).strip("_"))
    return {"name": name.lower(), "sql": sql, "raw_sql": raw_sql,
            "bare_column": bare, "qualifier": qualifier,
            "roles": [role], "is_expression": not isinstance(node, exp.Column),
            # The author wrote this word. It outranks a name we fell back to, because
            # `wk` is what the dashboard says and `sold_at` is what the table says.
            "_named": bool(alias)}


def is_temporal(node: exp.Expression | None, datatype: str | None = None) -> bool:
    """Temporal by CONSTRUCTION or by declared type.

    `DATE_TRUNC('week', x)` is a date whatever the column turns out to be, which is why this
    still answers on a run with no warehouse and no catalogue reachable — the SQL forces it.
    """
    if datatype and normalise_datatype(datatype) in _TEMPORAL_DATATYPES:
        return True
    if node is None:
        return False
    for n in ([node] + list(node.walk())):
        cls = type(n).__name__
        if cls in _TEMPORAL_EXTRA:
            return True
        if isinstance(n, exp.Func) and _TEMPORAL_NAME.search(cls):
            return True
        if isinstance(n, exp.Cast):
            to = n.args.get("to")
            if to is not None and normalise_datatype(to.sql()) in _TEMPORAL_DATATYPES:
                return True
    return False


def normalise_datatype(datatype: str | None) -> str:
    """`VARCHAR(50)` -> `varchar`. Length and precision are not part of the kind decision."""
    return str(datatype or "").split("(")[0].strip().lower()


def dimension_observations(tree: exp.Expression, dialect: str,
                           metric_aliases: Iterable[str] = (),
                           rename: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every dimension this statement evidences, in every structural role it plays.

    GROUP BY is the obvious one and the least complete. A column PROJECTED beside an
    aggregate, a window PARTITION BY key, and a filtered column are all equally sliceable —
    the filtered role most of all, since it is the one case where the values are known too.
    The filtered role is added by the caller, which alone can see whether a predicate varies
    across the family (a parameter, so a knob) or is pinned (definitional, so population).
    """
    skip = {str(a).lower() for a in metric_aliases}
    found: list[dict[str, Any]] = []

    group = tree.find(exp.Group)
    if group is not None:
        for e in resolve_group_expressions(group):
            found.append(_dim_entry(e, dialect, "grouped", rename=rename))

    select = tree.find(exp.Select)
    if select is not None:
        for p in select.expressions:
            inner = p.this if isinstance(p, exp.Alias) else p
            if any(isinstance(n, (exp.AggFunc, exp.Window)) for n in inner.walk()):
                continue        # an aggregate is the metric, not a dimension
            alias = p.alias if isinstance(p, exp.Alias) and p.alias else None
            if (alias or "").lower() in skip:
                continue
            found.append(_dim_entry(inner, dialect, "projected", alias, rename=rename))

    for w in tree.find_all(exp.Window):
        for part in (w.args.get("partition_by") or []):
            found.append(_dim_entry(part, dialect, "window_partition", rename=rename))

    return merge_observations(found)


def merge_observations(obs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same expression seen in several roles is ONE dimension carrying several roles.

    Keyed on the qualified SQL, not the bare name: `d.region` and `s.region` are different
    columns on different tables and collapsing them would publish a dimension that reads the
    wrong one.
    """
    by_sql: dict[str, dict[str, Any]] = {}
    for o in obs:
        key = _WS.sub(" ", o["sql"].strip().lower())
        cur = by_sql.get(key)
        if cur is None:
            by_sql[key] = {**o, "roles": list(dict.fromkeys(o["roles"]))}
            continue
        cur["roles"] = list(dict.fromkeys(cur["roles"] + o["roles"]))
        # A name the author actually wrote beats one we fell back to.
        if o.get("_named") and not cur.get("_named"):
            cur["name"], cur["_named"] = o["name"], True
    for d in by_sql.values():
        d.pop("_named", None)
    return sorted(by_sql.values(), key=lambda d: (d["name"], d["sql"]))


def join_clauses(tree: exp.Expression, dialect: str,
                 rename: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every JOIN in the statement, verbatim, with the alias it introduces.

    The registry never parses `join.sql` — it is opaque text — so the verbatim clause is both
    the most accurate thing to publish and the cheapest to produce. The alias is what lets a
    dimension say WHICH join it needs.
    """
    out: list[dict[str, Any]] = []
    for j in tree.find_all(exp.Join):
        right = j.this
        if not isinstance(right, exp.Table):
            continue
        table = _qualified(right)
        alias = (right.alias or right.name or "").lower()
        on = j.args.get("on")
        out.append({
            "table": table,
            "alias": alias,
            "sql": _renamed(j, rename or {}).sql(dialect=dialect),
            "on": sorted({_col_name(c) for c in on.find_all(exp.Column)}) if on else [],
            "type": (j.side or j.kind or "inner").lower(),
        })
    return out


def _renamed(node: exp.Expression, rename: dict[str, str]) -> exp.Expression:
    """A COPY of `node` with the named qualifiers rewritten. Operates on the tree rather than
    on rendered text because a JOIN clause is not a parseable statement on its own — reparsing
    it silently returns the input unchanged, which looks like a no-op rename and ships a
    clause bound to an alias the canonical statement never declares."""
    if not rename:
        return node
    lower = {k.lower(): v for k, v in rename.items()}
    out = node.copy()
    for col in out.find_all(exp.Column):
        tbl = (col.table or "").lower()
        if tbl in lower:
            col.set("table", exp.to_identifier(lower[tbl]))
    return out


def realias(expr_sql: str, rename: dict[str, str], dialect: str) -> str:
    """Rewrite SPECIFIC column qualifiers, leaving every other alias alone.

    `requalify` puts every qualifier onto one alias, which is right for a lifted aggregate and
    wrong for a dimension or a join: `JOIN retail.dim_dept AS d ON d.dept_id = f.dept_id` must
    keep `d` (it is the joined table) while `f` becomes the metric's own alias, because the
    canonical statement says `FROM retail.fct_sales m`. Collapsing both onto `m` produces a
    join that reads the fact table on the wrong side; leaving both alone produces a clause
    that references an alias the canonical statement never binds. Neither runs.
    """
    if not rename:
        return expr_sql
    try:
        node = sqlglot.parse_one(expr_sql, read=dialect)
    except Exception:
        return expr_sql
    lower = {k.lower(): v for k, v in rename.items()}
    for col in node.find_all(exp.Column):
        tbl = (col.table or "").lower()
        if tbl in lower:
            col.set("table", exp.to_identifier(lower[tbl]))
    return node.sql(dialect=dialect)
