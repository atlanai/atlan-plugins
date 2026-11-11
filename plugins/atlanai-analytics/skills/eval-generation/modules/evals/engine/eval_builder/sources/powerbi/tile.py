"""One Power BI visual as a tile: what it displays, and the SQL that computes it.

The tile SQL is assembled from the semantic model, never from a captured
query (Power BI does not keep one):

    WITH <lookup tables as VALUES>,
         f0 AS (<the fact table's relation: native SQL or the warehouse table>),
         f  AS (SELECT f0.*, <calculated columns the tile reads> FROM f0 [WHERE <Power Query row filters>]),
         <one CTE per related table the tile reads>
    SELECT <dimensions>, <measures>
    FROM f [LEFT JOIN <related table> ON <relationship>]
    WHERE <report, page, slicer and visual filters, at their saved state>
    GROUP BY <dimensions>

The fact is the one table every measure aggregates. A filter or dimension on
another table reaches it only along an active many-to-one relationship from
the fact, as Power BI's filter propagation does; a filter on a table with no
such path does not affect the visual and is recorded, not applied. A calendar
table (CALENDAR/CALENDARAUTO) joined on its date is folded into the fact's own
date column, its attributes translated from their DAX.

Refused, with the reason: a measure outside the DAX subset (time
intelligence, context modifiers, SELECTEDVALUE), a measure over two facts,
TopN/relative-date/measure filters, custom and AI visuals, and anything whose
SQL does not parse in the source dialect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from eval_builder.elements import Dimension, ElementKind, FilterSpec, Measure, VisualElement
from eval_builder.sources.powerbi import dax
from eval_builder.sources.powerbi.dax import Lookup, Refuse
from eval_builder.sources.powerbi.model import Model
from eval_builder.sources.powerbi.mquery import MRefuse, Relation, relation
from eval_builder.sources.powerbi.report import Field, FilterDef, Page, Projection, Report, Visual

KINDS = {
    "card": ElementKind.KPI, "cardVisual": ElementKind.KPI, "kpi": ElementKind.KPI,
    "multiRowCard": ElementKind.SINGLE_VALUE, "gauge": ElementKind.GAUGE,
    "lineChart": ElementKind.LINE, "areaChart": ElementKind.AREA, "stackedAreaChart": ElementKind.AREA,
    "hundredPercentStackedAreaChart": ElementKind.AREA,
    "barChart": ElementKind.BAR, "clusteredBarChart": ElementKind.BAR, "columnChart": ElementKind.BAR,
    "clusteredColumnChart": ElementKind.BAR, "hundredPercentStackedBarChart": ElementKind.BAR,
    "hundredPercentStackedColumnChart": ElementKind.BAR, "ribbonChart": ElementKind.BAR,
    "lineClusteredColumnComboChart": ElementKind.COMBO, "lineStackedColumnComboChart": ElementKind.COMBO,
    "pieChart": ElementKind.PIE, "donutChart": ElementKind.DONUT, "funnel": ElementKind.FUNNEL,
    "waterfallChart": ElementKind.WATERFALL, "scatterChart": ElementKind.SCATTER,
    "map": ElementKind.MAP, "filledMap": ElementKind.MAP, "azureMap": ElementKind.MAP, "shapeMap": ElementKind.MAP,
    "tableEx": ElementKind.TABLE, "table": ElementKind.TABLE,
    "pivotTable": ElementKind.PIVOT, "matrix": ElementKind.PIVOT, "treemap": ElementKind.UNKNOWN,
}
NON_DATA = {"slicer", "advancedSlicerVisual", "listSlicer", "textSlicer", "textbox", "image", "shape",
            "basicShape", "actionButton", "pageNavigator", "bookmarkNavigator", "group"}
AI_VISUALS = {"decompositionTreeVisual", "keyDriversVisual", "qnaVisual", "smartNarrative",
              "aiNarratives", "scriptVisual", "pythonVisual", "rVisual"}

DIM_ROLES = {"Category", "Series", "Legend", "Rows", "Columns", "Axis", "Details", "Group",
             "Location", "Latitude", "Longitude", "Breakdown"}
TARGET_ROLES = {"TargetValue", "Goal", "Target"}
HELPER_ROLES = {"Tooltips", "MinValue", "MaxValue", "TrendLine"}
# everything else that carries a number: Y, Y2, Values, Data, Size, X, Indicator, ColumnY, LineY …

_CMP = {0: "=", 1: ">", 2: ">=", 3: "<", 4: "<="}
_BUCKET = {"Year": "year", "Quarter": "quarter", "Month": "month", "Day": "day"}


# Every fragment this adapter writes is in one house dialect, and the finished
# tile is rendered once into the warehouse's dialect by sqlglot. Customer SQL
# (a Power Query native query) is never part of that: it sits in a placeholder
# while the tile is rendered and is put back verbatim.
HOUSE = "databricks"
_NATIVE = re.compile(r'SELECT \* FROM [`"\[]?__NATIVE_(\d+)__[`"\]]?', re.I)


def q(name: str, dialect: str = HOUSE) -> str:
    return exp.to_identifier(name, quoted=True).sql(dialect=dialect)


def needs_quotes(name: str, dialect: str) -> bool:
    """Whether a catalogued name must stay quoted in `dialect` to mean itself:
    a space or symbol, a keyword, or a case the dialect would fold."""
    from sqlglot.dialects.dialect import Dialect, NormalizationStrategy

    d = Dialect.get_or_raise(dialect)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name.upper() in {
            k.upper() for k in d.tokenizer_class.KEYWORDS}:
        return True
    strategy = d.NORMALIZATION_STRATEGY
    if strategy == NormalizationStrategy.UPPERCASE:
        return name != name.upper()
    if strategy == NormalizationStrategy.LOWERCASE:
        return name != name.lower()
    return False                                # case-insensitive (or case-sensitive and kept as written)


def render(house_sql: str, target: str, natives: dict[int, str] | None = None, expression: bool = False) -> str:
    """House SQL -> `target`, quoting a name only where the target needs it,
    with each native query put back verbatim."""
    tree = sqlglot.parse_one(house_sql, read=HOUSE)
    for ident in tree.find_all(exp.Identifier):
        if ident.args.get("quoted") and not ident.name.upper().startswith("__NATIVE_") \
                and not needs_quotes(ident.name, target):
            ident.set("quoted", False)
    out = tree.sql(dialect=target)
    for i, sql in (natives or {}).items():
        out, n = _NATIVE.subn(lambda m, sql=sql, i=i: f"\n{sql}\n" if m.group(1) == str(i) else m.group(0), out)
        if not n:
            raise Refuse("invalid tile SQL: the native query placeholder was lost in rendering")
    return out


@dataclass
class _Join:
    alias: str
    table: str
    on: str
    cte: str | None = None                      # the CTE body, when the table needs one


@dataclass
class _Scope:
    """What one tile's SQL has to define: calculated columns on the fact,
    joins to related tables, lookup CTEs."""
    fact: str
    calc: dict[str, str] = field(default_factory=dict)       # fact calc column -> row SQL over f0
    joins: dict[str, _Join] = field(default_factory=dict)    # table -> join
    lookups: dict[str, str] = field(default_factory=dict)    # lookup table -> CTE name
    physical: set[str] = field(default_factory=set)
    natives: dict[int, str] = field(default_factory=dict)    # placeholder id -> verbatim native SQL
    native_ids: dict[str, int] = field(default_factory=dict)


class Builder:
    def __init__(self, model: Model, report: Report, workbook: str, workbook_id: str,
                 dialect: str | None = None, origin: str = "powerbi"):
        self.m, self.r, self.wb, self.wb_id = model, report, workbook, workbook_id
        self.dialect_override, self.origin = dialect, origin
        self._rel: dict[str, Relation | str] = {}
        from eval_builder.sources.powerbi.mquery import parameters
        self.params_m = parameters(model.expressions)
        self.lookups = {}
        self.calendars = {}
        self.params = {}
        for t in model.tables.values():
            if t.dax:
                dt = dax.datatable(t.dax)
                if dt:
                    self.lookups[t.name] = dt
                    continue
                cal = dax.date_table(t.dax)
                if cal is not None:
                    self.calendars[t.name] = cal
                    continue
                fp = dax.field_parameter(t.dax)
                if fp:
                    self.params[t.name] = fp

    # -- relations -------------------------------------------------------

    def relation(self, table: str) -> Relation:
        if table not in self._rel:
            t = self.m.table(table)
            try:
                if t is None:
                    raise MRefuse(f"table {table} is not in the model")
                if t.dax is not None and not t.m:
                    raise MRefuse(f"calculated table {table} (DAX) is not a warehouse relation")
                rel = relation(t.m or "", lambda name, _d: q(name, HOUSE), self.params_m)
                if self.dialect_override:
                    rel.dialect = self.dialect_override
                if rel.kind == "native":
                    try:
                        tree = sqlglot.parse_one(rel.sql, read=rel.dialect)
                        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
                        rel.tables = sorted({".".join(p for p in (x.catalog, x.db, x.name) if p)
                                             for x in tree.find_all(exp.Table) if x.name.lower() not in ctes})
                    except Exception:  # noqa: BLE001 - the tile check catches unparseable SQL
                        pass
                self._rel[table] = rel
            except MRefuse as e:
                self._rel[table] = str(e)
        r = self._rel[table]
        if isinstance(r, str):
            raise Refuse(r)
        return r

    def _phys(self, table: str, name: str) -> str:
        try:
            return self.relation(table).physical(name)
        except MRefuse as e:
            raise Refuse(str(e)) from None

    def _many_to_one(self, fact: str, table: str):
        return next((r for r in self.m.relationships if r.active and r.from_table == fact
                     and r.to_table == table), None)

    # -- the tile --------------------------------------------------------

    def build(self, page: Page, v: Visual) -> VisualElement | None:
        if v.type in NON_DATA or not v.roles:
            return None
        kind = KINDS.get(v.type)
        el = VisualElement(tool="powerbi", workbook=self.wb, page=page.name,
                           element_id=f"{self.wb_id}:{page.id}:{v.id}", title=v.title or "",
                           kind=kind or ElementKind.UNKNOWN, raw_kind=v.type, workbook_id=self.wb_id,
                           shape=f"powerbi:{v.type}")
        el.provenance = {"report_format": self.r.format, "model_origin": self.m.origin,
                         "page_id": page.id, "visual_id": v.id}
        try:
            if v.type in AI_VISUALS:
                raise Refuse(f"AI visual: {v.type}")
            if kind is None:
                raise Refuse(f"custom visual: {v.type}")
            self._build(page, v, el)
        except Refuse as e:
            el.refusal, el.sql = str(e), None
        if not el.title:
            el.title = ", ".join(m.label for m in el.displayed_measures) or v.type
        return el

    def _build(self, page: Page, v: Visual, el: VisualElement) -> None:
        roles = self._resolve_parameters(page, v, el)
        meas, dims, helpers = [], [], []
        for role, projs in roles.items():
            for p in projs:
                if not p.active:
                    continue
                f = p.field
                in_table_values = el.kind in (ElementKind.TABLE,) and role == "Values"
                if f.kind in ("column", "datelevel") and (role in DIM_ROLES or in_table_values):
                    dims.append(p)
                elif f.kind in ("measure", "aggregation", "column"):
                    meas.append((role, p))
                else:
                    raise Refuse(f"unsupported field {f.ref} in role {role}")
        # Measures first: they fix the fact table.
        translated = []
        fact = None
        for role, p in meas:
            r = "target" if role in TARGET_ROLES else "helper" if role in HELPER_ROLES else "value"
            try:
                expr, f_tab, refs = self._measure(p.field, fact)
            except Refuse as e:
                if r == "helper" or str(e).startswith("display measure"):
                    el.notes.append(f"left out {p.label!r}: {e}")
                    continue
                raise
            fact = fact or f_tab
            translated.append((p, r, expr, refs))
        if not translated:
            raise Refuse("decorative tile (no measure)")
        scope = _Scope(fact)
        target = self.relation(fact).dialect
        dialect = HOUSE                         # every fragment is written in the house dialect
        el.dialect = target
        for p, r, expr, refs in translated:
            sql = self._bind(expr, scope, dialect)
            el.measures.append(Measure(label=p.label, expr=sql, role=r,
                                       agg=p.field.func if p.field.kind == "aggregation" else "CUSTOM",
                                       source_ref=p.field.ref,
                                       description=self._dax_of(p.field)))
        for p in dims:
            sql, bucket = self._dimension(p.field, scope, dialect)
            el.dimensions.append(Dimension(label=p.label, expr=sql, bucket=bucket, source_ref=p.field.ref,
                                           axis=next((rl.lower() for rl, ps in roles.items() if p in ps), None)))
        el.filters = self._filters(page, v, scope, dialect, el)
        house, natives = self._assemble(el, scope, dialect)
        try:
            el.sql = render(house, target, natives)
            for part in el.measures + el.dimensions:
                part.expr = render(part.expr, target, expression=True)
            for f in el.filters:
                if f.expr:
                    f.expr = render(f.expr, target, expression=True)
        except Refuse:
            raise
        except Exception as e:  # noqa: BLE001
            raise Refuse(f"invalid tile SQL: does not render in {target}: {str(e).splitlines()[0][:80]}")
        rel = self.relation(fact)
        el.original_sql = rel.sql
        el.source_relation = rel.tables
        if len(rel.tables) == 1:
            for m_ in el.measures:
                m_.physical_columns = sorted(f"{rel.tables[0]}.{c}" for c in scope.physical)
        el.provenance.update({"fact_table": fact, "relation_kind": rel.kind,
                              "measures_inlined": sorted({x for _, _, _, refs in translated for x in refs})})
        try:
            tree = sqlglot.parse_one(el.sql, read=target)
            if isinstance(tree, exp.Command):
                raise ValueError("parsed as a command")
        except Exception as e:  # noqa: BLE001
            raise Refuse(f"invalid tile SQL: {str(e).splitlines()[0][:100]}")

    # -- field parameters ------------------------------------------------

    def _resolve_parameters(self, page: Page, v: Visual, el: VisualElement) -> dict[str, list[Projection]]:
        """A role bound to a field parameter shows the field its slicer selects."""
        roles = {k: list(ps) for k, ps in v.roles.items()}
        for role in v.field_parameter_roles:
            chosen = None
            for s in page.visuals:
                sf = s.slicer_filter
                if sf and sf.field and sf.field.table in self.params and sf.body:
                    vals = _in_values(sf.body)
                    picked = []
                    for val in vals or []:
                        text = val[1:-1].replace("''", "'") if val.startswith("'") and val.endswith("'") else val
                        try:
                            picked.append(dax.split_col(text))
                        except Refuse:
                            pass
                    if picked:
                        labels = {tc: lab for lab, tc in self.params[sf.field.table].items()}
                        chosen = [Projection(Field("column", t, c), labels.get((t, c), c)) for t, c in picked]
            if chosen:
                roles[role] = chosen
                el.notes.append(f"{role} is a field parameter; its slicer selects "
                                + ", ".join(p.field.ref for p in chosen))
            else:
                el.notes.append(f"{role} is a field parameter with no saved selection; the saved axis is used")
        return roles

    # -- measures --------------------------------------------------------

    def _dax_of(self, f: Field) -> str | None:
        return self.m.measure(f.name) if f.kind == "measure" else None

    def _measure(self, f: Field, fact: str | None) -> tuple[str, str, list[str]]:
        """(SQL with {col:T|C} placeholders, fact table, measures inlined)."""
        if f.kind == "aggregation" or f.kind == "column":
            func = f.func
            if f.kind == "column":
                col = self._column(f.table, f.name)
                sb = (col.summarize_by or "").lower() if col else ""
                func = {"sum": "SUM", "average": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
                        "distinctcount": "COUNT_DISTINCT"}.get(sb)
                if func is None:
                    raise Refuse(f"unsupported field {f.ref}: a column as a value with no default aggregation")
            sqlf = {"SUM": "SUM", "AVG": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT",
                    "COUNT_DISTINCT": "COUNT(DISTINCT"}.get(func)
            if sqlf is None:
                raise Refuse(f"DAX: implicit aggregation {func} on {f.ref}")
            ph = _ph(f.table, f.name)
            home = self.m.table(f.table)
            if home is None:
                raise Refuse(f"unresolved field: table {f.table}")
            if fact and home.name != fact:
                raise Refuse(f"measures span tables ({fact}, {home.name})")
            return (f"COUNT(DISTINCT {ph})" if func == "COUNT_DISTINCT" else f"{sqlf}({ph})"), home.name, []
        src = self.m.measure(f.name)
        if src is None:
            raise Refuse(f"unresolved field: measure [{f.name}] is not in the model")
        node = dax.parse(src)
        if dax.returns_text(node):
            raise Refuse("display measure: returns text")
        em = dax.MeasureEmitter(_Placeholders(self), lambda t: t)
        em.home.append(self.m.measure_home(f.name))
        sql = em.emit(node)
        if em.fact is None:
            raise Refuse(f"display measure: [{f.name}] is a constant (an axis bound or a spacer)")
        home = self.m.table(em.fact)
        if home is None:
            raise Refuse(f"unresolved field: table {em.fact}")
        if fact and home.name != fact:
            raise Refuse(f"measures span tables ({fact}, {home.name})")
        return sql, home.name, em.refs

    def _column(self, table: str, name: str):
        return self.m.column(table, name)

    # -- binding placeholders to the fact scope ---------------------------

    def _bind(self, sql: str, scope: _Scope, dialect: str) -> str:
        def sub(m):
            return self._col_sql(m.group(1), m.group(2), scope, dialect)
        return re.sub(r"\{col:([^|}]*)\|([^}]*)\}", sub, sql)

    def _col_sql(self, table: str, name: str, scope: _Scope, dialect: str) -> str:
        t = self.m.table(table)
        if t is None:
            raise Refuse(f"unresolved field: table {table}")
        table = t.name
        if table == scope.fact:
            col = self.m.column(table, name)
            if col is None:
                raise Refuse(f"unresolved field: {table}[{name}]")
            if col.calculated:
                if name not in scope.calc:
                    scope.calc[name] = ""            # reserve: a self-reference refuses below
                    row = dax.RowEmitter(_StageResolver(self, scope, dialect)).emit(dax.parse(col.expression))
                    scope.calc[name] = row
                return f"f.{q(name, dialect)}"
            phys = self._phys(table, col.source_column or col.name)
            scope.physical.add(phys)
            return f"f.{q(phys, dialect)}"
        rel = self._many_to_one(scope.fact, table)
        if rel is None:
            raise Refuse(f"relationships: no active many-to-one path from {scope.fact} to {table}")
        if table in self.calendars:
            key = self._col_sql(scope.fact, rel.from_column, scope, dialect)
            if name.lower() == rel.to_column.lower():
                return key
            attr = next((a for n, a in self.calendars[table].items() if n.lower() == name.lower()), None)
            if attr is None:
                col = self.m.column(table, name)
                if col is None or not col.calculated:
                    raise Refuse(f"unresolved field: {table}[{name}]")
                attr = dax.parse(col.expression)
            return dax.RowEmitter(_CalendarResolver(table, rel.to_column, key)).emit(attr)
        j = self._join(table, rel, scope, dialect)
        if table in self.lookups:
            if name not in self.lookups[table][0]:
                raise Refuse(f"unresolved field: {table}[{name}]")
            return f"{j.alias}.{q(name, dialect)}"
        col = self.m.column(table, name)
        if col is None:
            raise Refuse(f"unresolved field: {table}[{name}]")
        if col.calculated:
            raise Refuse(f"calculated column {table}[{name}] on a related table")
        return f"{j.alias}.{q(self._phys(table, col.source_column or col.name), dialect)}"

    def _native_ref(self, table: str, r: Relation, scope: _Scope) -> str:
        """The relation as house SQL: a navigated table is ours to write; a
        native query is the customer's, so it becomes a placeholder."""
        if r.kind != "native":
            return r.sql
        if table not in scope.native_ids:
            scope.native_ids[table] = len(scope.natives)
            scope.natives[scope.native_ids[table]] = r.sql
        return f"SELECT * FROM __NATIVE_{scope.native_ids[table]}__"

    def _join(self, table: str, rel, scope: _Scope, dialect: str) -> _Join:
        if table in scope.joins:
            return scope.joins[table]
        alias = f"d{len(scope.joins) + 1}"
        fk = self._col_sql(scope.fact, rel.from_column, scope, dialect)
        if table in self.lookups:
            body = _values_cte(*self.lookups[table], dialect)
            to = q(rel.to_column, dialect)
        else:
            r = self.relation(table)
            fact_dialect = self.relation(scope.fact).dialect
            if r.dialect != fact_dialect:
                raise Refuse(f"relationships: {table} is in {r.dialect}, the fact in {fact_dialect}")
            src = self._native_ref(table, r, scope)
            body = src + (f" WHERE {' AND '.join(r.where)}" if r.where else "")
            col = self._column(table, rel.to_column)
            to = q(self._phys(table, (col.source_column if col else None) or rel.to_column), dialect)
        j = _Join(alias, table, f"{fk} = {alias}.{to}", body)
        scope.joins[table] = j
        return j

    def _dimension(self, f: Field, scope: _Scope, dialect: str) -> tuple[str, str | None]:
        if f.kind == "datelevel":
            base = self._col_sql(f.table, f.name, scope, dialect)
            unit = _BUCKET.get(f.func or "")
            if unit is None:
                raise Refuse(f"unsupported field: date level {f.func}")
            return (base if unit == "day" else f"TRUNC(CAST({base} AS DATE), '{unit.upper()}')"), unit
        sql = self._col_sql(f.table, f.name, scope, dialect)
        bucket = next((u for u in ("month", "quarter", "year", "week") if f"'{u.upper()}')" in sql and "TRUNC(" in sql), None)
        return sql, bucket

    # -- filters ---------------------------------------------------------

    def _filters(self, page: Page, v: Visual, scope: _Scope, dialect: str, el: VisualElement) -> list[FilterSpec]:
        defs = list(self.r.filters) + list(page.filters)
        for s in page.visuals:
            if s.slicer_filter is not None and s is not v:
                defs.append(s.slicer_filter)
        defs += v.filters
        out = []
        for d in defs:
            label = d.field.ref if d.field else d.name
            spec = FilterSpec(expr=None, label=label, scope={"report": "workbook", "page": "page",
                                                             "slicer": "page", "visual": "element"}[d.scope],
                              origin=f"powerbi-{self.r.format}", kind=_kind(d), applied=False,
                              state="set" if d.body else "unset")
            if d.field and d.field.table in self.params:
                continue                                    # a field-parameter slicer picks fields, not rows
            if not d.body:
                out.append(spec)
                continue
            if d.type in ("TopN", "RelativeDate", "RelativeTime", "Tuple"):
                raise Refuse(f"unsupported filter: {d.type} on {label}")
            try:
                expr, values = _condition(d.body, lambda t, c: self._col_sql(t, c, scope, dialect))
            except Refuse as e:
                if str(e).startswith("relationships:"):
                    spec.applied = False
                    spec.expr = None
                    el.notes.append(f"filter on {label} does not reach this visual (no relationship to {scope.fact})")
                    out.append(spec)
                    continue
                raise
            # isInvertedSelectionMode is how the card displays; the condition
            # already says NOT IN, so it is never applied a second time.
            spec.expr, spec.values, spec.applied = expr, values, True
            if d.field and d.field.kind == "column":
                spec.column = d.field.ref
            out.append(spec)
        return out

    # -- assembly --------------------------------------------------------

    def _assemble(self, el: VisualElement, scope: _Scope, dialect: str) -> str:
        rel = self.relation(scope.fact)
        ctes = [f"{name} AS ({_values_cte(*self.lookups[t], dialect)})" for t, name in scope.lookups.items()]
        ctes.append(f"f0 AS ({self._native_ref(scope.fact, rel, scope)})")
        calc = "".join(f", {expr} AS {q(c, dialect)}" for c, expr in scope.calc.items())
        where = f" WHERE {' AND '.join(rel.where)}" if rel.where else ""
        ctes.append(f"f AS (SELECT f0.*{calc} FROM f0{where})")
        joins = ""
        for j in scope.joins.values():
            ctes.append(f"{j.alias} AS ({j.cte})")
            joins += f"\nLEFT JOIN {j.alias} ON {j.on}"
        sel = [f"{d.expr} AS {q(d.label, dialect)}" for d in el.dimensions]
        sel += [f"{m.expr} AS {q(m.label, dialect)}" for m in el.measures]
        preds = [f.expr for f in el.filters if f.applied and f.expr]
        sql = f"WITH {', '.join(ctes)}\nSELECT {', '.join(sel)}\nFROM f{joins}"
        if preds:
            sql += "\nWHERE " + "\n  AND ".join(preds)
        if el.dimensions:
            sql += "\nGROUP BY " + ", ".join(d.expr for d in el.dimensions)
        return sql, dict(scope.natives)


