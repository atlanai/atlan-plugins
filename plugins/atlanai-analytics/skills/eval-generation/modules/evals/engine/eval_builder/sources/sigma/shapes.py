"""Which shape of Sigma's compiled SQL an element has, read from its main query.

Sigma compiles every element to one query: `WITH W1 .. Wn` CTEs that build the
element's rows (the warehouse source, parent elements, lookups), then a main
query that is what the tile computes. Across the elements of a workbook the
main query takes a handful of forms:

    F1  a `LIMIT 1000` row preview CROSS JOINed to a one-row aggregate:
        a KPI, a gauge, a funnel, a table summary
    F2  the preview LEFT JOINed to per-group aggregates `ON EQUAL_NULL(keys)`:
        bars, donuts, combos, grouped tables, a period-comparison KPI
    F3  both: a pivot (cells plus totals), a grouped table with a summary
    F4  compact: the grouped rows themselves, no preview
    F5  DISTINCT row headers plus grand totals: a pivot whose cell values are
        not in the SQL at all
    F6  no aggregate of the element's rows in the main query: a listing, or a
        chart whose numbers were computed in a CTE (Top-N, a grouped parent)

Only F1-F4 carry what the tile displays. F5, F6 and anything that matches no
family are refused with a reason: a wrong guess about an unfamiliar shape puts
a plausible but wrong number into an answer key, while a refusal costs one
candidate. So the classifier proves each structural claim — a CROSS JOIN is to
exactly one row, a LEFT JOIN is to at most one row per key — with the same
proofs `prune` uses to drop joins, and calls anything it cannot prove UNKNOWN.

Two things in the main query look like the tile's aggregates and are not:

- Sigma's Lookup compiles to `EQUAL_NULL(MIN(x), MAX(x))` beside `MAX(x)`,
  usually `GROUP BY key`: the single value of x per key, a row-level
  attribute. A pivot of a ratio computed upstream uses the same pattern for
  its cells, which is why that pivot is F6 (its number lives in a CTE).
- A Rollup or Lookup into another element aggregates *that* element's rows.
  The tile's own aggregate reads the relation the preview samples (both read
  Wn); a block whose aggregates read a different relation is someone else's,
  and a listing that carries one is still a listing.

Both are decided per aggregate projection, because Sigma puts a period
comparison's lookup in the same SELECT as the KPI's own COUNT.

`Lineage` follows a value through the qualified query to the projections that
aggregate it and the warehouse columns it reads. The tile extraction uses the
same walk for measures, dimensions and filters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, ScopeType, traverse_scope

from eval_builder.prune import (
    dialect, _aggregate_only, _conjuncts, _from_alias, _is_cross, _is_left, _join_keys,
    _one_row, _unique_keys,
)

# Atlan stores at most this many characters of `sigmaDataElementQuery`. A cut
# query is exactly this long, or a few characters shorter and ending in "...";
# either way it has lost the trailer Sigma appends to every complete query.
TRUNCATED_AT = 90_000

# Sigma appends `-- Sigma Σ {request-id, user-id, role, warehouse, email}`.
_TRAILER = re.compile(r"--\s*Sigma\s*Σ.*\Z", re.S)

_CLAUSES = ("where", "group", "having", "distinct", "qualify")


def strip_trailer(sql: str | None) -> str:
    """The query without Sigma's request trailer, which names the viewer."""
    return _TRAILER.sub("", sql or "").strip()


def truncated(raw: str) -> bool:
    return len(raw) >= TRUNCATED_AT or (raw.rstrip().endswith("...") and not _TRAILER.search(raw))


# ---------------------------------------------------------------------------
# Aggregates and lookups
# ---------------------------------------------------------------------------

def _between(node: exp.Expression, top: exp.Expression, kinds: tuple) -> bool:
    """Is there a node of `kinds` on the path from `node` up to `top` (inclusive)?"""
    p = node.parent if node is not top else None
    while p is not None:
        if isinstance(p, kinds):
            return True
        if p is top:
            return False
        p = p.parent
    return False


