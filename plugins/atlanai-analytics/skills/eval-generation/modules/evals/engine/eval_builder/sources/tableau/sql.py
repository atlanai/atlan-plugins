"""A tile's SQL: one SELECT at the grain the tile displays.

    SELECT <dimensions>, <aggregated measures>
    FROM (<custom SQL> | <schema.table> | <join tree> | <one relationship table>) AS src
    WHERE <row filters, in Tableau's order>
    GROUP BY <dimensions>
    HAVING <filters on aggregates>

Without level-of-detail expressions and table calculations Tableau's order of
operations collapses to this shape: extract, datasource, context and dimension
filters all remove rows before the view aggregates, and measure filters apply
after.

A FIXED expression is computed between the context filters and the dimension
filters, so a tile that reads one gets row stages under that SELECT, one per
nesting depth (`_staged`):

    s1: SELECT <columns read later>, AGG(expr) OVER (PARTITION BY dims) AS lod_1
        FROM <relation> AS src WHERE <extract, datasource and context filters>
    s2: SELECT ..., <FIXED expressions that read lod_1> FROM (s1) AS s1
    SELECT <dimensions>, <measures> FROM (sK) AS t
    WHERE <dimension filters> GROUP BY ... HAVING ...

INCLUDE/EXCLUDE expressions and table calculations need yet another shape, so
tiles with them are refused upstream instead of being forced into this one.

Custom SQL is the datasource's own query, often 35 columns wide for a tile
that reads ten. It is pruned to what the tile reads (sqlglot's qualify,
pushdown_projections and eliminate_ctes) under `prune.py`'s guard: when a star
or an unqualified column sits beside a derived source, what reads what cannot
be proved, so the SQL stays whole. A join is never dropped: an inner join
decides which rows exist, so removing one is a population decision, not
pruning.

Fragments arrive in the house dialect (`calc.HOUSE`) and the custom SQL in the
warehouse's; both become one tree, generated in the warehouse's dialect. The
result must parse there and again after conversion to Snowflake, or the tile
is refused: a golden SQL nobody can run is not an eval.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.eliminate_ctes import eliminate_ctes
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from eval_builder.prune import _dangling, _drop_redundant_aliases
from eval_builder.sources.tableau.calc import (
    HOUSE, LOD_TABLE, Ctx, Refusal, ident, lod_refs, relation_alias,
)
from eval_builder.sources.tableau.model import DS, Relation, bracket_parts

_NOW = re.compile(r"\b(CURRENT_DATE|CURRENT_TIMESTAMP|GETDATE|SYSDATE|NOW|LOCALTIMESTAMP)\b", re.I)


def render(text: str, dialect: str) -> str:
    """A house-dialect fragment in the warehouse's dialect, for the element."""
    return tidy(sqlglot.parse_one(text, read=HOUSE)).sql(dialect=dialect)


def _relation(ds: DS, rel: Relation, ctx: Ctx, customs: dict[str, Relation]) -> str:
    if rel.kind == "table":
        name = ".".join(ident(p, ds.dialect()) for p in bracket_parts(rel.table))
        return f"{name} AS {relation_alias(ctx, ds, rel)}"
    if rel.kind == "text":
        placeholder = f"__custom_sql_{len(customs)}__"
        customs[placeholder] = rel
        return f"{placeholder} AS {relation_alias(ctx, ds, rel)}"
    if rel.kind == "join" and len(rel.children) == 2:
        kw = {"inner": "INNER JOIN", "left": "LEFT JOIN", "right": "RIGHT JOIN",
              "full": "FULL OUTER JOIN"}.get(rel.join or "")
        if kw is None:
            raise Refusal(f"unsupported relation: join type {rel.join}")
        left, right = (_relation(ds, c, ctx, customs) for c in rel.children)
        return f"{left} {kw} {right} ON {_join_clause(rel.on, ds, ctx)}"
    raise Refusal(f"unsupported relation: relation of kind {rel.kind}")


