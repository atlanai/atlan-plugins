# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""One measure to one statement, by the measure's form: plain, blended, detached, TREATAS, composite, member-grain, window and rank. Part of bicompile; see __init__.py."""
from __future__ import annotations

import re
from typing import Any
from metrics_agent.lib import declared as _declared
from metrics_agent.lib import slugs
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

from .common import (  # noqa: F401
    CompileError,
    Result,
    _calls_table_calc,
)

# --------------------------------------------------------------------------- statements
def _literal(value: Any, dialect: str | None = None) -> str:
    """One scalar literal. A LIST is not a scalar and is never stringified here.

    `str(['AW25', 'SS25'])` yields `"['AW25', 'SS25']"`, which quotes into the single garbage
    literal `'[''AW25'', ''SS25'']'`. That SQL runs, matches nothing, and reports a number —
    the precise failure this layer exists to prevent, and one no syntax check would catch.
    """
    if value is None:
        return "NULL"
    if isinstance(value, (list, tuple, set)):
        raise CompileError("a multi-value filter is not a scalar literal")
    text = str(value)
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    return _quote(text, dialect)


def _literal_list(values: Any, dialect: str | None = None) -> str:
    """The comma-separated body of an `IN (...)`. Order preserved, duplicates kept: the
    widget's own list is the evidence, not a set we tidied."""
    items = list(values) if isinstance(values, (list, tuple, set)) else [values]
    return ", ".join(_literal(v, dialect) for v in items)


# DAX functions that aggregate rows — the ones a CALCULATE filter gates. Language semantics,
# not a tunable: which functions aggregate is a fact about DAX.
_DAX_AGGREGATES = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "COUNTROWS",
                   "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "MEDIAN", "STDEV.P", "VAR.P",
                   "COUNTBLANK"}

_SIMPLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _ident(name: str) -> str:
    """A column name as SQL text: bare when it is a plain LOWERCASE identifier, double-quoted
    (and case-preserved) otherwise. `Account ID` and `Continuity %` are not legal bare
    identifiers; `Pod` is, but a bare name folds to POD, and a query that aliased the column
    as the quoted `"Pod"` has no column POD — so any uppercase letter means quote."""
    if _SIMPLE_IDENT.match(name) and name == name.lower():
        return name
    return '"' + name.replace('"', '""') + '"'


def _legal(name: str) -> str:
    """A column name as written when it is a legal bare identifier, else double-quoted.
    Unlike `_ident` it never quotes a bare name for its case (`ACCOUNT_ID` stays as written,
    folding as the warehouse folds it); it only quotes what cannot parse bare (`Close Date`,
    `ai maturity level`), which before was emitted as is and refused as unparseable SQL."""
    return name if _SIMPLE_IDENT.match(name) else '"' + name.replace('"', '""') + '"'


def _measure_alias(name: str) -> str:
    """A measure's column alias: `slugs.column_alias` (a symbol kept as a word)."""
    return slugs.column_alias(name)


def _native_colmap(columns: dict) -> dict[str, str]:
    """A native-query table's columns, by lower-cased name: each model caption, and each of the
    query's own output columns under its own name.

    A2 names a column by its warehouse name once a Power Query rename is reversed (`tool_type`
    for the caption `Tool Type`). For a native-query table that name is the query's output
    column, so it must resolve as surely as the caption does; matching captions alone refused
    22 of 53 statements on a live model. Where the two spellings collide, the caption wins."""
    colmap = {k.lower(): v for k, v in columns.items()}
    for v in columns.values():
        colmap.setdefault(str(v).lower(), v)
    return colmap


