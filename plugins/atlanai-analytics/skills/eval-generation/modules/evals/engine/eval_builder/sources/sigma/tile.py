"""One Sigma element -> one VisualElement and the SQL of its tile.

`shapes.classify` says which SELECTs compute the tile (its blocks). This module
reads the tile's own words off the outer SELECT — every column Sigma emits
carries its display label as the outermost alias — and binds each label to
what computes it:

- a measure is a label whose value is aggregated in a block;
- a dimension is a label on a key the block is grouped by: the preview column
  an F2/F3 block is joined ON, or a GROUP BY key of a grouped block;
- the rest is not what the tile shows: the preview's columns (the "underlying
  data" a viewer can expand), Sigma internals (`COALESCE_824`, `V_1751`), sort
  helpers (`sort-<column id>-0`), colour-scale ranges (`<column id>-min`),
  conditional-format rules (`<rule id>_<column id>`), KPI comparison arithmetic
  and pivot totals.

The tile SQL is the block that holds the displayed measures, re-projected to
the dimensions and those measures under their display labels and collapsed by
`prune.collapse_to_tile`. Its answer is the block's, not the preview's:

- one row (F1) or one row per group (F2-F4), where the original repeats the
  value once per previewed row;
- every group, where the original's LIMIT 1000 preview can hide some;
- one row (COUNT 0, SUM NULL) when nothing passes the filters, where the
  original returns no rows at all because its preview is empty.

Roles follow the tile's kind. A KPI, gauge or funnel keeps
its value(s) in the SQL; a KPI's comparison column (the subtrahend of Sigma's
`Delta comparison`, or the previous period of `Comparison by date`) and a
gauge's single goal are kept as measures with role comparison / target, with
their expressions, outside the SQL. A period-comparison KPI shows its latest
period, so its SQL is the per-period query cut to that row. Charts keep every
displayed measure. Pivots and tables keep the finest block; coarser blocks are
subtotals. A table with several measures keeps one, chosen by
`choose_measure` from Atlan's usage counts.

Physical columns come from walking the qualified query (`shapes.Lineage`) and
are cross-checked against Atlan's ColumnProcess lineage for the field of the
same label; on disagreement the SQL-derived columns are kept and the note says
so. A value measure or dimension that cannot be traced refuses the element.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, ScopeType

from eval_builder.elements import (
    Dimension, ElementKind, FilterSpec, Measure, VisualElement, kind_from_sigma,
)
from eval_builder.prune import TileRefused, dialect, _conjuncts, collapse_to_tile
from eval_builder.sources.sigma.shapes import (
    Block, Lineage, Shape, Trace, base, classify, has_agg, in_cte, own, strip_trailer, table_name,
)

_INTERNAL = re.compile(r"^[A-Z_]+_\d+$")                       # COALESCE_824, V_1751
_SORT = re.compile(r"^sort-.+-\d+$")                           # sort-<column id>-0
_RANGE = re.compile(r"^([A-Za-z0-9_-]{10})-(min|max)$")        # <column id>-min
_RULE = re.compile(r"^[A-Za-z0-9_-]{10}_([A-Za-z0-9_-]{10})$")  # <rule id>_<column id>
_TOTAL = re.compile(r" \((Grand Total|Row Total|Parent Row Total|Column Total"
                    r"|Parent Column Total)\)$")
_COMPARISON_MATH = frozenset({"Percentage comparison", "Relative comparison", "Delta comparison"})
_BY_DATE = "Comparison by date"
_BUCKETS = frozenset({"day", "week", "month", "quarter", "year"})
_SINGLE = (ElementKind.KPI, ElementKind.SINGLE_VALUE)


@dataclass
class Tile:
    """An element on its way to a candidate."""

    element: VisualElement
    shape: Shape
    block: Block | None = None
    dims: list[tuple[Dimension, exp.Expression]] = field(default_factory=list)
    exprs: dict[str, exp.Expression] = field(default_factory=dict)   # measure label -> block expression
    latest: bool = False                    # a period-comparison KPI: the latest period only
    notes: list[str] = field(default_factory=list)
    raw_exprs: dict[str, str] = field(default_factory=dict)          # "measure:<label>" -> alias-based text
    labels: dict[str, list[tuple[str, frozenset]]] = field(default_factory=dict)  # outer column -> fields


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _display(e: exp.Expression) -> str:
    """Snowflake SQL without the Q<n> qualifiers, which mean nothing to a reader."""
    e = e.copy()
    for c in e.find_all(exp.Column):
        c.set("table", None)
    return e.sql(dialect=dialect())


def _agg(e: exp.Expression) -> str:
    if isinstance(e, exp.Count):
        return "COUNT_DISTINCT" if isinstance(e.this, exp.Distinct) else "COUNT"
    return {exp.Sum: "SUM", exp.Avg: "AVG", exp.Min: "MIN", exp.Max: "MAX"}.get(type(e), "CUSTOM")


def _bucket(e: exp.Expression) -> str | None:
    if isinstance(e, (exp.DateTrunc, exp.TimestampTrunc, exp.DatetimeTrunc, exp.TimeTrunc)):
        unit = e.args.get("unit")
        name = (unit.name if unit is not None else "").lower()
        return name if name in _BUCKETS else None
    return None


def _projection(scope: Scope, name: str) -> exp.Expression | None:
    node = scope.expression
    if not isinstance(node, exp.Select):
        return None
    p = next((p for p in node.selects if p.alias_or_name == name), None)
    return None if p is None else p.unalias()


def _substitute(e: exp.Expression, alias: str, scope: Scope) -> exp.Expression | None:
    """`e` read in the trailer, rewritten in the block's own scope."""
    if isinstance(e, exp.Column):
        p = _projection(scope, e.name) if e.table == alias else None
        return None if p is None else p.copy()
    e = e.copy()
    for c in list(e.find_all(exp.Column)):
        if c.table == alias:
            p = _projection(scope, c.name)
            if p is None:
                return None
            c.replace(p.copy())
    return e