def _join_clause(e, ds: DS, ctx: Ctx) -> str:
    if e is None:
        raise Refusal("unsupported relation: join without a condition")
    op, kids = e.get("op"), e.findall("expression")
    if not kids:                            # an operand: [relation].[column]
        parts = bracket_parts(op)
        rel = ds.relations.get(parts[0]) if len(parts) == 2 else None
        if rel is None:
            raise Refusal(f"unsupported relation: join operand {op!r}")
        return f"{relation_alias(ctx, ds, rel)}.{ident(parts[1], ds.dialect())}"
    if op in ("AND", "OR"):
        return "(" + f" {op} ".join(_join_clause(k, ds, ctx) for k in kids) + ")"
    if op in ("=", "<>", "<", ">", "<=", ">=") and len(kids) == 2:
        return f"{_join_clause(kids[0], ds, ctx)} {op} {_join_clause(kids[1], ds, ctx)}"
    raise Refusal(f"unsupported relation: join condition operator {op!r}")


def assemble(ctx: Ctx, items: list[tuple[str, str]], group: list[str], where: list[tuple[str, bool]],
             having: list[str], dialect: str) -> tuple[str, list[str], list[str]]:
    """-> (tile SQL in `dialect`, notes, the warehouse tables it reads).

    `where` pairs each row predicate with whether Tableau applies it before
    FIXED expressions (extract, datasource and context filters: True) or after
    (the sheet's dimension filters: False); without a FIXED the split is moot."""
    if not ctx.relations:
        raise Refusal("no warehouse relation: the tile reads none")
    if len({id(ds) for ds, _ in ctx.relations.values()}) > 1:
        raise Refusal("blend: the tile's fields resolve into more than one datasource")
    ds = next(iter(ctx.relations.values()))[0]
    customs: dict[str, Relation] = {}
    if ds.top.kind == "collection":
        used = {rel.name for _, rel in ctx.relations.values()}
        if len(used) != 1:
            raise Refusal(f"relationships: fields from {len(used)} logical tables, which Tableau "
                          "aggregates separately before joining")
        frm = _relation(ds, next(iter(ctx.relations.values()))[1], ctx, customs)
    else:
        frm = _relation(ds, ds.top, ctx, customs)
    tree = tidy(_tree(ctx, frm, items, group, where, having))   # the house part only; custom SQL stays as written
    src_dialect = ds.dialect()
    for placeholder, rel in customs.items():
        try:
            inner = sqlglot.parse_one(rel.sql.rstrip().rstrip(";"), read=src_dialect)
        except Exception as e:  # noqa: BLE001
            raise Refusal(f"custom SQL [{rel.name}] does not parse as {src_dialect}: "
                          f"{type(e).__name__}") from None
        for t in list(tree.find_all(exp.Table)):
            if t.name == placeholder:
                t.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(t.alias))))
    custom_aliases = {ctx.alias_of[(id(ds), rel.name)] for rel in customs.values()}
    tree, notes = _prune(tree, src_dialect, custom_aliases) if customs else (tree, [])
    for scope in traverse_scope(tree):      # every column read from custom SQL must be one it produces
        for col in scope.columns:
            src = scope.sources.get(col.table) if col.table else None
            if isinstance(src, Scope) and isinstance(src.expression, exp.Select) and not src.expression.is_star:
                if col.name.lower() not in {n.lower() for n in src.expression.named_selects}:
                    raise Refusal(f"custom SQL does not produce column {col.name} (datasource metadata drift)")
    out = tree.sql(dialect=dialect, pretty=True)
    try:
        sqlglot.parse_one(out, read=dialect)
    except Exception as e:  # noqa: BLE001
        raise Refusal(f"invalid tile SQL: does not parse back in {dialect}: {type(e).__name__}") from None
    if _NOW.search(out):
        ctx.flags.add("needs_as_of")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = sorted({".".join(p for p in (t.catalog, t.db, t.name) if p) for t in tree.find_all(exp.Table)
                     if t.db or t.name.lower() not in ctes})
    return out, notes, tables


def _tree(ctx: Ctx, frm: str, items: list[tuple[str, str]], group: list[str],
          where: list[tuple[str, bool]], having: list[str]) -> exp.Select:
    """The tile SELECT: one SELECT over the relation, or over row stages when
    the tile reads a FIXED expression. A FIXED a helper measure alone brought
    in (a tooltip's) is not computed."""
    parse = lambda text: sqlglot.parse_one(text, read=HOUSE)  # noqa: E731
    tile = [e for e, _ in items] + group + [p for p, _ in where] + having
    used, frontier = set(), lod_refs(" ".join(tile))
    while frontier - used:
        used |= frontier
        frontier = {n for l in ctx.lods if l.name in used for n in lod_refs(" ".join([l.sql] + l.partition))}
    lods = [l for l in ctx.lods if l.name in used]
    if lods:
        return _staged(ctx, frm, items, group, where, having, lods)
    tree = parse(f"SELECT 1 FROM {frm}")
    tree.set("expressions", [exp.alias_(parse(e), a) for e, a in items])
    for w, _ in where:
        tree = tree.where(parse(w))
    if group:
        tree = tree.group_by(*(parse(g) for g in group))
    for h in having:
        tree = tree.having(parse(h))
    return tree