def _compile_unit(unit: dict, rules, dialect: str, *, alias: str = "t",
                 columns: dict[str, set[str]] | None = None,
                 measures: dict[str, str] | None = None,
                 table_of: dict[str, str] | None = None,
                 logical: dict[str, str] | None = None,
                 model: "_declared.Model | None" = None,
                 model_graph: list[dict] | None = None,
                 types: dict[tuple[str, str], str] | None = None,
                 row_formulas: set[str] | None = None, text_key: bool | str = False,
                 renames: dict[tuple[str, str], str] | None = None) -> Result:
    """One question unit -> one statement, or a named reason why not.

    `table_sql` is each table the reader gave a statement of its own (`source_lineage.
    statement`: the rows the BI tool's query steps leave), by its name: the statement reads it
    as that statement wherever it reads the table, joins included. `renames` maps (warehouse
    table, bare, lower; model column, lower) to the column the table reads, where a formula
    names a column by another name than the table's.

    `model_graph` is every relationship of the model, on warehouse tables: a measure may read
    a column of a table the unit itself does not (a CALCULATE filter on a dimension), and the
    unit's own graph holds only the edges its grouping and filters need. `types` is each
    column's data type by (model table, column), lower; `row_formulas` the keys of `measures`
    that are calculated columns (row expressions) rather than measures. `text_key`: the model
    compares text without case and without a column's trailing spaces, so a filter on text
    values does too.

    `columns` is the resolved warehouse schema per table, which the join planner needs to know
    WHERE a grouping column lives. Passed in rather than read off the unit because a
    regression case must be able to pin it: a case whose join plan depends on a schema it does
    not carry would pass or fail for reasons invisible in the case file.

    `table_of` maps a model table name (lower) to the warehouse table it reads. With it, a
    measure that references a column of a DIFFERENT warehouse table is refused rather than
    bound to this statement's alias; without it, behaviour is unchanged.

    `model` is the BI model a `formula_language: sql` measure is placed in (lib/declared): its
    roles, the names its formulas use and its relationships. A regression case states the
    same as `logical` (role -> warehouse table) beside the unit's join graph. No other
    language reads either.
    """
    columns = {k: {c.lower() for c in v} for k, v in (columns or {}).items()}
    if table_of:
        # An edge end the unit does not read keeps the model's table name (A2 rewrites only
        # what the unit reads), while a formula's table resolves through `table_of`: matched
        # on the bare name, `Date` never met `d_date_vw` and a prior-year lookup through the
        # date table found no key. Both ends are named as the warehouse reads them.
        unit = {**unit, "join_graph": _physical_edges(unit.get("join_graph") or [], table_of)}
        if model_graph is not None:
            model_graph = _physical_edges(model_graph, table_of)
    if renames:
        # A relationship names its columns as the model shows them; the statement joins on
        # the columns the tables read. A table whose model column differs from what it reads
        # (a calculated table's `Online Flag`, read from its statement as `Online_Flag`)
        # joined as `j1.Online Flag = t.Online_Flag`, which does not parse.
        unit = {**unit, "join_graph": _renamed_edges(unit.get("join_graph") or [], renames)}
        if model_graph is not None:
            model_graph = _renamed_edges(model_graph, renames)
    # `text_key="source"`: the unit's rows are read from the source on every query
    # (DirectQuery), which compares and groups text itself; nothing is folded, not even a
    # DAX filter's literal.
    text_key_in = text_key
    source_compared = text_key == "source"
    text_key = bool(text_key) and not source_compared
    res = Result()
    tables = unit.get("warehouse_tables") or []
    if not tables:
        res.reason = "no warehouse table resolved for this unit"
        return res
    # A2 orders warehouse_tables measure-home first, so [0] is the fact table the aggregates
    # actually read. Sorting alphabetically here would pick a dimension table and compute the
    # measure against the wrong grain.
    source = tables[0]

    # -- a NATIVE-QUERY table is the query's RESULT SET, not the physical table in its FROM.
    # Its columns are the query's own output (often derived: `period`, `weeks`, a CASE tier),
    # and several logical tables can read the same physical object through different
    # queries. Compiling against the physical table produced `invalid identifier` on 8 of 9
    # witnessed statements on a live model, and refused most of the rest as "not on" the
    # table. So the statement reads the query itself, as a derived table, and every column is
    # the query's output column rather than the model caption.
    native = unit.get("native_source") or None
    # The language a calculated field this unit names as a column, a filter or its sort is
    # written in, when the reader says the unit may name one. Decided from the data the
    # reader wrote, never from which tool the unit came from.
    calc_language = unit.get("expression_language") or None
    colmap = None
    if native:
        colmap = _native_colmap(native.get("columns") or {})
        # A calc used as a grouping column is compiled from its formula over the query's
        # columns, so it is not "missing" from the query.
        missing = [g for g in unit.get("group_by", []) if g.lower() not in colmap
                   and not (calc_language and g.lower() in (measures or {}))]
        if missing:
            res.reason = (f"grouping column(s) {sorted(missing)} are not columns of the native "
                          f"query behind {native.get('logical')!r}, and a join across two "
                          f"native queries is not planned")
            return res
        source = "(\n" + native["sql"].strip().rstrip(";") + "\n)"
        joins, plan = [], _JoinPlan(alias)
        graph, userel = [], set()
    else:
        # -- a BLEND: measures or filters of a table only a `blend` edge reaches are a
        # statement of their own, aggregated over the links the view groups by and joined to
        # the rows of the rest of the unit after aggregation.
        blended = _blend_parts(unit, source, columns)
        if blended is not None:
            def again(u: dict) -> Result:
                return _compile_unit(u, rules, dialect, alias=alias, columns=columns,
                                     measures=measures, table_of=table_of, logical=logical,
                                     model=model, model_graph=model_graph, types=types,
                                     row_formulas=row_formulas, text_key=text_key_in,
                                     renames=renames)
            return _blend_statement(unit, blended, again, rules, dialect)
        # -- joins: a grouping column that does not live on the source needs one, and the
        # unit's join_graph is the evidence for it. Refusing outright (as the old draft did)
        # was correct only while that evidence did not reach here.
        def again(u: dict) -> Result:
            return _compile_unit(u, rules, dialect, alias=alias, columns=columns,
                                 measures=measures, table_of=table_of, logical=logical,
                                 model=model, model_graph=model_graph, types=types,
                                 row_formulas=row_formulas, text_key=text_key_in,
                                 renames=renames)
        # MEASURES THAT ITERATE MEMBERS (a table's rows, a column's values) and evaluate an
        # expression over other rows per member: each is a statement at the members' grain,
        # aggregated again, joined to the rest on the visual's rows.
        grains, ranks, treatas = {}, {}, {}
        if measures and table_of and not unit.get("top_n"):
            # A member iterator that would be read wrongly is refused by name, for the
            # measure it is in (`_member_grain` raises it).
            for m in unit.get("measures") or []:
                if m.get("formula_language") == "dax" and m.get("formula"):
                    try:
                        _statement_form(m["formula"], measures, table_of,
                                        list(model_graph or [])
                                        + list(unit.get("join_graph") or []),
                                        set(row_formulas or ()))
                    except _NoTransition as exc:
                        res = Result()
                        res.coverage = REFUSED
                        res.reason = f"measure {m['name']!r}: {exc}"
                        return res
            # A measure that is a scalar expression OVER such forms (IF(ISBLANK([S]),
            # BLANK(), RANKX(..)), DIVIDE(COUNTROWS(FILTER(VALUES(k), ..)), ..)): each part is
            # a measure of its own, and the expression is computed over their values per row.
            try:
                done = _composite_statement(unit, measures, table_of, list(model_graph or [])
                                            + list(unit.get("join_graph") or []),
                                            set(row_formulas or ()), again, rules, dialect)
            except _NoTransition as exc:
                done = Result()
                done.coverage = REFUSED
                done.reason = f"a part of a measure: {exc}"
            if done is not None:
                return done
            for m in unit.get("measures") or []:
                if m.get("formula_language") == "dax" and m.get("formula"):
                    rk = (_rank_form(m["formula"], measures, table_of, set(row_formulas or ()),
                                     graph=list(model_graph or [])
                                     + list(unit.get("join_graph") or []))
                          or _window_form(m["formula"], measures, table_of,
                                          set(row_formulas or ())))
                    if rk:
                        ranks[m["name"]] = rk
                        continue
                    g = _member_grain(m["formula"], measures, table_of,
                                      list(model_graph or []) + list(unit.get("join_graph")
                                                                     or []),
                                      set(row_formulas or ()), columns=columns)
                    if g:
                        grains[m["name"]] = g
                        continue
                    tr = _treatas_form(m["formula"], measures, table_of,
                                       list(model_graph or [])
                                       + list(unit.get("join_graph") or []))
                    if tr:
                        treatas[m["name"]] = tr
        if grains or ranks or treatas:
            rest = [m for m in unit.get("measures") or [] if m["name"] not in grains
                    and m["name"] not in ranks and m["name"] not in treatas]
            parts = []
            if rest:
                sub = dict(unit, measures=rest)
                parts.append((sub, again(sub)))
            for m in unit.get("measures") or []:
                if m["name"] in grains:
                    parts.append((dict(unit, measures=[m]), _member_grain_statement(
                        unit, m, grains[m["name"]], rules, dialect, again, table_of,
                        measures)))
                elif m["name"] in ranks and ranks[m["name"]].get("fn"):
                    parts.append((dict(unit, measures=[m]), _window_statement(
                        unit, m, ranks[m["name"]], rules, dialect, again, measures)))
                elif m["name"] in ranks:
                    parts.append((dict(unit, measures=[m]), _rank_statement(
                        unit, m, ranks[m["name"]], rules, dialect, again)))
                elif m["name"] in treatas:
                    parts.append((dict(unit, measures=[m]), _treatas_statement(
                        unit, m, treatas[m["name"]], again)))
            return _joined_statements(unit, parts, rules, dialect)
        homes = _measure_homes(unit, source, measures, table_of)
        rowless_names = set()
        if measures is not None and table_of and unit.get("group_by") \
                and unit.get("blank_rows") == "hidden":
            rowless_names = {m["name"] for m in unit.get("measures") or []
                             if m.get("formula_language") == "dax" and m.get("formula")
                             and not _tables_read(m["formula"], measures, table_of,
                                                  rows_only=True)}
        mixed = bool(rowless_names) and len(rowless_names) < len(unit.get("measures") or [])
        if (homes or mixed) and not unit.get("top_n"):
            # Measures of different tables (two facts beside one dimension): each table's
            # measures are a statement of their own over that table, joined on the rows.
            # A measure that reads no row (a test of the context alone) is a statement of
            # its own beside the table's aggregates: it has a value on the table's blank
            # member, which a statement over the table's rows does not list.
            by_home: dict[tuple, list[dict]] = {}
            for m in unit.get("measures") or []:
                by_home.setdefault((homes.get(m["name"], source),
                                    m["name"] in rowless_names), []).append(m)
            parts = []
            for (home, _rowless), ms in by_home.items():
                sub = dict(unit, measures=ms, warehouse_tables=[home] + [
                    t for t in tables if t != home])
                parts.append((sub, _compile_unit(
                    sub, rules, dialect, alias=alias, columns=columns, measures=measures,
                    table_of=table_of, logical=logical, model=model, model_graph=model_graph,
                    types=types, row_formulas=row_formulas, text_key=text_key_in,
                    renames=renames)))
            return _joined_statements(unit, parts, rules, dialect)
        graph, extra, userel, split = _measure_graph(unit, source, measures, table_of,
                                                     model_graph)
        if calc_language and measures and table_of:
            # A CALCULATED COLUMN the visual groups or filters by is a row expression; one that
            # reads a related table's column (RELATED) reads it through the relationship, so
            # that column is planned like a measure's.
            tm = {k.lower(): v for k, v in table_of.items()}
            gbt0 = list(unit.get("group_by_tables") or [])
            named = [(g, gbt0[i] if i < len(gbt0) else None)
                     for i, g in enumerate(unit.get("group_by") or [])]
            named += [(f["column"], f.get("table")) for f in unit.get("filters") or []
                      if f.get("column")]
            for col, hint in named:
                key = (f"{_bare(hint)}[{str(col).lower()}]" if hint else str(col).lower())
                formula = measures.get(key) or measures.get(str(col).lower())
                if not formula:
                    continue
                for k, v in _expanded_tokens(formula, measures):
                    t = _table_ref(v) if k in ("qualified", "quoted_qualified") else None
                    phys = tm.get(str(t or "").lower())
                    if phys and _bare(phys) not in (_bare(source), _bare(hint)):
                        req = (_column_ref(v).lower(), phys)
                        if req not in extra:
                            extra.append(req)
        if split:
            # The measures join the tables along different relationships: one statement per
            # set of measures that agree, joined on the visual's rows.
            groups: dict = {}
            tm = {k.lower(): v for k, v in (table_of or {}).items()}
            for m in unit.get("measures") or []:
                key = _relationship_spec(m, measures, tm, _bare(source), []) or frozenset()
                groups.setdefault(key, []).append(m)
            if unit.get("top_n"):
                res.coverage = REFUSED
                res.reason = ("a Top N over measures that join the tables along different "
                              "relationships: the ranking reads one statement's rows")
                return res
            parts = []
            for ms in groups.values():
                sub = dict(unit, measures=ms)
                parts.append((sub, _compile_unit(
                    sub, rules, dialect, alias=alias, columns=columns, measures=measures,
                    table_of=table_of, logical=logical, model=model, model_graph=model_graph,
                    types=types, row_formulas=row_formulas, text_key=text_key_in,
                    renames=renames)))
            return _joined_statements(unit, parts, rules, dialect)
        # A level of detail over a related table's field: its own statement joins that table
        # through the same relationships, so the plan places the field as it places one the
        # view filters by (a member of a related table, never a measure's repeated rows).
        lod_fields = _lod_related_columns(unit, source, columns, measures, graph)
        plan = _plan_joins(unit, source, alias, columns, extra=extra, graph=graph,
                           fields=lod_fields, whole_model=model_graph is not None)
        if plan.detached and not plan.unreachable:
            return _detached_statement(unit, plan, graph, lambda u: _compile_unit(
                u, rules, dialect, alias=alias, columns=columns, measures=measures,
                table_of=table_of, logical=logical, model=model, model_graph=model_graph,
                types=types, row_formulas=row_formulas, text_key=text_key_in, renames=renames),
                measures=measures, table_of=table_of)
        if plan.unreachable:
            gb = {str(g).lower() for g in unit.get("group_by") or []}
            kind = ("grouping column(s)" if plan.unreachable <= gb else "column(s)")
            res.reason = (f"{kind} {sorted(plan.unreachable)} are not on {source} and "
                          f"no declared relationship reaches them"
                          + "".join(f"; {w}" for w in plan.reasons))
            return res
        joins = list(plan.clauses)

    # A SQL MEASURE IS PLACED, NEVER TRANSLATED. It is already SQL in the declared dialect, so
    # no rule applies; the engine still owns the statement's SHAPE. Each table qualifier in it
    # is a ROLE of the model: the home role is this statement's alias, and every other role
    # gets its own alias and its own LEFT JOIN along declared relationships that never fan out,
    # on every key column. Names the formula uses (facts, other metrics) are expanded first.
    placed: dict[str, str] = {}
    sql_measures = [m for m in unit.get("measures", [])
                    if m.get("formula_language") == "sql" and m.get("formula")]
    if sql_measures and not native:
        if model is None and logical:
            model = _declared.Model.from_graph(logical, unit.get("join_graph") or [])
        named = sorted({q for m in sql_measures for q in _declared.qualifiers(m["formula"])})
        if model is None and named:
            res.reason = (f"SQL measure reads table qualifier(s) {named} but the model's "
                          f"logical tables were not supplied")
            return res
        if model is not None:
            homes = {str(m.get("home_table") or "").lower() or model.role_of(source)
                     for m in sql_measures}
            if len(homes) != 1 or None in homes:
                res.reason = (f"SQL measures over {sorted(str(h) for h in homes)}: one "
                              f"statement is computed over one table of the model")
                return res
            try:
                exprs, extra, _names = _declared.place_in_statement(
                    model, homes.pop(), [m["formula"] for m in sql_measures], alias,
                    len(joins))
            except _declared.NotPlaced as exc:
                res.reason = f"SQL measure {exc}"
                return res
            placed = {m["name"]: e for m, e in zip(sql_measures, exprs)}
            joins = joins + extra

    # A CALCULATED FIELD used as a grouping column or a filter is an EXPRESSION, not a
    # column: compiled from its formula like a measure, never emitted as `t.<caption>`. Only
    # for a unit whose reader declared its `expression_language`; over a native statement the
    # calc reads the statement's output columns.
    calc_sql: dict[str, str] = {}
    calc_names: set[str] = set()
    # The level-of-detail expressions any formula of this statement registers (`_lod`).
    lod_reg: list[dict] = []

    def _calc(name: str, table: str | None = None) -> str | None:
        key = str(name).lower()
        if not calc_language:
            return None
        formula_key = key
        if table and f"{_bare(table)}[{key}]" in (measures or {}):
            formula_key = f"{_bare(table)}[{key}]"
        elif table:
            formula_key = next((k for k in (measures or {})
                                if k == f"{str(table).lower()}[{key}]"), key)
        if formula_key not in (measures or {}):
            return None
        if colmap is not None and key in colmap:
            return None
        # Read on the table the column is on: the statement's own, or a joined one.
        on = plan.at(name, table) if colmap is None else alias
        if (formula_key, on) not in calc_sql:
            p = _Parser(_tokens(measures[formula_key], calc_language), rules, calc_language,
                        on, dialect, colmap, measures, None, (key, formula_key))
            p.lods = lod_reg
            if calc_language == "dax" and table_of and colmap is None:
                # A calculated column is evaluated on each row of its table (a row context):
                # RELATED reads the related row, through the statement's join.
                cctx = _UnitContext(source)
                cctx.plan, cctx.unplaced = plan, plan.unplaced
                cctx.types = dict(types or {})
                p.ctx, p.row_level = cctx, True
                p.home = ({k.lower(): v for k, v in table_of.items()},
                          str(table) if table and on != alias else source)
            sql = p.parse()
            if on != alias and plan.keys.get(on):
                # A calculated column of a JOINED table is evaluated on that table's rows; a
                # fact row the join does not match has no such row, and its value is the
                # blank member's: NULL, not the formula over NULL columns.
                sql = f"CASE WHEN {on}.{plan.keys[on][0]} IS NULL THEN NULL ELSE ({sql}) END"
            calc_sql[(formula_key, on)] = f"({sql})"
            res.rules_used += p.used
            res.provisional_rules += p.provisional
            res.unknown += p.unknown
        calc_names.add(key)
        return calc_sql[(formula_key, on)]

    gb_tables = list(unit.get("group_by_tables") or [])

    def _gcol(g: str, i: int) -> str:
        hint = gb_tables[i] if i < len(gb_tables) else None
        if colmap is not None:
            if g.lower() not in colmap:
                return _calc(g) or f"{alias}.{_ident(g)}"
            return f"{alias}.{_ident(colmap[g.lower()])}"
        return _calc(g, hint) or f"{plan.at(g, hint)}.{_legal(g)}"

    # TEXT GROUPED AS THE MODEL HOLDS IT. A model that compares text without case and
    # without trailing spaces (`text_key`) keeps ONE value for 'Retail', 'retail' and 'Retail '
    # (seen live): such a column is grouped by its folded key and shows one spelling of it.
    # Which spelling the model keeps is the first it loaded, which no statement can know; the
    # statement shows the least (MIN), and a reader compares the members by their key.
    inv_table = {_bare(v): str(k).lower() for k, v in (table_of or {}).items()}
    folded: set[int] = set()
    # The source groups the text (DirectQuery) and keeps 'Retail' and 'retail' apart, but the
    # model shows ONE spelling per value that differs only in case (seen live, DirectQuery
    # and Dual): the least of the spellings equal without case.
    cased: set[int] = set()
    if (text_key or source_compared) and colmap is None:
        in_branches = {al for br in plan.branches.values() for al in br["aliases"]}
        for i, g in enumerate(unit.get("group_by", [])):
            hint = gb_tables[i] if i < len(gb_tables) else None
            at = plan.at(g, hint)
            if at in in_branches:
                continue
            wt = plan.table_at.get(at) or _bare(source)
            ty = (types or {}).get((inv_table.get(wt, wt), str(g).lower())) or ""
            if re.match(r"(?i)^(string|text|varchar|char|nvarchar|nchar)\b", ty):
                (cased if source_compared else folded).add(i)

    def _gkey(g: str, i: int) -> str:
        """The grouping expression: the column, or its folded key (`folded`)."""
        return _Parser._text_key(_gcol(g, i)) if i in folded else _gcol(g, i)

    select: list[str] = []
    for i, g in enumerate(unit.get("group_by", [])):
        try:
            expr = _gcol(g, i)
        except CompileError as exc:
            res.reason = f"grouping calc {g!r}: {exc}"
            return res
        if i in folded:
            select.append(f"MIN({expr}) AS "
                          f"{_measure_alias(g) if g.lower() in calc_names else _legal(g)}")
            res.rules_used.append("engine.text_key_group")
            continue
        if i in cased:
            select.append(f"MIN({expr}) OVER (PARTITION BY UPPER({expr})) AS "
                          f"{_measure_alias(g) if g.lower() in calc_names else _legal(g)}")
            res.rules_used.append("engine.text_case_label")
            continue
        labels = (unit.get("member_labels") or {}).get(str(g).lower())
        if labels:
            # The view SHOWS a label for some values (aliases): the row is still the stored
            # value's, under its label.
            select.append(_labelled(expr, labels, dialect) + " AS "
                          + (_measure_alias(g) if g.lower() in calc_names else _legal(g)))
            res.rules_used.append("engine.member_labels")
            continue
        select.append(f"{expr} AS {_measure_alias(g)}" if g.lower() in calc_names else expr)

    ctx = _UnitContext(tables[0])
    ctx.lods = lod_reg
    ctx.plan = plan if not native else None
    ctx.unplaced = plan.unplaced
    ctx.top_n = unit.get("top_n")
    ctx.measure_filtered = any(str(f.get("stage") or "") == "measure" and f.get("measure")
                               for f in unit.get("filters") or [])
    ctx.types = dict(types or {})
    ctx.row_formulas = set(row_formulas or ())
    ctx.relationships_used = userel
    ctx.text_key = text_key
    ctx.source_compared = source_compared
    ctx.renames = dict(renames or {})
    for i, g in enumerate(unit.get("group_by", [])):
        hint = gb_tables[i] if i < len(gb_tables) else None
        at = plan.at(g, hint) if not native else alias
        ctx.groups.append((str(g).lower(), plan.table_at.get(at), _gkey(g, i)))
    for f in unit.get("filters") or []:
        if f.get("column") and _filter_reads_column(f):
            at = plan.at(f["column"], f.get("table")) if not native else alias
            ctx.filters.append((str(f["column"]).lower(), plan.table_at.get(at)))
            ctx.filter_values.append((str(f["column"]).lower(), plan.table_at.get(at),
                                      str(f.get("op") or ""), f.get("value")))
            if str(f.get("op") or "").lower() == "top_n" and isinstance(f.get("value"), dict) \
                    and str(f["value"].get("population") or "") == "filtered":
                ctx.top_n_filters.append((str(f["column"]).lower(), plan.table_at.get(at)))
    if not native:
        ctx.date_keys = _date_keys(graph, tables[0], ctx.types)
        ctx.graph = graph

    failed: list[str] = []
    ranked_by = None        # the first measure that compiled: what a Top-N ranks by
    alias_of: dict[str, str] = {}   # compiled expression -> its select alias
    for m in unit.get("measures", []):
        if m["name"] in placed:
            select.append(f"{placed[m['name']]} AS {_measure_alias(m['name'])}")
            alias_of.setdefault(placed[m["name"]], _measure_alias(m["name"]))
            ranked_by = ranked_by or _measure_alias(m["name"])
            continue
        seen_gaps, seen_unknown = len(res.untranslatable), len(res.unknown)
        elsewhere = _measure_elsewhere(m, unit, source, columns) if not native else None
        if elsewhere:
            # A shelf aggregation of a column of ANOTHER table: read off this statement's
            # alias it aggregates nothing, or a same-named column of the wrong table.
            failed.append(f"{m.get('name')!r}: {elsewhere}")
            res.untranslatable.append({"construct": "measure of another table",
                                       "in_measure": m.get("name") or "", "why": elsewhere})
            continue
        try:
            expr = _compile_measure(m, rules, alias, dialect, res, colmap, measures,
                                    unit, home=(table_of, source) if table_of and not native
                                    else None, ctx=ctx)
        except _TimeIntelligence:
            if plan.branches:
                res.coverage = REFUSED
                res.reason = ("time intelligence over a many-to-many (or both-ways) path: "
                              "one statement per date set does not carry its distinct members")
                return res
            return _compile_time_unit(unit, rules, dialect, alias=alias, columns=columns,
                                      measures=measures, table_of=table_of, source=source,
                                      plan=plan, joins=joins, ctx=ctx, gcol=_gcol,
                                      calc=_calc, native=native)
        except CompileError as exc:
            # One measure the compiler cannot do must not cost the ones it can. A unit with
            # four measures and one table calculation is three answers plus a declared gap,
            # not zero answers.
            failed.append(f"{m.get('name')!r}: {exc}")
            # THE GAP IS NAMED ON THE RESULT, whatever stopped the measure. Only a declared
            # construct used to leave an `untranslatable` entry; an unknown function or a
            # field that is not a column left none, and a partial unit reached its candidate
            # with nothing saying what it lacks.
            new = res.untranslatable[seen_gaps:]
            for gap in new:
                gap.setdefault("in_measure", m.get("name") or "")
            if not new:
                res.untranslatable.append({
                    "construct": ", ".join(dict.fromkeys(res.unknown[seen_unknown:]))
                                 or "measure",
                    "in_measure": m.get("name") or "", "why": str(exc)})
            continue
        select.append(f"{expr} AS {_measure_alias(m['name'])}")
        alias_of.setdefault(expr, _measure_alias(m["name"]))
        ranked_by = ranked_by or _measure_alias(m["name"])

    # EVERY value the visual shows ignores the same visual filters (each measure is wholly a
    # CALCULATE that replaces or removes them, keeping the visual's grouping): those filters
    # then choose no row of any value, and the statement is the unit without them, every
    # measure compiled over its rows as it stands. An aggregate left outside the CALCULATE
    # reads the visual's rows, filters included, even when it names no column (COUNTROWS is
    # COUNT(*)): DIVIDE([n], CALCULATE([n], ALL(T))) keeps them on its numerator.
    drops = {tuple(sorted(map(str, a["drop"]))) for a in ctx.context_aggs if a.get("drop")}

    def _outside(x: str) -> str:
        return re.sub(r"__CTXAGG\d+__", "", x.rsplit(" AS ", 1)[0])

    if (len(drops) == 1 and not failed and all(a.get("drop") for a in ctx.context_aggs)
            and all(len(a["keep"]) == len(ctx.groups) for a in ctx.context_aggs)
            and all(not re.search(rf"\b{re.escape(alias)}\.|\bj\d+\.|\bOVER\b", _outside(x))
                    and not _agg_call(_outside(x))
                    for x in select[len(unit.get("group_by") or []):])):
        gone = {(str(c).lower(), t) for c, t in ctx.context_aggs[0]["drop"]}
        grouped = {c for c, _t, _s in ctx.groups}

        def _gone(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table"))) if not native else None
            return col not in grouped and ((col, at) in gone or (col, None) in gone)
        kept = [f for f in unit.get("filters") or [] if not _gone(f)]
        if len(kept) < len(unit.get("filters") or []):
            return _compile_unit(dict(unit, filters=kept), rules, dialect, alias=alias,
                                 columns=columns, measures=measures, table_of=table_of,
                                 logical=logical, model=model, model_graph=model_graph,
                                 types=types, row_formulas=row_formulas, text_key=text_key_in,
                                 renames=renames)

    shown = _rows_not_on_source(ctx)
    # A measure NOT BLANK on a row with no rows of the statement's table (a + 0, COALESCE, an
    # IF with an else): the visual then lists every member of the columns it groups by, also
    # those with no rows, with the measure's value on an empty row there.
    on_empty: dict[str, str] = {}
    on_empty_ctx: dict[str, tuple[str, list[dict]]] = {}
    if shown and not native:
        for m in unit.get("measures", []):
            if m.get("formula_language") != "dax" or not m.get("formula") \
                    or m["name"] in placed or any(f.startswith(f"{m.get('name')!r}:")
                                                  for f in failed):
                continue
            verdict = _blank_on_empty(m, rules, alias, dialect, measures, unit, table_of,
                                      source, ctx)
            empty = (_empty_value(m, rules, alias, dialect, measures, unit, table_of, source,
                                  ctx) if verdict is not True else None)
            mine = next((x for x in select if x.endswith(f" AS {_measure_alias(m['name'])}")), "")
            # A Top N FILTER (the members a ranking keeps) narrows the members listed, as
            # any filter on the column's table does; one kept by the statement's LIMIT is not.
            limit_top = bool(unit.get("top_n")) and not ctx.top_n_filters
            if verdict is not True and empty is not None and unit.get("group_by") \
                    and not limit_top and not re.search(r"\bOVER\b|__CTXAGG", mine):
                on_empty[_measure_alias(m["name"])] = empty
                continue
            wider = (_empty_context_value(m, rules, alias, dialect, measures, unit, table_of,
                                          source, ctx)
                     if verdict is not True and unit.get("group_by")
                     and not limit_top else None)
            if wider is not None:
                # Not blank on an empty row because it reads a wider context (a total over
                # ALL, an ALLEXCEPT that removes a dimension's filter): seen live, the
                # visual lists every member, with that context's value there.
                on_empty_ctx[_measure_alias(m["name"])] = wider
                continue
            if verdict is not True:
                res.coverage = REFUSED
                res.untranslatable.append({
                    "construct": "measure not blank on an empty row", "in_measure": m["name"],
                    "why": "the visual then shows rows the statement does not have"})
                res.reason = (
                    f"measure {m['name']!r} is "
                    + ("not BLANK" if verdict is False else "not shown to be BLANK")
                    + f" on a row of the visual with no rows of {source} (a + 0, COALESCE, an "
                    f"IF with an else, a total over ALL), so the visual shows every member of "
                    f"{', '.join(shown)} it groups or filters by, also those with no rows; "
                    f"a statement over {source}'s rows does not have them")
                return res

    # The statement's own table's BLANK member (a key a relationship into it holds and it
    # lacks) has no row of the table, so a statement over its rows lists it nowhere. A measure
    # not BLANK there (a test of the context: ISFILTERED of the grouped column is TRUE on
    # every row the visual lists) shows that row too: its value on an empty row, where every
    # such measure has one that reads no row. Only where no measure of the statement reads a
    # row: an aggregate's value on the blank member is not its value on an empty row (the
    # member's own blank value is one distinct value of each of the table's columns).
    own_blank: dict[str, str] | None = None
    if not shown and not native and unit.get("group_by") and not failed \
            and unit.get("blank_rows") == "hidden" and measures is not None and table_of:
        own_blank = {}
        for m in unit.get("measures", []):
            if m.get("formula_language") != "dax" or not m.get("formula") \
                    or m["name"] in placed \
                    or _tables_read(m["formula"], measures, table_of, rows_only=True):
                own_blank = None
                break
            if _blank_on_empty(m, rules, alias, dialect, measures, unit, table_of, source,
                               ctx) is True:
                continue
            empty = _empty_value(m, rules, alias, dialect, measures, unit, table_of, source,
                                 ctx)
            if empty is None:
                own_blank = None
                break
            own_blank[_measure_alias(m["name"])] = empty

    if failed and not any(" AS " in x for x in select):
        # Nothing was translated. PARTIAL means "a statement, with a named gap"; with no
        # statement there is nothing partial about it, and calling it partial would let a
        # unit with no SQL look like one that half-worked.
        res.coverage = REFUSED
        res.reason = "; ".join(f"measure {f}" for f in failed)
        return res
    if failed:
        res.reason = "; ".join(f"measure {f}" for f in failed)

    if not select:
        res.reason = "nothing to select: the unit has neither a measure nor a grouping column"
        return res

    special: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                                "display": []}
    # The language a Top N's ranking measure and a measure filter are read in: the widget's,
    # else its measures'.
    measure_language = calc_language or next(
        (x.get("formula_language") for x in unit.get("measures") or []
         if x.get("formula_language")), None)
    try:
        ctx_preds, dim_preds, deferred = _plan_filters(unit, rules, alias, res, colmap,
                                                       resolve=_calc, dialect=dialect,
                                                       place=plan.at, text_key=text_key,
                                                       special=special)
    except CompileError as exc:
        res.reason = f"filter calc: {exc}"
        return res
    if deferred:
        # A filter the compiler cannot express must NOT be silently omitted. The widget
        # applies it, so a statement without it computes a different number — and unlike a
        # syntax error, that failure ships. The reason distinguishes the two causes: "no rule
        # for this operator" and "this operator cannot take a list" need different fixes, and
        # a message that conflates them sends the reviewer after the wrong one.
        named = ("multi-value", "not-a-column", "no-value", "measure-stage", "member-filter",
                 "relative-date")
        no_rule = sorted({str(f.get("op")) for f in deferred if f.get("_why") not in named})
        multi = sorted({str(f.get("op")) for f in deferred if f.get("_why") == "multi-value"})
        not_col = sorted({str(f.get("column")) for f in deferred
                          if f.get("_why") == "not-a-column"})
        no_value = sorted({str(f.get("column")) for f in deferred
                           if f.get("_why") == "no-value"})
        after_agg = sorted({str(f.get("column")) for f in deferred
                            if f.get("_why") == "measure-stage"})
        member = sorted({str(f.get("column")) for f in deferred
                         if f.get("_why") == "member-filter"})
        parts = [f"{f.get('_detail')} (on {f.get('column')!r})" for f in deferred
                 if f.get("_why") == "relative-date"]
        if member:
            parts.append(f"filter(s) on {member} keep the members that pass a Top N or a "
                         f"condition, which this statement does not compute")
        if not_col:
            parts.append(f"filter column(s) {not_col} are not columns of the native query")
        if no_value:
            parts.append(f"filter(s) on {no_value} carry no value the BI file recorded")
        if after_agg:
            parts.append(f"measure-stage filter(s) on {after_agg} apply after aggregation "
                         "and are not compiled")
        if no_rule:
            parts.append(f"no operator rule for {', '.join(no_rule)}")
        if multi:
            parts.append(f"{', '.join(multi)} received a multi-value filter and only a "
                         f"membership operator can express one")
        res.coverage = REFUSED
        res.reason = ("; ".join(parts)
                      + f" on {sorted({f['column'] for f in deferred})}; the filter would "
                        f"have been dropped, which changes the number")
        return res

    # DATA SOURCE filters apply to every row before any context filter, Top N or condition:
    # the innermost tier, and context filters next. One on a joined table's column keeps the
    # statement's own rows whose related row passes it: a semi-join (EXISTS over the joins
    # that reach that column) in the population every later step reads. Alone, a data
    # source filter on a joined column is read after the join, the same rows.
    ds_joined = [p for p in special["datasource"] if re.search(r"\bj\d+\.", p)]
    ctx_joined = [p for p in ctx_preds if re.search(r"\bj\d+\.", p)]
    early_joined = []
    if ctx_joined or (ds_joined and (ctx_preds or special["members"] or unit.get("top_n"))):
        try:
            early_joined = [_semi_join(p, joins, alias) for p in ds_joined + ctx_joined]
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"a data source or context filter on a column of a joined table: "
                          f"{exc}")
            return res
        res.rules_used.append("engine.filter_semi_join")
        ctx_preds = [p for p in ctx_preds if p not in ctx_joined]
        # Read on the joined rows too: a related table's own rows with no row of the
        # statement's table (an outer join) are kept only where they pass it.
        ds_joined = ds_joined + ctx_joined
    dim_preds = ds_joined + dim_preds
    try:
        dim_preds = _branch_split(dim_preds, plan)
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res
    base_preds = [p for p in special["datasource"] if p not in ds_joined] + ctx_preds
    src = source
    if early_joined:
        # The semi-joins read the statement's own row by its alias.
        inner = "\n       AND ".join(base_preds + early_joined)
        src = (f"(\n    SELECT {alias}.*\n    FROM   {source} {alias}\n"
               f"    WHERE  {inner}\n  )")
    elif base_preds:
        # Data source and context filters narrow the population BEFORE anything downstream
        # is computed. The boundary is structural: flattening it into the outer WHERE
        # produces a statement that runs and returns a different number.
        inner = "\n       AND ".join(_strip_alias(p, alias) for p in base_preds)
        src = f"(\n    SELECT *\n    FROM   {source}\n    WHERE  {inner}\n  )"
    try:
        member_preds, top_done = _member_filters(
            special["members"], unit, rules, alias, dialect, res, src=src, plan=plan,
            measures=measures, calc=_calc, calc_language=calc_language or measure_language,
            dim_preds=dim_preds, joins=joins,
            # Beside an aggregate over a level of detail the statement is re-aggregated, and
            # beside a table calculation the window runs over the kept members only (a Top N
            # keeps them BEFORE table calculations run): either way a Top N is its members,
            # never the outer statement's LIMIT (a LIMIT after the window would compute it
            # over every member).
            limit_ok=not any(r["mode"] == "grain" for r in lod_reg)
            and not any(" OVER (" in x for x in select),
            compile_by=lambda formula: _compile_measure(
                {"name": "top_n_by", "formula": formula,
                 "formula_language": measure_language}, rules, alias, dialect, res, colmap,
                measures, unit))
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res

    if unit.get("top_n") and not top_done and any(" OVER (" in x for x in select):
        # A Top N the member filters did not read (no filter names it) beside a table
        # calculation: the window would run over every member and the LIMIT keep some.
        res.coverage = REFUSED
        res.reason = (f"a table calculation beside a Top {int(unit['top_n'])} that no filter "
                      f"names: the calculation runs over the kept members only")
        return res
    lod_sub: dict[str, str] = {}
    if lod_reg:
        try:
            lod_joins, lod_sub = _lod_parts(lod_reg, src, alias, rules, dialect,
                                            columns.get(source) if not native else None,
                                            [p for p in dim_preds if not _agg_call(p)],
                                            plan=plan, plan_joins=list(joins))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = str(exc)
            return res
        joins = joins + lod_joins
    def _rows_without(drop: list) -> tuple[str, list[str]]:
        """The statement's FROM and row predicates with the filters on `drop` taken off
        (what ALL / REMOVEFILTERS of a column the visual filters reads)."""
        gone = {(str(c).lower(), t) for c, t in drop}

        def dropped(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table"))) if not native else None
            return (col, at) in gone or (col, None) in gone
        if unit.get("top_n") and not top_done:
            raise CompileError("an ALL / REMOVEFILTERS of a filtered column beside a Top N "
                               "kept by the statement's LIMIT")
        kept = [f for f in unit.get("filters") or [] if not dropped(f)]
        sp: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                               "display": []}
        c_p, d_p, deferred_ = _plan_filters(dict(unit, filters=kept), rules, alias, Result(),
                                            colmap, resolve=_calc, dialect=dialect,
                                            place=plan.at, text_key=text_key, special=sp)
        if deferred_:
            raise CompileError("a filter it keeps does not compile on its own")
        if sp["members"]:
            # A Top N filter it keeps: the SAME members, ranked within every filter of the
            # visual (seen live: ALL(status) beside a top 3 products by the Returned sales
            # reads those three products' rows of every status), so the predicate is the
            # statement's own.
            mem, _done = _member_filters(
                sp["members"], unit, rules, alias, dialect, Result(), src=src, plan=plan,
                measures=measures, calc=_calc,
                calc_language=calc_language or measure_language, dim_preds=dim_preds,
                joins=joins, limit_ok=False,
                compile_by=lambda formula: _compile_measure(
                    {"name": "top_n_by", "formula": formula,
                     "formula_language": measure_language}, rules, alias, dialect, Result(),
                    colmap, measures, unit))
        else:
            mem = []
        ds_j = [p for p in sp["datasource"] if re.search(r"\bj\d+\.", p)]
        base = [p for p in sp["datasource"] if p not in ds_j] + c_p
        from_ = source
        if base:
            inner_ = "\n       AND ".join(_strip_alias(p, alias) for p in base)
            from_ = f"(\n    SELECT *\n    FROM   {source}\n    WHERE  {inner_}\n  )"
        return from_, ds_j + [p for p in d_p if not _agg_call(p)] + mem

    # A predicate over an AGGREGATE (a filter on a calc that sums) is a HAVING, not a WHERE:
    # SQL refuses an aggregate in WHERE, and Tableau applies it after aggregation.
    _agg = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", re.I)
    having = [p for p in dim_preds if _agg.search(p)]
    for f in special["measure_tests"]:
        # A test of a measure's value on each row of the view (a filter on a measure): a
        # HAVING over the measure as the statement computes it, an empty value compared as
        # the filter's stand-in where it says so.
        name = str(f["measure"])
        mine = next((x for x in unit.get("measures") or []
                     if str(x.get("name")).lower() == name.lower()), None)
        try:
            if not measure_language and not (mine or {}).get("formula_language"):
                raise CompileError("no formula language to read the measure in")
            expr = _compile_measure(
                mine or {"name": "measure_filter", "formula": f"[{name}]",
                         "formula_language": measure_language},
                rules, alias, dialect, res, colmap, measures, unit,
                home=(table_of, source) if table_of and not native else None)
            if " OVER (" in expr:
                raise CompileError("its statement is a window, which a HAVING cannot test")
            pred, why = _test_pred(expr, str(f.get("op") or ""), f.get("value"), f, rules,
                                   dialect, res, force=True)
            if pred is None:
                raise CompileError(why or f"no rule for the test {f.get('op')!r}")
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"the filter on the measure {name!r} does not compile ({exc}); "
                          f"dropping it would show other rows")
            return res
        res.rules_used.append("engine.measure_filter")
        having.append(pred)
    row_joins = list(joins)         # the statement's joins before any context statement's
    if ctx.context_aggs:
        try:
            if any(_agg_call(p) for p in dim_preds):
                raise CompileError("beside a filter on an aggregate calculation: which rows "
                                   "the wider context holds then depends on its value")
            passing = None
            if having and unit.get("group_by"):
                # The rows a filter on a measure keeps are a filter on the visual's grouping
                # columns: ALLSELECTED keeps it (its statement reads only the rows of the
                # combinations that pass), ALL of a grouped column takes it off.
                gk = [_gkey(g, i) for i, g in enumerate(unit["group_by"])]
                nse_row = rules.operator("null_safe_eq", dialect)
                nse_emit = (nse_row or {}).get("emit") or {}
                nse = ((nse_emit.get("dialect_templates") or {}).get(str(dialect).lower())
                       or nse_emit.get("template"))
                if not nse:
                    raise CompileError(f"the rows a measure filter keeps are matched NULL to "
                                       f"NULL, which has no form for the {dialect} dialect")
                rows_ = [p for p in dim_preds if not _agg_call(p)] + member_preds
                inner = (f"SELECT {', '.join(f'{k} AS p{i}' for i, k in enumerate(gk))}\n"
                         f"FROM   {src} {alias}" + "".join(f"\n{j}" for j in row_joins)
                         + ("\nWHERE  " + "\n  AND  ".join(rows_) if rows_ else "")
                         + f"\nGROUP BY {', '.join(gk)}\nHAVING " + "\n  AND  ".join(having))
                passing = ("EXISTS (\n    SELECT 1 FROM (\n" + _indent(inner, "      ")
                           + "\n    ) p\n    WHERE  " + "\n      AND  ".join(
                               nse.replace("{0}", f"p.p{i}").replace("{1}", k)
                               for i, k in enumerate(gk)) + "\n  )")
                res.rules_used.append("engine.dax_context_beside_measure_filter")
            for a in ctx.context_aggs:
                if a.get("drop"):
                    a["src"], a["where"] = _rows_without(a["drop"])
                if a.get("selected") and passing:
                    a["where"] = ([p for p in dim_preds if not _agg_call(p)] + member_preds
                                  + [passing])
            ctx_joins, ctx_sub = _context_statements(
                ctx.context_aggs, src, alias, joins,
                [p for p in dim_preds if not _agg_call(p)] + member_preds, rules, dialect)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a non-additive aggregate over a wider context: {exc}"
            return res
        joins = joins + ctx_joins
        lod_sub.update(ctx_sub)
    if ctx.lifts:
        # A table calculation over another one's result: the grouped rows as a statement of
        # their own, the outer calculation run over them, joined back to each row of this
        # statement on its grouping (one row per group, so no aggregate changes).
        try:
            joins = joins + _lifted_joins(ctx, unit, src, alias, joins, [
                p for p in dim_preds if not _agg.search(p)] + member_preds, having, _gkey,
                rules, dialect, lod_reg)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a table calculation over another one: {exc}"
            return res
        res.rules_used.append("engine.table_calc_nested")
    sql = "SELECT " + ",\n       ".join(select) + f"\nFROM   {src} {alias}"
    for j in joins:
        sql += f"\n{j}"
    # A member filter's subquery aggregates inside itself; it is a row predicate.
    dim_preds = [p for p in dim_preds if not _agg.search(p)] + member_preds
    n_groups = len(unit.get("group_by") or [])
    if unit.get("blank_rows") == "hidden" and n_groups:
        # The view leaves out a row whose every value is empty: the statement keeps the rows
        # where at least one of its values is not NULL (`engine.blank_rows_hidden`). Each
        # term is a projection's own expression, which a reader of the statement recognises
        # as hiding empty rows, not as a restriction of the population.
        hide = _blank_rows_having(select[n_groups:])
        if hide:
            having.append(hide)
            res.rules_used.append("engine.blank_rows_hidden")
    windowed = any(" OVER (" in x for x in select)
    # A value computed across the visual's rows (a window) cannot be tested by a HAVING: the
    # empty rows are left out of the finished rows instead (`_hide_blank_rows_after`).
    hide_after = (unit.get("blank_rows") == "hidden" and n_groups and windowed
                  and len(select) > n_groups)
    if special["display"] and not windowed:
        # Hidden members with nothing computed across the view's rows: leaving their rows out
        # of the statement leaves every other row as it is.
        dim_preds = dim_preds + special["display"]
        res.rules_used.append("engine.hidden_members")
    try:
        padded = _padded_crosstab(unit, select, src, alias, joins, dim_preds, having, lod_reg,
                                  windowed, rules, dialect, _gkey)
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res
    if padded:
        sql = padded
        res.rules_used.append("engine.table_calc_densify")
    if dim_preds and not padded:
        sql += "\nWHERE  " + "\n  AND  ".join(dim_preds)
    if unit.get("group_by") and not padded:
        sql += "\nGROUP BY " + ", ".join(_gkey(g, i) for i, g in enumerate(unit["group_by"]))
    if having:
        sql += "\nHAVING " + "\n  AND  ".join(having)
    if hide_after and not padded:
        if unit.get("top_n") and not top_done:
            res.coverage = REFUSED
            res.reason = ("a Top N kept by the statement's LIMIT beside a value computed "
                          "across the visual's rows: the rows with every value empty are "
                          "left out before the ranking, which this statement cannot order")
            return res
        if any(r["mode"] == "grain" for r in lod_reg):
            res.coverage = REFUSED
            res.reason = ("an aggregate over a level-of-detail expression beside a value "
                          "computed across the visual's rows, with empty rows hidden")
            return res
        sql = _hide_blank_rows_after(sql, select, n_groups)
        res.rules_used.append("engine.blank_rows_hidden")
    def _with_wider(sql_: str) -> str:
        """The every-member statement with the value on an empty row of each measure that
        reads a wider context (`on_empty_ctx`): that context's own statement, grouped by the
        columns it keeps and joined to each row on them (NULL matching NULL), or one value."""
        if not on_empty_ctx:
            return sql_
        op = rules.operator("null_safe_eq", dialect)
        if op is None:
            raise CompileError(f"it joins the wider context to the rows, NULL matching NULL, "
                               f"which has no form for the {dialect} dialect")
        emit_ = op.get("emit") or {}
        tmpl = (emit_.get("dialect_templates") or {}).get(str(dialect).lower()) \
            or emit_["template"]
        gb = list(unit["group_by"])
        names = [(x.rsplit(" AS ", 1)[1] if " AS " in x else x.rsplit(".", 1)[-1]).strip()
                 for x in select[:n_groups]]
        gks = [_gkey(g, i) for i, g in enumerate(gb)]
        gcs = [_gcol(g, i) for i, g in enumerate(gb)]

        def outer(expr: str) -> str:
            for i, (k, c) in enumerate(zip(gks, gcs)):
                if expr == k:
                    return k.replace(c, f"w.{names[i]}") if c in k else f"w.{names[i]}"
            raise CompileError(f"a wider context kept by {expr!r}, which the rows do not show")
        extra, exprs, n = [], {}, 0
        for name, (esql, aggs) in on_empty_ctx.items():
            for a in aggs:
                n += 1
                if a["drop"]:
                    src_a, where_a = _rows_without(a["drop"])
                else:
                    src_a, where_a = src, list(dim_preds)
                body = f"FROM   {src_a} {alias}" + "".join(f"\n{j}" for j in row_joins)
                if where_a:
                    body += "\nWHERE  " + "\n  AND  ".join(where_a)
                body = body.replace("\n", "\n    ")
                if a["keep"]:
                    keys_ = ", ".join(f"{k} AS k{i}" for i, k in enumerate(a["keep"]))
                    on = "\n   AND ".join(tmpl.replace("{0}", f"e{n}.k{i}")
                                          .replace("{1}", outer(k))
                                          for i, k in enumerate(a["keep"]))
                    extra.append(f"LEFT JOIN (\n    SELECT {keys_}, {a['sql']} AS v\n    {body}"
                                 f"\n    GROUP BY {', '.join(a['keep'])}\n  ) e{n}\n    ON {on}")
                else:
                    extra.append(f"CROSS JOIN (\n    SELECT {a['sql']} AS v\n    {body}\n  ) e{n}")
                esql = esql.replace(f"__EMPTYAGG{a['n']}__", f"e{n}.v")
            exprs[name] = esql
        cols = names + [x.rsplit(" AS ", 1)[1].strip() for x in select[n_groups:]]
        sel = [f"COALESCE(w.{c}, {exprs[c]}) AS {c}" if c in exprs else f"w.{c}" for c in cols]
        res.rules_used.append("engine.value_on_empty_rows_wider")
        return ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n" + _indent(sql_)
                + "\n) w\n" + "\n".join(extra))

    if (on_empty or on_empty_ctx) and unit.get("blank_rows") != "shown":
        try:
            sql = _with_every_member(sql, select, n_groups, [_gcol(g, i) for i, g in
                                     enumerate(unit["group_by"])], src, alias,
                                     [j for j in plan.clauses if not j.startswith("__")],
                                     [p for p in dim_preds if p not in member_preds], rules,
                                     dialect, graph=list(model_graph or graph or []),
                                     members=member_preds, folded=set(folded))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"a measure not BLANK on an empty row makes the visual list every "
                          f"member of its grouping columns: {exc}")
            return res
        for name, empty in on_empty.items():
            sql = re.sub(rf"(?m)^(\s*)v\.{re.escape(name)}(,?)$",
                         lambda mm, e=empty, n=name: f"{mm.group(1)}COALESCE(v.{n}, {e}) AS {n}"
                                                     f"{mm.group(2)}", sql, count=1)
        try:
            sql = _with_wider(sql)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = (f"a measure not BLANK on an empty row makes the visual list every "
                          f"member of its grouping columns: {exc}")
            return res
        res.rules_used.append("engine.value_on_empty_rows")
    if unit.get("blank_rows") == "shown" and n_groups:
        try:
            if unit.get("top_n") and not top_done:
                raise CompileError("beside a Top N kept by the statement's LIMIT, whose "
                                   "members are ranked over the rows")
            # A Top N FILTER keeps the members a ranking keeps: a filter on the column's own
            # table, which narrows that table's members (and the fact's).
            sql = _with_every_member(sql, select, n_groups, [_gcol(g, i) for i, g in
                                     enumerate(unit["group_by"])], src, alias, joins,
                                     [p for p in dim_preds if p not in member_preds], rules,
                                     dialect, graph=list(model_graph or graph or []),
                                     members=member_preds, folded=set(folded))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"the visual shows items with no data: {exc}"
            return res
        for name, empty in on_empty.items():
            # A member with no rows shows the measure's value on an empty row there too.
            sql = re.sub(rf"(?m)^(\s*)v\.{re.escape(name)}(,?)$",
                         lambda mm, e=empty, n=name: f"{mm.group(1)}COALESCE(v.{n}, {e}) AS {n}"
                                                     f"{mm.group(2)}", sql, count=1)
        try:
            sql = _with_wider(sql)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"the visual shows items with no data: {exc}"
            return res
        res.rules_used.append("engine.every_member")
    if unit.get("top_n") and not top_done:
        # A LIMIT with no ORDER BY returns ANY n rows: a different answer on every run. The
        # BI Top-N ranks by its measure, largest first; the order the file names wins.
        if unit.get("sort"):
            # The sort the BI file names is AUTHOR-CONTROLLED TEXT in the vendor's formula
            # language. It is compiled like a measure — never pasted into the statement —
            # and a sort the compiler cannot read refuses the unit: ordering by some other
            # measure returns a different n rows.
            try:
                order = _top_n_order(
                    str(unit["sort"]), unit, rules, alias, dialect, res, colmap, measures,
                    alias_of, group_expr=lambda n: next(
                        (_gkey(g, i) for i, g in enumerate(unit.get("group_by") or [])
                         if str(g).lower() == n), None))
            except CompileError as exc:
                res.coverage = REFUSED
                res.reason = (f"the Top {int(unit['top_n'])} ranks by {unit['sort']!r}, "
                              f"which does not compile ({exc}); ordering by another measure "
                              f"would return different rows")
                return res
            sql += f"\nORDER BY {order}"
            res.rules_used.append("engine.top_n_by_sort")
        elif ranked_by:
            sql += f"\nORDER BY {ranked_by} DESC"
            res.rules_used.append("engine.top_n_by_first_measure")
        else:
            res.coverage = REFUSED
            res.reason = (f"a Top {int(unit['top_n'])} with no measure to rank by: a LIMIT "
                          "without an order returns arbitrary rows")
            return res
        sql += "\n" + _limit(int(unit["top_n"]), dialect)

    grain = [r for r in lod_reg if r["mode"] == "grain"]
    if grain:
        # An aggregate over a FIXED or INCLUDE level of detail reads the view's rows at the
        # level of detail's dimensions too: a statement at that grain, aggregated again.
        try:
            if having or (unit.get("top_n") and not top_done):
                raise CompileError("beside a measure filter or a Top N kept by LIMIT")
            nse_row = rules.operator("null_safe_eq", dialect)
            nse_emit = (nse_row or {}).get("emit") or {}
            nse = ((nse_emit.get("dialect_templates") or {}).get(str(dialect).lower())
                   or nse_emit.get("template"))
            sql = _grain_statement(select, [_gcol(g, i) for i, g in
                                            enumerate(unit.get("group_by") or [])],
                                   grain, src, alias, joins, dim_preds, lod_sub, nse)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"an aggregate over a level-of-detail expression: {exc}"
            return res
        res.rules_used.append("engine.tableau_lod_grain")
    if special["display"] and windowed:
        # Hidden members beside a calculation across the view's rows (a running sum, a percent
        # of total): their rows are computed with the others, then left out of the view.
        shown = [re.sub(rf"\b(?:{re.escape(alias)}|j\d+)\.", "h.", p)
                 for p in special["display"]]
        cols = ",\n       ".join(f"h.{_out_name(x)}" for x in select)
        sql = (f"SELECT {cols}\nFROM   (\n" + sql + "\n) h\nWHERE  "
               + "\n  AND  ".join(shown))
        res.rules_used.append("engine.hidden_members")
    for key, val in lod_sub.items():
        sql = sql.replace(key, val)
    try:
        sql = _expand_branches(sql, plan, rules, dialect)
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = str(exc)
        return res
    if unit.get("distinct_rows") and n_groups and not unit.get("measures") and not native \
            and not joins and not unit.get("top_n"):
        # A table of one table's columns only lists that table's BLANK member too when a
        # relationship into it has a key it lacks (seen live: from a second many-side table,
        # from an inactive relationship's empty keys, and through a chain: every product's
        # category exists, a sale's product does not, and the categories list a blank).
        test = _blank_member_test(source, list(model_graph or graph or []))
        kept_blank = [_blank_in(f) for f in unit.get("filters") or []]
        passes = all(k is True for k in kept_blank)
        if test and passes:
            nulls = ", ".join(f"NULL AS {_out_name(x)}" for x in select[:n_groups])
            sql = f"{sql}\nUNION\nSELECT {nulls}\nFROM   (SELECT 1 AS one) z\nWHERE  {test}"
            res.rules_used.append("engine.blank_member_of_a_table")
        elif test and None in kept_blank:
            res.gotchas.append("the table's blank member is not listed: a filter of the "
                               "visual whose test of a blank is not read here")

    if own_blank and n_groups and not windowed and not padded and not grain \
            and not unit.get("top_n") and not unit.get("distinct_rows"):
        test = _blank_member_test(source, list(model_graph or graph or []))
        kept_blank = [_blank_in(f) for f in unit.get("filters") or []]
        names = [_out_name(x) for x in select]
        if test and all(k is True for k in kept_blank) and len(set(names)) == len(names):
            # One blank row, as the visual shows one: where the table's own rows already
            # make a row of empty grouping values, the blank member is that row.
            cols = [f"NULL AS {n}" for n in names[:n_groups]] \
                + [f"{own_blank.get(n, 'NULL')} AS {n}" for n in names[n_groups:]]
            is_blank = " AND ".join(f"r_.{n} IS NULL" for n in names[:n_groups])
            some = " OR ".join(f"b_.{n} IS NOT NULL" for n in names[n_groups:])
            sql = (f"SELECT r_.*\nFROM   (\n{sql}\n) r_\nUNION ALL\nSELECT b_.*\nFROM   (\n"
                   f"SELECT {', '.join(cols)}\n) b_\nWHERE  ({test})\n  AND  ({some})"
                   f"\n  AND  NOT EXISTS (\nSELECT 1\nFROM   (\n{sql}\n) r_\nWHERE  {is_blank})")
            res.rules_used.append("engine.blank_member_value")
        elif test and None in kept_blank:
            res.gotchas.append("the table's blank member is not listed: a filter of the "
                               "visual whose test of a blank is not read here")
    if not n_groups and select and not windowed and not unit.get("top_n") \
            and not unit.get("distinct_rows") and all(_constant_item(x, alias) for x in select):
        # A card of measures that read no row (a constant, a parameter written in as its
        # value, a context test): one value, as the visual shows it, never one per row of the
        # table the statement would otherwise read.
        sql = "SELECT " + ",\n       ".join(select)
        res.rules_used.append("engine.constant_measure")
    res.sql = sql
    res.coverage = PARTIAL if (res.untranslatable or failed) else TRANSLATED
    if res.coverage == PARTIAL and not res.reason:
        # A partial with no failed measure: a declared construct outside the measures (a
        # filter that switches the view rather than the rows). Said, never left blank.
        res.reason = "; ".join(
            f"{g.get('construct')}: {g.get('why')}" if g.get("why") else str(g.get("construct"))
            for g in res.untranslatable)
    return res