# ---------------------------------------------------------------- resolvers

def _ph(table: str, col: str) -> str:
    return f"{{col:{table}|{col}}}"


class _Placeholders:
    """Measure translation runs before the fact is known, so columns become
    placeholders the tile binds once it is."""

    def __init__(self, b: Builder):
        self.b = b

    def column(self, table, column):
        return _ph(table, column)

    def measure(self, name):
        return self.b.m.measure(name)

    def measure_home(self, name):
        return self.b.m.measure_home(name)

    def count_rows(self, table):
        return None

    def lookup(self, table):
        return None


class _StageResolver:
    """Row context of a fact calculated column: columns of the fact read from
    f0, lookups read from their CTEs."""

    def __init__(self, b: Builder, scope: _Scope, dialect: str):
        self.b, self.scope, self.dialect = b, scope, dialect

    def column(self, table, column):
        t = self.b.m.table(table)
        if t is None or t.name != self.scope.fact:
            raise Refuse(f"calculated column reads another table ({table}) outside a lookup")
        col = self.b.m.column(table, column)
        if col is None:
            raise Refuse(f"unresolved field: {table}[{column}]")
        if col.calculated:
            if self.scope.calc.get(column) == "":
                raise Refuse(f"calculated column {table}[{column}] refers to itself")
            return "(" + dax.RowEmitter(self).emit(dax.parse(col.expression)) + ")"
        phys = self.b._phys(t.name, col.source_column or col.name)
        self.scope.physical.add(phys)
        return f"f0.{q(phys, self.dialect)}"

    def measure(self, name):
        return None

    def count_rows(self, table):
        raise Refuse("COUNTROWS in a calculated column")

    def lookup(self, table):
        t = self.b.m.table(table)
        if t is None or t.name not in self.b.lookups:
            return None
        cols = self.b.lookups[t.name][0]
        name = self.scope.lookups.setdefault(t.name, f"lk{len(self.scope.lookups) + 1}")
        alias = f"{name}_r"
        dialect = self.dialect

        def column(c):
            if c not in cols:
                raise Refuse(f"unresolved field: {table}[{c}]")
            return f"{alias}.{q(c, dialect)}"
        return Lookup(cte=name, column=column, alias=alias)