def _inline(scope: Scope, e: exp.Expression) -> exp.Expression:
    """`e` with references into sub-blocks replaced by what they compute, down
    to the aggregate: the measure at the tile's grain (SUM(AMOUNT), not Q38.SUM_1702)."""
    if has_agg(e):
        return e.copy()
    if isinstance(e, exp.Column):
        src = scope.sources.get(e.table)
        if isinstance(src, Scope) and src.scope_type is not ScopeType.CTE:
            p = _projection(src, e.name)
            if p is not None:
                return _inline(src, p)
        return e.copy()
    out = e.copy()
    for c in list(out.find_all(exp.Column)):
        src = scope.sources.get(c.table)
        if isinstance(src, Scope) and src.scope_type is not ScopeType.CTE:
            p = _projection(src, c.name)
            if p is not None:
                c.replace(_inline(src, p))
    return out


# A reader of the element sees Sigma's internal aliases (IF_1379, V_1751,
# DATETRUNC_1742) read back into what they mean. Display text only: the tile
# SQL keeps Sigma's names, and the alias-based text stays in provenance.
_MAX_DEPTH, _MAX_TEXT, _PEEK = 5, 600, 120
_ATOMS = (exp.Column, exp.Literal, exp.Func, exp.Paren, exp.Null, exp.Boolean)


def _definition(scope: Scope, col: exp.Column):
    """Follow a column through pass-through projections to where it is defined:
    (scope, expression) where it is computed, (None, Column) for a warehouse
    column, (None, None) where it cannot be followed (a union, an unknown name)."""
    s, c = scope, col
    for _ in range(100):
        src = s.sources.get(c.table) if c.table else None
        if isinstance(src, exp.Table):
            return None, exp.Column(this=c.this.copy())
        if not isinstance(src, Scope) or not isinstance(src.expression, exp.Select):
            return None, None
        node = src.expression
        p = next((p for p in node.selects if p.alias_or_name == c.name), None)
        if p is None:
            owners = {x.table for x in node.selects if isinstance(x, exp.Column) and x.is_star}
            if any(isinstance(x, exp.Star) for x in node.selects):
                owners |= set(src.selected_sources)
            if len(owners) != 1:
                return None, None
            s, c = src, exp.Column(this=c.this.copy(), table=exp.to_identifier(owners.pop()))
            continue
        e = p.unalias()
        if not (isinstance(e, exp.Column) and not e.is_star):
            return src, e
        s, c = src, e
    return None, None