def _lifted_joins(ctx: "_UnitContext", unit: dict, src: str, alias: str, joins: list[str],
                  preds: list[str], having: list[str], gkey, rules, dialect: str,
                  lods: list[dict]) -> list[str]:
    """The joins of each lifted table calculation (`ctx.lifts`): `lw<n>`, the statement's
    grouped rows (its FROM, WHERE, GROUP BY and HAVING) with the calculation's arguments as
    columns, the window over them, keyed by the grouping columns NULL matching NULL."""
    groups = list(unit.get("group_by") or [])
    if any((m.get("table_calc") or {}).get("pad") for m in unit.get("measures") or []):
        raise CompileError("on a densified crosstab (the empty cells are not grouped rows)")
    if any(r.get("mode") == "grain" for r in lods):
        raise CompileError("beside an aggregate over a level of detail read at its grain")
    keys = [gkey(g, i) for i, g in enumerate(groups)]
    op = rules.operator("null_safe_eq", dialect)
    emit = (op or {}).get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit.get("template")
    if groups and not nse:
        raise CompileError(f"its join on the grouping, NULL matching NULL, has no form for "
                           f"the {dialect} dialect")
    body = f"FROM   {src} {alias}" + "".join(f"\n{j}" for j in joins)
    if preds:
        body += "\nWHERE  " + "\n  AND  ".join(preds)
    if groups:
        body += "\nGROUP BY " + ", ".join(keys)
    if having:
        body += "\nHAVING " + "\n  AND  ".join(having)
    out = []
    for lift in ctx.lifts:
        n = lift["n"]
        cols = ([f"{k} AS k{i}" for i, k in enumerate(keys)]
                + [f"{a} AS a{i}" for i, a in enumerate(lift["args"])]
                + [f"{x} AS {name}" for name, x in lift["sorts"]])
        inner = "SELECT " + ",\n       ".join(cols) + "\n" + body
        if lift.get("recursive"):
            outer = _recursive_rows(lift["recursive"], inner, len(keys), len(lift["args"]),
                                    nse)
        else:
            outer = ("SELECT " + "".join(f"__q.k{i} AS k{i}, " for i in range(len(keys)))
                     + f"{lift['window']} AS v\nFROM   (\n" + _indent(inner) + "\n) __q")
        if keys:
            on = "\n   AND ".join(nse.replace("{0}", f"lw{n}.k{i}").replace("{1}", k)
                                   for i, k in enumerate(keys))
            out.append(f"LEFT JOIN (\n{_indent(outer)}\n) lw{n}\n    ON {on}")
        else:
            out.append(f"CROSS JOIN (\n{_indent(outer)}\n) lw{n}")
    return out