def _staged(ctx: Ctx, frm: str, items: list[tuple[str, str]], group: list[str],
            where: list[tuple[str, bool]], having: list[str], lods: list) -> exp.Select:
    """The tile SELECT over row stages that hold the FIXED expressions as
    window columns, one stage per nesting depth (module docstring). Stage k
    reads stage k-1 as `s<k-1>` and passes on what later stages read; the tile
    reads the last stage as `t`."""
    if set(ctx.alias_of.values()) != {"src"}:
        raise Refusal('LOD FIXED in "?": over a join tree, which needs one row stage per relation (not implemented)')
    early = [p for p, first in where if first]
    late = [p for p, first in where if not first]
    for p in early:
        if lod_refs(p):
            raise Refusal('LOD FIXED in "?": an extract, datasource or context filter reads a FIXED value, '
                          "which Tableau computes after those filters")
    parse = lambda text: sqlglot.parse_one(text, read=HOUSE)  # noqa: E731
    depth = max(l.depth for l in lods)
    tile = [e for e, _ in items] + group + late + having
    texts = {d: [t for l in lods if l.depth == d for t in [l.sql] + l.partition] for d in range(1, depth + 1)}

    def reads(k: int) -> tuple[dict[str, exp.Identifier], set[str]]:
        """The relation columns and FIXED columns read after stage k."""
        cols: dict[str, exp.Identifier] = {}
        names: set[str] = set()
        for text in tile + [t for d in range(k + 1, depth + 1) for t in texts[d]]:
            for c in parse(text).find_all(exp.Column):
                if c.table == "src":
                    cols.setdefault(c.name, c.this.copy())
                elif c.table == LOD_TABLE:
                    names.add(c.name)
                elif c.table:
                    raise Refusal(f'LOD FIXED in "?": column {c.sql(HOUSE)} is not read from the relation')
        return cols, names

    def col(table: str, name: exp.Identifier) -> exp.Column:
        return exp.Column(this=name.copy(), table=exp.to_identifier(table))

    def requalify(e: exp.Expression, table: str) -> exp.Expression:
        for c in e.find_all(exp.Column):
            if c.table in ("src", LOD_TABLE):
                c.set("table", exp.to_identifier(table))
        return e

    cols, _ = reads(1)
    stage = parse(f"SELECT 1 FROM {frm}")       # a passed-through column keeps its name unaliased
    stage.set("expressions", [col("src", i) for i in cols.values()]
              + [exp.alias_(parse(l.sql), l.name) for l in lods if l.depth == 1])
    for p in early:
        stage = stage.where(parse(p))
    for k in range(2, depth + 1):
        prev = f"s{k - 1}"
        cols, names = reads(k)
        stage = exp.select(
            *[col(prev, i) for i in cols.values()],
            *[col(prev, exp.to_identifier(l.name)) for l in lods if l.depth < k and l.name in names],
            *[exp.alias_(requalify(parse(l.sql), prev), l.name) for l in lods if l.depth == k],
        ).from_(stage.subquery(prev, copy=False))
    outer = exp.select(*[exp.alias_(requalify(parse(e), "t"), a) for e, a in items]).from_(
        stage.subquery("t", copy=False))
    for p in late:
        outer = outer.where(requalify(parse(p), "t"))
    if group:
        outer = outer.group_by(*(requalify(parse(g), "t") for g in group))
    for h in having:
        outer = outer.having(requalify(parse(h), "t"))
    return outer