def _readable(scope: Scope, e: exp.Expression, depth: int = 0) -> exp.Expression:
    """`e` for a reader: every Sigma internal alias, and every reference to a
    sub-block's aggregate, replaced by the expression that defines it, down the
    CTE chain to warehouse column names. An alias five levels down, or whose
    expansion would pass 600 characters, stays, with a comment that opens its
    definition."""
    out = e.copy()
    for c in [out] if isinstance(out, exp.Column) else list(out.find_all(exp.Column)):
        if c.is_star:
            continue
        rep = _readable_column(scope, c, depth)
        if c is out:
            return rep
        if not isinstance(rep, _ATOMS) and isinstance(c.parent, (exp.Binary, exp.Unary)) \
                and not isinstance(c.parent, exp.Paren):
            rep = exp.paren(rep, copy=False)    # a replaced operand keeps its precedence
        c.replace(rep)
    return out


def _readable_column(scope: Scope, c: exp.Column, depth: int) -> exp.Expression:
    src, d = _definition(scope, c)
    if isinstance(d, exp.Column):
        return d                                # a warehouse column, by its own name
    keep = exp.Column(this=c.this.copy())
    # Expanded: Sigma's internal aliases, and (as `_inline` does) an aggregate
    # of a sub-block in the main query. A name someone chose (MAU) is kept.
    if d is None or not (_INTERNAL.match(c.name) or (has_agg(d) and not in_cte(src))):
        return keep
    if depth >= _MAX_DEPTH:
        return _noted(keep, _display(d))
    rep = _readable(src, d, depth + 1)
    text = rep.sql(dialect=dialect())
    return _noted(keep, text) if len(text) > _MAX_TEXT else rep


def _noted(col: exp.Column, definition: str) -> exp.Column:
    col.add_comments([f"{col.name} = {definition[:_PEEK].replace('*/', '* /')}…"])
    return col


def _text(scope: Scope, e: exp.Expression, raw: str, key: str, raws: dict) -> str:
    """The readable text of `e`; its alias-based text goes to `raws` when they differ."""
    text = _readable(scope, e).sql(dialect=dialect())
    if text != raw:
        raws[key] = raw
    return text


def _trace(lin: Lineage, scope: Scope, e: exp.Expression) -> Trace:
    t = lin.expr(scope, e)
    if has_agg(e):                      # aggregated in this very SELECT
        t = Trace(set(t.columns), [(scope, e)], list(t.unresolved))
    return t


def _own(t: Trace, ref_base: str | None) -> bool:
    return any(own(s, p, ref_base) for s, p in t.leaves)


def _to_trailer(shape: Shape, proj: exp.Expression) -> exp.Expression | None:
    """An outer projection, followed through Sigma's pass-through wrappers."""
    scope, e = shape.lineage.root, proj.unalias()
    while scope is not shape.trailer:
        if not isinstance(e, exp.Column):
            return None
        src = scope.sources.get(e.table)
        if not isinstance(src, Scope):
            return None
        p = _projection(src, e.name)
        if p is None:
            return None
        scope, e = src, p
    return e


def _drop_reason(label: str, field_ids: set[str]) -> str | None:
    if _INTERNAL.match(label):
        return "Sigma internal"
    if _SORT.match(label):
        return "sort helper"
    m = _RANGE.match(label)
    if m and (not field_ids or m.group(1) in field_ids):
        return "colour-scale range"
    m = _RULE.match(label)
    if m and (not field_ids or m.group(1) in field_ids):
        return "conditional-format rule"
    if _TOTAL.search(label):
        return "pivot total"
    if label in _COMPARISON_MATH:
        return "KPI comparison arithmetic"
    return None