def _recursive_rows(rec: dict, inner: str, nkeys: int, nargs: int, nse: str | None) -> str:
    """The grouped rows (`inner`: k<i> the grouping, a<i> the formula's parts) numbered
    along the compute-using within each partition (`__b`), and the recursion over them
    (`__r`): the first row of each partition from its anchor, every next row from the row
    before (`__r.v`). One row per grouped row: k<i> and v."""
    ks = [f"k{i}" for i in range(nkeys)]
    part = [f"__q.k{p}" for p in rec["partition"]]
    pby = f"PARTITION BY {', '.join(part)} " if part else ""
    numbered = ("SELECT " + "".join(f"__q.{k}, " for k in ks)
                + "".join(f"__q.a{i}, " for i in range(nargs))
                + f"ROW_NUMBER() OVER ({pby}ORDER BY {rec['order']}) AS rn\n"
                + "FROM   (\n" + _indent(inner) + "\n) __q")
    carry = "".join(f"__b.{k}, " for k in ks)
    on = [f"__b.rn = __r.rn + 1"] + [
        (nse or "{0} = {1}").replace("{0}", f"__b.k{p}").replace("{1}", f"__r.k{p}")
        for p in rec["partition"]]
    # A dialect that caps a recursion's depth by default states its own cap on `__r`
    # (`recursion_clause`, e.g. MAX RECURSION LEVEL): one level per row of a partition.
    level = f" {rec['level']}" if rec.get("level") else ""
    text = (f"__b AS (\n{_indent(numbered)}\n), __r{level} AS (\n"
            f"    SELECT {carry}__b.rn, {rec['anchor']} AS v\n"
            f"    FROM   __b\n    WHERE  __b.rn = 1\n"
            f"    UNION ALL\n"
            f"    SELECT {carry}__b.rn, {rec['step']} AS v\n"
            f"    FROM   __r\n    JOIN   __b\n      ON " + "\n     AND ".join(on) + "\n)\n",
            "SELECT " + "".join(f"__r.{k} AS {k}, " for k in ks) + "__r.v AS v\nFROM   __r")
    if not rec.get("hoist"):
        return f"{rec['with']} {text[0]}{text[1]}"
    # A dialect that allows a WITH only at the top of a statement (`hoist`): the CTEs, named
    # for this recursion, are marked here and moved to the top (`_hoist_recursions`).
    n = rec["n"]
    named = [re.sub(r"\b__([br])\b", rf"__\g<1>{n}", t) for t in text]
    return f"/*__rec{n}__*/{named[0]}/*__sel{n}__*/{named[1]}/*__end{n}__*/"


def _hoist_recursions(sql: str, opener: str, suffix: str) -> str:
    """The recursions marked in `sql` (`_recursive_rows` with `hoist`) as CTEs at the top of
    the statement, each subquery reading its recursion's own CTE, and the statement's
    `suffix` (a dialect's recursion cap, e.g. OPTION (MAXRECURSION 0)) at its end."""
    defs: list[str] = []
    for m in list(re.finditer(r"/\*__rec(\d+)__\*/", sql)):
        n = m.group(1)
        start = sql.index(f"/*__rec{n}__*/")
        mid = sql.index(f"/*__sel{n}__*/", start)
        end = sql.index(f"/*__end{n}__*/", mid)
        defs.append(sql[start + len(f"/*__rec{n}__*/"):mid].rstrip())
        sql = sql[:start] + sql[mid + len(f"/*__sel{n}__*/"):end] + sql[end + len(f"/*__end{n}__*/"):]
    if not defs:
        return sql
    head = f"{opener} " + ",\n".join(defs)
    if re.match(r"(?is)^\s*WITH\s", sql):
        sql = head + ",\n" + re.sub(r"(?is)^\s*WITH\s+", "", sql, count=1)
    else:
        sql = head + "\n" + sql
    return sql + (f"\n{suffix}" if suffix else "")


def _labelled(expr: str, labels: list[dict], dialect: str | None) -> str:
    """A grouping column shown with its labels: each labelled value as its label, every
    other value as it is (as text, when a label makes the column text)."""
    whens = []
    for lab in labels:
        v = lab.get("value")
        test = f"{expr} IS NULL" if v is None else f"{expr} = {_literal(v, dialect)}"
        whens.append(f"WHEN {test} THEN {_quote(str(lab.get('label') or ''), dialect)}")
    numeric = any(lab.get("value") is not None and _NUM_LIT.fullmatch(str(lab["value"]))
                  for lab in labels)
    rest = f"CAST({expr} AS VARCHAR)" if numeric else expr
    return "CASE " + " ".join(whens) + f" ELSE {rest} END"


def _out_name(item: str) -> str:
    """The column name a select item yields: its alias, else its column."""
    if " AS " in item:
        return item.rsplit(" AS ", 1)[1].strip()
    return item.rsplit(".", 1)[-1].strip()


def _blend_parts(unit: dict, source: str, columns: dict[str, set[str]]) -> dict | None:
    """The unit split at its BLEND edges (`kind: blend`, from the statement's table): the
    primary unit (every measure, filter and table but the blended ones) and, per secondary
    table, its unit: the measures that aggregate its columns and the filters on it, grouped
    by the links whose primary column the view groups by (the active links). None when no
    measure or filter reads a blended table."""
    tables = list(unit.get("warehouse_tables") or [])
    blends = [e for e in unit.get("join_graph") or [] if str(e.get("kind")) == "blend"]
    # The PRIMARY is the table the blend edges start from, wherever the unit lists it (a
    # view that shows only the secondary's measures lists the secondary first).
    starts = {_bare(e.get("from_table")) for e in blends
              if any(_bare(t) == _bare(e.get("from_table")) for t in tables)}
    if len(starts) == 1 and _bare(source) not in starts:
        source = next(t for t in tables if _bare(t) in starts)
    edges = [e for e in blends if _bare(e.get("from_table")) == _bare(source)]
    if not edges:
        return None
    src_cols = _columns_of(source, columns)
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    gbt = list(unit.get("group_by_tables") or [])
    parts: dict[str, dict] = {}
    taken: set[int] = set()
    for sec in dict.fromkeys(e["to_table"] for e in edges):
        sec_cols = _columns_of(sec, columns)
        def on_secondary(m: dict) -> bool:
            # A shelf aggregate of a column only the secondary holds, or a calc of the
            # secondary's (every field its formula names is the secondary's alone).
            if not m.get("formula"):
                name = str(m.get("name") or "").lower()
                return name in sec_cols and name not in src_cols
            # A calc's pill carries a compute-using whether or not its formula calls a
            # table calculation; only one that calls one runs over the blended rows.
            refs = {r.lower() for r in re.findall(r"\[([^\[\]]+)\]", str(m["formula"]))}
            return bool(refs) and refs <= sec_cols and not refs & src_cols \
                and not (m.get("table_calc") and _calls_table_calc(str(m["formula"])))
        ms = [m for m in unit.get("measures") or [] if on_secondary(m)]
        # Its compute-using names the PRIMARY's view fields; a calc of the secondary that
        # calls no table calculation is a plain aggregate over the secondary's rows.
        sec_ms = [{k: v for k, v in m.items() if k != "table_calc"} for m in ms]
        fs = [f for f in unit.get("filters") or []
              if f.get("table") and _bare(f["table"]) == _bare(sec)]
        # A grouping column only the secondary holds: shown, never grouped by (its one value
        # per row of the primary, or '*').
        dims = [g for i, g in enumerate(unit.get("group_by") or [])
                if str(g).lower() in sec_cols and str(g).lower() not in src_cols
                and (not (i < len(gbt) and gbt[i]) or _bare(gbt[i]) == _bare(sec))]
        if not ms and not fs and not dims:
            continue
        links = []
        for e in edges:
            if e["to_table"] != sec:
                continue
            col = str(e.get("from_column") or "").lower()
            if col in gb:
                i = gb.index(col)
                hint = gbt[i] if i < len(gbt) else None
                if not hint or _bare(hint) == _bare(source):
                    links.append((unit["group_by"][i], str(e["to_column"])))
        parts[sec] = {"measures": sec_ms, "filters": fs, "links": links, "dims": dims}
        taken |= {id(x) for x in ms + fs}
    if not parts:
        return None
    blended = set(parts)
    shown = {str(g).lower() for p in parts.values() for g in p["dims"]}
    keep = [i for i, g in enumerate(unit.get("group_by") or []) if str(g).lower() not in shown]
    primary = dict(unit, measures=[m for m in unit.get("measures") or [] if id(m) not in taken],
                   filters=[f for f in unit.get("filters") or [] if id(f) not in taken],
                   group_by=[unit["group_by"][i] for i in keep],
                   group_by_tables=[gbt[i] if i < len(gbt) else None for i in keep]
                   if gbt else gbt,
                   warehouse_tables=[source] + [t for t in tables
                                                if t not in blended and t != source],
                   join_graph=[e for e in unit.get("join_graph") or []
                               if str(e.get("kind")) != "blend"])
    return {"primary": primary, "secondary": parts}


def _blend_statement(unit: dict, blended: dict, again, rules, dialect: str) -> Result:
    """A BLEND as one statement: the primary unit's rows (`p`), and each secondary table's
    measures aggregated over the active links under its own filters (`s1`, ...), LEFT JOINed
    on the links, NULL matching NULL (seen live: a NULL member of the primary gets the
    secondary's NULL member; a member only the secondary has is not shown). A filter on the
    secondary also drops the primary's rows whose links it leaves no row for (an inner join).
    A dimension of the secondary never splits the primary's rows: each shows its one value
    there, or '*' (`agg.single_value`). With no active link the secondary's one total row is
    beside every row (seen live)."""
    res = Result()
    primary = blended["primary"]
    gb = list(primary.get("group_by") or [])
    p_res = again(primary) if (gb or primary.get("measures")) else None
    if p_res is not None and not p_res.sql:
        return p_res
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        res.coverage = REFUSED
        res.reason = (f"a data blend joins the secondary on its links, NULL matching NULL, "
                      f"which has no form for {dialect}")
        return res
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    names_of: dict[str, str] = {}
    froms: list[str] = []
    if p_res is not None:
        pg, pv = _leaf_names(p_res, primary)
        names_of.update({v: f"p.{v}" for v in pv})
        froms.append(f"(\n{p_res.sql}\n) p")
        for k in ("rules_used", "provisional_rules", "unknown", "untranslatable", "gotchas"):
            getattr(res, k).extend(getattr(p_res, k))
    for k, (sec, part) in enumerate(blended["secondary"].items(), start=1):
        links = part["links"]
        attrs = [{"name": d, "formula": None, "formula_language": None,
                  "aggregation": "single_value"} for d in part["dims"]]
        sub = dict(unit, measures=part["measures"] + attrs, filters=part["filters"],
                   warehouse_tables=[sec], join_graph=[],
                   group_by=[c for _g, c in links], group_by_tables=[sec for _ in links],
                   top_n=None, sort=None)
        sub.pop("totals", None)
        r = again(sub)
        if not r.sql:
            res.coverage = REFUSED
            res.reason = f"the blend's secondary table {sec}: {r.reason}"
            return res
        sg, sv = _leaf_names(r, sub)
        names_of.update({v: f"s{k}.{v}" for v in sv})
        if p_res is None:
            froms.append(f"(\n{r.sql}\n) s{k}")
        elif links:
            on = " AND ".join(tmpl.format(f"p.{pg[gb.index(g)]}", f"s{k}.{sg[i]}")
                              for i, (g, _c) in enumerate(links))
            # A filter on the secondary also leaves out the primary's rows whose links keep
            # no secondary row (seen live): the join keeps only linked rows then.
            kind = "INNER JOIN" if part["filters"] else "LEFT JOIN"
            froms.append(f"{kind} (\n{r.sql}\n) s{k} ON {on}")
        else:
            froms.append(f"CROSS JOIN (\n{r.sql}\n) s{k}")
        for x in ("rules_used", "provisional_rules", "unknown", "untranslatable", "gotchas"):
            getattr(res, x).extend(getattr(r, x))
    keyed = dict(zip([str(g).lower() for g in gb], pg if p_res is not None else []))
    keys = []
    for g in unit.get("group_by") or []:
        if str(g).lower() in keyed:
            keys.append(f"p.{keyed[str(g).lower()]}")
        elif _measure_alias(g) in names_of:
            keys.append(f"{names_of[_measure_alias(g)]} AS {g}")
    vals = [names_of[_measure_alias(m["name"])] + f" AS {_measure_alias(m['name'])}"
            for m in unit.get("measures") or [] if _measure_alias(m["name"]) in names_of]
    res.sql = "SELECT " + ",\n       ".join(keys + vals) + "\nFROM   " + "\n".join(froms)
    res.rules_used.append("engine.blend")
    res.coverage = PARTIAL if res.untranslatable else TRANSLATED
    return res


def _leaf_names(res: Result, unit: dict) -> tuple[list[str], list[str]]:
    """The output names of a compiled unit's grouping columns and of the measures it holds."""
    sql = res.sql or ""
    groups = [(_measure_alias(g) if re.search(rf"\bAS {re.escape(_measure_alias(g))}\b", sql)
               else str(g)) for g in unit.get("group_by") or []]
    vals = [_measure_alias(m["name"]) for m in unit.get("measures") or []
            if re.search(rf"\bAS {re.escape(_measure_alias(m['name']))}\b", sql)]
    return groups, vals


def _filtered_by(graph: list[dict], table: str) -> set[str]:
    """The tables whose filters reach `table` (it included): along active relationships from
    the one side to the many side, both ways where the relationship filters both ways, and
    along a many-to-many one (or one whose multiplicity the model does not state) from its
    `to` table to its `from` table, the direction the join plan walks it (`_plan_joins`)."""
    out, frontier = {_bare(table)}, [_bare(table)]
    while frontier:
        cur = frontier.pop()
        for e in graph:
            if e.get("is_active") is False \
                    or str(e.get("kind") or "relationship") in _NOT_WALKED:
                continue
            fs, ts = _card_ends(e.get("cardinality"))
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
            for near, far, near_end, far_end, forward in ((a, b, fs, ts, True),
                                                          (b, a, ts, fs, False)):
                if near != cur or far in out:
                    continue
                unstated = far_end is None or (far_end == "many" and near_end == "many")
                if far_end == "one" or both or (forward and unstated):
                    out.add(far)
                    frontier.append(far)
    return out


def _context_tested(formula: str, measures: dict[str, str] | None,
                    table_of: dict[str, str] | None) -> set[str]:
    """The warehouse tables (bare) whose context a DAX formula tests (`_CONTEXT_TESTS`),
    through the measures it names."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}
    out: set[str] = set()
    try:
        toks = _expanded_tokens(formula, measures)
    except CompileError:
        return out
    for i, (k, v) in enumerate(toks):
        if not (k == "func" and v.upper() in _CONTEXT_TESTS and i + 1 < len(toks)
                and toks[i + 1][0] == "lparen"):
            continue
        end, args = _call_args(toks, i + 1)
        for ak, av in (args[0] if end is not None and args else []):
            t = None
            if ak in ("qualified", "quoted_qualified"):
                t = _table_ref(av)
            elif ak == "str" and av.startswith("'"):
                t = av[1:-1].replace("''", "'")
            elif ak == "func" and av.lower() in tmap:
                t = av
            phys = tmap.get(str(t or "").lower())
            if phys:
                out.add(_bare(phys))
    return out


def _detached_statement(unit: dict, plan: _JoinPlan, graph: list[dict], again,
                        measures: dict[str, str] | None = None,
                        table_of: dict[str, str] | None = None) -> Result:
    """A visual that groups by a column of a table its measures' rows do not reach in the
    direction filters flow (one-to-many against a single-direction relationship, or along an
    inactive one). That table filters nothing: every measure has the value of the rows the
    other columns and filters choose, beside EVERY member of the column (the members its own
    table's filters leave, its blank member included), as a grouping over unrelated tables
    crosses their members. A filter on such a table narrows only its members."""
    det = plan.detached
    gb, gbt = list(unit.get("group_by") or []), list(unit.get("group_by_tables") or [])

    def detached_at(col: str, hint: Any) -> str | None:
        hit = det.get(str(col).lower())
        if hit is None:
            return None
        if hint and hit[1] and _bare(hint) != _bare(hit[1]):
            return None
        return hit[0]
    leaf_gb: list = []
    leaf_gbt: list = []
    own: list = []
    for i, g in enumerate(gb):
        t = detached_at(g, gbt[i] if i < len(gbt) else None)
        if t is None:
            leaf_gb.append(g)
            leaf_gbt.append(gbt[i] if i < len(gbt) else None)
        else:
            own.append((i, g, t))
    on_table: dict[str, list[dict]] = {}
    leaf_filters = []
    for f in unit.get("filters") or []:
        t = detached_at(f.get("column"), f.get("table")) if f.get("column") else None
        if t is None:
            leaf_filters.append(f)
        else:
            on_table.setdefault(_bare(t), []).append(f)
    # A test of the visual's context (ISFILTERED, HASONEVALUE, ...) reads the grouping and
    # the filters the leaf statement is compiled without. That changes nothing for a table
    # the detached tables do not filter (the statement's own, by definition); for one they do
    # filter, the detached table itself included, the test would read a context without
    # them: refused by name.
    apart = {_bare(t) for _i, _g, t in own} | set(on_table)
    for m in unit.get("measures") or []:
        if m.get("formula_language") != "dax" or not m.get("formula"):
            continue
        for tested in sorted(_context_tested(m["formula"], measures, table_of)):
            if apart & _filtered_by(graph, tested):
                res = Result()
                res.coverage = REFUSED
                res.reason = (f"measure {m['name']!r} tests the context of {tested} (ISFILTERED, "
                              f"HASONEVALUE, SELECTEDVALUE and their kin) beside a column of "
                              f"{sorted(apart & _filtered_by(graph, tested))[0]}, which does "
                              f"not filter the measure's rows and is listed apart from them")
                return res
    leaf_unit = dict(unit, group_by=leaf_gb, filters=leaf_filters)
    leaf_unit.pop("group_by_tables", None)
    if any(leaf_gbt):
        leaf_unit["group_by_tables"] = leaf_gbt
    if unit.get("top_n"):
        res = Result()
        res.coverage = REFUSED
        res.reason = ("a Top N beside a column of a table that does not filter the measures: "
                      "the ranking would run over members that all share one value")
        return res
    leaf = again(leaf_unit)
    if not leaf.sql or not own:
        # Only filters on such tables: they choose no row of the measures.
        return leaf
    lgroups, lvals = _leaf_names(leaf, leaf_unit)
    parts, cols = [], {}
    tables = list(dict.fromkeys(t for _i, _g, t in own))
    for k, t in enumerate(tables):
        mine = [(i, g) for i, g, tt in own if tt == t]
        mu = {"unit_id": f"{unit.get('unit_id')}::members", "title": unit.get("title"),
              "measures": [], "group_by": [g for _i, g in mine],
              "group_by_tables": [t] * len(mine), "filters": on_table.get(_bare(t), []),
              "warehouse_tables": [t], "join_graph": []}
        m = again(mu)
        if not m.sql:
            return m
        body = m.sql
        blank = _blank_member_test(t, graph)
        if blank and f"FROM   {t} t" in body:
            # Its BLANK member: a row of the table with every column empty, kept by the
            # table's own filters as any row is.
            b = body.replace(f"FROM   {t} t",
                             f"FROM   (SELECT 1 AS one_) o_\nLEFT JOIN {t} t ON 1 = 0", 1)
            head, sep, tail = b.partition("\nGROUP BY")
            head += ("\n  AND  " if "\nWHERE  " in head else "\nWHERE  ") + f"({blank})"
            body = f"{body}\nUNION\n{head}{sep}{tail}"
        parts.append(f"(\n{body}\n) d{k}")
        for (i, g), name in zip(mine, _leaf_names(m, mu)[0]):
            cols[i] = f"d{k}.{name}"
    li = iter(lgroups)
    keys = [cols[i] if i in cols else f"v.{next(li)}" for i in range(len(gb))]
    sql = ("SELECT " + ",\n       ".join(keys + [f"v.{x}" for x in lvals])
           + "\nFROM   " + "\nCROSS JOIN ".join(parts)
           + "\nCROSS JOIN (\n" + leaf.sql + "\n) v")
    if lvals:
        sql += "\nWHERE  (" + "\n    OR ".join(f"v.{x} IS NOT NULL" for x in lvals) + ")"
    leaf.sql = sql
    leaf.rules_used = list(leaf.rules_used) + ["engine.detached_members"]
    return leaf


# Scalar functions a composite measure is computed through, over its parts' values.
_SCALAR_WRAPPERS = {"IF", "DIVIDE", "ISBLANK", "COALESCE", "ABS", "ROUND", "ROUNDUP",
                    "ROUNDDOWN", "INT", "NOT", "AND", "OR", "SWITCH", "BLANK", "TRUE", "FALSE",
                    "IFERROR", "SIGN", "POWER", "SQRT", "MOD", "IF.EAGER"}


def _statement_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                    graph: list[dict], row_formulas: set[str]) -> bool:
    """Whether a formula compiles only as a statement of its own (a rank, a window, a member
    iterator)."""
    return bool(_rank_form(formula, measures, table_of, row_formulas, graph=graph)
                or _window_form(formula, measures, table_of, row_formulas)
                or _member_grain(formula, measures, table_of, graph, row_formulas)
                or _treatas_form(formula, measures, table_of, graph))


def _treatas_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                  graph: list[dict], depth: int = 0) -> dict | None:
    """`CALCULATE(e, TREATAS(VALUES(T[c]), F[x]), ...)` where T is a table no relationship of
    the model touches (a target table at another grain): {formula, edges, members, over}, or
    None. The formula is the CALCULATE without its TREATAS arguments; each becomes a virtual
    relationship F[x] -> T[c] (`edges`) that only this measure joins along, and T's members are
    always joined (`members`): seen live, the measure reads the rows whose F[x] is one of the
    values T holds in the row's context (its grouping and its own filters; a blank value
    matches the blank), and it replaces the visual's filter on F[x] (`over`)."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures:
            return _treatas_form(measures[key], measures, table_of, graph, depth + 1)
        return None
    head, inner = _call_head(toks)
    if head != "CALCULATE":
        return None
    parts = _split_top(inner)
    tmap = {k.lower(): v for k, v in table_of.items()}
    touched = {_bare(e.get(k)) for e in graph or [] for k in ("from_table", "to_table")}
    keep, edges, members, over = [parts[0]], [], [], []
    for arg in parts[1:]:
        th, tin = _call_head(arg)
        if th != "TREATAS":
            keep.append(arg)
            continue
        tparts = _split_top(tin)
        if len(tparts) != 2:
            return None
        vh, vin = _call_head(tparts[0])
        if vh not in ("VALUES", "DISTINCT") or len(vin) != 1 \
                or vin[0][0] not in ("qualified", "quoted_qualified") \
                or len(tparts[1]) != 1 \
                or tparts[1][0][0] not in ("qualified", "quoted_qualified"):
            return None
        t_phys = tmap.get(str(_table_ref(vin[0][1]) or "").lower())
        f_phys = tmap.get(str(_table_ref(tparts[1][0][1]) or "").lower())
        if not t_phys or not f_phys or _bare(t_phys) in touched \
                or _bare(t_phys) == _bare(f_phys):
            return None
        c, x = _column_ref(vin[0][1]), _column_ref(tparts[1][0][1])
        edges.append({"from_table": f_phys, "from_column": x, "to_table": t_phys,
                      "to_column": c, "cardinality": "M:M", "kind": "relationship",
                      "is_active": True, "join_type": "inner",
                      "name": f"TREATAS {_bare(t_phys)}.{c} -> {_bare(f_phys)}.{x}"})
        members.append((c, t_phys))
        over.append((x, f_phys))
    if not edges:
        return None
    body = keep[0] if len(keep) == 1 else (
        [("func", "CALCULATE"), ("lparen", "(")]
        + [t for i, a in enumerate(keep) for t in ([("comma", ",")] if i else []) + a]
        + [("rparen", ")")])
    return {"formula": _text_of(body), "edges": edges, "members": members, "over": over}