def has_agg(proj: exp.Expression) -> bool:
    """Does this projection aggregate its SELECT's rows (not a window function,
    not a scalar subquery)?"""
    e = proj.unalias()
    return any(not _between(a, e, (exp.Window, exp.Select, exp.Subquery))
               for a in e.find_all(exp.AggFunc))


def _null_eq(e: exp.Expression) -> bool:
    return (isinstance(e, exp.EqualNull) and isinstance(e.this, exp.Min)
            and isinstance(e.expression, exp.Max) and e.this.this == e.expression.this)


def is_lookup(select: exp.Select, proj: exp.Expression) -> bool:
    """Sigma's Lookup pattern: EQUAL_NULL(MIN(x), MAX(x)), MAX(x) beside it, or
    IFF(EQUAL_NULL(MIN(x), MAX(x)), MAX(x), NULL) once qualify inlines it."""
    e = proj.unalias()
    if _null_eq(e):
        return True
    if (isinstance(e, exp.If) and _null_eq(e.this) and isinstance(e.args.get("true"), exp.Max)
            and isinstance(e.args.get("false"), exp.Null)
            and e.args["true"].this == e.this.this.this):
        return True
    if isinstance(e, exp.Max):
        return any(_null_eq(p.unalias()) and p.unalias().this.this == e.this
                   for p in select.selects)
    return False


def table_name(t: exp.Table) -> str:
    """DB.SCHEMA.TABLE as qualify normalised it: unquoted names upper case,
    quoted ones as written (a Sigma CSV upload is a quoted lower-case table)."""
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

