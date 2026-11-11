"""Page-level filters from a BI asset, and their injection into a query.

A dashboard's page filters are the part of it a person actually touches. The
compiled query behind an element is written as if nothing were selected, so the
filter never reaches the SQL, and `select.score_discriminating` sees a query
with no business rule in it — which is exactly what `rank`'s floor removes. The
element that a human uses every day scores as "any agent passes this".

Where a filter is recovered depends on the source:

  Tableau   exactly, from the .twb/.tds: `sources.tableau` reads worksheet,
            dashboard-card, datasource and extract filters into the tile's
            `FilterSpec`s, so nothing here parses Tableau XML any more.
  Sigma     nowhere exact. Atlan's model has no filter concept at all --
            SigmaDataElement carries only query/type/field-count -- and the
            compiled SQL omits an unset filter entirely. The inference below
            (the columns every element on a page projects) remains for the
            query-history file path only; tiles carry their own filters.
  Power BI  in the PBIR report files, behind Fabric `getDefinition`, which 404s
            on Pro shared capacity. Not recoverable from a catalog crawl.

This module keeps the Sigma inference, the shared injector, and the tile
variants: a dashboard filter card whose column has `columnTopValues` in Atlan
yields copies of the tile at other saved values.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

# Tableau decorates a field reference inside a filter: `none:date_id:qk`.
_TABLEAU_DECORATION = re.compile(r"^(?:none|usr|attr|sum|min|max|qk|nk):|:(?:qk|nk|ok)$")
# `&quot;332 - Some Dealer&quot;` -> `332 - Some Dealer`
_QUOTED = re.compile(r'^\s*["\']?(.*?)["\']?\s*$', re.S)


@dataclass
class Filter:
    """One page-level filter, resolved to something injectable."""

    column: str                      # warehouse column, once resolved
    caption: str | None = None       # the dashboard's own label for it
    values: list[str] = field(default_factory=list)
    kind: str = "categorical"        # categorical | relative_date
    formula: str | None = None       # set when the filter binds to a calc field
    field_ref: str | None = None     # the field as the report names it
    period: tuple[str, str, str] | None = None   # (first, last, unit) for relative_date
    page: str | None = None          # the page it was inferred on, when grouped by page

    @property
    def injectable(self) -> bool:
        """Is there enough here to write a predicate?"""
        if not self.resolved:
            return False
        return bool(self.values) if self.kind == "categorical" else self.period is not None

    @property
    def resolved(self) -> bool:
        """Can this be injected as-is?

        A filter bound to a calculated field names the calculation, not a
        column. Translating the formula is the Tableau transformer's job and it
        is known to emit unparseable SQL, so an unresolved filter is reported
        rather than guessed at.
        """
        return bool(self.column) and self.formula is None

    def describe(self) -> str:
        label = self.caption or self.column or self.field_ref or "?"
        if self.kind == "relative_date":
            return f"{label}: {self.values[0] if self.values else 'relative date window'}"
        if self.values:
            return f"{label} = {', '.join(self.values[:3])}"
        return f"{label}: no value selected"


# ---------------------------------------------------------------------------
# Tableau
# ---------------------------------------------------------------------------

# A workbook is a customer file, so it is untrusted input. ElementTree does not
# resolve external entities, but it does expand internal ones, which is the
# billion-laughs shape: a few hundred bytes of nested ENTITY declarations expand
# to gigabytes and take the process with them. A workbook has no legitimate use
# for a DTD, so refusing one costs nothing and removes the class of attack
# without reaching for a third-party parser.
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)
# A .twbx is a zip from outside, so its entries are untrusted too: a path
# escaping the archive root, or one that expands far beyond its compressed size.
_MAX_TWB_BYTES = 256 * 1024 * 1024


# ---------------------------------------------------------------------------
# Sigma -- no filter metadata exists, so infer candidates
# ---------------------------------------------------------------------------

def candidates_from_projections(sqls: list[str], columns: set[str],
                                dialect: str | None = None) -> list[Filter]:
    """Columns every element on a page projects bare -- candidate page filters.

    A page filter has to apply to every element that uses its column, so the
    intersection across the page is a far better signal than any one element:
    on one workbook it scored 3/4 precision at full recall, against 3/11 from
    the widest element alone.

    It is still a heuristic. These are candidates for confirmation, not filters,
    and they carry no value and no label -- which is why `origin` says so.
    """
    common: set[str] | None = None
    for sql in sqls:
        try:
            tree = sqlglot.parse_one(sql, read=dialect or _run_dialect())
        except Exception:  # noqa: BLE001 - one bad element should not sink the page
            continue
        outer = tree.find(exp.Select)
        if outer is None:
            continue
        bare = set()
        for proj in outer.args.get("expressions") or []:
            if proj.find(exp.AggFunc) is not None:
                continue
            col = proj.find(exp.Column)
            if col is not None and col.name in columns:
                bare.add(col.name)
        # An element that projects no dimension at all -- a scalar headline
        # tile -- says nothing about which columns the page filters on, and
        # intersecting with its empty set would wipe out every candidate the
        # other elements agree on. Abstain rather than veto.
        if not bare:
            continue
        common = bare if common is None else (common & bare)
    return [Filter(column=c, field_ref=c) for c in sorted(common or ())]


def infer_page_filters(sqls: list[str], schema: dict[str, list[dict]],
                       dialect: str | None = None,
                       pages: list[str | None] | None = None,
                       ) -> tuple[list[Filter], list[str]]:
    """Inferred page filters for a source that records none, with their values.

    Returns the filters that can actually be injected, and the names of the
    candidates that could not be — because inference finds a *column* and a
    predicate needs a value.

    `pages`, parallel to `sqls`, groups elements by the page they sit on before
    intersecting. A page filter belongs to one page, so intersecting across a
    whole workbook finds only what every page happens to share. On a
    multi-page workbook that is generic columns such as ID, NAME and
    IS_DELETED. Each filter returned carries its page, and should
    only be injected into elements of that page.

    The value has to come from the catalog, as `sample_values` on the column in
    `schema.json` (the shape Atlan's `columnTopValues` produces). Without it
    nothing is injected: inventing a literal would produce golden SQL that
    returns no rows, and an eval whose answer key is empty passes for the wrong
    reason. Reporting the candidate and skipping it is the honest failure.
    """
    samples: dict[str, list[str]] = {}
    for cols in schema.values():
        for c in cols:
            vals = [str(v) for v in (c.get("sample_values") or []) if str(v).strip()]
            if vals:
                samples.setdefault(c["column"], vals)

    # Intersect per table, never across the whole source. Two elements reading
    # different tables share no columns by construction, so a global
    # intersection is empty whenever a source covers more than one — which is
    # the normal case, and it silently produced no filters at all.
    groups: dict[str | None, list[str]] = {}
    for sql, page in zip(sqls, pages if pages is not None else [None] * len(sqls)):
        groups.setdefault(page, []).append(sql)

    found: dict[tuple[str | None, str], Filter] = {}
    for page, page_sqls in groups.items():
        for table, cols in schema.items():
            leaf = table.split(".")[-1].lower()
            columns = {c["column"] for c in cols}
            relevant = [s for s in page_sqls if leaf in s.lower()]
            if len(relevant) < 2:
                continue          # one element cannot corroborate a page filter
            for f in candidates_from_projections(relevant, columns, dialect):
                f.page = page
                found.setdefault((page, f.column), f)

    ready, unusable = [], []
    for f in found.values():
        if f.column in samples:
            # One value, not a spread: `select.shape_key` normalises literals,
            # so variants differing only by value dedupe back to a single
            # candidate. A second value buys nothing.
            f.values = samples[f.column][:1]
            ready.append(f)
        else:
            unusable.append(f.column if f.page is None else f"{f.column} ({f.page})")
    return ready, unusable


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------

def grouped_columns(sql: str, dialect: str | None = None) -> set[str]:
    """Columns the query groups by, at any level."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect or _run_dialect())
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for group in tree.find_all(exp.Group):
        out |= {c.name for c in group.find_all(exp.Column) if c.name}
    return out


_UNITS = {"day": "day", "week": "week", "month": "month",
          "quarter": "quarter", "year": "year"}


def _predicate(f: Filter) -> exp.Expression | None:
    """One filter -> one boolean expression, or None if it cannot be written."""
    col = exp.column(f.column)
    if f.kind == "categorical":
        return exp.In(this=col,
                      expressions=[exp.Literal.string(v) for v in f.values])
    if f.kind == "relative_date" and f.period:
        first, last, unit = f.period
        unit = _UNITS.get(unit.lower())
        if unit is None:
            return None
        # Anchored on CURRENT_DATE. `select.check_deterministic` treats that as
        # "needs an as-of anchor" rather than non-deterministic, which is the
        # right reading: the window is fixed once the record records `as_of`.
        lower = exp.func("DATEADD", exp.var(unit),
                         exp.Literal.number(int(first)), exp.func("CURRENT_DATE"))
        upper = exp.func("DATEADD", exp.var(unit),
                         exp.Literal.number(int(last)), exp.func("CURRENT_DATE"))
        return exp.and_(exp.GTE(this=col, expression=lower),
                        exp.LTE(this=col.copy(), expression=upper))
    return None


def _output_names(select: exp.Select) -> set[str]:
    """The column names a SELECT exposes to whatever reads from it."""
    names = set()
    for proj in select.args.get("expressions") or []:
        name = proj.alias_or_name
        if name:
            names.add(name.upper())
    return names


def _sources(select: exp.Select) -> list:
    """The FROM and JOIN sources of this SELECT, ignoring nested ones."""
    out = []
    # sqlglot renamed this arg to `from_` in 30.x; a miss here reads as "this
    # SELECT has no FROM", which silently narrows the scope to the joins alone.
    frm = select.args.get("from_") or select.args.get("from")
    if frm is not None:
        out.append(frm.this)
    for join in select.args.get("joins") or []:
        out.append(join.this)
    return out


def _scope(select: exp.Select, by_leaf: dict[str, set[str]],
           cte_outputs: dict[str, set[str]]) -> tuple[set[str], bool]:
    """Columns resolvable in this SELECT, and whether any source is unknown.

    A source is unknown when it is a physical table the schema does not
    describe. The caller may not conclude a column is out of scope from a
    partial picture, so that case is reported rather than folded in.
    """
    names: set[str] = set()
    unknown = False
    for source in _sources(select):
        if isinstance(source, exp.Table):
            leaf = (source.name or "").lower()
            if leaf in cte_outputs:
                names |= cte_outputs[leaf]
            elif leaf in by_leaf:
                names |= by_leaf[leaf]
            else:
                unknown = True
        elif isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
            names |= _output_names(source.this)
        else:
            unknown = True
    return names, unknown


def inject(sql: str, filters: list[Filter], dialect: str | None = None,
           schema: dict[str, list[dict]] | None = None) -> tuple[str, list[Filter]]:
    """Add each filter as a predicate on every scan of a base table.

    Returns the rewritten SQL and the filters actually applied. A filter is
    skipped, never silently mangled, when:

      * it has no value to bind -- an unset control constrains nothing;
      * it binds to a calculated field, whose formula this module will not
        guess at;
      * its column is in the GROUP BY. That last one matters more than it
        looks: filtering a dimension you also group by collapses the grain, and
        because `score_discriminating` counts the added filter without noticing
        what it destroyed, the degenerate variant *outranks* its own parent. On
        one workbook the highest-scoring candidate of all was a two-dimensional
        pivot reduced to a single row.

    With `schema`, a predicate is also skipped for any individual SELECT whose
    own FROM/JOIN scope cannot resolve the column. Touching a base table is not
    the same as having the column in scope: an outer SELECT reading a CTE that
    does not project it, joined to a table that does not carry it, passes the
    table test and still produces SQL the warehouse rejects with an invalid
    identifier. The check is one-sided -- it skips only when every source is
    known and none of them has the column -- so a partial schema costs recall,
    never correctness. Without `schema` the old behaviour stands.
    """
    grouped = grouped_columns(sql, dialect)
    try:
        tree = sqlglot.parse_one(sql, read=dialect or _run_dialect())
    except Exception:  # noqa: BLE001
        return sql, []

    by_leaf: dict[str, set[str]] = {}
    cte_outputs: dict[str, set[str]] = {}
    if schema:
        for table, cols in schema.items():
            leaf = table.split(".")[-1].lower()
            by_leaf.setdefault(leaf, set()).update(
                c["column"].upper() for c in cols if c.get("column"))
        for cte in tree.find_all(exp.CTE):
            if isinstance(cte.this, exp.Select):
                cte_outputs[(cte.alias or "").lower()] = _output_names(cte.this)

    applied: list[Filter] = []
    for f in filters:
        if not f.injectable or f.column in grouped:
            continue
        pred = _predicate(f)
        if pred is None:
            continue
        touched = False
        for select in tree.find_all(exp.Select):
            if select.find(exp.Table) is None:
                continue
            if schema:
                names, unknown = _scope(select, by_leaf, cte_outputs)
                if not unknown and f.column.upper() not in names:
                    continue
            select.where(pred.copy(), copy=False)
            touched = True
        if touched:
            applied.append(f)

    if not applied:
        return sql, []
    return tree.sql(dialect=dialect or _run_dialect()), applied


def filter_variants(base, filters: list, schema: dict[str, list[dict]] | None = None) -> list:
    """A filtered twin of `base` per applicable filter, plus one with them all.

    Bounded deliberately at N+1 rather than the full power set. Varying a
    filter's *value* is pointless -- `shape_key` normalises literals, so those
    variants dedupe back to one -- and the full set of column combinations grows
    a set that reads as far more varied than it is.
    """
    usable = [f for f in filters
              if f.injectable and f.column and f.column.lower() in base.sql.lower()]
    if not usable:
        return []

    groups = [[f] for f in usable]
    if len(usable) > 1:
        groups.append(usable)

    out, seen = [], {base.sql.strip()}
    for group in groups:
        sql, applied = inject(base.sql, group, schema=schema)
        if not applied or sql.strip() in seen:
            continue
        seen.add(sql.strip())
        out.append(type(base)(
            query_id=f"{base.query_id}+{'+'.join(f.column for f in applied)}",
            sql=sql,
            run_count=base.run_count,
            distinct_users=base.distinct_users,
            user_name=base.user_name,
            page=getattr(base, "page", None),
            element_type=getattr(base, "element_type", None),
            original_sql=getattr(base, "original_sql", None),
        ))
    return out


# ---------------------------------------------------------------------------
# Tiles -- a dashboard filter set to another value the catalog has seen
# ---------------------------------------------------------------------------
#
# A tile records its own filters, so nothing here is inferred. What a tile
# cannot say is which OTHER values its dashboard filter takes; Atlan's
# `columnTopValues` can, when the column has been profiled. Profiled values
# are often absent, so this path is mostly exercised by tests.

def _variable(f) -> bool:
    """Can a tile be varied on this filter? A dashboard filter card on one
    physical column, applied at a categorical value its SQL states. A source or
    element filter is part of what the tile means, and a range or relative date
    set to one top value would change the kind of question it is."""
    return (f.scope == "dashboard" and bool(f.column) and f.kind == "categorical"
            and f.applied and bool(f.expr))


def variant_filters(element) -> list:
    return [f for f in element.filters if _variable(f)]


def _top_values(raw) -> list[str]:
    """Values out of `columnTopValues`, most frequent first. Each entry is a
    ColumnValueFrequencyMap struct, with or without its `attributes` wrapper."""
    found = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        a = item["attributes"] if isinstance(item.get("attributes"), dict) else item
        value = a.get("columnValue", a.get("value"))
        freq = a.get("columnValueFrequency", a.get("frequency"))
        if value is not None and str(value).strip():
            found.append((-(freq if isinstance(freq, int | float) else 0), str(value)))
    return list(dict.fromkeys(v for _, v in sorted(found, key=lambda t: t[0])))


def fetch_top_values(column: str) -> list[str]:
    """Atlan's top values for one physical column, `[DB.]SCHEMA.TABLE.COLUMN`.

    The same name catalogued twice (a dev and a prod connection) yields only
    the values every copy lists, so a variant never filters on a value one of
    them lacks. Raises on a failed lookup; the caller decides what that costs.
    """
    from eval_builder import schema_check as sc

    parts = column.split(".")
    if len(parts) < 2:
        return []
    *qual, table, name = parts
    filters = [
        {"term": {"__typeName.keyword": "Column"}},
        {"term": {"__state": "ACTIVE"}},
        sc._ci("name.keyword", name),
        {"bool": {"should": [sc._ci("tableName.keyword", table),
                             sc._ci("viewName.keyword", table)],
                  "minimum_should_match": 1}},
    ]
    if len(qual) >= 1:
        filters.append(sc._ci("schemaName.keyword", qual[-1]))
    if len(qual) >= 2:
        filters.append(sc._ci("databaseName.keyword", qual[-2]))
    res = sc._search({"dsl": {"size": 20, "query": {"bool": {"filter": filters}}},
                      "attributes": ["name", "qualifiedName", "columnTopValues"]})
    copies = [vals for e in res.get("entities") or []
              if (vals := _top_values((e.get("attributes") or {}).get("columnTopValues")))]
    if not copies:
        return []
    common = set.intersection(*(set(v) for v in copies))
    return [v for v in copies[0] if v in common]


def _conjuncts(node: exp.Expression):
    node = node.unnest()
    if isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    else:
        yield node


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _swap_predicate(sql: str, expr: str, value: str,
                    dialect: str) -> tuple[str, str] | None:
    """`sql` with its outermost SELECT's predicate on `expr`'s column replaced
    by `column = value`, plus that predicate. None when the outermost SELECT
    does not filter on the column -- it is then applied deeper, inside custom
    SQL or a CTE, which is the datasource's meaning and not the viewer's pick."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect or _run_dialect())
        cols = list(sqlglot.parse_one(expr, read=dialect or _run_dialect()).find_all(exp.Column))
    except Exception:  # noqa: BLE001
        return None
    where = tree.args.get("where") if isinstance(tree, exp.Select) else None
    if where is None or len({c.name.lower() for c in cols}) != 1:
        return None
    ref = cols[0]

    def on_column(node: exp.Expression) -> bool:
        used = list(node.find_all(exp.Column))
        return bool(used) and node.find(exp.Literal) is not None and all(
            u.name.lower() == ref.name.lower()
            and (not u.table or not ref.table or u.table.lower() == ref.table.lower())
            for u in used)

    conjuncts = list(_conjuncts(where.this))
    hits = [c for c in conjuncts if on_column(c)]
    if not hits:
        return None
    numeric = all(not lit.is_string for h in hits for lit in h.find_all(exp.Literal))
    literal = (exp.Literal.number(value) if numeric and _is_number(value)
               else exp.Literal.string(value))
    pred = exp.EQ(this=hits[0].find(exp.Column).copy(), expression=literal)
    kept = [c for c in conjuncts if all(c is not h for h in hits)]
    tree.set("where", exp.Where(this=exp.and_(*kept, pred) if kept else pred))
    return tree.sql(dialect=dialect or _run_dialect()), pred.sql(dialect=dialect or _run_dialect())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tile_variants(base, top_values, per_filter: int = 2) -> list:
    """Twins of a tile with one dashboard filter set to another value.

    `top_values(column)` gives the values, most frequent first; the saved value
    is skipped and at most `per_filter` are taken per filter. Each twin carries
    a copy of the element with that filter's value set, and says so in a note.
    """
    el = base.element
    if el is None or not base.sql:
        return []
    out = []
    for i, f in enumerate(el.filters):
        if not _variable(f):
            continue
        made = 0
        for value in top_values(f.column) or []:
            if made >= per_filter:
                break
            if value in f.values:
                continue
            swapped = _swap_predicate(base.sql, f.expr, value, el.dialect)
            if swapped is None:
                break          # nothing to replace at the outermost SELECT
            sql, pred = swapped
            twin = copy.deepcopy(el)
            g = twin.filters[i]
            g.values, g.expr, g.state = [value], pred, "set"
            twin.sql = sql
            note = (f"variant: dashboard filter {f.label or f.column} set to {value!r}, "
                    f"one of Atlan's most frequent values for it (saved: "
                    f"{', '.join(f.values) or 'none'})")
            twin.notes = [*twin.notes, note]
            out.append(type(base)(
                query_id=f"{base.query_id}+{_slug(f.column)}={_slug(value)}",
                sql=sql, run_count=base.run_count, distinct_users=base.distinct_users,
                user_name=base.user_name, page=base.page, element_type=base.element_type,
                original_sql=base.original_sql, element=twin, notes=[note]))
            made += 1
    return out


def _run_dialect() -> str:
    from eval_builder import dialect as D

    return D.run()