def _treatas_statement(unit: dict, m: dict, tr: dict, again) -> Result:
    """One measure of `_treatas_form` as a statement: the unit with the measure's virtual
    relationships added to its graph, the target tables' members always joined (inner, a
    blank matching a blank), and the visual's filters on the replaced columns taken off."""
    if len({_bare(t) for _c, t in tr["members"]}) < len(tr["members"]):
        res = Result()
        res.reason = (f"measure {m['name']!r}: two TREATAS of one table's columns in one "
                      f"CALCULATE are each a set of their own (every pair of their values, not "
                      f"the table's rows), which one join of the table's members does not hold")
        return res
    over = {(str(x).lower(), _bare(t)) for x, t in tr["over"]}
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    gbt = list(unit.get("group_by_tables") or [])
    tables = list(unit.get("warehouse_tables") or [])
    grouped = [i for i, g in enumerate(gb)
               if (g, _bare(gbt[i] if i < len(gbt) and gbt[i] else tables[0])) in over]
    if grouped:
        # The visual groups by a column TREATAS replaces: the measure has the value of the
        # row's other columns beside every member of it the visual lists (seen live: every
        # channel row shows the same value; a filter on the channel leaves its members).
        ug = dict(unit, group_by=[g for i, g in enumerate(unit["group_by"]) if i not in grouped],
                  totals=[])
        if gbt:
            ug["group_by_tables"] = [t for i, t in enumerate(gbt) if i not in grouped]
        r = _treatas_statement(ug, m, tr, again)
        if not r.sql:
            return r
        mu = dict(unit, measures=[], totals=[], top_n=None,
                  group_by=[unit["group_by"][i] for i in grouped])
        if gbt:
            mu["group_by_tables"] = [gbt[i] for i in grouped]
        mem = again(mu)
        if not mem.sql:
            return mem
        lg, lv = _leaf_names(r, ug)
        mg = _leaf_names(mem, mu)[0]
        li, mi = iter(lg), iter(mg)
        keys = [f"d.{next(mi)}" if i in grouped else f"v.{next(li)}" for i in range(len(gb))]
        r.sql = ("SELECT " + ",\n       ".join(
            [f"{k} AS {_out_name(k.split('.', 1)[1])}" for k in keys] + [f"v.{x}" for x in lv])
            + "\nFROM   (\n" + _indent(mem.sql) + "\n) d\nCROSS JOIN (\n" + _indent(r.sql)
            + "\n) v" + ("\nWHERE  (" + "\n    OR ".join(f"v.{x} IS NOT NULL" for x in lv)
                          + ")" if lv else ""))
        r.rules_used.append("engine.detached_members")
        return r

    def replaced(f: dict) -> bool:
        return bool(f.get("column")) and (str(f["column"]).lower(), _bare(
            f.get("table") or tables[0])) in over
    homes = [t for t in dict.fromkeys(e["from_table"] for e in tr["edges"])]
    sub = dict(unit, measures=[{**m, "formula": tr["formula"]}],
               filters=[f for f in unit.get("filters") or [] if not replaced(f)],
               join_graph=list(unit.get("join_graph") or []) + tr["edges"],
               warehouse_tables=homes + [t for t in dict.fromkeys(
                   tables + [t for _c, t in tr["members"]]) if t not in homes],
               member_joins=[{"column": c, "table": t} for c, t in tr["members"]])
    r = again(sub)
    if r.sql:
        r.rules_used.append("engine.dax_treatas_table")
        r.gotchas.append(
            f"{m['name']}: TREATAS of "
            + ", ".join(f"{_bare(t)}.{c}" for c, t in tr["members"])
            + ": the rows whose "
            + ", ".join(f"{_bare(t)}.{x}" for x, t in tr["over"])
            + " is one of the values that table holds in the row's context (a blank matches "
              "a blank); the visual's own filter on that column is replaced")
    return r


# Tests of the visual's context: they read which values a column has in the row's context, not
# the rows of its table, so a measure whose only reference to a table is one of these does not
# aggregate that table (`_row_tokens`).
_CONTEXT_TESTS = {"SELECTEDVALUE", "HASONEVALUE", "ISFILTERED", "ISINSCOPE", "ISCROSSFILTERED"}


def _row_tokens(toks: list) -> list:
    """`toks` as far as they name the tables whose rows they aggregate: without the arguments
    of the context tests (`_CONTEXT_TESTS`), and of a CALCULATE without its filter arguments
    (a filter on a dimension's column is read through the relationship from the table the
    expression aggregates, not as rows of the dimension)."""
    out, i = [], 0
    while i < len(toks):
        k, v = toks[i]
        if k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
            up = v.upper()
            if up in _CONTEXT_TESTS or up in ("CALCULATE", "CALCULATETABLE"):
                end, args = _call_args(toks, i + 1)
                if end is not None:
                    if up not in _CONTEXT_TESTS and args:
                        out += [toks[i], toks[i + 1]] + _row_tokens(args[0]) + [toks[end]]
                    i = end + 1
                    continue
        out.append(toks[i])
        i += 1
    return out


def _tables_read(formula: str, measures: dict[str, str], table_of: dict[str, str],
                 rows_only: bool = False) -> set[str]:
    """The warehouse tables (bare) a DAX formula's columns and table names read, through
    the measures it names; with `rows_only`, those whose ROWS it reads (a context test such
    as SELECTEDVALUE reads none)."""
    tmap = {k.lower(): v for k, v in table_of.items()}
    out: set[str] = set()
    try:
        toks = _expanded_tokens(formula, measures)
    except CompileError:
        return out
    if rows_only:
        toks = _row_tokens(toks)
    for k, v in toks:
        t = None
        if k in ("qualified", "quoted_qualified"):
            t = _table_ref(v)
        elif k == "str" and v.startswith("'"):
            t = v[1:-1].replace("''", "'")
        elif k == "func" and v.lower() in tmap:
            t = v
        phys = tmap.get(str(t or "").lower())
        if phys:
            out.add(_bare(phys))
    return out


# Aggregates a composite measure's part may compute over another table than the statement's.
_FOREIGN_AGGREGATES = {"MIN", "MAX", "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTROWS",
                       "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "COUNTBLANK", "SUMX",
                       "AVERAGEX", "MINX", "MAXX", "COUNTX", "CALCULATE", "FIRSTDATE",
                       "LASTDATE"}


def _inline_vars(toks: list) -> list | None:
    """`VAR a = e1 VAR b = e2 RETURN body` as `body` with each variable replaced by its
    parenthesised expression, where that is the same measure: every use of a variable is
    outside any function that changes the context it is evaluated in (only scalar wrappers,
    `_SCALAR_WRAPPERS`, lie between it and the top). A variable is evaluated where it is
    defined, so inside a CALCULATE or an iterator its value would change, and that formula
    is None. A formula without variables is returned as it is."""
    if not toks or toks[0] != ("func", "VAR"):
        return toks
    vars_: dict[str, list] = {}

    def subst(t: list) -> list | None:
        out, stack = [], []
        for i, (k, v) in enumerate(t):
            nxt = t[i + 1] if i + 1 < len(t) else None
            if k == "func" and nxt is not None and nxt[0] == "lparen":
                stack.append(v.upper())
                out.append((k, v))
                continue
            if k == "lparen" and (not out or out[-1][0] != "func"):
                stack.append("(")
            elif k == "rparen" and stack:
                stack.pop()
            if k == "func" and v.upper() in vars_:
                if any(f not in _SCALAR_WRAPPERS and f != "(" for f in stack):
                    return None
                out += [("lparen", "(")] + vars_[v.upper()] + [("rparen", ")")]
                continue
            out.append((k, v))
        return out
    i, n = 0, len(toks)
    while i < n and toks[i] == ("func", "VAR"):
        if i + 2 >= n or toks[i + 1][0] != "func" or toks[i + 2] != ("op", "="):
            return None
        name, j, depth = toks[i + 1][1].upper(), i + 3, 0
        while j < n:
            k, _v = toks[j]
            if k == "lparen":
                depth += 1
            elif k == "rparen":
                depth -= 1
            elif depth == 0 and toks[j] in (("func", "VAR"), ("func", "RETURN")):
                break
            j += 1
        expr = subst(toks[i + 3:j])
        if expr is None or not expr:
            return None
        vars_[name] = expr
        i = j
    if i >= n or toks[i] != ("func", "RETURN"):
        return None
    return subst(toks[i + 1:])


def _filter_reaches(filter_tables: set[str], target: str, graph: list[dict]) -> bool:
    """Whether a filter on any of `filter_tables` (bare) filters `target`'s rows: along active
    relationships from the one side to the many side, both ways where one filters both."""
    seen, todo = {_bare(target)}, [_bare(target)]
    while todo:
        cur = todo.pop()
        for e in graph:
            if e.get("is_active") is False or str(e.get("kind") or "") in _NOT_WALKED:
                continue
            fs, ts = _card_ends(e.get("cardinality"))
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
            for near, far, far_end in ((a, b, ts), (b, a, fs)):
                if near == cur and (far_end == "one" or both) and far not in seen:
                    seen.add(far)
                    todo.append(far)
    return bool(seen & {_bare(t) for t in filter_tables})


def _distributed(toks: list, measures: dict[str, str], table_of: dict[str, str],
                 graph: list[dict]) -> list:
    """`CALCULATE(expr, filters)` whose `expr` is scalar wrappers and operators over leaves (a
    measure, an aggregate, a literal) as `expr` with each leaf in its own `CALCULATE(leaf,
    filters)`: a scalar function does not read the context, so the two are the same measure,
    and the leaves can then be read over different tables (`_composite_form`). A filter that
    does not reach a leaf's table (no relationship carries it there) is not given to that
    leaf: in DAX it filters nothing there either. Anything else is returned as it is."""
    head, inner = _call_head(toks)
    if head != "CALCULATE":
        return toks
    args = _split_top(inner)
    if len(args) < 2:
        return toks
    expr, filters = args[0], args[1:]
    if any(k in ("qualified", "quoted_qualified") or (k == "func" and v.upper() == "VAR")
           for k, v in expr):
        return toks
    ftabs = [_tables_read(_text_of(f), measures, table_of) for f in filters]

    def wrap(leaf: list) -> list:
        own = _tables_read(_text_of(leaf), measures, table_of, rows_only=True)
        keep = [f for f, ft in zip(filters, ftabs)
                if not (len(own) == 1 and ft and graph
                        and not _filter_reaches(ft, next(iter(own)), graph))]
        if not keep:
            return leaf
        out = [("func", "CALCULATE"), ("lparen", "(")] + leaf
        for f in keep:
            out += [("comma", ",")] + f
        return out + [("rparen", ")")]

    def walk(t: list) -> list | None:
        out, i = [], 0
        while i < len(t):
            k, v = t[i]
            if k == "func" and i + 1 < len(t) and t[i + 1][0] == "lparen":
                end, fargs = _call_args(t, i + 1)
                if end is None:
                    return None
                if v.upper() in _SCALAR_WRAPPERS:
                    out += [t[i], t[i + 1]]
                    for j, a in enumerate(fargs):
                        w = walk(a)
                        if w is None:
                            return None
                        out += ([("comma", ",")] if j else []) + w
                    out.append(("rparen", ")"))
                else:
                    out += wrap(t[i:end + 1])
                i = end + 1
                continue
            if k == "col":
                out += wrap([t[i]])
            else:
                out.append(t[i])
            i += 1
        return out
    done = walk(expr)
    return done if done is not None else toks