@dataclass
class Trace:
    """Where a value comes from."""

    columns: set[str] = field(default_factory=set)          # DB.SCHEMA.TABLE.COLUMN
    leaves: list[tuple[Scope, exp.Expression]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    plain: bool = False                                      # one column, passed through untouched

    def merge(self, other: Trace) -> None:
        self.columns |= other.columns
        self.leaves += [lf for lf in other.leaves if lf not in self.leaves]
        self.unresolved += other.unresolved


class Lineage:
    """Follows values through a qualified query, scope by scope.

    A value's aggregate leaves are the projections that aggregate it — the
    nearest ones: a SUM over a CTE column that itself came from a GROUP BY
    has one leaf, the SUM. Its columns are every warehouse column it reads
    through the CTEs, including the inputs of those aggregates.
    """

    def __init__(self, tree: exp.Expression):
        self.scopes = traverse_scope(tree)
        self.root = self.scopes[-1]
        self._memo: dict = {}

    def expr(self, scope: Scope, e: exp.Expression) -> Trace:
        """An expression evaluated in `scope`."""
        out = Trace()
        cols = [e] if isinstance(e, exp.Column) else list(e.find_all(exp.Column))
        for c in cols:
            if c.is_star:
                continue
            if _between(c, e, (exp.Select, exp.Subquery)):
                out.unresolved.append(f"{c.sql(dialect=dialect())} (inside a subquery)")
                continue
            out.merge(self.column(scope, c.table, c.name))
        out.plain = isinstance(e, exp.Column) and len(cols) == 1 and not out.unresolved \
            and self.column(scope, e.table, e.name).plain
        return out

    def column(self, scope: Scope, table: str, name: str) -> Trace:
        key = (id(scope.expression), table, name)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = Trace(unresolved=[f"{table}.{name} (cycle)"])
        src = scope.sources.get(table) if table else None
        if isinstance(src, exp.Table):
            t = Trace(columns={f"{table_name(src)}.{name}"}, plain=True)
        elif isinstance(src, Scope):
            t = self.output(src, name)
        else:
            t = Trace(unresolved=[f"{table}.{name}" if table else f"unqualified {name}"])
        self._memo[key] = t
        return t

    def output(self, scope: Scope, name: str) -> Trace:
        """The output column `name` of a derived scope (subquery, CTE, union)."""
        node = scope.expression
        if isinstance(node, exp.SetOperation):
            branches = _branches(scope)
            first = branches[0].expression
            names = first.named_selects if isinstance(first, exp.Select) else []
            if name not in names:
                return Trace(unresolved=[f"{name} (union output)"])
            i = names.index(name)
            out = Trace()
            for b in branches:
                if not isinstance(b.expression, exp.Select) or len(b.expression.selects) <= i:
                    out.unresolved.append(f"{name} (union branch)")
                    continue
                out.merge(self._projection(b, b.expression.selects[i]))
            return out
        if not isinstance(node, exp.Select):
            return Trace(unresolved=[f"{name} (from a {type(node).__name__})"])
        proj = next((p for p in node.selects if p.alias_or_name == name), None)
        if proj is None:
            return self._through_star(scope, name)
        return self._projection(scope, proj)

    def _projection(self, scope: Scope, proj: exp.Expression) -> Trace:
        t = self.expr(scope, proj.unalias())
        if has_agg(proj):
            # Computed here: the aggregates below it are its inputs, not its leaves.
            t = Trace(columns=set(t.columns), leaves=[(scope, proj)],
                      unresolved=list(t.unresolved))
        return t

    def _through_star(self, scope: Scope, name: str) -> Trace:
        node = scope.expression
        stars = [p for p in node.selects if isinstance(p, exp.Star)
                 or (isinstance(p, exp.Column) and p.is_star)]
        owners = set()
        for s in stars:
            if isinstance(s, exp.Column) and s.table:
                owners.add(s.table)
            else:
                owners |= set(scope.selected_sources)
        if len(owners) != 1:
            return Trace(unresolved=[f"{name} (not projected)"])
        t = self.column(scope, owners.pop(), name)
        return Trace(set(t.columns), list(t.leaves), list(t.unresolved), t.plain)


def _branches(scope: Scope) -> list[Scope]:
    out = []
    for s in scope.union_scopes:
        out += _branches(s) if isinstance(s.expression, exp.SetOperation) else [s]
    return out


def in_cte(scope: Scope) -> bool:
    s = scope
    while s is not None:
        if s.scope_type is ScopeType.CTE:
            return True
        s = s.parent
    return False


def base(scope: Scope) -> str | None:
    """The relation a SELECT's rows come from: its FROM, followed through
    subqueries (left branch of a union) to a CTE or a warehouse table."""
    s = scope
    for _ in range(100):
        node = s.expression
        if isinstance(node, exp.SetOperation):
            s = _branches(s)[0]
            continue
        frm = node.args.get("from_") if isinstance(node, exp.Select) else None
        if frm is None:
            return None
        src = s.sources.get(frm.this.alias_or_name)
        if isinstance(src, exp.Table):
            return table_name(src)
        if not isinstance(src, Scope):
            return None
        if src.scope_type is ScopeType.CTE:
            return "cte:" + frm.this.name.upper()
        s = src
    return None


def own(leaf_scope: Scope, leaf: exp.Expression, ref_base: str | None) -> bool:
    """Does this aggregate compute the tile: in the main query (an aggregate in
    a CTE belongs to a parent element), not a Lookup, over `ref_base`?"""
    return (not in_cte(leaf_scope) and not is_lookup(leaf_scope.expression, leaf)
            and (ref_base is None or base(leaf_scope) == ref_base))


def _leaves(lineage: Lineage, scope: Scope) -> list[tuple[Scope, exp.Expression]]:
    return [lf for p in scope.expression.selects
            for lf in lineage.output(scope, p.alias_or_name).leaves]


# ---------------------------------------------------------------------------
# The shape
# ---------------------------------------------------------------------------

@dataclass
class Block:
    """A SELECT the tile's numbers come from."""

    scope: Scope
    alias: str | None             # its alias in the trailer; None when it is the trailer's own rows
    grain: str                    # "grand" (exactly one row) | "grouped" (one row per key)
    keys: dict[str, str] = field(default_factory=dict)   # rows column -> block column, from ON


@dataclass
class Shape:
    family: str                   # F1..F6, UNKNOWN, or "" when refused before classifying
    refusal: str | None = None
    tree: exp.Select | None = None            # parsed and qualified
    lineage: Lineage | None = None
    trailer: Scope | None = None              # reads the rows and joins the blocks
    rows: str | None = None                   # alias of the trailer's FROM source
    rows_kind: str | None = None              # rows | distinct | grouped | block
    blocks: list[Block] = field(default_factory=list)


def _refused(family: str, why: str, **kw) -> Shape:
    return Shape(family, why, **kw)


def _plain_wrapper(scope: Scope) -> bool:
    s = scope.expression
    return (isinstance(s, exp.Select) and not s.args.get("joins")
            and not any(s.args.get(k) for k in _CLAUSES)
            and all(isinstance(p.unalias(), exp.Column) for p in s.selects))


def _descend(scope: Scope) -> Scope:
    """Past the pass-through SELECTs Sigma wraps around the main query."""
    while _plain_wrapper(scope):
        src = scope.sources.get(_from_alias(scope.expression) or "")
        if not (isinstance(src, Scope) and src.scope_type is ScopeType.DERIVED_TABLE
                and isinstance(src.expression, exp.Select)):
            break
        scope = src
    return scope


def _on_pairs(join: exp.Join, rows: str) -> dict[str, str]:
    """rows column -> block column for each equality in the ON clause."""
    alias = join.alias_or_name
    pairs = {}
    for cond in _conjuncts(join.args["on"]):
        if not isinstance(cond, (exp.EQ, exp.EqualNull, exp.NullSafeEQ)):
            continue
        a, b = cond.this, cond.expression
        if isinstance(a, exp.Column) and isinstance(b, exp.Column):
            if a.table == rows and b.table == alias:
                pairs[a.name] = b.name
            elif b.table == rows and a.table == alias:
                pairs[b.name] = a.name
    return pairs


def _aggregates(select: exp.Select) -> bool:
    return any(has_agg(p) and not is_lookup(select, p) for p in select.selects)


def _one_row_block(scope: Scope) -> bool:
    """`prune._one_row`, plus an aggregate SELECT that also projects constants
    (a gauge's `10 AS max`): still exactly one row."""
    if _one_row(scope):
        return True
    s = scope.expression
    return (isinstance(s, exp.Select) and s.args.get("from_") is not None
            and not any(s.args.get(k) for k in ("group", "having", "qualify", "limit", "offset"))
            and any(_aggregate_only(p) for p in s.selects)
            and all(_aggregate_only(p) or p.unalias().find(exp.Column) is None
                    for p in s.selects))


def classify(raw_sql: str | None) -> Shape:
    """Parse, qualify and classify one element's compiled SQL."""
    raw = raw_sql or ""
    if truncated(raw):
        return _refused("", "truncated by Atlan at 90,000 chars")
    sql = strip_trailer(raw)
    if not sql:
        return _refused("", "empty SQL")
    try:
        tree = sqlglot.parse_one(sql, read=dialect())
    except Exception as e:  # noqa: BLE001 - any parse failure is a refusal
        return _refused("", f"SQL does not parse ({type(e).__name__})")
    if not isinstance(tree, exp.Select):
        return _refused("UNKNOWN", f"UNKNOWN: the query is a {type(tree).__name__}, not a SELECT")
    # A table read without an alias gets one from qualify, upper-cased: a quoted
    # lower-case name ("sigma_df_csv_...") then no longer matches the columns
    # qualified with it. Its own name, quoted as written, is the same alias.
    for t in tree.find_all(exp.Table):
        if not t.alias and t.this is not None:
            t.set("alias", exp.TableAlias(this=exp.to_identifier(t.name, quoted=t.this.quoted)))
    try:
        tree = qualify(tree, dialect=dialect(), validate_qualify_columns=False,
                       quote_identifiers=False)
        lin = Lineage(tree)
    except Exception as e:  # noqa: BLE001
        return _refused("", f"SQL does not qualify ({str(e).splitlines()[0][:80]})")

    kw = {"tree": tree, "lineage": lin}
    main_scopes = [s for s in lin.scopes if not in_cte(s) and isinstance(s.expression, exp.Select)]
    if not any(_aggregates(s.expression) for s in main_scopes):
        upstream = any(_aggregates(s.expression) for s in lin.scopes
                       if in_cte(s) and isinstance(s.expression, exp.Select))
        where = "aggregate in a CTE" if upstream else "row-level listing"
        return _refused("F6", f"F6: no aggregate in the main query ({where})", **kw)

    t = _descend(lin.root)
    T = t.expression
    joins = T.args.get("joins") or []
    kw["trailer"] = t
    not_own = "F6: no aggregate of the element's own rows in the main query (lookups and rollups only)"
    if _aggregates(T):
        # Compact: the grouped rows themselves, whatever they join to get there.
        grain = "grouped" if T.args.get("group") else "grand"
        return Shape("F4", rows_kind="block", blocks=[Block(t, None, grain)], **kw)
    if not joins:
        return _refused("F6", not_own, **kw)

    rows = _from_alias(T)
    r = t.sources.get(rows or "")
    if any(T.args.get(k) for k in ("group", "having", "distinct", "qualify")) or r is None:
        return _refused("UNKNOWN", "UNKNOWN: the trailer is not a plain join of rows and aggregates", **kw)
    rs = r.expression if isinstance(r, Scope) else None
    if not isinstance(r, Scope) or r.scope_type is ScopeType.CTE or not isinstance(rs, exp.Select):
        rows_kind = "rows"                    # a CTE or table read directly
    elif rs.args.get("distinct") is not None and not rs.args.get("group"):
        rows_kind = "distinct"
    elif rs.args.get("group") is not None and _aggregates(rs):
        rows_kind = "grouped"
    elif not _aggregates(rs):
        rows_kind = "rows"
    else:
        return _refused("UNKNOWN", "UNKNOWN: the trailer's rows are a one-row aggregate", **kw)
    kw.update(rows=rows, rows_kind=rows_kind)

    memo: dict = {}
    ref_base = base(t)
    blocks = []
    upstream = False
    for j in joins:
        alias = j.alias_or_name
        js = t.sources.get(alias)
        # Only a join that brings in the element's own aggregate is part of the
        # tile; lookups, rollups and row-level joins only reshape the preview.
        if not (isinstance(js, Scope) and isinstance(js.expression, exp.Select)):
            continue
        leaves = _leaves(lin, js)
        if not any(own(s, p, ref_base) for s, p in leaves):
            upstream |= any(in_cte(s) and not is_lookup(s.expression, p) for s, p in leaves)
            continue
        if _is_cross(j):
            if not _one_row_block(js):
                return _refused("UNKNOWN", f"UNKNOWN: CROSS JOIN {alias} is not provably one row", **kw)
            blocks.append(Block(js, alias, "grand"))
        elif _is_left(j) and any(k <= _join_keys(j) for k in _unique_keys(js, memo)):
            blocks.append(Block(js, alias, "grouped", _on_pairs(j, rows)))
        else:
            return _refused("UNKNOWN", f"UNKNOWN: the aggregate joined as {alias} is not provably "
                                       "one row per key", **kw)

    grand = [b for b in blocks if b.grain == "grand"]
    grouped = [b for b in blocks if b.grain == "grouped"]
    if rows_kind == "grouped":
        if grouped:
            return _refused("UNKNOWN", "UNKNOWN: grouped rows LEFT JOINed to per-group aggregates", **kw)
        return Shape("F3" if grand else "F4", blocks=[Block(r, None, "grouped")] + grand, **kw)
    if not blocks:
        why = "F6: no aggregate in the main query (aggregate in a CTE)" if upstream else not_own
        return _refused("F6", why, **kw)
    if rows_kind == "distinct" and not grouped:
        return _refused("F5", "F5: pivot headers without cell values", blocks=blocks, **kw)
    family = "F3" if grand and grouped else ("F2" if grouped else "F1")
    return Shape(family, blocks=blocks, **kw)