def _axis_total(label: str, measures: set[str], dims: list[str]) -> bool:
    """`<measure> (<dim>)` or `<measure> (<dim>-<dim>)`: a pivot subtotal.
    Labels nest parentheses ("Sum of X (Account) (Name)"), so every split is tried."""
    if not label.endswith(")") or not dims:
        return False
    for i in range(len(label)):
        if label.startswith(" (", i) and label[:i] in measures and label[:i] != label:
            axis = rest = label[i + 2:-1]
            for d in sorted(dims, key=len, reverse=True):
                rest = rest.replace(d, "")
            if rest != axis and rest.strip("-") == "":
                return True
    return False


def _atlan_check(label: str, columns: list[str], fields: dict, inputs: dict) -> str:
    f = fields.get(label)
    # Compared without case: Atlan upper-cases a table the SQL quotes in lower case.
    atlan = {_column_of(q).upper() for q in inputs.get(f["qualifiedName"], ())} if f else set()
    sql = {c.upper() for c in columns}
    if not atlan:
        verdict = "no atlan lineage"
    elif atlan == sql:
        verdict = "atlan agrees"
    elif atlan < sql:
        verdict = "atlan subset"
    else:
        verdict = "atlan disagrees: atlan reads " + ", ".join(sorted(atlan - sql)[:3])
    return f'lineage "{label}": {verdict}'


def _column_of(qn: str) -> str:
    """default/snowflake/<conn>/DB/SCHEMA/TABLE/COLUMN -> DB.SCHEMA.TABLE.COLUMN"""
    return ".".join(qn.split("/")[3:])


# ---------------------------------------------------------------------------
# Binding labels to the blocks
# ---------------------------------------------------------------------------

@dataclass
class _Item:
    label: str
    index: int
    block: Block
    expr: exp.Expression                # in the block's scope
    trace: Trace
    cls: str                            # measure | lookup | constant | dim


def analyse(record: dict, workbook: dict, fields: list[dict], inputs: dict[str, set[str]],
            provenance: dict) -> Tile:
    """Classify the element and bind its labels. Call `render` for the SQL."""
    raw = record.get("sigmaDataElementQuery") or ""
    qn = record.get("qualifiedName") or ""
    raw_kind = record.get("sigmaDataElementType") or ""
    parts = qn.split("/")
    el = VisualElement(
        tool="sigma", workbook=workbook.get("name") or "", page=record.get("sigmaPageName"),
        element_id=parts[-1], workbook_id=parts[3] if len(parts) > 3 else None,
        title=(record.get("name") or "").strip(), kind=kind_from_sigma(raw_kind),
        raw_kind=raw_kind, original_sql=strip_trailer(raw) or None,
        provenance={**provenance, "element_qualified_name": qn,
                    "source_url": record.get("sourceURL")})
    tile = Tile(el, Shape(""))
    try:
        tile.shape = classify(raw)
        el.shape = tile.shape.family or None
        if tile.shape.refusal:
            el.refusal = tile.shape.refusal
            return tile
        _bind(tile, fields, inputs)
    except TileRefused as e:
        el.refusal = str(e)
    except Exception as e:  # noqa: BLE001 - one odd element must not stop the workbook
        el.refusal = f"internal error ({type(e).__name__}: {str(e)[:80]})"
    el.notes = list(tile.notes)
    return tile


