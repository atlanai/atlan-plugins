"""Cut a BI element's SQL down to what its question actually reads.

A Sigma element's compiled SQL carries its whole upstream dataset, not the
question. On one Sigma workbook a KPI tile that is a plain `COUNT(ID)` over
accounts of one type arrived as 20K characters: three CTEs, a usage-history
branch with window functions, a hard-coded exclusion list, and two Sigma
input tables. None of that fed the count, yet every
detector in `select` scored it, so the tile tied the hardest metric in the
workbook at discriminating 1.0, and the sensitivity scan withheld it for a
customer domain sitting in a branch the count never read.

So candidates are pruned before they are scored or scanned, in two steps that
make different kinds of claim:

1. Question scope (a choice). Sigma wraps a KPI, chart or pivot in its
   "underlying data" form: a `SELECT * ... LIMIT 1000` preview of the dataset,
   CROSS JOINed or LEFT JOINed to the aggregate the element displays, with
   every dataset column projected at the top. The element's question is the
   aggregate, so the preview's columns are dropped from the outer SELECT,
   keeping the measures and the dimensions they are joined on. When nothing
   reads the preview any more and every aggregate is one row, the preview goes
   too: the answer becomes the measure once instead of once per previewed row.
   A Table element is left alone, because its rows ARE what the person sees.

2. Row-preserving elimination (provable). With the outer projection fixed,
   unused projections are pushed down (sqlglot `pushdown_projections`),
   unused CTEs removed (`eliminate_ctes`), and an unused join removed only
   when removing it cannot change any row: a LEFT JOIN to a source that has at
   most one row per join key, or a CROSS JOIN to a source that always has
   exactly one row. sqlglot's own `eliminate_joins` cannot prove the Sigma
   case — it looks one level down for a GROUP BY and does not read
   EQUAL_NULL as an equality — so uniqueness is proved here, through
   pass-through selects and non-multiplying joins.

Anything this cannot attribute — an unqualified column beside a derived
source, a star it cannot expand, a reference left dangling — returns the
original SQL with a note. A candidate is never scored on SQL that might be
wrong, and the pruned text is only used when something was removed, so a
query with nothing to prune keeps its exact original text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.eliminate_ctes import eliminate_ctes
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

import contextvars
from contextlib import contextmanager

# The dialect of the SQL being pruned: the warehouse's, set per tile by its
# adapter from the connector (Sigma: the Atlan qualified name). Never assumed.
_DIALECT = contextvars.ContextVar("prune_dialect", default="snowflake")


def dialect() -> str:
    return _DIALECT.get()


@contextmanager
def dialect_scope(name: str):
    token = _DIALECT.set(name)
    try:
        yield
    finally:
        _DIALECT.reset(token)

# Sigma's element type for a plain table. Its rows are the question, so the
# measure scoping in step 1 must not apply; step 2 still does.
_ROW_ELEMENT_TYPES = frozenset({"table"})


@dataclass
class Pruned:
    sql: str
    changed: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Row-count facts about a scope
# ---------------------------------------------------------------------------

def _inside(node: exp.Expression, kind: type, stop: exp.Expression) -> bool:
    """Has `node` an ancestor of `kind`, up to and including `stop`?"""
    p = node.parent
    while p is not None:
        if isinstance(p, kind):
            return True
        if p is stop:
            return False
        p = p.parent
    return False


def _aggregate_only(proj: exp.Expression) -> bool:
    """Every column sits inside an aggregate, and nothing is windowed."""
    e = proj.unalias()
    if e.find(exp.AggFunc) is None or e.find(exp.Window) is not None:
        return False
    return all(_inside(c, exp.AggFunc, e) for c in e.find_all(exp.Column))


def _source(scope: Scope, alias: str):
    return scope.sources.get(alias)


def _is_cross(j: exp.Join) -> bool:
    """A join without a condition — not NATURAL or ASOF, which have one implied."""
    return (j.args.get("on") is None and not j.args.get("using") and not j.side
            and (j.kind or "").upper() in ("", "CROSS") and not j.args.get("method"))


def _is_left(j: exp.Join) -> bool:
    return ((j.side or "").upper() == "LEFT" and (j.kind or "").upper() in ("", "OUTER")
            and not j.args.get("method") and j.args.get("on") is not None)


def _from_alias(select: exp.Select) -> str | None:
    frm = select.args.get("from_")
    return frm.this.alias_or_name if frm is not None else None


def _one_row(scope: Scope) -> bool:
    """Exactly one row whatever the input — not "at most one".

    An aggregate without GROUP BY returns one row even over nothing; HAVING,
    QUALIFY or LIMIT can take it to zero, and a CROSS JOIN with zero rows
    empties the whole query, so those disqualify it.
    """
    s = scope.expression
    if not isinstance(s, exp.Select):
        return False
    if any(s.args.get(k) for k in ("group", "having", "qualify", "limit", "offset")):
        return False
    if s.selects and all(_aggregate_only(p) for p in s.selects):
        return True
    # A pass-through over one-row sources joined without a condition.
    if s.args.get("where") is not None:
        return False
    base = _from_alias(s)
    if base is None or not isinstance(_source(scope, base), Scope):
        return False
    if not _one_row(_source(scope, base)):
        return False
    for join in s.args.get("joins") or []:
        if not _is_cross(join):
            return False
        src = _source(scope, join.alias_or_name)
        if not (isinstance(src, Scope) and _one_row(src)):
            return False
    return True


def _conjuncts(e: exp.Expression):
    if isinstance(e, exp.And):
        yield from _conjuncts(e.this)
        yield from _conjuncts(e.expression)
    elif isinstance(e, exp.Paren):
        yield from _conjuncts(e.this)
    else:
        yield e


def _join_keys(join: exp.Join) -> set[str]:
    """Columns of the joined source that the ON clause pins by equality.

    EQUAL_NULL and IS NOT DISTINCT FROM count: a GROUP BY puts NULLs in one
    group, so a null-safe match still finds at most one row per key. Other
    conjuncts only narrow the match and are ignored.
    """
    alias = join.alias_or_name
    on = join.args.get("on")
    keys: set[str] = set()
    if on is None:
        return keys
    for cond in _conjuncts(on):
        if not isinstance(cond, (exp.EQ, exp.EqualNull, exp.NullSafeEQ)):
            continue
        for mine, other in ((cond.this, cond.expression), (cond.expression, cond.this)):
            if (isinstance(mine, exp.Column) and mine.table == alias
                    and alias not in exp.column_table_names(other)):
                keys.add(mine.name)
    return keys


def _unique_keys(scope: Scope, memo: dict) -> list[frozenset[str]]:
    """Output-column sets on which `scope` has at most one row.

    `frozenset()` means at most one row in total. Proved, not guessed: a
    GROUP BY whose every expression is projected, DISTINCT, a one-row
    aggregate, LIMIT 1, or a pass-through select that projects its source's
    key bare and whose joins cannot multiply rows.
    """
    k = id(scope.expression)
    if k in memo:
        return memo[k]
    memo[k] = []                       # cycle guard
    s = scope.expression
    out: list[frozenset[str]] = []
    if isinstance(s, exp.Select):
        limit = s.args.get("limit")
        if _one_row(scope) or (
                not s.args.get("group") and s.selects
                and all(_aggregate_only(p) for p in s.selects)):
            out = [frozenset()]
        elif limit is not None and limit.expression is not None \
                and limit.expression.this == "1":
            out = [frozenset()]
        elif s.args.get("distinct") is not None and not s.args["distinct"].args.get("on"):
            out = [frozenset(s.named_selects)]
        elif s.args.get("group") is not None:
            group = s.args["group"]
            if not any(group.args.get(a) for a in ("rollup", "cube", "grouping_sets")):
                names = set()
                for g in group.expressions:
                    hit = next((p.alias_or_name for p in s.selects if p.unalias() == g), None)
                    if hit is None:
                        break
                    names.add(hit)
                else:
                    out = [frozenset(names)]
        else:
            out = _passthrough_keys(scope, memo)
    memo[k] = out
    return out


def _non_multiplying(scope: Scope, join: exp.Join, memo: dict) -> bool:
    """Can this join give a left row more than one partner?"""
    if join.is_semi_or_anti_join:
        return True
    if (join.side or "").upper() in ("RIGHT", "FULL"):
        return False
    src = _source(scope, join.alias_or_name)
    if not isinstance(src, Scope):
        return False
    keys = _unique_keys(src, memo)
    joined = _join_keys(join)
    return any(key <= joined for key in keys)


def _passthrough_keys(scope: Scope, memo: dict) -> list[frozenset[str]]:
    s = scope.expression
    base_alias = _from_alias(s)
    base = _source(scope, base_alias) if base_alias else None
    if not isinstance(base, Scope):
        return []
    if not all(_non_multiplying(scope, j, memo) for j in s.args.get("joins") or []):
        return []
    out = []
    for key in _unique_keys(base, memo):
        names = set()
        for col in key:
            hit = next((p.alias_or_name for p in s.selects
                        if isinstance(p.unalias(), exp.Column)
                        and p.unalias().table == base_alias and p.unalias().name == col),
                       None)
            if hit is None:
                break
            names.add(hit)
        else:
            out.append(frozenset(names))
    return out


# ---------------------------------------------------------------------------
# Step 2: row-preserving elimination
# ---------------------------------------------------------------------------

def _join_is_read(scope: Scope, join: exp.Join) -> bool:
    """Named anywhere outside the join itself — including from a correlated
    subquery, which sqlglot files under the child scope. Over-counting only
    keeps a join that could have gone."""
    alias = join.alias_or_name
    own = {id(c) for c in join.find_all(exp.Column)}
    return any(c.table == alias and id(c) not in own
               for c in scope.expression.find_all(exp.Column))


def _drop_dead_joins(tree: exp.Expression) -> int:
    removed = 0
    memo: dict = {}
    for scope in traverse_scope(tree):
        joins = scope.expression.args.get("joins") or []
        if not joins or scope.unqualified_columns:
            continue
        for join in reversed(list(joins)):
            if join.is_semi_or_anti_join or join.args.get("using"):
                continue
            src = _source(scope, join.alias_or_name)
            if not isinstance(src, Scope) or _join_is_read(scope, join):
                continue
            if _is_left(join):
                safe = any(key <= _join_keys(join) for key in _unique_keys(src, memo))
            elif _is_cross(join):
                safe = _one_row(src)
            else:
                safe = False
            if safe:
                join.pop()
                scope.remove_source(join.alias_or_name)
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# Step 1: question scope for Sigma's underlying-data form
# ---------------------------------------------------------------------------

@dataclass
class _Trailer:
    t: exp.Select          # the select joining the preview to the aggregates
    preview: str           # alias of the LIMITed preview it reads FROM
    dropped: int           # outer columns dropped


def _is_preview(scope: Scope, memo: dict) -> bool:
    """A LIMITed row sample, possibly under pass-through selects that add a
    column or a lookup — Sigma wraps its preview that way when an element
    carries a calculated or looked-up field."""
    s = scope.expression
    # A WHERE keeps it a sample of rows; grouping or DISTINCT would not.
    if not isinstance(s, exp.Select) or any(
            s.args.get(k) for k in ("group", "distinct", "having", "qualify")):
        return False
    if s.args.get("limit") is not None:
        # Its own lookups do not matter: the preview is either dropped whole
        # or kept as is for the dimension a chart joins on.
        return True
    base = _source(scope, _from_alias(s) or "")
    return (isinstance(base, Scope) and _is_preview(base, memo)
            and all(_non_multiplying(scope, j, memo) for j in s.args.get("joins") or []))


def _scope_of(tree: exp.Expression, node: exp.Expression) -> Scope | None:
    return next((s for s in traverse_scope(tree) if s.expression is node), None)


def _trailer(tree: exp.Select) -> tuple[exp.Select, Scope] | None:
    """The select that joins a preview to aggregates, if this is that shape."""
    root = _scope_of(tree, tree)
    candidates = [(tree, root)]
    src = _source(root, _from_alias(tree) or "") if root else None
    if (isinstance(src, Scope) and isinstance(src.expression, exp.Select)
            and not tree.args.get("joins")
            and not any(tree.args.get(k) for k in ("where", "group", "having", "distinct"))):
        candidates.insert(0, (src.expression, src))
    for t, scope in candidates:
        if scope is None or not t.args.get("joins"):
            continue
        if any(t.args.get(k) for k in ("where", "group", "having", "distinct", "qualify")):
            continue
        memo: dict = {}
        preview = _source(scope, _from_alias(t) or "")
        if not (isinstance(preview, Scope) and _is_preview(preview, memo)):
            continue
        aggregate_joins = True
        for j in t.args["joins"]:
            js = _source(scope, j.alias_or_name)
            if not isinstance(js, Scope):
                aggregate_joins = False
            elif _is_cross(j):
                aggregate_joins &= _one_row(js)
            elif _is_left(j):
                aggregate_joins &= any(k <= _join_keys(j) for k in _unique_keys(js, memo))
            else:
                aggregate_joins = False
        if aggregate_joins:
            return t, scope
    return None


def _scope_to_measure(tree: exp.Select) -> _Trailer | None:
    found = _trailer(tree)
    if found is None:
        return None
    t, tscope = found
    preview = _from_alias(t)
    joins = {j.alias_or_name for j in t.args["joins"]}
    # D-side columns the joins are keyed on: the dimensions a chart groups by.
    dims = {(c.table, c.name) for j in t.args["joins"] if j.args.get("on") is not None
            for c in j.args["on"].find_all(exp.Column) if c.table == preview}

    def reads(proj: exp.Expression) -> list[exp.Column] | None:
        """The trailer columns an outer projection reads, through the wrapper."""
        if tree is t:
            return list(proj.find_all(exp.Column))
        col = proj.unalias()
        if not isinstance(col, exp.Column):
            return None
        hit = next((p for p in t.selects if p.alias_or_name == col.name), None)
        return None if hit is None else list(hit.find_all(exp.Column))

    keep, measures, dropped = [], 0, 0
    kept_refs: set[tuple[str, str]] = set()
    for proj in tree.selects:
        cols = reads(proj)
        if cols is None or any(not c.table for c in cols):
            return None                     # cannot tell what it reads
        if any(c.table in joins for c in cols):
            measures += 1                   # Sigma wraps some in IFF(V_n, ...)
        elif not cols or not all((c.table, c.name) in dims for c in cols):
            dropped += 1
            continue
        keep.append(proj)
        kept_refs |= {(c.table, c.name) for c in proj.find_all(exp.Column)}
    if not measures or not dropped:
        return None

    tree.set("expressions", keep)
    kept_names = {p.alias_or_name for p in keep}
    order = tree.args.get("order")
    if order is not None:
        terms = [o for o in order.expressions
                 if all((c.table, c.name) in kept_refs or (not c.table and c.name in kept_names)
                        for c in o.find_all(exp.Column))]
        if terms:
            order.set("expressions", terms)
        else:
            order.pop()
    return _Trailer(t=t, preview=preview, dropped=dropped)


def _drop_preview(tree: exp.Expression, tr: _Trailer) -> bool:
    """FROM preview CROSS JOIN one-row aggregate -> FROM the aggregate.

    The one step that changes the row count: N identical copies of the measure
    become one. Taken only when nothing reads the preview and every join is a
    condition-free join to a one-row source.
    """
    scope = _scope_of(tree, tr.t)
    if scope is None or scope.source_columns(tr.preview):
        return False
    joins = tr.t.args.get("joins") or []
    if not joins:
        return False
    for j in joins:
        src = _source(scope, j.alias_or_name)
        if not (_is_cross(j) and isinstance(src, Scope) and _one_row(src)):
            return False
    first = joins[0]
    source = first.this
    first.pop()
    tr.t.set("from_", exp.From(this=source))
    return True


# ---------------------------------------------------------------------------
# Guards and the entry point
# ---------------------------------------------------------------------------

def _unattributable(tree: exp.Expression) -> str | None:
    """A reason pruning cannot be trusted on this tree, or None."""
    for scope in traverse_scope(tree):
        # qualify inlines Snowflake's lateral alias references (`a + 1 AS X,
        # X * 2 AS Y`). One it left behind would look like a column of the
        # table, and pruning X's projection would leave it pointing at nothing.
        if isinstance(scope.expression, exp.Select):
            computed = {p.alias for p in scope.expression.selects
                        if isinstance(p, exp.Alias)
                        and not (isinstance(p.this, exp.Column) and p.this.name == p.alias)}
            clash = computed & {c.name for c in scope.columns}
            if clash:
                return f"{sorted(clash)[0]} is read in the SELECT that computes it"
        # selected_sources, not sources: every CTE is in `sources` everywhere.
        derived = any(isinstance(s, Scope) for _, s in scope.selected_sources.values())
        if not derived:
            continue
        if scope.unqualified_columns:
            return f"unqualified column {scope.unqualified_columns[0].sql(dialect=dialect())}"
        if isinstance(scope.expression, exp.Select) and any(
                isinstance(p, exp.Star) or (isinstance(p, exp.Column) and p.is_star)
                for p in scope.expression.selects):
            return "a star over a derived source it could not expand"
    return None


def _dangling(tree: exp.Expression) -> str | None:
    """A column naming something its source no longer projects."""
    for scope in traverse_scope(tree):
        for col in scope.columns:
            if not col.table:
                continue
            src = _source(scope, col.table)
            if (isinstance(src, Scope) and isinstance(src.expression, exp.Select)
                    and not src.expression.is_star):
                if col.name not in src.expression.named_selects:
                    return f"{col.table}.{col.name}"
    return None


def _drop_redundant_aliases(tree: exp.Expression) -> None:
    """`Q1.X AS X` -> `Q1.X`. qualify adds one to every projection; they change
    no output name and double the length of what a reviewer reads."""
    for a in list(tree.find_all(exp.Alias)):
        if (a.arg_key == "expressions" and isinstance(a.parent, exp.Select)
                and isinstance(a.this, exp.Column) and not a.this.is_star
                and a.alias == a.this.name):
            a.replace(a.this)


def _counts(tree: exp.Expression) -> tuple[int, int]:
    return len(list(tree.find_all(exp.Join))), len(list(tree.find_all(exp.CTE)))


def _named(tree: exp.Expression) -> dict[int, tuple[exp.Select, set[str]]]:
    return {id(s): (s, set(s.named_selects) - {"*"}) for s in tree.find_all(exp.Select)}


def _columns_removed(before: dict, tree: exp.Expression) -> int:
    """Named projections gone from SELECTs that survived. A star that pushdown
    spelled out as the columns actually read is not a removal."""
    alive = {id(s) for s in tree.find_all(exp.Select)}
    return sum(len(names - set(s.named_selects))
               for k, (s, names) in before.items() if k in alive)


def prune(sql: str, element_type: str | None = None) -> Pruned:
    """The SQL this candidate should be scored, scanned and drafted from."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect())
    except Exception:  # noqa: BLE001 - unparseable SQL is select's call, not ours
        return Pruned(sql)
    if not isinstance(tree, exp.Select):
        return Pruned(sql)
    try:
        tree = qualify(tree, dialect=dialect(), validate_qualify_columns=False,
                       quote_identifiers=False)
        why = _unattributable(tree)
        if why:
            return Pruned(sql, notes=[f"not pruned: {why}"])
        joins0, ctes0 = _counts(tree)
        named0 = _named(tree)

        trailer = None
        if (element_type or "").strip().lower() not in _ROW_ELEMENT_TYPES:
            trailer = _scope_to_measure(tree)
        dropped_preview = False
        for _ in range(12):
            before = tree.sql(dialect=dialect())
            pushdown_projections(tree, dialect=dialect())
            _drop_dead_joins(tree)
            if trailer and not dropped_preview:
                dropped_preview = _drop_preview(tree, trailer)
            eliminate_ctes(tree)
            if tree.sql(dialect=dialect()) == before:
                break

        joins1, ctes1 = _counts(tree)
        cols = _columns_removed(named0, tree)
        structural = bool(trailer) or joins1 < joins0 or ctes1 < ctes0
        if not structural and not cols:
            return Pruned(sql)               # nothing the question does not read
        bad = _dangling(tree)
        if bad:
            return Pruned(sql, notes=[f"not pruned: {bad} would be left dangling"])
        _drop_redundant_aliases(tree)
        out = tree.sql(dialect=dialect())
        sqlglot.parse_one(out, read=dialect())  # must still parse
    except Exception as e:  # noqa: BLE001 - fall back to the original, loudly
        return Pruned(sql, notes=[f"not pruned: {type(e).__name__}: {str(e)[:80]}"])

    # The rewrite is qualified throughout, so it is longer per column than
    # Sigma's own text. Dropping a few dead columns from a table element does
    # not pay for that, and a reviewer is better served by the original.
    if not structural and len(out) >= len(sql):
        return Pruned(sql)

    bits = []
    if trailer:
        bits.append(f"{trailer.dropped} preview column(s) dropped from the outer SELECT")
    if dropped_preview:
        bits.append("the row preview it was cross-joined to removed (one row, not one per preview row)")
    if joins0 > joins1:
        bits.append(f"{joins0 - joins1} join(s) the answer does not read")
    if ctes0 > ctes1:
        bits.append(f"{ctes0 - ctes1} unused CTE(s)")
    if cols and not bits:
        bits.append(f"{cols} unused column(s)")
    return Pruned(out, changed=True, notes=[
        f"pruned to what the {'measure' if trailer else 'result'} reads: "
        f"{'; '.join(bits)} — {len(sql)} -> {len(out)} chars, original kept as provenance"])