def _prune(tree: exp.Expression, src_dialect: str | None,
           custom_aliases: set[str]) -> tuple[exp.Expression, list[str]]:
    """Custom SQL cut to the columns the tile reads; the original tree when the
    cut cannot be proved safe. `custom_aliases` are the subqueries that are
    custom SQL, the only ones whose cut is reported."""
    before = {sq.alias: len(sq.this.selects) for sq in tree.find_all(exp.Subquery)
              if sq.alias in custom_aliases and isinstance(sq.this, exp.Select)}
    try:
        q = qualify(tree.copy(), dialect=src_dialect, validate_qualify_columns=False,
                    quote_identifiers=False)
        why = _unprovable(tree, q)
        if why:
            return tree, [f"custom SQL kept whole: {why}"]
        for _ in range(8):
            prev = q.sql()
            pushdown_projections(q, dialect=src_dialect)
            eliminate_ctes(q)
            if q.sql() == prev:
                break
        bad = _dangling(q)
        if bad:
            return tree, [f"custom SQL kept whole: {bad} would be left dangling"]
        _drop_redundant_aliases(q)
    except Exception as e:  # noqa: BLE001 - keep the original, loudly
        return tree, [f"custom SQL kept whole: {type(e).__name__}: {str(e)[:80]}"]
    after = {sq.alias: len(sq.this.selects) for sq in q.find_all(exp.Subquery)
             if sq.alias in custom_aliases and isinstance(sq.this, exp.Select)}
    cut = [f"{a} {n} -> {after[a]} columns" for a, n in before.items() if after.get(a, n) < n]
    return q, ([f"custom SQL pruned to what the tile reads ({', '.join(cut)}); every join kept"]
               if cut else [])


def _unprovable(raw: exp.Expression, q: exp.Expression) -> str | None:
    """Why pruning cannot be proved safe, or None: `prune.py`'s guard, with its
    lateral-alias test made exact. A lateral reference (`a * 2 AS x, x + 1 AS y`)
    is an unqualified name a computed projection of the same SELECT defines, and
    is only visible before qualify; a qualified `dct.x` beside `(...) AS x` is a
    table column, which custom SQL is often full of."""
    for s in raw.find_all(exp.Select):
        computed = {p.alias for p in s.selects if isinstance(p, exp.Alias)
                    and not (isinstance(p.this, exp.Column) and p.this.name == p.alias)}
        lateral = computed & {c.name for c in s.find_all(exp.Column) if not c.table
                              and isinstance(c.find_ancestor(exp.Select, exp.Order), exp.Select)
                              and c.find_ancestor(exp.Select) is s}
        if lateral:
            return f"{sorted(lateral)[0]} is read in the SELECT that computes it"
    for scope in traverse_scope(q):
        if not any(isinstance(src, Scope) for _, src in scope.selected_sources.values()):
            continue
        if scope.unqualified_columns:
            return f"unqualified column {scope.unqualified_columns[0].sql()} beside a derived source"
        if isinstance(scope.expression, exp.Select) and any(
                isinstance(p, exp.Star) or (isinstance(p, exp.Column) and p.is_star)
                for p in scope.expression.selects):
            return "a star over a derived source"
    return None


_ATOMIC = (exp.Column, exp.Literal, exp.Boolean, exp.Null, exp.Func, exp.Paren, exp.Star,
           exp.Var, exp.Identifier, exp.Window, exp.Subquery)
_COMPARISON = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is, exp.In, exp.Like,
               exp.Between, exp.Not)


def _no_parens(e: exp.Expression) -> exp.Expression:
    e = e.copy()
    for p in list(e.find_all(exp.Paren)):
        p.replace(p.this)
    return e


def tidy(tree: exp.Expression) -> exp.Expression:
    """Drop parentheses that cannot change meaning. Kept only if the result
    re-parses to the same tree, parentheses aside; otherwise the input."""
    t = tree.copy()
    # sqlglot's Paren is a Unary and And/Or are Funcs, hence the exclusions.
    atomic = lambda n: isinstance(n, _ATOMIC) and not isinstance(n, (exp.Binary, exp.Unary))  # noqa: E731
    call = lambda n: isinstance(n, exp.Func) and not isinstance(n, exp.Binary)  # noqa: E731
    for _ in range(8):
        changed = False
        for p in list(t.find_all(exp.Paren)):
            child, parent = p.this, p.parent
            if (atomic(child) or call(parent)
                    or isinstance(parent, (exp.Where, exp.Having, exp.Alias, exp.Group, exp.Paren))
                    or (isinstance(child, _COMPARISON) and isinstance(parent, (exp.And, exp.Or)))
                    or (type(child) is type(parent) and isinstance(parent, exp.Binary) and p.arg_key == "this")):
                p.replace(child)
                changed = True
        if not changed:
            break
    try:
        if _no_parens(sqlglot.parse_one(t.sql(dialect=HOUSE), read=HOUSE)) == _no_parens(tree):
            return t
    except Exception:  # noqa: BLE001
        pass
    return tree