def _bind(tile: Tile, fields: list[dict], inputs: dict[str, set[str]]) -> None:
    el, shape, lin = tile.element, tile.shape, tile.shape.lineage
    by_name: dict[str, dict] = {}
    for f in fields:
        by_name.setdefault(f.get("name"), f)
    field_ids = {(f.get("qualifiedName") or "").rsplit("/", 1)[-1] for f in fields}
    rows_block = next((b for b in shape.blocks if b.alias is None), None)
    joined = {b.alias: b for b in shape.blocks if b.alias}
    trailer_base = base(shape.trailer)

    dropped: Counter = Counter()
    math: dict[str, exp.Expression] = {}         # KPI comparison arithmetic, by label
    items: list[_Item] = []
    key_labels: dict[tuple[str, str], str] = {}  # (block alias, rows column) -> label
    for i, p in enumerate(shape.tree.selects):
        label = p.alias_or_name
        why = _drop_reason(label, field_ids)
        e = _to_trailer(shape, p)
        if why == "KPI comparison arithmetic" and e is not None:
            math[label] = e
        if why or e is None:
            dropped[why or "not resolved through the outer SELECT"] += 1
            continue
        reads = {c.table for c in ([e] if isinstance(e, exp.Column) else e.find_all(exp.Column))}
        if shape.rows_kind == "block":
            block, be = rows_block, e
        elif len(reads) == 1 and next(iter(reads)) in joined:
            block = joined[next(iter(reads))]
            be = _substitute(e, block.alias, block.scope)
        elif reads == {shape.rows} and shape.rows_kind == "grouped":
            block, be = rows_block, _substitute(e, shape.rows, rows_block.scope)
        elif reads == {shape.rows} and isinstance(e, exp.Column):
            keyed = [b for b in joined.values() if e.name in b.keys]
            for b in keyed:
                key_labels.setdefault((b.alias, e.name), label)
            if not keyed:
                dropped["preview column"] += 1
            continue
        else:
            dropped["row-level column"] += 1
            continue
        if be is None:
            dropped["not resolved in its block"] += 1
            continue
        group = block.scope.expression.args.get("group")
        if group is not None and any(be == g for g in group.expressions):
            items.append(_Item(label, i, block, be, lin.expr(block.scope, be), "dim"))
            continue
        t = _trace(lin, block.scope, be)
        mine = _own(t, None if block.alias is None else trailer_base)
        cls = ("measure" if mine else "lookup" if t.leaves
               else "constant" if not t.columns and not t.unresolved else "row")
        if cls == "row":
            dropped["row-level column"] += 1
            continue
        items.append(_Item(label, i, block, be, t, cls))

    measures = [it for it in items if it.cls != "dim"]
    base_block = _base_block(tile, shape, measures)
    dims = _dims(tile, base_block, items, key_labels, by_name)
    dim_labels = [d.label for d, _, _ in dims]
    single = el.kind in _SINGLE or el.kind is ElementKind.GAUGE
    base_labels = {it.label for it in measures if it.block is base_block}
    kept = []
    for it in measures:
        if _axis_total(it.label, base_labels, dim_labels):
            dropped["pivot total"] += 1
            continue
        kept.append(it)

    # A KPI's comparison or a gauge's goal may sit in another block; it is
    # kept outside the SQL either way. Everywhere else other blocks are totals.
    roles = _roles(el.kind, [it for it in kept if single or it.block is base_block], math)
    for it in kept:
        role = roles.get(it.label, "subtotal")
        inl = _inline(it.block.scope, it.expr)
        if it.trace.unresolved:
            if role == "value":
                raise TileRefused(f'unattributable: "{it.label}" not traced to warehouse columns '
                                  f"({it.trace.unresolved[0]})")
            tile.notes.append(f'"{it.label}" left out: its columns cannot be traced')
            continue
        el.measures.append(Measure(
            label=it.label,
            expr=_text(it.block.scope, it.expr, _display(inl), f"measure:{it.label}", tile.raw_exprs),
            agg=None if it.cls == "constant" else _agg(inl),
            physical_columns=sorted(it.trace.columns), role=role,
            source_ref=_field_id(by_name, it.label)))
        if role == "value":
            if it.block is not base_block:
                raise TileRefused(f"{el.kind.value}: measures span blocks that do not prune")
            tile.exprs[it.label] = it.expr
        if role in ("target", "comparison"):
            tile.notes.append(f'{role} "{it.label}" not in tile SQL')
    if not tile.exprs:
        raise TileRefused(f"{shape.family}: no displayed measure")

    for d, be, t in dims:
        if t.unresolved:
            raise TileRefused(f'unattributable: dimension "{d.label}" not traced to warehouse '
                              f"columns ({t.unresolved[0]})")
        el.dimensions.append(d)
        tile.dims.append((d, be))
    tile.block = base_block
    if el.kind in _SINGLE and base_block.grain == "grouped" and len(_group_keys(base_block)) == 1:
        tile.latest = True
        tile.notes.append("latest period only: a period-comparison KPI shows its last period")

    for m in el.measures:
        tile.notes.append(_atlan_check(m.label, m.physical_columns, by_name, inputs))
    for d in el.dimensions:
        tile.notes.append(_atlan_check(d.label, d.physical_columns, by_name, inputs))
    if dropped:
        tile.notes.append("not displayed: " + ", ".join(
            f"{n} {why}" for why, n in sorted(dropped.items())))
    # Fields the outer SELECT projects as a plain column, for filter labels.
    for p in shape.tree.selects:
        e = p.unalias()
        if isinstance(e, exp.Column) and p.alias_or_name in by_name:
            tile.labels.setdefault(e.name, []).append(
                (p.alias_or_name, frozenset(lin.expr(lin.root, e).columns)))
    if tile.raw_exprs:
        el.provenance["raw_exprs"] = dict(tile.raw_exprs)