def _composite_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                    graph: list[dict], row_formulas: set[str], home: str | None = None
                    ) -> tuple[list, list[str]] | None:
    """A measure that is a scalar expression over parts, at least one of which compiles only
    as a statement of its own: (the expression's tokens with each part replaced by a
    `[__part<k>]` reference, each part's formula). None for any other formula (a top-level
    form, one with a variable or a bare column, or one whose parts are all plain).

    With `home` (the statement's table), a part that aggregates ONE other table only
    (`MIN(Date[Date])` beside a measure of the fact) is such a part too: in DAX it is that
    table's aggregate in the row's context, which a statement over that table computes
    (`_measure_homes`), joined on the visual's rows."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if _statement_form(formula, measures, table_of, graph, row_formulas):
        return None
    if home is not None and len(_tables_read(formula, measures, table_of, rows_only=True)) < 2:
        home = None
    if home is not None:
        # A measure over two fact tables is as often written with variables or inside one
        # CALCULATE (`VAR v = [Sales] - [Budget] RETURN ...`, `CALCULATE(DIVIDE([Orders],
        # [Sessions]), filter)`) as plainly; both are rewritten to the plain form where that
        # is the same measure (`_inline_vars`, `_distributed`).
        toks = _inline_vars(toks)
        if toks is None:
            return None
        toks = _distributed(toks, measures, table_of, graph)
    parts: list[str] = []
    expanding: list[str] = []

    def several(f: str) -> bool:
        return len(_tables_read(f, measures, table_of, rows_only=True)) >= 2

    def expanded(t: list) -> list | None:
        """A measure or CALCULATE that itself reads several tables, as its own composite
        expression (variables inlined, CALCULATE distributed over its leaves), to be split
        further; None where that does not apply."""
        if home is None:
            return None
        if len(t) == 1 and t[0][0] == "col":
            key = _column_ref(t[0][1]).lower()
            if key not in measures or key in row_formulas or key in expanding \
                    or not several(measures[key]) or len(expanding) > 8:
                return None
            try:
                inner = _strip_parens(tokenize(measures[key]))
            except CompileError:
                return None
            inner = _inline_vars(inner)
            if inner is None:
                return None
            expanding.append(key)
            try:
                w = walk(_distributed(inner, measures, table_of, graph))
            finally:
                expanding.pop()
            return None if w is None else [("lparen", "(")] + w + [("rparen", ")")]
        head, inner = _call_head(t)
        if head != "CALCULATE" or not several(_text_of(t)):
            return None
        args = _split_top(inner)
        if len(args) < 2:
            return None
        body = []
        for k, v in args[0]:
            if k == "col":
                key = _column_ref(v).lower()
                if key in measures and key not in row_formulas and several(measures[key]):
                    try:
                        sub = _inline_vars(_strip_parens(tokenize(measures[key])))
                    except CompileError:
                        return None
                    if sub is None:
                        return None
                    body += [("lparen", "(")] + sub + [("rparen", ")")]
                    continue
            body.append((k, v))
        new = [("func", "CALCULATE"), ("lparen", "(")] + body
        for f in args[1:]:
            new += [("comma", ",")] + f
        new.append(("rparen", ")"))
        dist = _distributed(new, measures, table_of, graph)
        if _call_head(dist)[0] == "CALCULATE":
            return None
        w = walk(dist)
        return None if w is None else [("lparen", "(")] + w + [("rparen", ")")]

    def own_statement(f: str) -> bool:
        if _statement_form(f, measures, table_of, graph, row_formulas):
            return True
        if home is None:
            return False
        # An aggregate (or a measure) of one other table's rows; a context test such as
        # HASONEVALUE reads no rows and stays in the statement.
        try:
            ft = _strip_parens(tokenize(f))
        except CompileError:
            return False
        fh = _call_head(ft)[0]
        agg = (fh in _FOREIGN_AGGREGATES or (len(ft) == 1 and ft[0][0] == "col")
               or (ft and ft[0] == ("func", "VAR")) or fh in _SCALAR_WRAPPERS)
        read = _tables_read(f, measures, table_of, rows_only=True)
        return agg and len(read) == 1 and _bare(home) not in read

    def walk(t: list) -> list | None:
        out, i = [], 0
        while i < len(t):
            k, v = t[i]
            if k == "func" and i + 1 < len(t) and t[i + 1][0] == "lparen":
                end, args = _call_args(t, i + 1)
                if end is None:
                    return None
                up = v.upper()
                scalar_minmax = up in ("MIN", "MAX") and len(args) == 2
                if up in _SCALAR_WRAPPERS or scalar_minmax:
                    out += [t[i], t[i + 1]]
                    for j, a in enumerate(args):
                        w = walk(a)
                        if w is None:
                            return None
                        if j:
                            out.append(("comma", ","))
                        out += w
                    out.append(("rparen", ")"))
                else:
                    ex = expanded(t[i:end + 1])
                    if ex is not None:
                        out += ex
                    else:
                        parts.append(_text_of(t[i:end + 1]))
                        out.append(("col", f"[__part{len(parts) - 1}]"))
                i = end + 1
                continue
            if k == "func" and v.upper() in ("VAR", "RETURN"):
                return None
            if k in ("qualified", "quoted_qualified"):
                return None
            if k == "col":
                key = _column_ref(v).lower()
                if key not in measures or key in row_formulas:
                    return None
                ex = expanded([t[i]])
                if ex is not None:
                    out += ex
                    i += 1
                    continue
                parts.append(measures[key])
                out.append(("col", f"[__part{len(parts) - 1}]"))
                i += 1
                continue
            out.append(t[i])
            i += 1
        return out
    outer = walk(toks)
    if outer is None or not any(own_statement(f) for f in parts):
        return None
    return outer, parts


def _composite_statement(unit: dict, measures: dict[str, str], table_of: dict[str, str],
                         graph: list[dict], row_formulas: set[str], again, rules,
                         dialect: str) -> Result | None:
    """The unit with each composite measure (`_composite_form`) split into its parts: the
    parts compiled as measures of their own (joined on the visual's rows), then each
    composite's expression over its parts' values, row by row with DAX's blank rules. A row
    where every measure the visual shows is blank is left out, as the visual leaves it out."""
    comps: dict[str, tuple[list, list[str]]] = {}
    for m in unit.get("measures") or []:
        if m.get("formula_language") == "dax" and m.get("formula"):
            c = _composite_form(m["formula"], measures, table_of, graph, row_formulas,
                                home=(unit.get("warehouse_tables") or [None])[0])
            if c is not None:
                comps[m["name"]] = c
    if not comps:
        return None
    inner: list[dict] = [m for m in unit.get("measures") or [] if m["name"] not in comps]
    names: dict[str, list[str]] = {}
    for n, (_outer, parts) in comps.items():
        names[n] = []
        for k, f in enumerate(parts):
            pname = f"__{_measure_alias(n)}_part{k}"
            names[n].append(pname)
            inner.append({"name": pname, "formula": f, "formula_language": "dax",
                          "aggregation": None})
    sub = dict(unit, measures=inner)
    r = again(sub)
    if r.coverage == REFUSED or not r.sql:
        return r
    groups, _vals = _leaf_names(r, sub)
    cols = [f"j.{g} AS {g}" if g != _out_name(g) else f"j.{g}" for g in groups]
    shown = []
    for m in unit.get("measures") or []:
        alias = _measure_alias(m["name"])
        if m["name"] in comps:
            outer, _parts = comps[m["name"]]
            text = _text_of(outer)
            for k, pname in enumerate(names[m["name"]]):
                text = text.replace(f"[__part{k}]", f"[{_measure_alias(pname)}]")
            try:
                expr, p = compile_expression(text, "dax", rules, "j", dialect)
            except CompileError as exc:
                res = Result()
                res.reason = f"measure {m['name']!r}: {exc}"
                return res
            r.rules_used += list(p.used)
            cols.append(f"{expr} AS {alias}")
        else:
            cols.append(f"j.{alias}")
        shown.append(cols[-1].rsplit(" AS ", 1)[0])
    body = "\n".join("    " + ln if ln.strip() else ln for ln in r.sql.splitlines())
    r.sql = ("SELECT " + ",\n       ".join(cols) + f"\nFROM   (\n{body}\n) j"
             + ("\nWHERE  " + "\n    OR ".join(f"({e}) IS NOT NULL" for e in shown)
                if shown else ""))
    r.rules_used.append("engine.dax_composite_measure")
    # A total grain of the unit: the same split, each total's parts over its own rows.
    r.totals = []
    for tot in unit.get("totals") or []:
        tu = dict(unit, group_by=list(tot.get("group_by") or []), totals=[])
        gbt = list(unit.get("group_by_tables") or [])
        if gbt:
            gb = list(unit.get("group_by") or [])
            tu["group_by_tables"] = [gbt[gb.index(g)] for g in tu["group_by"]
                                     if g in gb and gb.index(g) < len(gbt)]
        tr = _composite_statement(tu, measures, table_of, graph, row_formulas, again, rules,
                                  dialect) or again(tu)
        r.totals.append({"kind": tot.get("kind"), "group_by": list(tot.get("group_by") or []),
                         "sql": tr.sql or "", "reason": tr.reason or None})
    return r


def _constant_item(item: str, alias: str) -> bool:
    """Whether one select item reads no row of the statement: no column of any alias, no
    aggregate, no subquery."""
    expr = item.rsplit(" AS ", 1)[0] if " AS " in item else item
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_\"]", expr) or "SELECT" in expr.upper() \
            or "__" in expr:
        # A column, a subquery, or a placeholder another statement fills (`__LODAGG1__`).
        return False
    return not re.search(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|STDDEV\w*|VAR\w*|LISTAGG|"
                         r"ARRAY_AGG|ANY_VALUE|BOOL_\w+)\s*\(", expr, re.I)


def _joined_statements(unit: dict, parts: list[tuple[dict, Result]], rules,
                       dialect: str) -> Result:
    """Measures that join the tables differently (USERELATIONSHIP, CROSSFILTER) are one
    statement each, over the same grouping and filters; their rows are joined on the grouping
    columns, NULL matching NULL, keeping a row any of them has (a visual lists a row where any
    value is not blank)."""
    if len(parts) == 1:
        return parts[0][1]
    res = Result()
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        res.coverage = REFUSED
        res.reason = (f"measures joining the tables along different relationships are joined "
                      f"on their rows, NULL matching NULL, which has no form for {dialect}")
        return res
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    gb = list(unit.get("group_by") or [])
    names_of: dict[str, str] = {}
    froms, keyexprs = [], [[] for _ in gb]
    for k, (sub, r) in enumerate(parts):
        if not r.sql:
            res.coverage = REFUSED
            res.reason = r.reason
            res.untranslatable = list(r.untranslatable)
            return res
        groups, vals = _leaf_names(r, sub)
        for v in vals:
            names_of[v] = f"s{k}.{v}"
        if k == 0:
            froms.append(f"(\n{r.sql}\n) s{k}")
        else:
            on = " AND ".join(tmpl.format(f"s{k}.{groups[i]}",
                                          "COALESCE(" + ", ".join(keyexprs[i]) + ")"
                                          if len(keyexprs[i]) > 1 else keyexprs[i][0])
                              for i in range(len(gb)))
            froms.append(f"FULL OUTER JOIN (\n{r.sql}\n) s{k} ON {on}" if gb else
                         f"CROSS JOIN (\n{r.sql}\n) s{k}")
        for i in range(len(gb)):
            keyexprs[i].append(f"s{k}.{groups[i]}")
        res.rules_used += r.rules_used
        res.provisional_rules += r.provisional_rules
        res.unknown += r.unknown
        res.untranslatable += r.untranslatable
    keys = [(f"COALESCE({', '.join(x)})" if len(x) > 1 else x[0])
            + f" AS {_out_name(x[0])}" for x in keyexprs]
    vals = [names_of[_measure_alias(m["name"])] + f" AS {_measure_alias(m['name'])}"
            for m in unit.get("measures") or [] if _measure_alias(m["name"]) in names_of]
    res.sql = "SELECT " + ",\n       ".join(keys + vals) + "\nFROM   " + "\n".join(froms)
    res.rules_used.append("engine.statements_per_relationship")
    failed = [r.reason for _s, r in parts if r.reason]
    res.coverage = PARTIAL if (res.untranslatable or failed) else TRANSLATED
    res.reason = "; ".join(failed) if failed else ""
    return res


_ITERATORS = ("SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX")


def _text_of(toks: list) -> str:
    return " ".join(v for _k, v in toks)


def _strip_parens(toks: list) -> list:
    while len(toks) >= 2 and toks[0][0] == "lparen" and toks[-1][0] == "rparen" \
            and _call_args([("func", "X")] + toks, 1)[0] == len(toks):
        toks = toks[1:-1]
    return toks


# Functions whose value does not depend on the filter context (no table, no column).
_CONTEXT_FREE_FUNCS = {"BLANK", "TRUE", "FALSE", "IF", "DIVIDE", "COALESCE", "ABS", "ROUND",
                       "ROUNDUP", "ROUNDDOWN", "INT", "POWER", "SQRT", "VAR", "RETURN", "NOT",
                       "AND", "OR", "SWITCH", "DATE", "MOD", "SIGN", "EXP", "LN", "LOG"}


def _context_free(key: str, measures: dict[str, str], language: str,
                  seen: tuple = ()) -> bool:
    """Whether measure `key` is the same in every filter context: no column, table or
    aggregate in it or in a measure it names (a constant, such as a parameter written in as
    its value). Inside a row iterator such a measure needs no context transition."""
    formula = measures.get(key)
    if formula is None or key in seen:
        return False
    try:
        toks = _tokens(formula, language)
    except CompileError:
        return False
    for i, (k, v) in enumerate(toks):
        if k in ("qualified", "quoted_qualified"):
            return False
        if k == "col":
            if not _context_free(_column_ref(v).lower(), measures, language, seen + (key,)):
                return False
        elif k == "func":
            nxt = toks[i + 1][0] if i + 1 < len(toks) else None
            if v.upper() not in _CONTEXT_FREE_FUNCS and nxt == "lparen":
                return False
        elif k == "str" and v.startswith("'"):
            return False
    return True


def _not_row_level(toks: list, measures: dict[str, str], row_formulas: set[str]) -> bool:
    """Whether an iterator's expression computes over other rows than the iterated one: a
    measure (a context transition), an aggregate, a CALCULATE or RELATEDTABLE."""
    for k, v in toks:
        if k == "col" and _column_ref(v).lower() in measures \
                and _column_ref(v).lower() not in row_formulas \
                and not _context_free(_column_ref(v).lower(), measures, "dax"):
            return True
        if k == "func" and v.upper() in ("RELATEDTABLE", "CALCULATE", "COUNTROWS", "SUM",
                                         "AVERAGE", "MIN", "MAX", "COUNT", "DISTINCTCOUNT",
                                         "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX"):
            return True
    return False


class _NoTransition(CompileError):
    """A member iterator whose expression aggregates with no context transition: refused by
    name for the measure it is in, never compiled as if CALCULATE were there."""


class _RowValueUnplaced(_NoTransition):
    """A member iterator's test or value reads a column of the iterated row whose one value
    per member is not certain: refused by name for the measure it is in."""


# Functions that only compute over the values given them, so a column read inside them is
# still the iterated row's own value.
_ROW_SCALAR_FUNCS = (_CONTEXT_FREE_FUNCS - {"VAR", "RETURN"}) | {"ISBLANK", "YEAR", "MONTH",
                                                                 "DAY", "QUARTER"}


def _member_row_values(toks: list, phys: str, tmap: dict[str, str], own: set[str],
                       whole: bool, head: str) -> list:
    """An iterator's per-member test or value with each bare column of the iterated table
    (`'Date'[Year] = 2024` in `FILTER('Date', ...)`) read as the member's one value, MIN of it.

    The per-member statement groups the rows by the members' keys, where a bare column is
    not grouped and SQL rejects it. Each member has one value of such a column when the
    column is the key itself (`own`: VALUES' column, a text one grouped by its folded key),
    or when the key is unique (`whole`: the one end of every relationship ending on it), so
    MIN of it is that value. A column read inside an aggregate, an iterator or a
    CALCULATE is over other rows and left as it is."""
    out, stack = [], []
    for i, (k, v) in enumerate(toks):
        if k == "lparen":
            prev = toks[i - 1] if i else None
            stack.append(prev[1].upper() if prev and prev[0] == "func" else "(")
        elif k == "rparen" and stack:
            stack.pop()
        if k in ("qualified", "quoted_qualified") \
                and _bare(tmap.get(str(_table_ref(v) or "").lower()) or "") == _bare(phys) \
                and all(f == "(" or f in _ROW_SCALAR_FUNCS for f in stack):
            if not (whole or _column_ref(v).lower() in own):
                raise _RowValueUnplaced(
                    f"{head} over {_bare(phys)} reads {v.strip()} of each member, and the "
                    f"members' key is not known to be unique (the one end of a relationship), "
                    f"so the member's one value of it is not certain")
            out += [("func", "MIN"), ("lparen", "("), (k, v), ("rparen", ")")]
            continue
        out.append((k, v))
    return out


# What reads a row iterator's row as a filter (a context transition): a CALCULATE, and
# RELATEDTABLE (a CALCULATETABLE). A measure reference is a `col` token, not a call.
_TRANSITIONS = {"CALCULATE", "CALCULATETABLE", "RELATEDTABLE"}
_PLAIN_AGGREGATES = {"SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTBLANK", "COUNTROWS",
                     "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "MIN", "MAX"}


def _untransitioned_aggregate(toks: list) -> str | None:
    """The first aggregate in an iterator's expression that no context transition wraps
    (`SUMX(Customer, SUM(Sales[Quantity]))`), or None. Such an aggregate is evaluated in the
    filter context the iterator runs in, not the row's: every row adds the same whole total
    (Power BI's own answer). Compiling it per member would read it as if CALCULATE were
    there. An iterator nested in it counts too, unless it iterates a RELATEDTABLE."""
    i = 0
    while i < len(toks):
        k, v = toks[i]
        call = k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen"
        if not call:
            i += 1
            continue
        up = v.upper()
        end, args = _call_args(toks, i + 1)
        if end is None:
            return None
        if up in _TRANSITIONS:
            i = end + 1
            continue
        if up in ("MIN", "MAX") and len(args) != 1:
            i += 2                                    # MIN(a, b): a scalar, not an aggregate
            continue
        if up in _PLAIN_AGGREGATES:
            return up
        if up in _ITERATORS and args and _call_head(args[0])[0] != "RELATEDTABLE":
            return up
        i += 2
    return None


def _member_grain(formula: str, measures: dict[str, str], table_of: dict[str, str],
                  graph: list[dict], row_formulas: set[str], depth: int = 0,
                  columns: dict[str, set[str]] | None = None) -> dict | None:
    """A DAX measure that iterates the MEMBERS of a table or a column and evaluates, per
    member, an expression over other rows (`AVERAGEX(Customer, COUNTROWS(RELATEDTABLE(
    Sales)))`, `SUMX(VALUES(Date[Month]), [Sales])`, `COUNTROWS(FILTER(VALUES(Customer[Key]),
    [Sales] > 1000))`): {iterator, table, keys, expr, pred, blank_member}, or None for any
    other formula.

    Each member is a row of the visual's rows at a finer grain (the visual's grouping plus
    the members' keys), so the measure is the statement at that grain, aggregated again
    (`_member_grain_statement`). Iterating a TABLE skips its blank row (seen live: 200
    customers, not 201); VALUES / DISTINCT of a column include its blank member."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures and key not in row_formulas:
            return _member_grain(measures[key], measures, table_of, graph, row_formulas,
                                 depth + 1, columns)
        return None
    head, inner = _call_head(toks)
    # A form rewritten from CALCULATE: its expression is under that CALCULATE's transition.
    transitioned = head == "CALCULATE"
    if head == "CALCULATE":
        # CALCULATE(e, FILTER(VALUES(k) or a table, test of a measure)): e over the members
        # that pass, each read in its own context. For an e that sums its rows that is the
        # sum of e over those members, SUMX(FILTER(...), e) (seen live, the same number).
        cparts = _split_top(inner)
        if len(cparts) != 2 or _call_head(cparts[1])[0] != "FILTER":
            return None
        if _additive(_text_of(cparts[0]), measures):
            head, inner = "SUMX", cparts[1] + [("comma", ",")] + cparts[0]
        else:
            # A non-additive expression over the members that pass, read per member and
            # combined as it combines: DISTINCTCOUNT of the iterated column counts the members
            # that pass (its blank one too, as DISTINCTCOUNT counts a blank); MIN / MAX are
            # the least / greatest of the members' own. Anything else is not such a form.
            eh, ein = _call_head(_strip_parens(cparts[0]))
            fh, fin = _call_head(cparts[1])
            ftab = _split_top(fin)[0] if fin else []
            vh, vin = _call_head(ftab)
            if eh == "DISTINCTCOUNT" and vh in ("VALUES", "DISTINCT") and len(ein) == 1 \
                    and len(vin) == 1 and ein[0][0] in ("qualified", "quoted_qualified") \
                    and vin[0][0] in ("qualified", "quoted_qualified") \
                    and _column_ref(ein[0][1]).lower() == _column_ref(vin[0][1]).lower() \
                    and str(_table_ref(ein[0][1]) or "").lower() == str(
                        _table_ref(vin[0][1]) or "").lower():
                head, inner = "COUNTROWS", cparts[1]
            elif eh in ("MIN", "MAX") and len(_split_top(ein)) == 1:
                head = eh + "X"
                inner = cparts[1] + [("comma", ","), ("func", "CALCULATE"),
                                     ("lparen", "(")] + cparts[0] + [("rparen", ")")]
            else:
                return None
    if head not in _ITERATORS + ("COUNTROWS",):
        return None
    parts = _split_top(inner)
    if head == "COUNTROWS":
        if len(parts) != 1:
            return None
        table_toks, expr = parts[0], None
    else:
        if len(parts) != 2:
            return None
        table_toks, expr = parts
    pred = None
    top = None
    th, tin = _call_head(table_toks)
    if th == "TOPN":
        # TOPN(n, members, order by[, ASC | DESC]): the n members first by the expression
        # (DESC when not said), every member tied with the n-th kept, in the row's context.
        tparts = _split_top(tin)
        if len(tparts) not in (3, 4):
            return None
        nt = _text_of(tparts[0]).replace(" ", "")
        if not nt.isdigit() or int(nt) < 1:
            return None
        order = "DESC"
        if len(tparts) == 4:
            ot = _text_of(tparts[3]).replace(" ", "").upper()
            if ot not in ("ASC", "DESC", "0", "1"):
                return None
            order = "ASC" if ot in ("ASC", "1") else "DESC"
        top = {"n": int(nt), "by": tparts[2], "order": order}
        table_toks = tparts[1]
        th, tin = _call_head(table_toks)
    if th == "FILTER":
        fparts = _split_top(tin)
        if len(fparts) != 2:
            return None
        table_toks, pred = fparts
        th, tin = _call_head(table_toks)
    tmap = {k.lower(): v for k, v in table_of.items()}
    keys: list[tuple[str, str]] = []           # (column, warehouse table)
    if th in ("VALUES", "DISTINCT"):
        if len(tin) != 1 or tin[0][0] not in ("qualified", "quoted_qualified"):
            return None
        phys = tmap.get(str(_table_ref(tin[0][1]) or "").lower())
        if not phys:
            return None
        keys = [(_column_ref(tin[0][1]), phys)]
        own, whole = {keys[0][0].lower()}, False
        table, blank_member = phys, True
        # The values of a column of a table no relationship ends on (a fact's column): its
        # members are the ones its rows hold in the row's context.
        held = not any(_bare(e.get("to_table")) == _bare(phys) and e.get("is_active", True)
                       for e in graph)
    elif th is None and len(table_toks) == 1 and table_toks[0][0] in ("func", "str"):
        name = table_toks[0][1]
        if table_toks[0][0] == "str":
            if not name.startswith("'"):
                return None
            name = name[1:-1].replace("''", "'")
        phys = tmap.get(name.lower())
        if not phys:
            return None
        # The table's key: the column the relationships INTO it end on. A table no
        # relationship ends on (a fact) has no key here, and its rows are not members.
        ends = sorted({str(e.get("to_column")) for e in graph
                       if _bare(e.get("to_table")) == _bare(phys) and e.get("to_column")
                       and e.get("is_active", True)})
        rows = False
        if len(ends) == 1:
            keys = [(ends[0], phys)]
            # The key is unique when every relationship ending on it ends on its one side.
            own = {ends[0].lower()}
            whole = all(_card_ends(e.get("cardinality"))[1] == "one" for e in graph
                        if _bare(e.get("to_table")) == _bare(phys)
                        and str(e.get("to_column")) == ends[0] and e.get("is_active", True))
        elif not ends and columns:
            # A table no relationship ends on (a fact): each ROW is iterated, and a measure
            # in it is read in that row's context (a context transition), which filters every
            # column of the table to the row's values. Rows alike in every column are one
            # filter, so the members are the distinct rows, each weighed by how many rows it
            # stands for (`rows`).
            cols = next((sorted(c) for t, c in columns.items() if _bare(t) == _bare(phys)), [])
            if not cols:
                return None
            keys, rows = [(c, phys) for c in cols], True
            own, whole = None, True             # every column is a key the statement groups
        else:
            return None
        table, blank_member, held = phys, rows, False
    else:
        return None
    body = [t for t in (expr, pred, top["by"] if top else None) if t]
    if head == "COUNTROWS" and pred is None and top is None:
        return None
    if not any(_not_row_level(t, measures, row_formulas) for t in body):
        return None
    # A FILTER's test is read row by row with no transition in either form.
    for t in ([pred] if transitioned else body):
        agg = _untransitioned_aggregate(t) if t else None
        if agg:
            raise _NoTransition(
                f"{head} over {_bare(table)} evaluates {agg} with no context transition (no "
                f"CALCULATE or measure around it): each member adds the total of the visual's "
                f"whole context, which is not compiled")

    def per_member(t: list) -> str:
        # RELATEDTABLE(R) is R's rows of the member: at the member's grain, R itself.
        out, i = [], 0
        while i < len(t):
            k, v = t[i]
            if k == "func" and v.upper() == "RELATEDTABLE" and i + 1 < len(t) \
                    and t[i + 1][0] == "lparen":
                end, args = _call_args(t, i + 1)
                if end is None or len(args) != 1:
                    raise CompileError("RELATEDTABLE takes one table")
                out += args[0]
                i = end + 1
                continue
            out.append((k, v))
            i += 1
        return _text_of(out)
    # A bare column of the iterated row is the member's one value (raises by name when that
    # is not certain). A table iterated row by row groups every column, so it reads as it is.
    if own is not None:
        expr, pred = [None if t is None else _member_row_values(t, table, tmap, own, whole,
                                                                head) for t in (expr, pred)]
    if top and own is not None:
        top = {**top, "by": _member_row_values(top["by"], table, tmap, own, whole, head)}
    try:
        return {"iterator": head, "table": table, "keys": keys,
                "expr": per_member(expr) if expr else None,
                "pred": per_member(pred) if pred else None,
                "top": ({**top, "by": per_member(top["by"])} if top else None),
                "blank_member": blank_member, "held": held,
                "rows": bool(th is None and blank_member and len(keys) > 1)}
    except CompileError:
        return None


def _dax_table_name(grain: dict, table_of: dict[str, str]) -> str:
    """The model's name of the table a member iterator iterates, quoted for DAX."""
    name = next((k for k, v in table_of.items() if _bare(v) == _bare(grain["table"])),
                _bare(grain["table"]))
    return "'" + str(name).replace("'", "''") + "'"


def _formula_home(formula: str, measures: dict[str, str], table_of: dict[str, str],
                  default: str) -> str:
    """The one warehouse table a formula aggregates (the first one it names), else
    `default`."""
    tmap = {k.lower(): v for k, v in table_of.items()}
    for k, v in _expanded_tokens(formula, measures):
        t = _table_ref(v) if k in ("qualified", "quoted_qualified") else (
            v if k == "func" and v.lower() in tmap else None)
        phys = tmap.get(str(t or "").lower())
        if phys:
            return phys
    return default


def _member_grain_statement(unit: dict, m: dict, grain: dict, rules, dialect: str,
                            again, table_of: dict[str, str], measures: dict[str, str]
                            ) -> Result:
    """One member-iterating measure (`_member_grain`) as a statement: the unit at the grain
    of the visual's grouping plus the members' keys, with the expression (and the FILTER's
    predicate) evaluated per member, then aggregated to the visual's rows by the iterator's
    own aggregate (blanks skipped, as DAX skips them)."""
    res = Result()
    groups = list(unit.get("group_by") or [])
    gtabs = list(unit.get("group_by_tables") or [None] * len(groups))
    tables = list(unit.get("warehouse_tables") or [])
    sub_g, sub_t = list(groups), list(gtabs)
    key_names: list[str] = []
    for col, phys in grain["keys"]:
        hit = next((i for i, (g, t) in enumerate(zip(groups, gtabs))
                    if str(g).lower() == str(col).lower()
                    and _bare(t or tables[0]) == _bare(phys)), None)
        if hit is None:
            sub_g.append(col)
            sub_t.append(phys)
        key_names.append(col)
    hidden = []
    if grain["expr"]:
        hidden.append({"name": "member value", "formula": grain["expr"],
                       "formula_language": "dax"})
    if grain["pred"]:
        hidden.append({"name": "member test", "formula": f"IF ( {grain['pred']} , 1 )",
                       "formula_language": "dax"})
    if grain.get("top"):
        hidden.append({"name": "member rank by", "formula": grain["top"]["by"],
                       "formula_language": "dax"})
    if grain.get("rows"):
        # How many of the table's rows each distinct row stands for.
        hidden.append({"name": "member rows", "formula": f"COUNTROWS ( {_dax_table_name(grain, table_of)} )",
                       "formula_language": "dax"})
    exists = (grain.get("held") and not grain.get("rows") and len(_tables_read(
        " ".join(h["formula"] for h in hidden), measures, table_of)
        | {_bare(grain["table"])}) > 1)
    if exists:
        # A value or test reading another table (`MIN(Date[Date])` in the test) is joined on
        # the rows from a statement of its own, which lists rows of the members' grain that
        # the iterated table does not hold in that row's context. The members are only the
        # ones it holds (VALUES in the row's context): a row of the table there.
        hidden.append({"name": "member exists",
                       "formula": f"COUNTROWS ( {_dax_table_name(grain, table_of)} )",
                       "formula_language": "dax"})
    home = _formula_home(" ".join(h["formula"] for h in hidden), measures, table_of,
                         tables[0] if tables else grain["table"])
    sub = {k: v for k, v in unit.items() if k not in ("totals", "top_n", "sort")}
    sub.update(group_by=sub_g, group_by_tables=sub_t, measures=hidden, blank_rows="hidden",
               warehouse_tables=[home] + [t for t in dict.fromkeys(tables + [grain["table"]])
                                          if _bare(t) != _bare(home)])
    r = again(sub)
    if not r.sql or r.coverage == REFUSED:
        res.coverage = REFUSED
        res.reason = (f"measure {m['name']!r} iterates the members of {grain['table']}: "
                      f"{r.reason or 'the statement at their grain does not compile'}")
        res.untranslatable = list(r.untranslatable)
        return res
    names, vals = _leaf_names(r, sub)
    if len(vals) != len(hidden):
        res.coverage = REFUSED
        res.reason = f"measure {m['name']!r}: the per-member values did not compile"
        return res
    outer = names[:len(groups)]
    key_cols = [names[sub_g.index(k)] if k in sub_g else None for k in key_names]
    if exists:
        present, vals = f"g.{vals[-1]}", vals[:-1]
    if grain.get("rows"):
        vals_wo = vals[:-1]
    else:
        vals_wo = vals
    rank_by = None
    if grain.get("top"):
        # The rank-by value is the hidden measure after the value and the test.
        k = (1 if grain["expr"] else 0) + (1 if grain["pred"] else 0)
        rank_by = vals_wo[k]
        vals = [v for v in vals if v != rank_by]
    val = f"g.{vals[0]}" if grain["expr"] else None
    weight = f"g.{vals[-1]}" if grain.get("rows") else None
    test = (f"g.{vals[-2 if weight else -1]} = 1" if grain["pred"] else None)
    if weight:
        # Each distinct row counts as many times as rows it stands for.
        v = f"CASE WHEN {test} THEN {val} END" if test and val else val
        it = grain["iterator"]
        if it == "COUNTROWS":
            agg = f"NULLIF(SUM(CASE WHEN {test} THEN {weight} END), 0)"
        elif it == "SUMX":
            agg = f"SUM({weight} * {v})"
        elif it == "AVERAGEX":
            agg = (f"SUM({weight} * {v}) / NULLIF(SUM(CASE WHEN {v} IS NOT NULL THEN "
                   f"{weight} END), 0)")
        elif it in ("MINX", "MAXX"):
            agg = f"{it[:3]}({v})"
        else:
            agg = f"NULLIF(SUM(CASE WHEN {v} IS NOT NULL THEN {weight} END), 0)"
    elif grain["iterator"] == "COUNTROWS":
        agg = f"NULLIF(COUNT(CASE WHEN {test} THEN 1 END), 0)"
    else:
        emit = (rules.function("dax", grain["iterator"], dialect) or {}).get("emit") or {}
        tmpl = emit.get("row_template") or emit.get("template")
        if not tmpl:
            res.coverage = REFUSED
            res.reason = f"no rule row for {grain['iterator']}"
            return res
        arg = f"CASE WHEN {test} THEN {val} END" if test else val
        agg = _apply({"template": tmpl}, [arg], grain["iterator"])
    where = ([f"g.{k} IS NOT NULL" for k in key_cols if k] if not grain["blank_member"]
             else [])
    if exists:
        where.append(f"{present} IS NOT NULL")
    # A text column grouped by its folded key (`engine.text_key_group`) shows one spelling
    # per member at the finer grain; at the visual's grain the spellings are one value again.
    folded = {n for n in outer if re.search(rf"(?m)^(?:SELECT|\s+)\s*MIN\(.*\) AS {re.escape(n)},?$",
                                            r.sql.split("\nFROM", 1)[0])
              and "OVER (" not in r.sql.split("\nFROM", 1)[0].split(f" AS {n}")[0][-200:]}
    sel = [(f"MIN(g.{n}) AS {n}" if n in folded else f"g.{n} AS {n}") for n in outer]
    sel.append(f"{agg} AS {_measure_alias(m['name'])}")
    inner_sql = r.sql
    if rank_by:
        # TOPN's members: ranked within the visual's row (RANK: every member tied with the
        # n-th is kept), a BLANK ordered as zero.
        t = grain["top"]
        part = f"PARTITION BY {', '.join('g0.' + n for n in outer)} " if outer else ""
        base = r.sql
        if (t["order"] == "ASC" or grain["iterator"] == "COUNTROWS") and not grain["held"] \
                and not grain.get("rows") and len(grain["keys"]) == 1:
            # The members TOPN ranks are every member of the column (its table's own filters
            # only), a member with no rows in the row's context ranking as a BLANK: first when
            # ascending (seen live: the bottom two products of a year are a never-sold one
            # and the least sold), counted by COUNTROWS.
            base = _every_member_rows(unit, grain, r, sub, names, again, rules, dialect)
            if base is None:
                res.coverage = REFUSED
                res.reason = (f"measure {m['name']!r}: TOPN ranks every member of "
                              f"{grain['table']}, whose members do not compile")
                return res
        if exists:
            # Only the members the iterated table holds in the row's context are ranked.
            base = (f"SELECT x.*\nFROM   (\n{_indent(r.sql)}\n) x\n"
                    f"WHERE  x.{present[2:]} IS NOT NULL")
        inner_sql = (f"SELECT g0.*,\n       RANK() OVER ({part}ORDER BY COALESCE(g0.{rank_by}, 0) "
                     f"{t['order']}) AS member_rank\nFROM   (\n" + _indent(base) + "\n) g0")
        where.append(f"g.member_rank <= {int(t['n'])}")
    sql = ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n"
           + "\n".join("    " + ln for ln in inner_sql.splitlines()) + "\n) g")
    if where:
        sql += "\nWHERE  " + "\n  AND  ".join(where)
    if outer:
        sql += "\nGROUP BY " + ", ".join(_Parser._text_key(f"g.{n}") if n in folded
                                          else f"g.{n}" for n in outer)
        if unit.get("blank_rows") == "hidden":
            sql += f"\nHAVING {agg} IS NOT NULL"
    res.sql = sql
    res.rules_used = list(r.rules_used) + ["engine.dax_member_iterator"]
    res.provisional_rules, res.unknown = list(r.provisional_rules), list(r.unknown)
    res.untranslatable = list(r.untranslatable)
    res.coverage = PARTIAL if r.coverage == PARTIAL else TRANSLATED
    res.reason = r.reason if r.coverage == PARTIAL else ""
    res.gotchas.append(
        f"{m['name']}: {grain['iterator']} over the members of {grain['table']} "
        f"({', '.join(k for k in key_names)}): the value per member, at the visual's grouping "
        f"and that key, then {'counted where the test holds' if grain['iterator'] == 'COUNTROWS' else 'aggregated'}"
        + ("; the table's blank row is not iterated" if not grain["blank_member"] else
           "; the blank member is a member"))
    return res


def _every_member_rows(unit: dict, grain: dict, r: Result, sub: dict, names: list[str],
                       again, rules, dialect: str) -> str | None:
    """The member grain's rows with every member of the iterated column beside each of the
    visual's rows: the statement's own rows, and a row of NULL values for each member it does
    not hold there (members of the column's table under that table's own filters)."""
    col, phys = grain["keys"][0]
    mu = {"unit_id": f"{unit.get('unit_id')}::members", "title": unit.get("title"),
          "measures": [], "group_by": [col], "group_by_tables": [phys],
          "filters": [f for f in unit.get("filters") or []
                      if f.get("table") and _bare(f["table"]) == _bare(phys)],
          "warehouse_tables": [phys], "join_graph": []}
    mem = again(mu)
    op = rules.operator("null_safe_eq", dialect)
    if not mem.sql or op is None:
        return None
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    groups = names[:len(sub["group_by"])]
    vals = [n for n in names[len(sub["group_by"]):]] if len(names) > len(sub["group_by"]) \
        else []
    gb = [str(g).lower() for g in sub["group_by"]]
    k = gb.index(str(col).lower())
    outer = [g for i, g in enumerate(groups) if i != k]
    mname = _leaf_names(mem, mu)[0][0]
    leaf_names, leaf_vals = _leaf_names(r, sub)
    sel = [(f"mm.{mname}" if i == k else f"o.{g}") + f" AS {g}" for i, g in enumerate(groups)]
    sel += [f"NULL AS {v}" for v in leaf_vals]
    on = " AND ".join([nse.format(f"v.{g}", f"o.{g}") for g in outer]
                      + [nse.format(f"v.{groups[k]}", f"mm.{mname}")])
    o = (f"(\n    SELECT DISTINCT {', '.join('x.' + g for g in outer)}\n    FROM   (\n"
         + _indent(_indent(r.sql)) + "\n    ) x\n) o\nCROSS JOIN " if outer else "")
    return (f"SELECT {', '.join(groups + leaf_vals)}\nFROM   (\n{_indent(r.sql)}\n) v0\n"
            f"UNION ALL\nSELECT " + ",\n       ".join(sel) + "\nFROM   " + o
            + f"(\n{_indent(mem.sql)}\n) mm\nWHERE  NOT EXISTS (\n    SELECT 1\n    FROM   (\n"
            + _indent(_indent(r.sql)) + f"\n    ) v\n    WHERE  {on}\n)")


def _rank_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
               row_formulas: set[str], depth: int = 0,
               graph: list[dict] | None = None) -> dict | None:
    """`RANKX(ALL(T[c]) | ALLSELECTED(T[c]), expr[, value[, ASC | DESC[, SKIP | DENSE]]])`
    as {column, table, kind, expr, value, order, ties}, or None for any other formula.
    `ALL(T)` / `ALLSELECTED(T)` of a whole table ranks its rows, each by the column the
    relationships into it end on (`table_wide`: every filter and grouping on T is removed)."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures and key not in row_formulas:
            return _rank_form(measures[key], measures, table_of, row_formulas, depth + 1,
                              graph)
        return None
    head, inner = _call_head(toks)
    if head != "RANKX":
        return None
    parts = _split_top(inner)
    if not 2 <= len(parts) <= 5:
        return None
    rh, rin = _call_head(parts[0])
    if rh not in ("ALL", "ALLSELECTED") or len(rin) != 1:
        return None
    tmap = {k.lower(): v for k, v in table_of.items()}
    table_wide = False
    if rin[0][0] in ("qualified", "quoted_qualified"):
        phys = tmap.get(str(_table_ref(rin[0][1]) or "").lower())
        column = _column_ref(rin[0][1])
    elif rin[0][0] in ("func", "str") and graph is not None:
        name = rin[0][1]
        if rin[0][0] == "str":
            if not name.startswith("'"):
                return None
            name = name[1:-1].replace("''", "'")
        phys = tmap.get(name.lower())
        ends = sorted({str(e.get("to_column")) for e in graph
                       if phys and _bare(e.get("to_table")) == _bare(phys)
                       and e.get("to_column") and e.get("is_active", True)})
        if len(ends) != 1:
            return None
        column, table_wide = ends[0], True
    else:
        return None
    if not phys:
        return None

    def word(i: int, allowed: tuple[str, ...], default: str) -> str | None:
        if len(parts) <= i or not parts[i]:
            return default
        w = parts[i][0][1].upper().strip("'\"") if len(parts[i]) == 1 else ""
        if w in ("0", "FALSE"):
            w = allowed[0]
        elif w in ("1", "TRUE"):
            w = allowed[1]
        return w if w in allowed else None
    order = word(3, ("DESC", "ASC"), "DESC")
    ties = word(4, ("SKIP", "DENSE"), "SKIP")
    if order is None or ties is None:
        return None
    value = _text_of(parts[2]) if len(parts) > 2 and parts[2] else None
    return {"column": column, "table": phys, "kind": rh, "table_wide": table_wide,
            "expr": _text_of(parts[1]), "value": value, "order": order, "ties": ties}


_ADDITIVE = {"SUM", "SUMX", "COUNT", "COUNTA", "COUNTROWS", "CALCULATE", "RELATED",
             "KEEPFILTERS", "FILTER"}


def _additive(formula: str, measures: dict[str, str]) -> bool:
    """Whether a formula is a sum of its rows' values (a SUM, a COUNT, a SUMX, their sums and
    differences): what the members of a window add up to."""
    toks = _expanded_tokens(formula, measures)
    depth_x = 0
    stack: list[bool] = []
    for i, (k, v) in enumerate(toks):
        if k == "func" and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
            if v.upper() not in _ADDITIVE:
                return False
        if k == "lparen":
            prev = toks[i - 1] if i else ("", "")
            inside = prev[0] == "func" and prev[1].upper() in ("SUMX", "FILTER")
            stack.append(inside)
            depth_x += inside
        elif k == "rparen" and stack:
            depth_x -= stack.pop()
        elif k == "op" and v == "/":
            return False
        elif k == "op" and v == "*" and depth_x == 0:
            return False
    return True


def _window_form(formula: str, measures: dict[str, str], table_of: dict[str, str],
                 row_formulas: set[str], depth: int = 0) -> dict | None:
    """`CALCULATE(expr, OFFSET(n, rel, ORDERBY(...)))`, `INDEX(n, rel, ORDERBY(...))` or
    `WINDOW(from, ABS|REL, to, ABS|REL, rel, ORDERBY(...))`, rel ALL / ALLSELECTED of columns
    of one table: {fn, n | bounds, columns [(col, table)], kind, order [(col, dir)], expr}, or
    None for any other formula."""
    try:
        toks = _strip_parens(tokenize(formula or ""))
    except CompileError:
        return None
    if len(toks) == 1 and toks[0][0] == "col" and depth < 8:
        key = _column_ref(toks[0][1]).lower()
        if key in measures and key not in row_formulas:
            return _window_form(measures[key], measures, table_of, row_formulas, depth + 1)
        return None
    head, inner = _call_head(toks)
    if head != "CALCULATE":
        return None
    parts = _split_top(inner)
    if len(parts) != 2:
        return None
    fn, fin = _call_head(parts[1])
    if fn not in ("OFFSET", "INDEX", "WINDOW", "TOPN"):
        return None
    args = _split_top(fin)
    tmap = {k.lower(): v for k, v in table_of.items()}

    def number(t: list) -> int | None:
        txt = _text_of(t).replace(" ", "")
        return int(txt) if re.fullmatch(r"-?\d+", txt) else None
    out: dict = {"fn": fn, "expr": _text_of(parts[0])}
    if fn == "TOPN":
        # TOPN(n, ALL | ALLSELECTED(columns), order expression[, ASC | DESC]): the members
        # ranked by the expression (a blank as 0, ties kept), the first n.
        if len(args) not in (3, 4):
            return None
        n = number(args[0])
        rh, rin = _call_head(args[1])
        d = _text_of(args[3]).upper() if len(args) == 4 else "DESC"
        d = {"0": "DESC", "FALSE": "DESC", "1": "ASC", "TRUE": "ASC"}.get(d, d)
        if n is None or n < 1 or rh not in ("ALL", "ALLSELECTED") or d not in ("ASC", "DESC"):
            return None
        cols = []
        for p in _split_top(rin):
            if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
                return None
            phys = tmap.get(str(_table_ref(p[0][1]) or "").lower())
            if not phys:
                return None
            cols.append((_column_ref(p[0][1]), phys))
        if not cols or len({_bare(t) for _c, t in cols}) != 1 or not _additive(
                out["expr"], measures):
            return None
        out.update(n=n, columns=cols, kind=rh, by=_text_of(args[2]), direction=d,
                   order=[(c, "ASC") for c, _t in cols])
        return out
    if fn == "WINDOW":
        if len(args) < 6:
            return None
        a, at, b, bt = number(args[0]), _text_of(args[1]).upper(), number(args[2]), \
            _text_of(args[3]).upper()
        if a is None or b is None or at not in ("ABS", "REL") or bt not in ("ABS", "REL"):
            return None
        out["bounds"] = ((a, at), (b, bt))
        rest = args[4:]
    else:
        if len(args) < 3:
            return None
        n = number(args[0])
        if n is None or (fn == "INDEX" and n == 0):
            return None
        out["n"] = n
        rest = args[1:]
    rh, rin = _call_head(rest[0])
    if rh not in ("ALL", "ALLSELECTED"):
        return None
    cols = []
    for p in _split_top(rin):
        if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
            return None
        phys = tmap.get(str(_table_ref(p[0][1]) or "").lower())
        if not phys:
            return None
        cols.append((_column_ref(p[0][1]), phys))
    if not cols or len({_bare(t) for _c, t in cols}) != 1:
        return None
    oh, oin = _call_head(rest[1]) if len(rest) > 1 else (None, [])
    if oh != "ORDERBY":
        return None
    # The optional arguments after ORDERBY: how blanks sort (DEFAULT / KEEP: a blank is the
    # lowest; FIRST; LAST) and PARTITIONBY (the window moves inside each combination of
    # those columns). MATCHBY is not read.
    blanks, partition = "DEFAULT", []
    for extra in rest[2:]:
        eh, ein = _call_head(extra)
        if eh == "PARTITIONBY":
            for p in _split_top(ein):
                if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
                    return None
                c = _column_ref(p[0][1])
                if c.lower() not in {x.lower() for x, _t in cols}:
                    return None
                partition.append(c)
        elif len(extra) == 1 and extra[0][1].upper() in ("DEFAULT", "KEEP", "FIRST", "LAST"):
            blanks = extra[0][1].upper()
        elif extra:
            return None
    order, ob = [], _split_top(oin)
    i = 0
    while i < len(ob):
        p = ob[i]
        if len(p) != 1 or p[0][0] not in ("qualified", "quoted_qualified"):
            return None
        d = "ASC"
        if i + 1 < len(ob) and len(ob[i + 1]) == 1 and ob[i + 1][0][1].upper() in (
                "ASC", "DESC", "0", "1", "TRUE", "FALSE"):
            w = ob[i + 1][0][1].upper()
            d = "DESC" if w in ("DESC", "0", "FALSE") else "ASC"
            i += 1
        c = _column_ref(p[0][1])
        if c.lower() not in {x.lower() for x, _t in cols}:
            return None
        order.append((c, d))
        i += 1
    if {c.lower() for c, _d in order} | {c.lower() for c in partition} != {
            c.lower() for c, _t in cols} or {c.lower() for c, _d in order} & {
            c.lower() for c in partition}:
        return None
    if fn == "WINDOW" and not _additive(out["expr"], measures):
        return None
    out.update(columns=cols, kind=rh, order=order, partition=partition, blanks=blanks)
    return out


def _window_statement(unit: dict, m: dict, win: dict, rules, dialect: str, again,
                      measures: dict[str, str] | None = None) -> Result:
    """OFFSET / INDEX / WINDOW over the members of the relation's columns (`_window_form`),
    the visual's other grouping columns kept: the expression for every member (a member with
    no rows is BLANK and keeps its place), ordered by ORDERBY, and the row's value read at the
    member OFFSET moves to, the INDEX-th member, or the sum over the WINDOW's members. The
    visual lists every member of its grouping columns whose value is not blank (the previous
    year's sales show on a year with none of its own)."""
    res = Result()
    op = rules.operator("null_safe_eq", dialect)
    row = rules.function("dax", win["fn"], dialect) or {}
    tmpl = (row.get("emit") or {}).get("window_template")
    if op is None or not tmpl:
        res.coverage = REFUSED
        res.reason = (f"{win['fn']} has no form for the {dialect} dialect (null-safe equality "
                      f"and the dax.{win['fn'].lower()} row's window)")
        return res
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    groups = list(unit.get("group_by") or [])
    tables = list(unit.get("warehouse_tables") or [])
    gtabs = list(unit.get("group_by_tables") or [None] * len(groups))
    rel = {(c.lower(), _bare(t)) for c, t in win["columns"]}

    def in_rel(g: str, t: str | None) -> bool:
        return (str(g).lower(), _bare(t or tables[0])) in rel
    grouped_rel = {(str(g).lower(), _bare(t or tables[0])) for g, t in zip(groups, gtabs)
                   if in_rel(g, t)}
    # A row with no current member (a total, or a visual that does not group by the
    # relation): the union of the members each member's window reaches (seen live: the
    # previous year at the grand total is every year but the last).
    # The visual grouping by the PARTITIONBY columns only: each partition's row has no
    # current member either, the union is taken within the partition (seen live: a year's
    # row shows the months its months' previous months reach).
    part_set = {(c.lower(), _bare(t)) for c, t in win["columns"]
                if c.lower() in {p.lower() for p in win.get("partition") or []}}
    union = win["fn"] != "TOPN" and (not grouped_rel or (part_set and grouped_rel == part_set))
    if win["fn"] != "TOPN" and grouped_rel and grouped_rel != rel and not union:
        res.coverage = REFUSED
        res.reason = (f"measure {m['name']!r}: {win['fn']} moves along "
                      f"{[c for c, _t in win['columns']]}, of which the visual groups by only "
                      f"some")
        return res
    if union and win["fn"] == "OFFSET" and not _additive(win["expr"], measures or {}):
        res.coverage = REFUSED
        res.reason = (f"measure {m['name']!r}: OFFSET on a row with no current member is the "
                      f"union of the members it reaches, which a non-additive expression is "
                      f"not summed over")
        return res
    others = [(g, t) for g, t in zip(groups, gtabs) if not in_rel(g, t)]
    rel_cols = [c for c, _t in win["columns"]]
    rel_tab = win["columns"][0][1]

    def on_rel(f: dict) -> bool:
        return (str(f.get("column") or "").lower(), _bare(f.get("table") or tables[0])) in rel
    filters = list(unit.get("filters") or [])
    base_filters = [f for f in filters if not on_rel(f)] if win["kind"] == "ALL" else filters
    member_filters = [] if win["kind"] == "ALL" else filters
    plain = {k: v for k, v in unit.items() if k not in ("totals", "top_n", "sort")}
    if tables and _bare(rel_tab) not in {_bare(t) for t in tables}:
        plain["warehouse_tables"] = tables + [rel_tab]

    def stmt(gb, gt, formula, flt, shown, by: str | None = None) -> Result:
        ms = [{"name": "member value", "formula": formula, "formula_language": "dax"}]
        if by:
            ms.append({"name": "member order", "formula": by, "formula_language": "dax"})
        return again(dict(plain, group_by=gb, group_by_tables=gt, filters=flt, blank_rows=shown,
                          measures=ms))
    s_r = stmt(groups, gtabs, "BLANK ( )", filters, "shown")
    if not groups:
        # A card: one row, with no grouping column.
        s_r.sql = "SELECT 1 AS one"
    b_r = stmt([g for g, _t in others] + rel_cols, [t for _g, t in others] + [rel_tab] * len(
        rel_cols), win["expr"], base_filters, "hidden", win.get("by"))
    m_r = stmt(rel_cols, [rel_tab] * len(rel_cols), "BLANK ( )", member_filters, "shown")
    for what, r in (("the visual's rows", s_r), ("the members' values", b_r),
                    ("the members", m_r)):
        if not r.sql or r.coverage == REFUSED:
            res.coverage = REFUSED
            res.reason = (f"measure {m['name']!r}: {win['fn']} over {win['kind']}"
                          f"({', '.join(rel_cols)}): {what} do not compile ({r.reason})")
            return res
    s_names, _sv = _leaf_names(s_r, dict(plain, group_by=groups, measures=[]))
    b_names, b_vals = _leaf_names(b_r, dict(plain, group_by=[g for g, _t in others] + rel_cols,
                                            measures=[{"name": "member value"},
                                                      {"name": "member order"}]))
    m_names, _mv = _leaf_names(m_r, dict(plain, group_by=rel_cols, measures=[]))
    if not b_vals:
        res.coverage = REFUSED
        res.reason = f"measure {m['name']!r}: the members' values did not compile"
        return res
    n_o = len(others)
    o_names = [f"o{i}" for i in range(n_o)]
    # f: every (other grouping, member) pair, with the member's value.
    s_other = [s_names[i] for i, (g, t) in enumerate(zip(groups, gtabs)) if not in_rel(g, t)]
    frame_sel = [f"o.{s} AS {o}" for s, o in zip(s_other, o_names)]
    frame_sel += [f"mm.{n} AS r{i}" for i, n in enumerate(m_names)]
    frame_sel.append(f"b.{b_vals[0]} AS v")
    if win.get("by"):
        frame_sel.append(f"b.{b_vals[1]} AS ov" if len(b_vals) > 1 else "NULL AS ov")
    on_b = [nse.format(f"b.{b_names[i]}", f"o.{s_other[i]}") for i in range(n_o)]
    on_b += [nse.format(f"b.{b_names[n_o + i]}", f"mm.{n}") for i, n in enumerate(m_names)]
    o_part = (f"(\n    SELECT DISTINCT {', '.join('s0.' + x for x in s_other)}\n    FROM   (\n"
              + "\n".join("        " + ln for ln in s_r.sql.splitlines()) + "\n    ) s0\n  ) o"
              ) if n_o else "(SELECT 1 AS one) o"
    frame = ("SELECT " + ", ".join(frame_sel) + f"\n  FROM   {o_part}\n  CROSS JOIN (\n"
             + "\n".join("    " + ln for ln in m_r.sql.splitlines()) + "\n  ) mm"
             + "\n  LEFT JOIN (\n" + "\n".join("    " + ln for ln in b_r.sql.splitlines())
             + "\n  ) b\n    ON  " + "\n    AND ".join(on_b))
    pos = {c.lower(): i for i, c in enumerate(rel_cols)}
    blanks = win.get("blanks") or "DEFAULT"

    def nulls(d: str, reverse: bool = False) -> str:
        # DEFAULT: a blank sorts lowest; FIRST / LAST: at that end whatever the direction.
        if blanks == "FIRST":
            return "NULLS LAST" if reverse else "NULLS FIRST"
        if blanks == "LAST":
            return "NULLS FIRST" if reverse else "NULLS LAST"
        low_first = (d == "ASC") != reverse
        return "NULLS FIRST" if low_first else "NULLS LAST"
    order = ", ".join(_sort_term(f"f.r{pos[c.lower()]}", d, nulls(d).split()[1], dialect)
                      for c, d in win["order"])
    pcols = [f"f.o{i}" for i in range(n_o)] + [f"f.r{pos[c.lower()]}"
                                               for c in win.get("partition") or []]
    part = (f"PARTITION BY {', '.join(pcols)} " if pcols else "")
    if win["fn"] == "WINDOW":
        (a, at), (b, bt) = win["bounds"]

        def bound(k: int, kind: str, lo: bool) -> str:
            if kind == "REL":
                return ("CURRENT ROW" if k == 0 else f"{abs(k)} PRECEDING" if k < 0
                        else f"{k} FOLLOWING")
            if k == 1 and lo:
                return "UNBOUNDED PRECEDING"
            if k == -1 and not lo:
                return "UNBOUNDED FOLLOWING"
            raise CompileError(f"WINDOW from an absolute position {k} other than the first "
                               f"or the last")
        try:
            spec = (f"{part}ORDER BY {order} ROWS BETWEEN {bound(a, at, True)} AND "
                    f"{bound(b, bt, False)}")
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"measure {m['name']!r}: {exc}"
            return res
        n = ""
    else:
        n = str(abs(win["n"]))
        if win["fn"] == "INDEX" and win["n"] < 0:
            # The order reversed, a blank (the lowest) last where it was first.
            order = ", ".join(_sort_term(f"f.r{pos[c.lower()]}",
                                         "ASC" if d == "DESC" else "DESC",
                                         nulls(d, True).split()[1], dialect)
                              for c, d in win["order"])
        spec = (f"{part}ORDER BY {order}" + (" ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED "
                                            "FOLLOWING" if win["fn"] == "INDEX" else ""))
    if union:
        # Each member's position (`rn`) of `cnt`; the members some member's window reaches.
        if win["fn"] == "OFFSET":
            k = win["n"]
            cond = f"f.rn <= f.cnt - {-k}" if k < 0 else f"f.rn > {k}"
        elif win["fn"] == "INDEX":
            k = win["n"]
            cond = f"f.rn = {k}" if k > 0 else f"f.rn = f.cnt + 1 - {-k}"
        else:
            (a, at), (b, bt) = win["bounds"]
            lo = (f"{1 + a}" if at == "REL" else str(a) if a > 0 else f"f.cnt + 1 - {-a}")
            hi = (f"f.cnt + {b}" if bt == "REL" else str(b) if b > 0 else f"f.cnt + 1 - {-b}")
            cond = f"f.rn >= {lo} AND f.rn <= {hi}"
        agg = "MAX" if win["fn"] == "INDEX" else "SUM"
        ordered = order.replace("f.r", "f0.r")
        p0 = part.replace("f.o", "f0.o").replace("f.r", "f0.r")
        frame = (f"SELECT f0.*, ROW_NUMBER() OVER ({p0}ORDER BY "
                 f"{ordered}) AS rn,\n         COUNT(*) OVER ({p0.strip()}) AS cnt"
                 f"\n  FROM   (\n  {frame}\n  ) f0")
        w = f"{agg}(CASE WHEN {cond} THEN f.v END)"
        spec = n = ""
    if win["fn"] == "TOPN":
        n = str(win["n"])
        spec = part.strip()
        rank_over = f"{part}ORDER BY COALESCE(f0.ov, 0) {win['direction']}"
    key = ("lag" if win.get("n", 0) < 0 else "lead") if win["fn"] == "OFFSET" else "value"
    if not union:
        w = (tmpl.get(key) if isinstance(tmpl, dict) else tmpl).replace("{0}", "f.v").replace(
            "{n}", n).replace("{over}", spec).replace("{rank}", "f.rk")
    if win["fn"] == "TOPN":
        rk = (tmpl.get("rank") or "").replace("{over}", rank_over.replace("f.o", "f0.o"))
        frame = (f"SELECT f0.*, {rk} AS rk\n  FROM   (\n  {frame}\n  ) f0")
    sel = [f"s.{x} AS {x}" for x in s_names] + [f"x.w AS {_measure_alias(m['name'])}"]
    s_grouped = {str(g).lower() for g, t in zip(groups, gtabs) if in_rel(g, t)}
    rcols = ([f"f.r{i}" for i, c in enumerate(rel_cols) if c.lower() in s_grouped] if union
             else [f"f.r{i}" for i in range(len(rel_cols))]
             if win["fn"] != "TOPN" else
             [f"f.r{i}" for i, c in enumerate(rel_cols) if c.lower() in {
                 str(g).lower() for g, t in zip(groups, gtabs) if in_rel(g, t)}])
    s_rel_by_col = {str(g).lower(): s_names[i] for i, (g, t) in enumerate(zip(groups, gtabs))
                    if in_rel(g, t)}
    on_x = [nse.format(f"x.{o}", f"s.{s}") for o, s in zip(o_names, s_other)]
    on_x += [nse.format(f"x.r{i}", f"s.{s_rel_by_col[c.lower()]}") for i, c in
             enumerate(rel_cols) if c.lower() in s_rel_by_col]
    sql = ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n"
           + "\n".join("    " + ln for ln in s_r.sql.splitlines()) + "\n) s"
           + f"\nLEFT JOIN (\n  SELECT {'' if union else 'DISTINCT '}{', '.join(['f.' + o for o in o_names] + rcols + ['1 AS one'])},"
           + f"\n         {w} AS w\n  FROM   (\n  {frame}\n  ) f"
           + (f"\n  GROUP BY {', '.join(['f.' + o for o in o_names] + rcols)}"
              if union and (o_names or rcols) else "")
           + "\n) x\n  ON  " + ("\n  AND ".join(on_x) if on_x else "1 = 1"))
    if unit.get("blank_rows") != "shown":
        sql += "\nWHERE  x.w IS NOT NULL"
    res.sql = sql
    res.rules_used = list(dict.fromkeys(s_r.rules_used + b_r.rules_used + m_r.rules_used
                                        + [f"engine.dax_{win['fn'].lower()}"]))
    partial = any(r.coverage == PARTIAL for r in (s_r, b_r, m_r))
    res.coverage = PARTIAL if partial else TRANSLATED
    res.reason = b_r.reason if partial else ""
    res.gotchas.append(
        f"{m['name']}: {win['fn']} over {win['kind']}({', '.join(rel_cols)}) ordered by "
        f"{', '.join(c + ' ' + d for c, d in win['order'])}: every member keeps its place, a "
        f"member with no rows too")
    return res


