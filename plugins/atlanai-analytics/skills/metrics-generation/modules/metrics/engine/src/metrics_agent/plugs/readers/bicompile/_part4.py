# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""bicompile, part 4 of 4: the BI→SQL compiler, split for the plugin directory's 256 KiB file limit. See __init__.py."""
from __future__ import annotations
import pathlib
import re
from typing import Any
import yaml
from metrics_agent.lib import bi_models as _bi_models
from metrics_agent.lib import declared as _declared
from metrics_agent.lib import slugs
from metrics_agent.lib.card_sql import statement_sha  # noqa: F401 - the review's, re-exported
from metrics_agent.plugs.readers import tableau_params as _tparams
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled
from ._part1 import (  # noqa: F401
    CORPUS,
    CompileError,
    RULES,
    Result,
    _DateChain,
    _SqlDate,
    _TimeIntelligence,
    _UnitContext,
    _apply,
    _bare,
    _call_args,
    _column_ref,
    _expanded_tokens,
    _ident,
    _sort_term,
    _sql_string,
    _table_ref,
    _tokens,
    tokenize,
)
from ._part2 import (  # noqa: F401
    _Parser,
    _legal,
    _measure_alias,
    _native_colmap,
    _semi_join,
)
from ._part3 import (  # noqa: F401
    _JOIN_CLAUSE,
    _JoinPlan,
    _NoTransition,
    _additive,
    _agg_call,
    _blank_in,
    _blank_member_test,
    _blank_on_empty,
    _blank_rows_having,
    _blend_parts,
    _blend_statement,
    _branch_split,
    _compile_measure,
    _composite_statement,
    _constant_item,
    _context_statements,
    _date_keys,
    _detached_statement,
    _empty_context_value,
    _empty_value,
    _expand_branches,
    _filter_reads_column,
    _grain_statement,
    _hide_blank_rows_after,
    _indent,
    _labelled,
    _leaf_names,
    _lifted_joins,
    _limit,
    _lod_parts,
    _lod_related_columns,
    _measure_elsewhere,
    _measure_graph,
    _measure_homes,
    _member_filters,
    _member_grain,
    _out_name,
    _padded_crosstab,
    _physical_edges,
    _plan_filters,
    _plan_joins,
    _rank_form,
    _ranked_members,
    _relationship_spec,
    _renamed_edges,
    _rows_not_on_source,
    _statement_form,
    _strip_alias,
    _tables_read,
    _test_pred,
    _text_of,
    _top_n_order,
    _treatas_form,
    _treatas_statement,
    _window_form,
    _with_every_member,
)


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


def compile_unit(unit: dict, rules, dialect: str, *, table_sql: dict[str, str] | None = None,
                 **kw) -> Result:
    """One question unit -> one statement (`_compile_unit`, which documents every argument),
    with each table that IS a statement (`table_sql`) read as that statement. A unit with the
    view's own calculations (`visual_matrix`) is read off the view's matrix, each level of it
    compiled here (readers/visualcalc.py)."""
    inner = kw.pop("_inner", False)
    if unit.get("visual_matrix"):
        from metrics_agent.plugs.readers import visualcalc as _vcalc  # noqa: PLC0415
        res = _vcalc.compile_matrix(
            unit, rules, dialect,
            lambda u: compile_unit(u, rules, dialect, table_sql=table_sql, _inner=True, **kw),
            kw)
        if not inner:
            _hoisted(res, rules, dialect)
        return res
    res = _compile_unit(unit, rules, dialect, **kw)
    if res.sql and table_sql:
        res.sql = _read_statements(res.sql, table_sql)
    if res.sql and unit.get("totals"):
        _compile_totals(unit, res, rules, dialect, table_sql, kw)
    if not inner:
        _hoisted(res, rules, dialect)
    return res


def _hoisted(res: Result, rules, dialect: str) -> None:
    """Each statement of `res` with its marked recursions moved to the top (a dialect whose
    WITH stands only at the top of a statement)."""
    if "/*__rec" not in (res.sql or "") and not any(
            "/*__rec" in str(t.get("sql") or "") for t in getattr(res, "totals", None) or []):
        return
    emit = (rules.operator("recursive_rows", dialect) or {}).get("emit") or {}
    dia = str(dialect or "").lower()
    opener = (emit.get("dialect_templates") or {}).get(dia) or emit.get("template")
    suffix = (emit.get("statement_suffix") or {}).get(dia) or ""
    res.sql = _hoist_recursions(res.sql, opener, suffix) if res.sql else res.sql
    for t in getattr(res, "totals", None) or []:
        if t.get("sql"):
            t["sql"] = _hoist_recursions(t["sql"], opener, suffix)


def _compile_totals(unit: dict, res: Result, rules, dialect: str,
                    table_sql: dict[str, str] | None, kw: dict) -> None:
    """Each total row the view shows (`unit.totals`) as its own statement: the unit grouped by
    only the columns the total keeps, with every filter, join and measure as the leaf rows
    have them. A total is the measures over the total's context, so a distinct count, a
    ratio or a measure over ALL comes out as the tool shows it, never as a sum of rows. A
    total that does not compile is named (the unit is then partial), never summed instead."""
    groups = list(unit.get("group_by") or [])
    tables = list(unit.get("group_by_tables") or [])
    for t in unit["totals"]:
        keep = [g for g in groups if g in set(t.get("group_by") or [])]
        entry = {"kind": t.get("kind") or ("subtotal" if keep else "grand_total"),
                 "group_by": keep, "sql": None, "reason": None}
        limited = {"engine.top_n_by_sort", "engine.top_n_by_first_measure"} & set(
            res.rules_used)
        if limited and keep != groups:
            # A Top N kept by LIMIT ranks the leaf rows; the total over the kept members is
            # not that LIMIT on fewer rows.
            entry["reason"] = (f"the Top {unit['top_n']} is kept by a LIMIT over the leaf "
                               f"rows, which a total over fewer columns cannot reuse")
        else:
            sub = {k: v for k, v in unit.items() if k not in ("totals", "group_by",
                                                              "group_by_tables")}
            sub["group_by"] = keep
            if tables and len(tables) == len(groups):
                sub["group_by_tables"] = [tables[groups.index(g)] for g in keep]
            r = _compile_unit(sub, rules, dialect, **kw)
            if r.sql and r.coverage != REFUSED:
                entry["sql"] = _read_statements(r.sql, table_sql) if table_sql else r.sql
                if r.coverage == PARTIAL:
                    entry["reason"] = r.reason or "partial"
                res.rules_used += [x for x in r.rules_used if x not in res.rules_used]
            else:
                entry["reason"] = r.reason or "the total does not compile"
        if entry["sql"] is None:
            res.coverage = PARTIAL
            res.untranslatable.append({"construct": "total row",
                                       "why": f"{entry['kind']} over {keep}: "
                                              f"{entry['reason']}"})
        res.totals.append(entry)
    if any(e["sql"] is None for e in res.totals) and not res.reason:
        res.reason = "; ".join(f"total {e['kind']} over {e['group_by']}: {e['reason']}"
                               for e in res.totals if e["sql"] is None)


def _limited(res: Result, unit: dict, dialect: str | None = None) -> Result:
    """A compiled statement cut to the unit's Top N rows by LIMIT: ordered by the unit's sort
    (`[name] DESC`, a grouping column or a measure, by its output name), else by its first
    measure descending; a blank sorts last."""
    if not res.sql or res.coverage == REFUSED:
        return res
    n = int(unit["top_n"])
    m = re.fullmatch(r"\s*\[([^\]]+)\]\s*(ASC|DESC)?\s*", str(unit.get("sort") or ""), re.I)
    names = [str(g) for g in unit.get("group_by") or []]
    if m:
        name, direction = m.group(1), (m.group(2) or "DESC").upper()
        hit = next((g for g in names if g.lower() == name.lower()), None)
        col = (_measure_alias(hit) if hit and re.search(
            rf"\bAS {re.escape(_measure_alias(hit))}\b", res.sql) else hit) or _measure_alias(name)
    elif unit.get("measures"):
        col, direction = _measure_alias(unit["measures"][0]["name"]), "DESC"
    else:
        res.coverage = REFUSED
        res.reason = (f"a Top {n} with no measure to rank by: a LIMIT without an order returns "
                      "arbitrary rows")
        res.sql = ""
        return res
    body = "\n".join("    " + ln if ln.strip() else ln for ln in res.sql.splitlines())
    res.sql = (f"SELECT *\nFROM   (\n{body}\n) lim\nORDER BY "
               f"{_sort_term(f'lim.{col}', direction, 'LAST', dialect)}\n{_limit(n, dialect)}")
    res.rules_used.append("engine.top_n_limit_after")
    return res