def _dims(tile: Tile, b: Block, items: list[_Item], key_labels: dict,
          by_name: dict) -> list[tuple[Dimension, exp.Expression, Trace]]:
    """The base block's keys under their display labels. A key with no label
    (a lookup-match flag such as COALESCE_1604) stays in the GROUP BY, so the
    grain is unchanged, and is noted rather than shown."""
    lin = tile.shape.lineage
    found: list[tuple[str, exp.Expression]] = []
    if b.alias:
        for rows_col, block_col in b.keys.items():
            be = _projection(b.scope, block_col)
            label = key_labels.get((b.alias, rows_col)) or next(
                (it.label for it in items if it.block is b and it.cls == "dim" and it.expr == be), None)
            if label is None or be is None:
                tile.notes.append(f"internal group key kept: {block_col}")
                continue
            found.append((label, be))
    else:
        labelled = [it for it in items if it.block is b and it.cls == "dim"]
        found = [(it.label, it.expr) for it in labelled]
        for g in _group_keys(b):
            if not any(it.expr == g for it in labelled):
                tile.notes.append(f"internal group key kept: {_display(g)}")
    out = []
    for label, be in found:
        inl = _inline(b.scope, be)
        text = _text(b.scope, be, _display(inl), f"dimension:{label}", tile.raw_exprs)
        out.append((Dimension(label=label, expr=text, bucket=_bucket(inl),
                              physical_columns=sorted(lin.expr(b.scope, be).columns),
                              source_ref=_field_id(by_name, label)),
                    be, lin.expr(b.scope, be)))
    return out


def _field_id(by_name: dict, label: str) -> str | None:
    f = by_name.get(label)
    return (f.get("qualifiedName") or "").rsplit("/", 1)[-1] if f else None


def _group_keys(b: Block) -> list[exp.Expression]:
    if b.alias:
        return list(b.keys)
    group = b.scope.expression.args.get("group")
    return list(group.expressions) if group is not None else []


def _base_block(tile: Tile, shape: Shape, measures: list[_Item]) -> Block:
    """The block whose measures the tile displays; the others are its totals."""
    shown = [it for it in measures if it.cls != "constant"]
    if not shown:
        raise TileRefused(f"{shape.family}: no displayed measure")
    if shape.rows_kind in ("block", "grouped"):
        return next(b for b in shape.blocks if b.alias is None)
    kind = tile.element.kind
    if kind in _SINGLE or kind is ElementKind.GAUGE:
        return min(shown, key=lambda it: it.index).block
    holding = [b for b in shape.blocks if any(it.block is b for it in shown)]
    grouped = [b for b in holding if b.grain == "grouped"]
    if not grouped:
        if len(holding) > 1:
            raise TileRefused(f"{kind.value}: measures span blocks that do not prune")
        return holding[0]
    finest = max(grouped, key=lambda b: len(b.keys))
    nested = all(set(b.keys) <= set(finest.keys) for b in grouped)
    if len(grouped) > 1 and not (nested and kind in (ElementKind.PIVOT, ElementKind.TABLE)):
        raise TileRefused(f"{kind.value}: measures span blocks that do not prune")
    return finest