class _CalendarResolver:
    """A calendar attribute's DAX, with the calendar's date as the fact's."""

    def __init__(self, table: str, date_col: str, key_sql: str):
        self.table, self.date_col, self.key = table, date_col, key_sql

    def column(self, table, column):
        if table.lower() == self.table.lower() and column.lower() == self.date_col.lower():
            return self.key
        raise Refuse(f"calendar attribute reads {table}[{column}]")

    def bare(self, name):
        if name.lower() == self.date_col.lower():
            return self.key
        raise Refuse(f"calendar attribute reads [{name}], not the calendar date")

    def measure(self, name):
        return None

    def count_rows(self, table):
        raise Refuse("COUNTROWS in a calendar attribute")

    def lookup(self, table):
        return None


# ---------------------------------------------------------------- filters (semantic query)

def _kind(d: FilterDef) -> str:
    return {"Categorical": "categorical", "Advanced": "predicate", "RelativeDate": "relative_date",
            "TopN": "top_n", "Slicer": "categorical"}.get(d.type, "predicate")


def _in_values(body: dict) -> list[str] | None:
    try:
        cond = body["Where"][0]["Condition"]["In"]
        return [row[0]["Literal"]["Value"] for row in cond["Values"]]
    except (KeyError, IndexError, TypeError):
        return None