def _rank_statement(unit: dict, m: dict, rank: dict, rules, dialect: str, again) -> Result:
    """RANKX over the members of a column (`_rank_form`), seen live: the row's value is
    compared with the expression evaluated for every member of ALL(column) (ignoring the
    visual's filters on it) or ALLSELECTED(column) (under them), the visual's other grouping
    columns kept; a BLANK compares as 0; SKIP gives 1 + the members that beat it, DENSE 1 + the
    distinct values that do. RANKX is never blank, so the visual lists every member of its
    grouping columns (`blank_rows: shown`)."""
    res = Result()
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        res.coverage = REFUSED
        res.reason = (f"RANKX joins the members to their values, NULL matching NULL, which "
                      f"has no form for the {dialect} dialect")
        return res
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    groups = list(unit.get("group_by") or [])
    tables = list(unit.get("warehouse_tables") or [])
    gtabs = list(unit.get("group_by_tables") or [None] * len(groups))
    col, phys = rank["column"], rank["table"]

    wide = bool(rank.get("table_wide"))

    def is_c(g: str, t: str | None) -> bool:
        # ALL(T) of a whole table: every column of T is ranked over, none kept.
        return ((wide or str(g).lower() == str(col).lower())
                and _bare(t or tables[0]) == _bare(phys))

    def on_c(f: dict) -> bool:
        return ((wide or str(f.get("column") or "").lower() == str(col).lower())
                and _bare(f.get("table") or tables[0]) == _bare(phys))
    others = [(g, t) for g, t in zip(groups, gtabs) if not is_c(g, t)]
    filters = list(unit.get("filters") or [])
    base_filters = [f for f in filters if not on_c(f)] if rank["kind"] == "ALL" else filters
    member_filters = [] if rank["kind"] == "ALL" else filters
    plain = {k: v for k, v in unit.items() if k not in ("totals", "top_n", "sort")}
    if tables and _bare(phys) not in {_bare(t) for t in tables}:
        plain["warehouse_tables"] = tables + [phys]

    def stmt(gb: list, gt: list, formula: str, flt: list, shown: str) -> Result:
        return again(dict(plain, group_by=gb, group_by_tables=gt, filters=flt,
                          blank_rows=shown, measures=[{"name": "rank value", "formula": formula,
                                                       "formula_language": "dax"}]))
    s_r = stmt(groups, gtabs, rank["value"] or rank["expr"], filters, "shown" if groups
               else "hidden")
    b_r = stmt([g for g, _t in others] + [col], [t for _g, t in others] + [phys], rank["expr"],
               base_filters, "hidden")
    m_r = stmt([col], [phys], "BLANK ( )", member_filters, "shown")
    for what, r in (("the row's value", s_r), ("the members' values", b_r),
                    ("the members", m_r)):
        if not r.sql or r.coverage == REFUSED:
            res.coverage = REFUSED
            res.reason = (f"measure {m['name']!r} ranks over {rank['kind']}({col}): "
                          f"{what} do not compile ({r.reason})")
            res.untranslatable = list(r.untranslatable)
            return res
    s_names, s_vals = _leaf_names(s_r, dict(plain, group_by=groups,
                                            measures=[{"name": "rank value"}]))
    b_names, b_vals = _leaf_names(b_r, dict(plain, group_by=[g for g, _t in others] + [col],
                                            measures=[{"name": "rank value"}]))
    m_names, _m = _leaf_names(m_r, dict(plain, group_by=[col], measures=[]))
    if not s_vals or not b_vals:
        res.coverage = REFUSED
        res.reason = f"measure {m['name']!r}: the ranked values did not compile"
        return res
    sv, bv = f"COALESCE(s.{s_vals[0]}, 0)", f"COALESCE(b.{b_vals[0]}, 0)"
    beats = f"{bv} {'>' if rank['order'] == 'DESC' else '<'} {sv}"
    if rank["ties"] == "DENSE":
        count = f"COUNT(DISTINCT CASE WHEN {beats} THEN {bv} END)"
    else:
        count = f"COUNT(CASE WHEN {beats} THEN 1 END)"
    # The row's other grouping columns, by name in each statement.
    s_other = [s_names[i] for i, (g, t) in enumerate(zip(groups, gtabs)) if not is_c(g, t)]
    on = [nse.format(f"b.{b_names[i]}", f"s.{s_other[i]}") for i in range(len(others))]
    on.append(nse.format(f"b.{b_names[-1]}", f"mm.{m_names[0]}"))

    def block(sql: str) -> str:
        return "\n".join("    " + ln for ln in sql.splitlines())
    keys = [f"s.{n}" for n in s_names]
    sel = [f"{k} AS {n}" for k, n in zip(keys, s_names)]
    sel.append(f"1 + {count} AS {_measure_alias(m['name'])}")
    sql = ("SELECT " + ",\n       ".join(sel)
           + f"\nFROM   (\n{block(s_r.sql)}\n) s"
           + f"\nCROSS JOIN (\n    SELECT DISTINCT mm0.{m_names[0]}\n    FROM   (\n"
           + "\n".join("    " + ln for ln in block(m_r.sql).splitlines())
           + f"\n    ) mm0\n) mm"
           + f"\nLEFT JOIN (\n{block(b_r.sql)}\n) b\n  ON  " + "\n  AND ".join(on)
           + "\nGROUP BY " + ", ".join(keys + [f"s.{s_vals[0]}"]))
    res.sql = sql
    res.rules_used = list(dict.fromkeys(s_r.rules_used + b_r.rules_used + m_r.rules_used
                                        + ["engine.dax_rankx"]))
    res.untranslatable = s_r.untranslatable + b_r.untranslatable
    partial = any(r.coverage == PARTIAL for r in (s_r, b_r, m_r))
    res.coverage = PARTIAL if partial else TRANSLATED
    res.reason = "; ".join(r.reason for r in (s_r, b_r) if r.reason) if partial else ""
    res.gotchas.append(
        f"{m['name']}: RANKX over {rank['kind']}({col}) {rank['order']} {rank['ties']}: "
        f"1 + the members of {col} whose value beats the row's (a blank is 0); RANKX is never "
        f"blank, so every member of the visual's grouping columns is a row")
    return res