def _roles(kind: ElementKind, items: list[_Item], math: dict) -> dict[str, str]:
    """Roles by the tile's kind: of the base block's measures, or of
    every block's for a KPI or gauge, whose comparison or goal may sit apart."""
    shown = sorted((it for it in items if it.cls != "constant"), key=lambda it: it.index)
    roles = {it.label: "helper" for it in items}
    if not shown:
        return roles
    if kind in _SINGLE:
        roles[shown[0].label] = "value"
        delta, b = math.get("Delta comparison"), shown[0].block
        sub = _substitute(delta, b.alias, b.scope) if delta is not None and b.alias else None
        for it in shown[1:]:
            if it.label == _BY_DATE or (isinstance(sub, exp.Sub) and sub.expression == it.expr):
                roles[it.label] = "comparison"
    elif kind is ElementKind.GAUGE:
        roles[shown[0].label] = "value"
        if len(shown) == 2:             # value, then exactly one goal; more is ambiguous
            roles[shown[1].label] = "target"
    else:
        # In a table or pivot a Lookup is a row attribute, not a measure.
        tabular = kind in (ElementKind.TABLE, ElementKind.PIVOT)
        for it in shown:
            roles[it.label] = "helper" if tabular and it.cls == "lookup" else "value"
    return roles


# ---------------------------------------------------------------------------
# The SQL
# ---------------------------------------------------------------------------

def render(tile: Tile) -> None:
    """Write the tile SQL, its filters and the tables it reads onto the element."""
    el = tile.element
    if el.refusal or tile.block is None:
        return
    el.sql, el.filters, el.source_relation = None, [], []
    projections = [exp.alias_(e.copy(), d.label, quoted=True) for d, e in tile.dims]
    projections += [exp.alias_(tile.exprs[m.label].copy(), m.label, quoted=True)
                    for m in el.measures if m.role == "value"]
    try:
        pruned = collapse_to_tile(tile.shape.tree, tile.block.scope.expression, projections)
        tree = sqlglot.parse_one(pruned.sql, read=dialect())
        if tile.latest:
            key = (exp.column(tile.dims[0][0].label, quoted=True) if tile.dims
                   else tree.args["group"].expressions[0].copy())
            tree = tree.order_by(exp.Ordered(this=key, desc=True, nulls_first=False)).limit(1)
        sql = tree.sql(dialect=dialect())
        sqlglot.parse_one(sql, read=dialect())
    except TileRefused as e:
        el.refusal = str(e)
        return
    except Exception as e:  # noqa: BLE001 - a rewrite that fails is a refusal, not a crash
        el.refusal = f"tile SQL could not be built ({type(e).__name__})"
        return
    lin = Lineage(tree)
    el.sql = sql
    raws = dict(tile.raw_exprs)
    el.filters = _filters(lin, tile.labels, raws)
    el.provenance.pop("raw_exprs", None)
    if raws:
        el.provenance["raw_exprs"] = raws
    el.source_relation = sorted({table_name(src) for s in lin.scopes
                                 for src in s.sources.values() if isinstance(src, exp.Table)})
    size = f"{len(el.original_sql or '')} -> {len(sql)} chars"
    el.notes = tile.notes + [f"{el.shape} {tile.block.grain} block kept; {'; '.join(pruned.notes)}; {size}"]