def _literal(v: str) -> str:
    if v == "null":
        return "NULL"
    if v in ("true", "false"):
        return v.upper()
    m = re.fullmatch(r"datetime'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?'", v)
    if m:
        return f"DATE '{m.group(1)}'" if m.group(2) == "00:00:00" else f"TIMESTAMP '{m.group(1)} {m.group(2)}'"
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)[LDM]?", v)
    if m:
        return m.group(1)
    if v.startswith("'") and v.endswith("'"):
        return v
    raise Refuse(f"unsupported filter: literal {v[:30]}")


def _condition(body: dict, col) -> tuple[str, list[str]]:
    """{From, Where} -> (SQL predicate, the literal values it names)."""
    aliases = {f["Name"]: f["Entity"] for f in body.get("From", []) if "Entity" in f}
    values: list[str] = []

    def field_sql(e: dict) -> str:
        if "Measure" in e or "Aggregation" in e:
            raise Refuse("unsupported filter: a filter on a measure")
        if "Column" not in e:
            raise Refuse(f"unsupported filter: expression {next(iter(e), '?')}")
        src = e["Column"]["Expression"].get("SourceRef", {})
        table = src.get("Entity") or aliases.get(src.get("Source", ""))
        if not table:
            raise Refuse("unsupported filter: unresolved source")
        return col(table, e["Column"]["Property"])

    def lit_of(e: dict) -> str:
        if "Literal" not in e:
            raise Refuse("unsupported filter: a comparison to a non-literal")
        s = _literal(e["Literal"]["Value"])
        values.append(s)
        return s

    def go(c: dict) -> str:
        if "In" in c:
            exprs = c["In"]["Expressions"]
            if len(exprs) != 1:
                raise Refuse("unsupported filter: a multi-column IN")
            target = field_sql(exprs[0])
            vals = [lit_of(row[0]) for row in c["In"]["Values"]]
            if any(x in ("TRUE", "FALSE") for x in vals):
                target = f"CAST({target} AS BOOLEAN)"   # DAX compares TRUE with 1; SQL needs the cast
            present = [x for x in vals if x != "NULL"]
            parts = [f"{target} IN ({', '.join(present)})"] if present else []
            if len(present) != len(vals):
                parts.append(f"{target} IS NULL")
            return "(" + " OR ".join(parts) + ")"
        if "Not" in c:
            return f"(NOT {go(c['Not']['Expression'])})"
        if "And" in c:
            return f"({go(c['And']['Left'])} AND {go(c['And']['Right'])})"
        if "Or" in c:
            return f"({go(c['Or']['Left'])} OR {go(c['Or']['Right'])})"
        if "Comparison" in c:
            k = c["Comparison"]
            op = _CMP.get(k.get("ComparisonKind"))
            if op is None:
                raise Refuse(f"unsupported filter: comparison kind {k.get('ComparisonKind')}")
            return f"({field_sql(k['Left'])} {op} {lit_of(k['Right'])})"
        for key, pat in (("Contains", "CONCAT('%', {v}, '%')"), ("StartsWith", "CONCAT({v}, '%')"),
                         ("EndsWith", "CONCAT('%', {v})")):
            if key in c:
                return f"({field_sql(c[key]['Left'])} LIKE {pat.format(v=lit_of(c[key]['Right']))})"
        raise Refuse(f"unsupported filter: condition {next(iter(c), '?')}")

    where = body.get("Where") or []
    if not where:
        raise Refuse("unsupported filter: empty")
    return " AND ".join(go(w["Condition"]) for w in where), values


def _values_cte(cols: list[str], rows: list[list[str]], dialect: str) -> str:
    """A DATATABLE as portable SQL: one SELECT per row, UNION ALL."""
    return " UNION ALL ".join(
        "SELECT " + ", ".join(f"{v} AS {q(c, dialect)}" for c, v in zip(cols, row)) for row in rows)