# The sibling modules' names, imported after this module's own definitions: the modules
# call each other, and a name is looked up only when a function runs.
from .parser import (  # noqa: E402
    _Parser,
    _column_ref,
    _quote,
    _table_ref,
    tokenize,
)
from .expressions import (  # noqa: E402
    _TimeIntelligence,
    _UnitContext,
    _apply,
    _call_args,
    _call_head,
    _semi_join,
    _split_top,
    _tokens,
    compile_expression,
)
from .units import (  # noqa: E402
    _agg_call,
    _blank_on_empty,
    _compile_measure,
    _compile_time_unit,
    _context_statements,
    _empty_context_value,
    _empty_value,
    _grain_statement,
    _indent,
    _lod_parts,
    _lod_related_columns,
    _padded_crosstab,
    _rows_not_on_source,
    _top_n_order,
)
from .joins import (  # noqa: E402
    _JoinPlan,
    _NOT_WALKED,
    _NUM_LIT,
    _bare,
    _blank_in,
    _blank_member_test,
    _blank_rows_having,
    _branch_split,
    _card_ends,
    _columns_of,
    _date_keys,
    _expand_branches,
    _expanded_tokens,
    _filter_reads_column,
    _hide_blank_rows_after,
    _limit,
    _measure_elsewhere,
    _measure_graph,
    _measure_homes,
    _member_filters,
    _physical_edges,
    _plan_filters,
    _plan_joins,
    _relationship_spec,
    _renamed_edges,
    _sort_term,
    _strip_alias,
    _test_pred,
    _with_every_member,
)