def _spine(scope: Scope):
    """The sources whose rows reach this SELECT: FROM, inner and cross joins."""
    node = scope.expression
    if isinstance(node, exp.SetOperation):
        yield from (s for s in scope.union_scopes)
        return
    if not isinstance(node, exp.Select) or node.args.get("from_") is None:
        return
    names = [node.args["from_"].this.alias_or_name]
    for j in node.args.get("joins") or []:
        if not j.side and (j.kind or "").upper() in ("", "INNER", "CROSS"):
            names.append(j.alias_or_name)
    for n in names:
        src = scope.sources.get(n)
        if isinstance(src, Scope):
            yield src


def _filters(lin: Lineage, labels: dict, raws: dict) -> list[FilterSpec]:
    """WHERE (and HAVING) conjuncts on the rows that reach the tile's aggregate.

    The aggregate's own SELECT, and anything above it, filters the element
    (scope "element"); everything below — subqueries and the CTE chain it
    reads — filters its source ("source"). LEFT-joined lookups do not filter
    the rows and are left out. A filter on one column the outer SELECT shows
    under exactly one field name, reading the same warehouse columns, carries
    that name as its label.
    """
    out: list[FilterSpec] = []
    seen: set[tuple[str, str]] = set()

    def visit(scope: Scope, element_level: bool) -> None:
        node = scope.expression
        level = "element" if element_level else "source"
        if isinstance(node, exp.Select):
            for clause in ("where", "having"):
                cond = node.args.get(clause)
                for c in _conjuncts(cond.this) if cond is not None else []:
                    raw = _display(c)
                    if (level, raw) in seen:
                        continue
                    seen.add((level, raw))
                    t = lin.expr(scope, c)
                    cols = {(x.table, x.name) for x in c.find_all(exp.Column)}
                    column = label = None
                    if len(cols) == 1:
                        ct = lin.column(scope, *next(iter(cols)))
                        if ct.plain and len(ct.columns) == 1:
                            column = next(iter(ct.columns))
                        named = [lab for lab, phys in labels.get(next(iter(cols))[1], [])
                                 if ct.columns and phys == ct.columns]
                        label = named[0] if len(named) == 1 else None
                    text = _text(scope, c, raw, f"filter:{len(out)}", raws)
                    out.append(FilterSpec(expr=text, label=label, column=column, scope=level,
                                          origin="sigma-sql", physical_columns=sorted(t.columns)))
            element_level = element_level and not any(has_agg(p) for p in node.selects)
        for child in _spine(scope):
            visit(child, element_level)

    visit(lin.root, True)
    return out


def choose_measure(tile: Tile, usage: dict[str, tuple[float, float, float]]) -> bool:
    """A table with several measures keeps one: the most used in the
    warehouse's query history, by Atlan's counts on its columns —
    sourceReadUserCount, then sourceReadCount, then popularityScore (each
    summed over the measure's columns), then the outer SELECT's order. The
    others stay on the element as role "alternate". True when it changed."""
    el = tile.element
    values = [m for m in el.measures if m.role == "value"]
    if el.kind is not ElementKind.TABLE or len(values) < 2 or el.refusal:
        return False
    rules = ("sourceReadUserCount", "sourceReadCount", "popularityScore")
    scores = [tuple(sum(usage.get(c.upper(), (0, 0, 0))[k] for c in m.physical_columns)
                    for k in range(3)) for m in values]
    order = sorted(range(len(values)), key=lambda i: (scores[i], -i), reverse=True)
    win, second = order[0], order[1]
    k = next((k for k in range(3) if scores[win][k] != scores[second][k]), None)
    rule = "outer projection order (tied with the runner-up)" if k is None else rules[k]
    for i, m in enumerate(values):
        if i != win:
            m.role = "alternate"
            tile.exprs.pop(m.label, None)
    tile.notes.append(
        f'table measure: kept "{values[win].label}" by {rule}; (users, reads, popularity) summed '
        "over each measure's columns: " + ", ".join(
            f'"{values[i].label}" ({scores[i][0]:g}, {scores[i][1]:g}, {scores[i][2]:.3g})'
            for i in order))
    return True