def prune_candidates(candidates: list) -> int:
    """Prune in place; the original moves to `original_sql`. Returns how many changed."""
    changed = 0
    for c in candidates:
        p = prune(c.sql, getattr(c, "element_type", None))
        if p.changed:
            c.original_sql = c.sql
            c.sql = p.sql
            changed += 1
        c.notes.extend(p.notes)
    return changed


# ---------------------------------------------------------------------------
# A BI tile's SQL (Sigma)
# ---------------------------------------------------------------------------

class TileRefused(Exception):
    """The tile SQL could not be derived without guessing; the message says why."""


def collapse_to_tile(tree: exp.Select, block: exp.Select,
                     projections: list[exp.Expression]) -> Pruned:
    """The SQL of one tile: the aggregate block that computes it, re-projected
    to what the tile shows, with the query's CTE chain.

    `prune` keeps Sigma's row preview so its answer is still the element's
    rows; a chart then answers once per previewed row instead of once per
    group. A tile is its aggregate: the block's FROM, WHERE and GROUP BY are
    kept untouched, so its groups and their values are the ones the element
    computes, and everything the block does not read goes in the same proven
    steps as step 2 above. A display LIMIT on the block itself (a grouped
    table's rows) is removed: the tile's answer is every group.

    `tree` is the qualified query and `block` a SELECT inside it.
    Raises TileRefused when the attribution guards fire.
    """
    tile = block.copy()
    tile.set("expressions", projections)
    limit = tile.args.get("limit")
    for k in ("with_", "order", "limit", "offset"):
        tile.set(k, None)
    if tree.args.get("with_") is not None:
        tile.set("with_", tree.args["with_"].copy())
    eliminate_ctes(tile)                # CTEs nothing reads cannot block the guard
    why = _unattributable(tile)
    if why:
        raise TileRefused(f"unattributable: {why}")
    for _ in range(12):
        before = tile.sql(dialect=dialect())
        pushdown_projections(tile, dialect=dialect())
        _drop_dead_joins(tile)
        eliminate_ctes(tile)
        if tile.sql(dialect=dialect()) == before:
            break
    bad = _dangling(tile)
    if bad:
        raise TileRefused(f"unattributable: {bad} would be left dangling")
    _drop_redundant_aliases(tile)
    for ident in tile.find_all(exp.Identifier):
        if ident.name == "_":           # pushdown's placeholder alias; Snowflake rejects it bare
            ident.set("quoted", True)
    out = tile.sql(dialect=dialect())
    sqlglot.parse_one(out, read=dialect())
    (joins0, ctes0), (joins1, ctes1) = _counts(tree), _counts(tile)
    notes = [f"{joins0 - joins1} join(s) and {ctes0 - ctes1} CTE(s) the tile does not read removed"]
    if limit is not None:
        notes.append(f"display {limit.sql(dialect=dialect())} on the grouped rows removed")
    return Pruned(out, changed=True, notes=notes)