def _read_statements(sql: str, table_sql: dict[str, str]) -> str:
    """Every `FROM <table>` / `JOIN <table>` of a table the reader gave a statement, as that
    statement. The reader names such a table by a name no warehouse table has, so a reference
    this misses fails loudly at the warehouse rather than reading the bare table."""
    for name, stmt in sorted(table_sql.items(), key=lambda kv: -len(kv[0])):
        body = "(\n" + "\n".join("    " + ln if ln.strip() else ln
                                  for ln in str(stmt).strip().rstrip(";").splitlines()) + "\n)"
        sql = re.sub(r"(\b(?:FROM|JOIN)\s+)" + re.escape(name) + r"(?=\s|$)",
                     lambda m: m.group(1) + body, sql, flags=re.I)
    return sql


def _compile_time_unit(unit: dict, rules, dialect: str, *, alias: str, columns, measures,
                       table_of, source: str, plan: "_JoinPlan", joins: list[str],
                       ctx: "_UnitContext", gcol, calc, native) -> Result:
    """A unit with a time-intelligence measure: one sub-statement per date set, combined.

    WHY A STATEMENT OF ITS OWN. A date set (`_DateSet`) is a function of the dates the
    visual's row stands for: the year-to-date of March 2025 reads 1 January to 31 March, the
    same period last year reads March 2024. No filter over the fact's own rows says that, so
    each date set gets a sub-statement that first lists, per visual row, the date table's
    dates under the visual's date filters (the row's context), derives the set from them, and
    reads the fact rows whose date is in it, through the relationship on that date column.
    The date table's own filters are replaced by the set, as the tool replaces them; every
    other filter and grouping stays.

    Every aggregate is a slot: an aggregate outside any date set is read by the ordinary
    statement, and each sub-statement fills the slots of its own date set. The rows are
    combined by the visual's grouping columns (UNION ALL, then one row per key), and each
    measure's expression reads its slots back, so `DIVIDE([Sales] - [Sales LY], [Sales LY])`
    is computed from both.
    """
    res = Result()
    res.rules_used.append("engine.dax_time_intelligence")
    if native:
        res.reason = "time intelligence over a native query's result set is not compiled"
        return res
    top_filters = [f for f in unit.get("filters") or []
                   if str(f.get("op") or "").lower() == "top_n"
                   and isinstance(f.get("value"), dict)
                   and str(f["value"].get("population") or "") == "filtered"]
    if unit.get("top_n") and not top_filters:
        # A Top N kept by the statement's LIMIT (the latest point of a KPI's trend, a visual's
        # item limit): the time intelligence is computed for every row, which no row's value
        # depends on, and the rows are then ordered and cut, as the visual cuts them.
        inner = _compile_time_unit({k: v for k, v in unit.items() if k not in ("top_n",)},
                                   rules, dialect, alias=alias, columns=columns,
                                   measures=measures, table_of=table_of, source=source,
                                   plan=plan, joins=joins, ctx=ctx, gcol=gcol, calc=calc,
                                   native=native)
        return _limited(inner, unit, dialect)
    row = rules.function("dax", "DATESYTD", "") or {}
    math = ((row.get("emit") or {}).get("date_math") or {}).get((dialect or "").lower())
    if not math:
        res.reason = (f"time intelligence has no date arithmetic for the {dialect or '?'} "
                      f"dialect (the DATESYTD row's date_math)")
        return res

    slots: list[dict] = []
    exprs: list[tuple[dict, str]] = []
    failed: list[str] = []
    home = (table_of, source) if table_of else None
    for m in unit.get("measures", []):
        if m.get("formula_language") != "dax" or not m.get("formula"):
            failed.append(f"{m.get('name')!r}: only a DAX measure is compiled beside time "
                          f"intelligence")
            continue
        seen = len(res.untranslatable)
        before = len(slots)
        try:
            expr = _compile_measure(m, rules, alias, dialect, res, None, measures, unit,
                                    home=home, ctx=ctx, slots=slots)
        except CompileError as exc:
            del slots[before:]
            failed.append(f"{m.get('name')!r}: {exc}")
            new = res.untranslatable[seen:]
            for gap in new:
                gap.setdefault("in_measure", m.get("name") or "")
            if not new:
                res.untranslatable.append({"construct": "measure", "in_measure": m.get("name")
                                           or "", "why": str(exc)})
            continue
        if not re.search(r"\bu\.s\d+\b", expr):
            # Nothing it reads is an aggregate (a constant): read once per row as it is.
            pass
        exprs.append((m, expr))
    if not exprs:
        res.coverage = REFUSED
        res.reason = "; ".join(f"measure {f}" for f in failed) or "no measure compiled"
        return res

    columns_used = {sl["date_set"].column for sl in slots if sl["date_set"] is not None}
    if len(columns_used) != 1:
        res.coverage = REFUSED
        res.reason = ("time intelligence over two date columns in one visual is not compiled"
                      if columns_used else "no date set compiled")
        return res
    dkey = next(iter(columns_used))
    info = ctx.date_keys.get(dkey) or ctx.lookup_keys[dkey]
    dtab, dcol, fcol = info["table"], info["column"], info["fact_column"]
    dbare = _bare(dtab)
    if dkey in ctx.lookup_keys and dkey not in ctx.date_keys:
        # A lookup date set reads the date table through its key, which the model may show
        # under another name than the warehouse's (`Date Sk` read as `date_sk`).
        dcol = ctx.renames.get((dbare, dcol.lower()), dcol)
        fcol = ctx.renames.get((_bare(source), fcol.lower()), fcol)

    def _date_col(c: str) -> str:
        return ctx.renames.get((dbare, c.lower()), c)

    key_col = info.get("key_column")

    def fact_on(dt: str) -> str:
        """The fact's rows on the date `dt`: through the date table's key row where the fact
        reaches the date table on a key, not the date (`_keyed_date`)."""
        if key_col:
            return (f"JOIN {dtab} kd ON kd.{dcol} = {dt}\n"
                    f"JOIN {source} {alias} ON {alias}.{fcol} = kd.{key_col}")
        return f"JOIN {source} {alias} ON {alias}.{fcol} = {dt}"
    if key_col:
        res.rules_used.append("engine.dax_date_set_through_key")

    sp: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                           "display": []}
    try:
        ctx_preds, dim_preds, deferred = _plan_filters(unit, rules, alias, res, None,
                                                       resolve=calc, dialect=dialect,
                                                       place=plan.at, text_key=ctx.text_key,
                                                       special=sp)
    except CompileError as exc:
        res.reason = f"filter calc: {exc}"
        return res
    if deferred or ctx_preds or sp["datasource"] or sp["measure_tests"] or sp["display"] \
            or any(f not in top_filters for f in sp["members"]):
        res.coverage = REFUSED
        res.reason = ("a filter time intelligence cannot carry (a context filter, or one the "
                      "compiler does not read) is applied to this visual")
        return res

    def top_preds(members: list[dict]) -> list[str]:
        """The members each Top N filter keeps, ranked within every filter of the visual by
        its measure as a plain statement computes it, as row predicates."""
        out = []
        for f in members:
            out.append(_ranked_members(
                f, calc(str(f["column"]), f.get("table"))
                or f"{plan.at(str(f['column']), f.get('table'))}.{f['column']}",
                f["value"], alias, dialect, res, src=source, joins=joins,
                dim_preds=dim_preds, compile_by=_rank_by))
        return out

    def _rank_by(formula: str) -> str:
        try:
            return _compile_measure({"name": "top_n_by", "formula": formula,
                                     "formula_language": "dax"}, rules, alias, dialect,
                                    Result(), None, measures, unit, home=home)
        except _TimeIntelligence:
            raise CompileError("its ranking measure is itself time intelligence") from None
    try:
        member_preds = top_preds(sp["members"])
    except CompileError as exc:
        res.coverage = REFUSED
        res.reason = f"time intelligence under a Top N filter: {exc}"
        return res
    if member_preds:
        res.rules_used.append("engine.dax_time_intelligence_top_n")

    # The date table's alias in the ordinary plan, when the visual reads it.
    d_alias = next((a for a, t in plan.table_at.items() if t == dbare and a != alias), None)
    d_ref = re.compile(rf"\b{re.escape(d_alias)}\.") if d_alias else None
    other_joins = [j for j in joins if not (d_alias and re.search(rf"\s{re.escape(d_alias)}\s+ON\s", j))]
    if d_ref and any(d_ref.search(j) for j in other_joins):
        res.coverage = REFUSED
        res.reason = f"a table joined through the date table {dtab} is not compiled beside " \
                     f"time intelligence"
        return res
    date_preds = [x for x in dim_preds if d_ref and d_ref.search(x)]
    other_preds = [x for x in dim_preds if x not in date_preds]
    if any(re.search(r"\bj\d+\.", d_ref.sub("", x)) or re.search(rf"\b{re.escape(alias)}\.", x)
           for x in date_preds) if d_ref else False:
        res.coverage = REFUSED
        res.reason = "a filter mixing the date table and another table is not compiled"
        return res
    # A Top N filter is a filter on its column's rows. On a column of the date table it
    # narrows the row's dates (the date set then replaces it, as the date table's other
    # filters): its outer test reads the date table's own alias in the row's dates.
    member_dates: list[str] = []
    date_preds_members: list[str] = []       # the date-table Top N predicates, in that order
    for p in member_preds:
        key = re.match(r"\(?\s*((\w+)\.\w+)\s+IN\s*\(", p)
        if d_alias and key and key.group(2) == d_alias:
            outer = key.group(1)
            col_ = outer.split(".", 1)[1]
            member_dates.append(
                p.replace(f"({outer} IN (", f"(d.{col_} IN (", 1)
                .replace(f"OR ({outer} IS NULL AND", f"OR (d.{col_} IS NULL AND", 1))
            date_preds.append(p)
            date_preds_members.append(p)
            res.rules_used.append("engine.dax_time_intelligence_top_n_dates")
        else:
            other_preds.append(p)
    dim_preds = dim_preds + member_preds

    # A CALCULATE filter that REPLACES one the visual applies (`slot_drops`): the slots it
    # holds read their rows without the visual's filter on that column, so their
    # sub-statement's WHERE is planned again without it. The date table's own filters are
    # replaced by the date set already; a dropped one there is the date set's business.
    def preds_without(drops: tuple) -> list[str]:
        if not drops:
            return other_preds
        gone = {(str(c).lower(), t) for c, t in drops}

        def dropped(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table")))
            return (col, at) in gone or (col, None) in gone
        kept = [f for f in unit.get("filters") or [] if not dropped(f)]
        sp2: dict[str, list] = {"datasource": [], "members": [], "measure_tests": [],
                                "display": []}
        c_p, d_p, deferred_ = _plan_filters(dict(unit, filters=kept), rules, alias, Result(),
                                            None, resolve=calc, dialect=dialect,
                                            place=plan.at, text_key=ctx.text_key, special=sp2)
        if deferred_ or c_p:
            raise CompileError("a filter it keeps does not compile on its own")
        res.rules_used.append("engine.dax_time_intelligence_override")
        # A Top N filter it keeps: the same members (ranked within every filter).
        mem = [p for f, p in zip(sp["members"], member_preds)
               if not dropped(f) and p not in date_preds]
        return [x for x in d_p if not (d_ref and d_ref.search(x))] + mem

    def date_preds_without(drops: tuple) -> tuple[list[str], list[str]]:
        """The visual's date table filters a slot keeps: (as the ordinary statement reads
        them, as the date table `d` reads them). An ALL of a date table column takes its
        filter off the row's dates too."""
        gone = {(str(c).lower(), t) for c, t in drops}

        def dropped(f: dict) -> bool:
            if not (f.get("column") and _filter_reads_column(f)):
                return False
            col = str(f["column"]).lower()
            at = plan.table_at.get(plan.at(f["column"], f.get("table")))
            return (col, at) in gone or (col, None) in gone
        if not drops or not any(dropped(f) for f in unit.get("filters") or []):
            return (date_preds, ([d_ref.sub("d.", x) for x in date_preds
                                 if x not in member_preds] + member_dates) if d_ref else [])
        kept = [f for f in unit.get("filters") or [] if not dropped(f)]
        _c, d_p, _x = _plan_filters(dict(unit, filters=kept), rules, alias, Result(), None,
                                    resolve=calc, dialect=dialect, place=plan.at,
                                    text_key=ctx.text_key,
                                    special={"datasource": [], "members": [],
                                             "measure_tests": [], "display": []})
        plain_d = [x for x in d_p if d_ref and d_ref.search(x)]
        kept_dates = [p for f, p in zip(sp["members"], member_preds)
                      if p in date_preds_members and not dropped(f)]
        return (plain_d + kept_dates,
                [d_ref.sub("d.", x) for x in plain_d]
                + [member_dates[date_preds_members.index(p)] for p in kept_dates])

    groups = list(unit.get("group_by", []))
    keys: list[str] = []            # each group's SQL in the ordinary statement
    date_cols: dict[int, str] = {}  # group index -> the date table's column
    for i, g in enumerate(groups):
        expr = gcol(g, i)
        keys.append(expr)
        if d_alias and expr == f"{d_alias}.{g}":
            date_cols[i] = g
        elif d_ref and d_ref.search(expr):
            res.coverage = REFUSED
            res.reason = (f"grouping {g!r} is an expression over the date table; time "
                          f"intelligence is compiled for its plain columns")
            return res

    def fmt(name: str, *a: str) -> str:
        return math[name].format(*a)

    n_slots = len(slots)

    def select_slots(own: list[int]) -> list[str]:
        return [f"{slots[i]['sql'] if i in own else 'NULL'} AS s{i + 1}"
                for i in range(n_slots)]

    def tail(group_exprs: list[str]) -> str:
        return ("\nGROUP BY " + ", ".join(group_exprs)) if group_exprs else ""

    branches: list[str] = []
    def drops_of(sl: dict) -> tuple:
        return tuple(sl.get("drops") or ())

    def keep_of(sl: dict) -> tuple[int, ...] | None:
        """The grouping columns (by index) a slot's CALCULATE keeps, or None for all."""
        kept = sl.get("keep")
        if kept is None:
            return None
        idx = tuple(i for i, (_c, _t, sql) in enumerate(ctx.groups) if sql in kept)
        return None if len(idx) == len(keys) else idx

    def kept_rows(branch: str, kept: tuple[int, ...] | None, own: list[int]) -> str:
        """A branch grouped by only the columns its CALCULATE keeps, on every row of the
        visual those columns' values stand for: crossed with the members of the columns it
        removes (the visual's rows list them under the visual's own filters; seen live, ALL
        of a grouped channel shows every channel's year to date on each channel's row)."""
        if kept is None:
            return branch
        removed = [i for i in range(len(keys)) if i not in kept]
        owners = {}
        for i in removed:
            at = re.match(r"(\w+)\.", keys[i])
            if not at:
                raise CompileError(f"grouping {groups[i]!r} is an expression")
            owners.setdefault(at.group(1), []).append(i)
        parts = []
        for a, idx in owners.items():
            cols = ", ".join(f"{keys[i]} AS k{i + 1}" for i in idx)
            if a == alias:
                body = (f"SELECT DISTINCT {cols}\n    FROM   {source} {alias}"
                        + "".join(f"\n    {j}" for j in joins)
                        + ("\n    WHERE  " + "\n      AND  ".join(dim_preds)
                           if dim_preds else ""))
            else:
                clause = next((m for m in (_JOIN_CLAUSE.match(j) for j in joins)
                               if m and m.group(2) == a), None)
                if clause is None:
                    raise CompileError(f"the members of {groups[idx[0]]!r} are not read")
                mine = [p for p in dim_preds if p not in member_preds
                        and set(re.findall(r"\b(\w+)\.", re.sub(r"'(?:[^']|'')*'", "", p)))
                        <= {a}]
                # A Top N filter on the table's column narrows its members.
                mine += [p for p in member_preds
                         if (re.match(r"\(?\s*(\w+)\.\w+\s+IN\s*\(", p) or [None, None])[1]
                         == a]
                body = (f"SELECT DISTINCT {cols}\n    FROM   {clause.group(1)} {a}"
                        + ("\n    WHERE  " + "\n      AND  ".join(mine) if mine else ""))
            parts.append(f"(\n    {body}\n  ) m{len(parts)}")
        where_m = {i: f"m{n}" for n, (_a, idx) in enumerate(owners.items()) for i in idx}
        sel = [f"{where_m[i]}.k{i + 1}" if i in where_m else f"w.k{i + 1}"
               for i in range(len(keys))]
        sel = [f"{x} AS k{i + 1}" for i, x in enumerate(sel)] + [
            f"w.s{i + 1}" for i in range(n_slots)]
        res.rules_used.append("engine.dax_time_intelligence_window")
        return ("SELECT " + ",\n       ".join(sel) + "\nFROM   (\n" + _indent(branch)
                + "\n) w\nCROSS JOIN " + "\nCROSS JOIN ".join(parts)
                + "\nWHERE  " + "\n   OR  ".join(f"w.s{i + 1} IS NOT NULL" for i in own))

    by_drops: dict[tuple, list[int]] = {}
    for i, sl in enumerate(slots):
        if sl["date_set"] is None:
            by_drops.setdefault((drops_of(sl), keep_of(sl)), []).append(i)
    for (drops, kept), plain in by_drops.items():
        try:
            preds = (date_preds_without(drops)[0] + preds_without(drops) if drops
                     else dim_preds)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a CALCULATE filter replacing the visual's: {exc}"
            return res
        mine = [i for i in range(len(keys)) if kept is None or i in kept]
        sel = [f"{keys[i]} AS k{i + 1}" for i in mine] + select_slots(plain)
        sql = "SELECT " + ",\n       ".join(sel) + f"\nFROM   {source} {alias}"
        for j in joins:
            sql += f"\n{j}"
        if preds:
            sql += "\nWHERE  " + "\n  AND  ".join(preds)
        try:
            branches.append(kept_rows(sql + tail([keys[i] for i in mine]), kept, plain))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = str(exc)
            return res

    by_set: dict[tuple, list[int]] = {}
    for i, sl in enumerate(slots):
        if sl["date_set"] is not None:
            by_set.setdefault((sl["date_set"].key, drops_of(sl), keep_of(sl)), []).append(i)
    for (_key, drops, kept), own in by_set.items():
        chain: _DateChain = slots[own[0]]["date_set"]
        try:
            set_preds = preds_without(drops)
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"a CALCULATE filter replacing the visual's: {exc}"
            return res
        # A CALCULATE filter on a date table column beside the date set is intersected with
        # it (seen live: DATESYTD beside MONTH <= 6 is, from July on, the first half): the
        # date table is joined again on the fact's date for the slots that read it.
        date_join = ([j for j in joins if d_alias and re.search(
            rf"\s{re.escape(d_alias)}\s+ON\s", j)]
                     if d_ref and any(d_ref.search(slots[i]["sql"]) for i in own) else [])
        if date_join:
            res.rules_used.append("engine.dax_date_set_date_filter")
        mine_d = [i for i in sorted(date_cols) if kept is None or i in kept]
        dk = [f"d.{date_cols[i]} AS k{i + 1}" for i in mine_d]
        dg = [f"d.{date_cols[i]}" for i in mine_d]
        kc = [f"k{i + 1}" for i in mine_d]
        dwhere = date_preds_without(drops)[1]
        where_d = ("\n    WHERE  " + "\n      AND  ".join(dwhere)) if dwhere else ""
        # The date table's BLANK member (a row of another relationship into it with no date:
        # seen live, the ship date's empty keys give the date table a blank year) is a row of
        # the visual too; a date set that does not read the row's dates (two literal dates, a
        # period back from a literal date) fills it like any other.
        blank = (_blank_member_test(dtab, [e for e in ctx.graph if e.get("is_active") is False])
                 if dk and not dwhere else None)
        constant = all(ds.kind == "between" or (ds.kind == "in_period" and ds.anchor)
                       or ds.open_end for ds in chain.steps[0])
        if len(chain.steps) == 1 and len(chain.steps[0]) == 1:
            ds = chain.steps[0][0]
        else:
            ds = None
        anchored = ds is None and any(x.anchor_fact for step in chain.steps for x in step)
        if ds is not None and ds.anchor_fact:
            # The anchor is the last date of the measure's table in the row's whole context
            # (every grouping column and filter), so the row's context is that statement.
            mine_all = [i for i in range(len(keys)) if kept is None or i in kept]
            anchor_sql = ("SELECT " + ", ".join([f"{keys[i]} AS k{i + 1}" for i in mine_all]
                                                + [f"MAX({alias}.{ds.anchor_fact}) AS hi"])
                          + f"\nFROM   {source} {alias}" + "".join(f"\n{j}" for j in joins)
                          + ("\nWHERE  " + "\n  AND  ".join(dim_preds) if dim_preds else "")
                          + (("\nGROUP BY " + ", ".join(keys[i] for i in mine_all))
                             if mine_all else ""))
            lo, hi = _date_set_range(ds, fmt)
            nse_row = rules.operator("null_safe_eq", dialect)
            nse_emit = (nse_row or {}).get("emit") or {}
            nse = ((nse_emit.get("dialect_templates") or {}).get(str(dialect).lower())
                   or nse_emit.get("template"))
            if not nse:
                res.coverage = REFUSED
                res.reason = f"no NULL-matching comparison for the {dialect} dialect"
                return res
            same = [nse.replace("{0}", f"c.k{i + 1}").replace("{1}", keys[i])
                    for i in mine_all if i not in date_cols]
            body = (f"FROM   (\n{_indent(anchor_sql)}\n) c\n"
                    f"JOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}\n"
                    + fact_on(f"s.{dcol}"))
            set_preds = list(set_preds) + same
            res.rules_used.append("engine.dax_date_set_fact_anchor")
            mine = mine_all
            bkeys = [f"c.k{i + 1}" for i in mine]
            sel = [f"{k} AS k{i + 1}" for i, k in zip(mine, bkeys)] + select_slots(own)
            sql = "SELECT " + ",\n       ".join(sel) + "\n" + body
            for j in other_joins + date_join:
                sql += f"\n{j}"
            if set_preds:
                sql += "\nWHERE  " + "\n  AND  ".join(set_preds)
            try:
                branches.append(kept_rows(sql + tail(bkeys), kept, own))
            except CompileError as exc:
                res.coverage = REFUSED
                res.reason = str(exc)
                return res
            res.rules_used.append("engine.dax_date_set_in_period")
            continue
        if ds is not None and ds.kind == "lookup":
            # The row's dates' `via` values, then the date table's rows whose `match` is one
            # of them, by the key the fact reaches the date table on.
            ctx_sql = ("(\n    SELECT DISTINCT " + ", ".join(dk + [f"d.{_date_col(ds.via)} AS dt"])
                       + f"\n    FROM   {dtab} d" + where_d + "\n  ) c")
            ck = [f"c.k{i + 1}" for i in mine_d]
            setq = ("(\n  SELECT DISTINCT " + ", ".join(ck + [f"s.{dcol} AS dt"])
                    + f"\n  FROM   {ctx_sql}\n  JOIN {dtab} s ON s.{_date_col(ds.match)} = c.dt"
                    + "\n) c")
            body = f"FROM   {setq}\nJOIN {source} {alias} ON {alias}.{fcol} = c.dt"
        elif ds is not None and ds.kind == "shift":
            part = f"PARTITION BY {', '.join(dg)} " if dg else ""
            ctx_sql = ("(\n    SELECT " + ", ".join(dk + [f"d.{dcol} AS dt",
                                                     f"LAG(d.{dcol}) OVER ({part}ORDER BY "
                                                     f"d.{dcol}) AS prev"])
                       + f"\n    FROM   {dtab} d" + where_d + "\n  ) c")
            lo, hi = _shift_bounds(ds, fmt)
            ck = [f"c.k{i + 1}" for i in mine_d]
            setq = ("(\n  SELECT DISTINCT " + ", ".join(ck + [f"s.{dcol} AS dt"])
                    + f"\n  FROM   {ctx_sql}\n  JOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}"
                    + "\n) c")
            body = f"FROM   {setq}\n" + fact_on("c.dt")
        elif ds is not None:
            ctx_sql = ("(\n  SELECT " + ", ".join(dk + [f"MIN(d.{dcol}) AS lo",
                                                   f"MAX(d.{dcol}) AS hi"])
                       + f"\n  FROM   {dtab} d" + where_d.replace("\n    ", "\n  ")
                       .replace("\n      AND", "\n    AND")
                       + (("\n  GROUP BY " + ", ".join(dg)) if dg else ""))
            if blank and constant:
                nulls = ", ".join(["NULL"] * (len(dk) + 2))
                ctx_sql += (f"\n  UNION ALL\n  SELECT {nulls}\n  FROM   (SELECT 1 AS one) z"
                            f"\n  WHERE  {blank}")
                res.rules_used.append("engine.dax_date_set_blank_member")
            ctx_sql += "\n) c"
            lo, hi = _date_set_range(ds, fmt)
            body = (f"FROM   {ctx_sql}\nJOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}"
                    "\n" + fact_on(f"s.{dcol}"))
        else:
            # Several steps, or several date sets in one CALCULATE: each step reads the dates
            # (one row per visual row and date) the step before it left, the first the
            # row's own dates under the visual's date filters.
            # A period back from the last date of the measure's own table in the chain:
            # that date is read over the fact rows of the step's dates under the row's whole
            # context, so the row's other grouping columns ride along with its dates.
            nondate = ([i for i in range(len(keys)) if (kept is None or i in kept)
                        and i not in date_cols] if anchored else [])
            kn = [f"k{i + 1}" for i in nondate]
            rel = ("SELECT " + ", ".join(dk + [f"n.{k}" for k in kn] + [f"d.{dcol} AS dt"])
                   + f"\nFROM   {dtab} d"
                   + (("\nCROSS JOIN (\n" + _indent(
                       "SELECT DISTINCT " + ", ".join(f"{keys[i]} AS k{i + 1}" for i in nondate)
                       + f"\nFROM   {source} {alias}" + "".join(f"\n{j}" for j in joins)
                       + ("\nWHERE  " + "\n  AND  ".join(dim_preds) if dim_preds else ""))
                       + "\n) n") if kn else "")
                   + where_d.replace("\n    ", "\n").replace("\n      AND", "\n  AND"))
            if blank and constant and not kn:
                rel += ("\nUNION ALL\nSELECT " + ", ".join(["NULL"] * (len(dk) + 1))
                        + f"\nFROM   (SELECT 1 AS one) z\nWHERE  {blank}")
                res.rules_used.append("engine.dax_date_set_blank_member")
            kc_all = kc + kn
            nse_row = rules.operator("null_safe_eq", dialect)
            nse = (((nse_row or {}).get("emit") or {}).get("dialect_templates") or {}).get(
                str(dialect).lower()) or ((nse_row or {}).get("emit") or {}).get("template")
            if anchored and kn and not nse:
                res.coverage = REFUSED
                res.reason = f"no NULL-matching comparison for the {dialect} dialect"
                return res

            def anchor_of(ds_x):
                def make(r_sql: str) -> str:
                    on = [f"{alias}.{fcol} = r.dt"] + [
                        nse.replace("{0}", f"r.k{i + 1}").replace("{1}", keys[i])
                        for i in nondate]
                    return ("SELECT " + ", ".join([f"r.{k}" for k in kc_all]
                                                  + [f"MAX({alias}.{ds_x.anchor_fact}) AS hi"])
                            + f"\nFROM   (\n{_indent(r_sql)}\n) r"
                            + f"\nJOIN {source} {alias} ON " + "\n  AND ".join(on)
                            + "".join(f"\n{j}" for j in other_joins)
                            + ("\nWHERE  " + "\n  AND  ".join(set_preds) if set_preds else "")
                            + (f"\nGROUP BY {', '.join('r.' + k for k in kc_all)}"
                               if kc_all else ""))
                return make if ds_x.anchor_fact else None
            if anchored:
                res.rules_used.append("engine.dax_date_set_fact_anchor")
            try:
                for step in chain.steps:
                    outs = [_date_step(x, rel, kc_all, dtab, dcol, fmt, anchor_of(x),
                                       date_col=_date_col)
                            for x in step]
                    if len(outs) > 1:
                        res.rules_used.append("engine.dax_date_sets_intersected")
                    rel = "\nINTERSECT\n".join(
                        "SELECT " + ", ".join([f"x{n}.{k}" for k in kc_all] + [f"x{n}.dt"])
                        + f"\nFROM   (\n{_indent(o)}\n) x{n}" for n, o in enumerate(outs))
                    for x in step:
                        res.rules_used.append(f"engine.dax_date_set_{x.kind}")
            except CompileError as exc:
                res.coverage = REFUSED
                res.reason = str(exc)
                return res
            if len(chain.steps) > 1:
                res.rules_used.append("engine.dax_date_set_chain")
            body = (f"FROM   (\n{_indent(rel)}\n) c\n"
                    + fact_on("c.dt"))
            if kn:
                body += "".join(
                    "\n  AND " + nse.replace("{0}", f"c.k{i + 1}").replace("{1}", keys[i])
                    for i in nondate)
        mine = [i for i in range(len(keys)) if kept is None or i in kept]
        bkeys = [f"c.k{i + 1}" if i in date_cols else keys[i] for i in mine]
        sel = [f"{k} AS k{i + 1}" for i, k in zip(mine, bkeys)] + select_slots(own)
        sql = "SELECT " + ",\n       ".join(sel) + "\n" + body
        for j in other_joins + date_join:
            sql += f"\n{j}"
        if set_preds:
            sql += "\nWHERE  " + "\n  AND  ".join(set_preds)
        try:
            branches.append(kept_rows(sql + tail(bkeys), kept, own))
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = str(exc)
            return res
        if ds is not None:
            res.rules_used.append(f"engine.dax_date_set_{ds.kind}")

    outer_keys = [f"u.k{i + 1}" for i in range(len(keys))]
    sel = [f"u.k{i + 1} AS {_measure_alias(g)}" for i, g in enumerate(groups)]
    sel += [f"{e} AS {_measure_alias(m['name'])}" for m, e in exprs]
    union = "\n  UNION ALL\n".join(branches)
    sql = ("SELECT " + ",\n       ".join(sel) + "\nFROM (\n" + union + "\n) u"
           + (("\nGROUP BY " + ", ".join(outer_keys)) if outer_keys else ""))
    if unit.get("blank_rows") == "hidden" and outer_keys:
        # The rows whose every value is empty are left out, as in the plain statement.
        hide = _blank_rows_having(sel[len(groups):])
        if hide:
            sql += f"\nHAVING {hide}"
            res.rules_used.append("engine.blank_rows_hidden")
        elif len(sel) > len(groups):
            sql = _hide_blank_rows_after(sql, sel, len(groups))
            res.rules_used.append("engine.blank_rows_hidden")
    res.sql = sql
    if failed:
        res.reason = "; ".join(f"measure {f}" for f in failed)
    res.coverage = PARTIAL if (failed or res.untranslatable) else TRANSLATED
    res.gotchas.append(
        f"time intelligence over {dtab}.{dcol}: each row's dates are the date table's under "
        f"the visual's date filters, and a date set reads the fact's rows through "
        f"{_bare(source)}.{fcol}")
    return res


def _shift_bounds(ds: "_DateSet", fmt) -> tuple[str, str]:
    """The dates a shift date set reads for one of the row's dates (`c.dt`, the date before
    it among the row's `c.prev`): a month-end date whose day before is also the row's
    extends to the end of the month it lands in."""
    if ds.part == "day":
        lo = fmt("add_days", "c.dt", str(ds.n))
        return lo, lo
    lo = fmt("add_months", "c.dt", str(ds.n))
    hi = (f"CASE WHEN c.dt = {fmt('month_end', 'c.dt')} AND c.prev = "
          f"{fmt('add_days', 'c.dt', '-1')} THEN "
          f"{fmt('month_end', fmt('add_months', 'c.dt', str(ds.n)))} ELSE {lo} END")
    return lo, hi


def _date_step(ds: "_DateSet", rel: str, kc: list[str], dtab: str, dcol: str, fmt,
               anchor=None, date_col=lambda c: c) -> str:
    """One date set read from the dates `rel` holds (columns `kc` and `dt`): the dates it
    gives for each of `rel`'s rows of the visual, as the same columns. A period back from
    the last date of the measure's own table reads that date over the fact rows of `rel`'s
    dates (`anchor(rel)`: the statement of `kc` and `hi`)."""
    inner = _indent(rel, "      ")
    part = f"PARTITION BY {', '.join(kc)} " if kc else ""
    ck = [f"c.{k}" for k in kc]
    if ds.kind == "lookup":
        # `rel` holds the key the fact reaches the date table on: each of its rows' `via`
        # value, then the rows whose `match` is one of them.
        return ("SELECT DISTINCT " + ", ".join(ck + [f"s.{dcol} AS dt"])
                + f"\nFROM   (\n{inner}\n) c"
                + f"\nJOIN {dtab} v ON v.{dcol} = c.dt"
                + f"\nJOIN {dtab} s ON s.{date_col(ds.match)} = v.{date_col(ds.via)}")
    if ds.anchor_fact and anchor is not None:
        lo, hi = _date_set_range(ds, fmt)
        ctx_sql = "(\n" + _indent(anchor(rel), "    ") + "\n  ) c"
    elif ds.kind == "shift":
        lo, hi = _shift_bounds(ds, fmt)
        ctx_sql = ("(\n    SELECT " + ", ".join(kc + ["dt", f"LAG(dt) OVER ({part}ORDER BY dt) "
                                                           f"AS prev"])
                   + f"\n    FROM   (\n{inner}\n    ) r\n  ) c")
    else:
        lo, hi = _date_set_range(ds, fmt)
        ctx_sql = ("(\n    SELECT " + ", ".join(kc + ["MIN(dt) AS lo", "MAX(dt) AS hi"])
                   + f"\n    FROM   (\n{inner}\n    ) r"
                   + (f"\n    GROUP BY {', '.join(kc)}" if kc else "") + "\n  ) c")
    return ("SELECT DISTINCT " + ", ".join(ck + [f"s.{dcol} AS dt"])
            + f"\nFROM   {ctx_sql}\nJOIN {dtab} s ON s.{dcol} BETWEEN {lo} AND {hi}")


def _date_set_range(ds: "_DateSet", fmt) -> tuple[str, str]:
    """The first and last date of a range date set, from the row's first (`c.lo`) and last
    (`c.hi`) date."""
    part_of = {1: "month", 3: "quarter", 12: "year"}
    if ds.kind == "to_date":
        if ds.year_end and ds.year_end[0] != 12:
            # The fiscal year holding the last date starts the day after its year end:
            # move the date on by the months after the year-end month, take that calendar
            # year's start, and move it back.
            k = 12 - ds.year_end[0]
            lo = fmt("add_months", fmt("trunc", fmt("add_months", "c.hi", str(k)), "year"),
                     str(-k))
        elif ds.period is None:
            # A running total: every date of the date table up to the row's last; with an
            # open end (DATESBETWEEN from a blank start), a row with no date (the date
            # table's blank row) reads every date.
            lo = fmt("date", "0001-01-01")
            if ds.open_end:
                return lo, f"COALESCE(c.hi, {fmt('date', '9999-12-31')})"
        else:
            lo = fmt("trunc", "c.hi", ds.period)
        return lo, "c.hi"
    if ds.kind == "parallel":
        part = part_of[ds.months]
        if ds.previous:
            return (fmt("add_months", fmt("trunc", "c.lo", part), str(-ds.months)),
                    fmt("add_days", fmt("trunc", "c.lo", part), "-1"))
        return (fmt("add_months", fmt("trunc", "c.lo", part), str(ds.n * ds.months)),
                fmt("add_days", fmt("add_months", fmt("trunc", "c.hi", part),
                                    str((ds.n + 1) * ds.months)), "-1"))
    if ds.kind == "in_period":
        # From the row's last date, or from a literal date (the same dates on every row).
        end = (ds.anchor if isinstance(ds.anchor, _SqlDate) else fmt("date", ds.anchor)) \
            if ds.anchor else "c.hi"
        if ds.part == "day":
            edge = fmt("add_days", end, str(ds.n))
        else:
            # From a month's last day the edge is the last day of the month it lands in
            # (seen live: 30 November back three months starts on 1 September, not 31
            # August), otherwise the same day, clamped.
            moved = fmt("add_months", end, str(ds.n))
            edge = (f"CASE WHEN {end} = {fmt('month_end', end)} THEN "
                    f"{fmt('month_end', moved)} ELSE {moved} END")
        if ds.n < 0:
            return fmt("add_days", edge, "1"), end
        if ds.n > 0:
            return end, fmt("add_days", edge, "-1")
        raise CompileError("DATESINPERIOD over zero intervals is empty")
    if ds.kind == "between":
        return tuple(b if isinstance(b, _SqlDate) else fmt("date", b) for b in (ds.lo, ds.hi))
    raise CompileError(f"date set {ds.kind} has no range")


# --------------------------------------------------------------------------- the reader hooks
# Constructs with no SQL counterpart, by language. A unit needing one is `partial` with the
# construct NAMED — never quietly rewritten into something that merely runs.
UNTRANSLATABLE = {
    "dax": ["CALCULATE", "SAMEPERIODLASTYEAR", "DATESYTD", "DATESQTD", "DATESMTD",
            "TOTALYTD", "PARALLELPERIOD", "DATEADD", "USERELATIONSHIP", "ALLEXCEPT"],
    "tableau-calc": ["PREVIOUS_VALUE", "SCRIPT_", "RAWSQL"],
}


def _untranslatable(unit: dict) -> list[dict]:
    found = []
    for m in unit["measures"]:
        f = (m.get("formula") or "").upper()
        lang = m.get("formula_language") or ""
        for tok in UNTRANSLATABLE.get(lang, []):
            if tok.upper() in f:
                found.append({"construct": tok.strip("("), "in_measure": m["name"]})
    return found


def _apply_item(formula: str, item: str, measure: str) -> str:
    """A calculation item's DAX applied to one measure: SELECTEDMEASURE() is the measure's own
    formula, SELECTEDMEASURENAME() its name, ISSELECTEDMEASURE([m], ...) whether it is one of
    them."""
    toks = tokenize(item)
    out, i = [], 0
    while i < len(toks):
        k, v = toks[i]
        up = v.upper() if k == "func" else ""
        if up in ("SELECTEDMEASURE", "SELECTEDMEASURENAME", "ISSELECTEDMEASURE") and \
                i + 1 < len(toks) and toks[i + 1][0] == "lparen":
            end, args = _call_args(toks, i + 1)
            if end is None:
                raise CompileError(f"{v} is not closed")
            if up == "SELECTEDMEASURE":
                out += [("lparen", "(")] + tokenize(formula) + [("rparen", ")")]
            elif up == "SELECTEDMEASURENAME":
                out.append(("str", '"' + measure.replace('"', '""') + '"'))
            else:
                names = {_column_ref(a[0][1]).lower() for a in args if len(a) == 1}
                out.append(("func", "TRUE" if measure.lower() in names else "FALSE"))
            i = end + 1
            continue
        out.append((k, v))
        i += 1
    return _text_of(out)


def _calculation_items(unit: dict, groups: dict[str, dict], again, rules, dialect: str
                       ) -> Result | None:
    """A unit that groups or filters by a CALCULATION GROUP's column: each item kept is the
    unit with every DAX measure replaced by the item's DAX over it, and the statements are
    stacked, the item's name in the column's place (a matrix of Current / YTD / PY columns).
    None when the unit names no calculation group."""
    gb = list(unit.get("group_by") or [])
    gbt = list(unit.get("group_by_tables") or [None] * len(gb))

    by_column: dict[str, list[dict]] = {}
    for cg0 in groups.values():
        if all(cg0 is not x for x in by_column.get(cg0["column"], [])):
            by_column.setdefault(cg0["column"], []).append(cg0)

    def group_of(col: Any, table: Any) -> dict | None:
        cg = groups.get(str(table or "").lower()) or groups.get(_bare(table or "").lower())
        if cg is None and not table and len(by_column.get(str(col).lower(), [])) == 1:
            # The unit does not name the column's table (a calculation group reads no
            # warehouse table): its column, when one calculation group holds it.
            cg = by_column[str(col).lower()][0]
        return cg if cg and str(col).lower() == cg["column"] else None
    at = [i for i, (g, t) in enumerate(zip(gb, gbt)) if group_of(g, t)]
    flt = [f for f in unit.get("filters") or [] if f.get("column")
           and group_of(f["column"], f.get("table"))]
    if not at and not flt:
        return None
    res = Result()
    # SEVERAL calculation groups: the one of lowest precedence is applied to the measure
    # first, and the others around its result (seen live: PY of Per Order is the prior year's
    # sales per prior-year order, Time Calc being of higher precedence). This expands the
    # lowest; each of its statements expands the next (`again`).
    present: list[dict] = []
    for cg_ in [group_of(gb[i], gbt[i]) for i in at] + [group_of(f["column"], f.get("table"))
                                                       for f in flt]:
        if all(cg_ is not x for x in present):
            present.append(cg_)
    if len([i for i in at if group_of(gb[i], gbt[i]) is present[0]]) > 1 \
            or any(len([i for i in at if group_of(gb[i], gbt[i]) is x]) > 1 for x in present):
        res.coverage = REFUSED
        res.reason = "one calculation group's column twice in one visual is not compiled"
        return res
    if len(present) > 1:
        precs = [x.get("precedence") if x.get("precedence") is not None else 0
                 for x in present]
        if len(set(precs)) < len(precs):
            res.coverage = REFUSED
            res.reason = ("two calculation groups of the same precedence in one visual: which "
                          "is applied around the other the model does not say")
            return res
        cg = present[precs.index(min(precs))]
        at = [i for i in at if group_of(gb[i], gbt[i]) is cg]
        flt = [f for f in flt if group_of(f["column"], f.get("table")) is cg]
    else:
        cg = present[0]
    items = list(cg["items"])
    for f in flt:
        vals = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
        keep = {str(v).lower() for v in vals}
        op = str(f.get("op") or "in")
        if op not in ("in", "not_in", "eq", "="):
            res.coverage = REFUSED
            res.reason = f"a {op!r} filter on a calculation group's column is not compiled"
            return res
        items = [it for it in items if (it["name"].lower() in keep) == (op != "not_in")]
    if not items:
        res.coverage = REFUSED
        res.reason = "the visual's filters keep no calculation item"
        return res
    if not at and len(items) > 1:
        res.coverage = REFUSED
        res.reason = ("several calculation items in the filter of a visual that does not show "
                      "them: Power BI applies no one item there")
        return res
    base = {k: v for k, v in unit.items() if k != "totals"}
    base["filters"] = [f for f in unit.get("filters") or [] if f not in flt]
    if at:
        i0 = at[0]
        base["group_by"] = gb[:i0] + gb[i0 + 1:]
        base["group_by_tables"] = gbt[:i0] + gbt[i0 + 1:]
    parts = []
    for it in items:
        try:
            ms = [{**m, "formula": _apply_item(m["formula"], it["expression"], m["name"])}
                  if m.get("formula_language") == "dax" and m.get("formula") else m
                  for m in unit.get("measures") or []]
        except CompileError as exc:
            res.coverage = REFUSED
            res.reason = f"calculation item {it['name']!r}: {exc}"
            return res
        r = again(dict(base, measures=ms))
        if not r.sql or r.coverage == REFUSED:
            res.coverage = REFUSED
            res.reason = f"calculation item {it['name']!r}: {r.reason}"
            res.untranslatable = list(r.untranslatable)
            return res
        parts.append((it, r))
    if not at:
        r = parts[0][1]
        r.gotchas.append(f"calculation item {items[0]['name']!r} applied to every measure"
                         + _item_formats(items))
        r.rules_used.append("engine.dax_calculation_item")
        return r
    names, vals = _leaf_names(parts[0][1], dict(base, measures=unit.get("measures") or []))
    out_col = _measure_alias(gb[at[0]])
    blocks = []
    for it, r in parts:
        n_k, n_v = _leaf_names(r, dict(base, measures=unit.get("measures") or []))
        sel = [f"p.{n} AS {names[j]}" for j, n in enumerate(n_k)]
        sel.insert(at[0], f"{_sql_string(chr(39) + it['name'].replace(chr(39), chr(39) * 2) + chr(39), dialect)} AS {out_col}")
        sel += [f"p.{v} AS {v}" if v in n_v else f"NULL AS {v}" for v in vals]
        blocks.append("SELECT " + ", ".join(sel) + "\nFROM   (\n"
                      + "\n".join("    " + ln for ln in r.sql.splitlines()) + "\n) p")
        res.rules_used += [x for x in r.rules_used if x not in res.rules_used]
        res.untranslatable += r.untranslatable
    res.sql = "\nUNION ALL\n".join(blocks)
    res.rules_used.append("engine.dax_calculation_items")
    partial = any(r.coverage == PARTIAL for _it, r in parts)
    for t in unit.get("totals") or []:
        # A total keeping the items' column is the items again over fewer columns; one that
        # does not has no item in context, so each measure is itself (one kept by a filter
        # still applies).
        keep = [g for g in gb if g in set(t.get("group_by") or [])]
        ktabs = [gbt[gb.index(g)] for g in keep]
        entry = {"kind": t.get("kind") or ("subtotal" if keep else "grand_total"),
                 "group_by": keep, "sql": None, "reason": None}
        sub = dict(unit, group_by=keep, group_by_tables=ktabs, totals=None)
        if gb[at[0]] in keep:
            r = _calculation_items(sub, groups, again, rules, dialect)
        else:
            one = items if len(items) == 1 and flt else []
            r = again(dict(sub, filters=base["filters"], measures=[
                {**m, "formula": _apply_item(m["formula"], one[0]["expression"], m["name"])}
                if one and m.get("formula_language") == "dax" and m.get("formula") else m
                for m in unit.get("measures") or []]))
        if r is not None and r.sql and r.coverage != REFUSED:
            entry["sql"] = r.sql
        else:
            entry["reason"] = (r.reason if r is not None else "") or "the total does not compile"
            partial = True
            res.untranslatable.append({"construct": "total row",
                                       "why": f"{entry['kind']} over {keep}: {entry['reason']}"})
        res.totals.append(entry)
    res.coverage = PARTIAL if partial else TRANSLATED
    res.reason = "; ".join([r.reason for _it, r in parts if r.reason]
                           + [f"total {e['kind']} over {e['group_by']}: {e['reason']}"
                              for e in res.totals if e["sql"] is None])
    res.gotchas.append(f"{gb[at[0]]}: one statement per calculation item "
                       f"({', '.join(it['name'] for it, _r in parts)}), each measure with the "
                       f"item's DAX applied" + _item_formats(items))
    for _it, r in parts:
        res.gotchas += [g for g in r.gotchas if g not in res.gotchas]
    return res


def _item_formats(items: list[dict]) -> str:
    """The gotcha's words on the items' format strings: they change how a value is shown,
    never the value, so the statement leaves them out (they stay in bi_facts)."""
    fmt = [f"{it['name']} {it['format_string']}" for it in items if it.get("format_string")]
    return ("; format strings are not applied (they change how a value is shown, not the "
            "value" + (f": {', '.join(fmt)}" if fmt else "") + ")")


class FormulaCompiler:
    """The compile part of the reader socket (`sockets.BIReader`), shared by the built-in
    readers. A reader mixes it in and declares its `languages`; A3 calls `prepare` once per
    model and `compile_unit` once per unit, and the A3b gate calls `compile_unit` with no model
    on each regression case."""

    compiler_rules = RULES
    compiler_corpus = CORPUS

    def prepare(self, facts: dict) -> dict:
        """What compiling a unit of this ONE model needs: names are looked up only within the
        model that declares them."""
        cols_of = {t["name"]: {(c["source_column"] or c["name"]).lower() for c in t["columns"]}
                   for t in facts["tables"]}
        # Every DAX measure's formula, so a measure that references another is compiled from
        # it.
        measure_formulas = {m["name"].lower(): m["formula"] for m in facts["measures"]
                            if m.get("formula") and m.get("formula_language") == "dax"}
        # A measure named with a trailing or leading space (`[Margin (%) LFL ]`) is referenced
        # with it, but a reference is read without it (`_column_ref`): it is found under the
        # trimmed name too, where no other measure has that name.
        for k, f in list(measure_formulas.items()):
            measure_formulas.setdefault(k.strip(), f)
        # TABLEAU: a calc references other calcs (and dimension calcs) by name too, so they
        # are inlined the same way. And a TABLEAU PARAMETER (`[Parameters].[X]`) is fixed to
        # its DECLARED DEFAULT — trap 4: the branch the dashboard shows when nobody touches
        # the control — and every object compiled through one says so in a gotcha.
        tp = _tparams.Params(facts)
        interactions = list(facts.get("interactions") or [])
        for k, f in tp.inline_index().items():
            measure_formulas.setdefault(k, f)
        # A DAX CALCULATED COLUMN is an expression over its own row, not a warehouse column:
        # a unit grouping or filtering by it, and a measure aggregating it, compile its
        # formula. Keyed by `table[column]` (how DAX names it) and, where no plain column or
        # measure takes the bare name and no other table's calculated column shares it, by
        # the bare name too.
        plain = {(c.get("source_column") or c["name"]).lower() for t in facts["tables"]
                 for c in t["columns"] if not c.get("is_calculated")}
        bare: dict[str, set[str]] = {}
        row_formulas: set[str] = set()
        for t in facts["tables"]:
            for c in t["columns"]:
                if not (c.get("is_calculated") and c.get("formula")
                        and c.get("formula_language") == "dax"):
                    continue
                for tn in {str(t.get("logical_name") or ""), str(t["name"])} - {""}:
                    key = f"{tn.lower()}[{c['name'].lower()}]"
                    measure_formulas.setdefault(key, c["formula"])
                    row_formulas.add(key)
                bare.setdefault(c["name"].lower(), set()).add(c["formula"])
        for k, formulas in bare.items():
            if len(formulas) == 1 and k not in plain and k not in measure_formulas:
                measure_formulas[k] = next(iter(formulas))
                row_formulas.add(k)
        # Each column's data type, by every name its table goes by: text is compared as the
        # model holds it, and a date set is read only over a date column.
        types: dict[tuple[str, str], str] = {}
        for t in facts["tables"]:
            names = {str(t.get("logical_name") or ""), str(t["name"]), _bare(t["name"])} - {""}
            for c in t["columns"]:
                if not c.get("data_type"):
                    continue
                for tn in names:
                    for cn in {c["name"], c.get("source_column") or c["name"]}:
                        types.setdefault((tn.lower(), str(cn).lower()), str(c["data_type"]))
        # Model table (lower) -> the warehouse table it reads, so a measure naming a column of
        # another table is refused instead of being read off this statement's table.
        table_of = {str(t.get("logical_name") or t["name"]).lower(): t["name"]
                    for t in facts["tables"]}
        # A `formula_language: sql` measure (the core's own language) is PLACED in its model by
        # ROLE (lib/declared): two roles over one table are two joins, and a name the formula
        # uses is expanded to what it stands for. No other language reads it.
        declared = (_declared.Model.of(facts)
                    if any(m.get("formula_language") == "sql" for m in facts["measures"])
                    else None)
        # Every relationship of the model on warehouse tables: a measure may read a column of
        # a table its unit does not (a CALCULATE filter on a dimension's column).
        graph = [{**r, "from_table": table_of.get(str(r.get("from_table") or "").lower(),
                                                  r.get("from_table")),
                  "to_table": table_of.get(str(r.get("to_table") or "").lower(),
                                           r.get("to_table"))}
                 for r in _bi_models.edges_on_source_columns(facts.get("relationships") or [],
                                                             facts["tables"])
                 if r.get("from_table") and r.get("to_table")]
        # A model whose formulas are DAX compares text as DAX does: without case, and a
        # column's value without its trailing spaces (seen live). Decided from the model's
        # own language, never from which tool it came from.
        text_key = any(m.get("formula_language") == "dax" for m in facts["measures"]) or any(
            c.get("formula_language") == "dax" for t in facts["tables"] for c in t["columns"])
        # A table the reader gave a statement of its own (`source_lineage.statement`), by its
        # name; and each column a formula names otherwise than its table reads it.
        statements = {t["name"]: (t.get("source_lineage") or {})["statement"]
                      for t in facts["tables"]
                      if (t.get("source_lineage") or {}).get("statement")}
        renames: dict[tuple[str, str], str] = {}
        for t in facts["tables"]:
            for c in t["columns"]:
                src = c.get("source_column")
                if src and not c.get("is_calculated") and str(src).lower() != str(
                        c["name"]).lower():
                    renames[(_bare(t["name"]), str(c["name"]).lower())] = str(src)
        # The labels a view shows for a column's values (aliases), by (table, column) and by
        # the bare column where one table holds it.
        labels: dict[tuple[str, str], list[dict]] = {}
        for t in facts["tables"]:
            for c in t["columns"]:
                if c.get("value_labels"):
                    for tn in {str(t.get("logical_name") or ""), str(t["name"]), ""} - {None}:
                        for cn in {c["name"], c.get("source_column") or c["name"]}:
                            labels.setdefault((tn.lower(), str(cn).lower()),
                                              c["value_labels"])
        # Calculation groups by every name their table goes by: the column naming the items
        # and each item's DAX (`_calculation_items`).
        calc_groups: dict[str, dict] = {}
        for t in facts["tables"]:
            cg = t.get("calculation_group")
            if cg:
                for key in {str(t.get("logical_name") or ""), str(t["name"]),
                            _bare(t["name"])} - {""}:
                    calc_groups[key.lower()] = {"column": str(cg["column"]).lower(),
                                                "items": list(cg.get("items") or []),
                                                "precedence": cg.get("precedence")}
        # A table whose rows the file fixes and no relationship reaches: a parameter, read
        # off its rows per unit (`fixedtables`).
        from metrics_agent.plugs.readers import fixedtables as _fixed  # noqa: PLC0415
        fixed = _fixed.index(facts, facts.get("relationships") or [])
        return {"columns": cols_of, "measures": measure_formulas, "params": tp,
                "calc_groups": calc_groups, "fixed": fixed, "interactions": interactions,
                "labels": labels,
                "table_of": table_of, "declared": declared, "graph": graph,
                "types": types, "row_formulas": row_formulas, "text_key": text_key,
                "statements": statements, "renames": renames,
                # Tables read from the source on every query (DirectQuery): there the source
                # compares and groups text, with case and trailing spaces.
                "source_compared": {str(t["name"]).lower() for t in facts["tables"]
                                    if t.get("storage_mode") == "directquery"}}

    def compile_unit(self, unit: dict, rules, dialect: str, *, model: dict | None = None,
                     columns: dict[str, set[str]] | None = None,
                     logical: dict[str, str] | None = None,
                     table_of: dict[str, str] | None = None,
                     types: dict[tuple[str, str], str] | None = None) -> Compiled:
        if model is None:
            return compile_unit(unit, rules, dialect, columns=columns, logical=logical,
                                table_of=table_of, types=types)
        tp = model["params"]
        model_measures = list(unit["measures"])
        # A filter's own expression (a condition, a Top N's ranking) names parameters too, and
        # a Top N's count may be one (`n_parameter`, already its current value).
        exprs = [str(v.get(k)) for f in unit.get("filters") or []
                 for v in [f.get("value")] if isinstance(v, dict)
                 for k in ("expression", "by") if v.get(k)]
        used = sorted(set(tp.used([m.get("formula") or "" for m in unit["measures"]] + exprs))
                      | {str(f["value"]["n_parameter"]) for f in unit.get("filters") or []
                         if isinstance(f.get("value"), dict)
                         and f["value"].get("n_parameter") in tp.default})

        def fixed(f: dict) -> dict:
            v = f.get("value")
            if not isinstance(v, dict) or not any(v.get(k) for k in ("expression", "by")):
                return f
            return {**f, "value": {**v, **{k: tp.fix(v[k]) for k in ("expression", "by")
                                           if v.get(k)}}}
        unit = {**unit, "measures": [{**m, "formula": tp.fix(m.get("formula"))}
                                     for m in unit["measures"]],
                "filters": [fixed(f) for f in unit.get("filters") or []]}
        labels = model.get("labels") or {}
        if labels:
            gbt = list(unit.get("group_by_tables") or [])
            shown = {}
            for i, g in enumerate(unit.get("group_by") or []):
                t = str(gbt[i] if i < len(gbt) and gbt[i] else "").lower()
                hit = labels.get((t, str(g).lower())) or labels.get(("", str(g).lower()))
                if hit:
                    shown[str(g).lower()] = hit
            if shown:
                unit["member_labels"] = shown
        if model.get("fixed"):
            from metrics_agent.plugs.readers import fixedtables as _fixed  # noqa: PLC0415
            done = _fixed.compile_with_fixed(
                unit, model, lambda u, m: self.compile_unit(
                    u, rules, dialect, model=m, columns=columns, logical=logical,
                    table_of=table_of, types=types), dialect)
            if done is not None:
                return done
        if model.get("calc_groups"):
            expanded = _calculation_items(unit, model["calc_groups"], lambda u: self.compile_unit(
                u, rules, dialect, model=model, columns=columns, logical=logical,
                table_of=table_of, types=types), rules, dialect)
            if expanded is not None:
                return expanded
        res = compile_unit(unit, rules, dialect, columns=model["columns"],
                           measures=model["measures"], table_of=model["table_of"],
                           model=model.get("declared"), model_graph=model.get("graph"),
                           types=model.get("types"), row_formulas=model.get("row_formulas"),
                           text_key=("source" if any(
                               str(t).lower() in (model.get("source_compared") or set())
                               for t in unit.get("warehouse_tables") or [])
                               else bool(model.get("text_key"))),
                           renames=model.get("renames"), table_sql=model.get("statements"))
        if res.coverage == REFUSED or not res.sql:
            return res
        # A partial result names its own gaps (`compile_unit`); they are kept as they are. The
        # old rescan of the formulas for a fixed token list replaced nothing it could name with
        # an EMPTY list, which is how a partial unit reached its candidate saying nothing.
        if tp.viewer and tp.viewer_used(
                [m.get("formula") or "" for m in model_measures] + exprs
                + [f"[{g}]" for g in unit.get("group_by") or []]
                + [f"[{f.get('column')}]" for f in unit.get("filters") or []
                   if f.get("column")]):
            res.gotchas.append(
                f"Compiled for the run's viewer ({tp.viewer.get('source') or 'viewer.json'}): "
                f"its domain and group tests return what this identity sees; another viewer "
                f"sees other rows.")
        if used:
            res.gotchas.append(
                "Tableau parameter(s) fixed at their DECLARED DEFAULT: "
                + ", ".join(f"[{p}] = {tp.value.get(p, tp.default.get(p))}" for p in used)
                + ". On the dashboard a user can change them; this statement is the default "
                  "view only.")
        # A SET OR PARAMETER AN ACTION CHANGES (`bi_facts.interactions`): the statement is
        # for the state the file saved; the dashboard's follows the viewer's selection.
        text = " ".join([m.get("formula") or "" for m in model_measures] + exprs
                        + [f"[{g}]" for g in unit.get("group_by") or []]
                        + [f"[{f.get('column')}]" for f in unit.get("filters") or []
                           if f.get("column")]).lower()
        for act in model.get("interactions") or []:
            hit = (act["target"] in used if act["kind"] == "parameter_action"
                   else f"[{str(act['target']).lower()}]" in text)
            if hit:
                what = ("set" if act["kind"] == "set_action" else "parameter")
                src = (f" from {', '.join(act['sources'])}" if act.get("sources") else "")
                res.gotchas.append(
                    f"The {what} [{act['target']}] is changed by an action"
                    f"{(' (' + act['activation'] + src + ')') if act.get('activation') else src}"
                    f": compiled for the {'members' if what == 'set' else 'value'} the "
                    f"workbook saved; on the dashboard it follows the viewer's selection.")
        return res
