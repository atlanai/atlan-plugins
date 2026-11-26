# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""One question unit to its statement: compile_unit, totals, time intelligence, a measure's empty and constant values, and level-of-detail expressions at their own grain. Part of bicompile; see __init__.py."""
from __future__ import annotations

import re
from typing import Any
from metrics_agent.lib import declared as _declared
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

from .common import (  # noqa: F401
    CompileError,
    Result,
)

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


def _indent(sql: str, by: str = "    ") -> str:
    return "\n".join(by + ln for ln in sql.splitlines())


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


_SORT = re.compile(r"^(?P<expr>.+?)\s+(?P<dir>ASC|DESC)\s*$", re.I | re.S)


def _top_n_order(sort: str, unit: dict, rules, alias: str, dialect: str, res: Result,
                 colmap, measures, alias_of: dict[str, str], group_expr=None) -> str:
    """`<expression> ASC|DESC` -> the ORDER BY term. The expression is read in the unit's
    formula language; when it is a measure the statement already selects, its alias."""
    m = _SORT.match(sort.strip())
    if not m:
        raise CompileError("a sort is `<expression> ASC|DESC`")
    direction = m.group("dir").upper()
    # A sort that NAMES one of the measures this statement selects orders by that measure.
    named = m.group("expr").strip().strip("[]").lower()
    for x in unit.get("measures", []):
        a = _measure_alias(x["name"])
        if named in (str(x["name"]).lower(), a.lower()) and a in alias_of.values():
            return f"{a} {direction}"
    # One of the columns it groups by (the latest point of a trend: its axis, descending).
    if group_expr is not None:
        g = group_expr(named)
        if g:
            return f"{g} {direction}"
    language = next((x.get("formula_language") for x in unit.get("measures", [])
                     if x.get("formula_language")), None) or unit.get("expression_language")
    if language is None:
        raise CompileError("no formula language to read the sort expression in")
    expr = _compile_measure({"name": "top_n_sort", "formula": m.group("expr"),
                             "formula_language": language}, rules, alias, dialect, res,
                            colmap, measures, unit)
    return f"{alias_of.get(expr, expr)} {direction}"


def _rows_not_on_source(ctx: "_UnitContext") -> list[str]:
    """The tables other than the statement's own the visual groups or filters by. A visual
    lists its grouping columns' members WITHIN one table only where they have rows under its
    filters on that table (seen live); across tables, a row shows wherever a measure is not
    blank, rows of the statement's table or not."""
    if not ctx.groups:
        return []
    out = [t for _c, t, _s in ctx.groups if t and t != ctx.source]
    out += [t for _c, t in ctx.filters if t and t != ctx.source]
    return sorted(set(out))


def _blank_on_empty(m: dict, rules, alias: str, dialect: str, measures, unit: dict,
                    table_of, source: str, ctx: "_UnitContext") -> bool | None:
    """Whether a measure is BLANK on a row with no rows of the statement's table: True, False,
    or None when that cannot be shown. Its formula is compiled with every aggregate BLANK
    (and one summed across the visual's other rows not), and the constant evaluated."""
    scratch = Result()
    try:
        sql = _compile_measure(m, rules, alias, dialect, scratch, None, measures, unit,
                               home=(table_of, source) if table_of else None, ctx=ctx,
                               empty=True)
    except (CompileError, _TimeIntelligence):
        return None
    try:
        import sqlglot  # noqa: PLC0415 - loaded by A3 already
        tree = sqlglot.parse_one(f"SELECT {sql}", read=dialect or None).expressions[0]
        return _const(tree) is None
    except Exception:  # noqa: BLE001 - anything not a constant we can read: not shown
        return None


def _empty_value(m: dict, rules, alias: str, dialect: str, measures, unit: dict, table_of,
                 source: str, ctx: "_UnitContext") -> str | None:
    """The SQL of a measure's value on a row with no rows of the statement's table (every
    aggregate BLANK), when it reads no column and no window: a constant the warehouse
    evaluates with the measure's own blank rules. None otherwise."""
    scratch = Result()
    try:
        sql = _compile_measure(m, rules, alias, dialect, scratch, None, measures, unit,
                               home=(table_of, source) if table_of else None, ctx=ctx,
                               empty=True)
    except (CompileError, _TimeIntelligence):
        return None
    if re.search(r"\b\w+\.\w+|\bOVER\b|__CTXAGG", sql):
        return None
    return sql


def _empty_context_value(m: dict, rules, alias: str, dialect: str, measures, unit: dict,
                         table_of, source: str, ctx: "_UnitContext"
                         ) -> tuple[str, list[dict]] | None:
    """A measure's value on a row with no rows of the statement's table when it reads an
    aggregate over a wider context (a total over ALL, an ALLEXCEPT): its SQL with each such
    aggregate a marker `__EMPTYAGGn__`, and the aggregates ({n, sql, keep, drop}), each read
    from its own statement grouped by the columns it keeps. None when it reads anything
    else of the row."""
    scratch, aggs = Result(), []
    try:
        sql = _compile_measure(m, rules, alias, dialect, scratch, None, measures, unit,
                               home=(table_of, source) if table_of else None, ctx=ctx,
                               empty=True, empty_aggs=aggs)
    except (CompileError, _TimeIntelligence):
        return None
    if not aggs or re.search(r"\b\w+\.\w+|\bOVER\b|__CTXAGG", sql):
        return None
    return sql, aggs


class _NotConstant(Exception):
    pass


def _const(n) -> Any:
    """A constant SQL expression's value, NULL as None, in SQL's three-valued logic. Only
    what an aggregate-free measure compiles to; anything else raises `_NotConstant`."""
    import sqlglot.expressions as E  # noqa: PLC0415

    def num(v):
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return v
        try:
            return float(v)
        except (TypeError, ValueError):
            return v

    if isinstance(n, E.Null):
        return None
    if isinstance(n, E.Boolean):
        return bool(n.this)
    if isinstance(n, E.Literal):
        return n.this if n.is_string else float(n.this)
    if isinstance(n, (E.Paren, E.Alias)):
        return _const(n.this)
    if isinstance(n, E.Cast):
        return _const(n.this)
    if isinstance(n, E.Neg):
        v = _const(n.this)
        return None if v is None else -num(v)
    if isinstance(n, (E.Add, E.Sub, E.Mul, E.Div, E.Pow, E.Mod)):
        a, b = _const(n.this), _const(n.expression)
        if a is None or b is None:
            return None
        return 1.0      # some number: only blank or not matters here
    if isinstance(n, E.DPipe):
        a, b = _const(n.this), _const(n.expression)
        return None if a is None or b is None else f"{a}{b}"
    if isinstance(n, E.Coalesce):
        for x in [n.this] + list(n.expressions):
            v = _const(x)
            if v is not None:
                return v
        return None
    if isinstance(n, E.Nullif):
        a, b = _const(n.this), _const(n.expression)
        return None if a is None or (b is not None and num(a) == num(b)) else a
    if isinstance(n, E.Is):
        v = _const(n.this)
        if not isinstance(n.expression, E.Null):
            raise _NotConstant
        return v is None
    if isinstance(n, E.Not):
        v = _const(n.this)
        return None if v is None else not v
    if isinstance(n, E.And):
        a, b = _const(n.this), _const(n.expression)
        if a is False or b is False:
            return False
        return None if a is None or b is None else True
    if isinstance(n, E.Or):
        a, b = _const(n.this), _const(n.expression)
        if a is True or b is True:
            return True
        return None if a is None or b is None else False
    if isinstance(n, (E.EQ, E.NEQ, E.GT, E.LT, E.GTE, E.LTE)):
        a, b = _const(n.this), _const(n.expression)
        if a is None or b is None:
            return None
        a, b = num(a), num(b)
        try:
            return {E.EQ: a == b, E.NEQ: a != b, E.GT: a > b, E.LT: a < b,
                    E.GTE: a >= b, E.LTE: a <= b}[type(n)]
        except TypeError:
            raise _NotConstant from None
    if isinstance(n, E.Case):
        subject = _const(n.this) if n.this is not None else None
        for branch in n.args.get("ifs") or []:
            cond = _const(branch.this)
            if n.this is not None:
                cond = None if subject is None or cond is None else num(subject) == num(cond)
            if cond is True:
                return _const(branch.args["true"])
        default = n.args.get("default")
        return _const(default) if default is not None else None
    if isinstance(n, E.If):
        cond = _const(n.this)
        if cond is True:
            return _const(n.args["true"])
        f = n.args.get("false")
        return _const(f) if f is not None else None
    if isinstance(n, (E.Upper, E.Lower, E.Trim, E.Abs, E.Round, E.Floor, E.Ceil)):
        v = _const(n.this)
        return None if v is None else v
    if isinstance(n, (E.Least, E.Greatest)):
        vals = [_const(x) for x in [n.this] + list(n.expressions)]
        return None if any(v is None for v in vals) else vals[0]
    raise _NotConstant


def _compile_measure(m: dict, rules, alias: str, dialect: str, res: Result,
                     colmap: dict[str, str] | None = None,
                     measures: dict[str, str] | None = None, unit: dict | None = None,
                     home: tuple[dict[str, str], str] | None = None,
                     ctx: "_UnitContext | None" = None, slots: list | None = None,
                     empty: bool = False, empty_aggs: list | None = None) -> str:
    """A measure is either a formula (compile it) or a column plus a shelf aggregation."""
    formula, language = m.get("formula"), m.get("formula_language")
    tc = m.get("table_calc")
    if tc and tc.get("refusal"):
        # Computed over the view's rows in a way the reader could not state: its bare
        # aggregate would be a different number, so the measure is refused by name.
        res.untranslatable.append({"construct": "table calculation",
                                   "in_measure": m.get("name") or "", "why": tc["refusal"]})
        raise CompileError(f"table calculation: {tc['refusal']}")
    if formula and language == "sql":
        # Already SQL. One that names no table is used as written; one that does is placed by
        # compile_unit, and reaching here means it could not be.
        if _declared.qualifiers(formula):
            raise CompileError("a SQL measure that names tables is placed in its model, "
                               "and none was supplied")
        return formula
    agg0 = (m.get("aggregation") or "").lower()
    if formula and language in _LOD_LANGUAGES and agg0 in _SHELF_LOD \
            and _reaches_lod(formula, measures or {}):
        # A calc that IS one level of detail, on an aggregating shelf: the shelf's aggregate
        # around the level of detail (`SUM({FIXED …})`), never per row then summed.
        formula, m = f"{_SHELF_LOD[agg0]}({formula.strip()})", {**m, "aggregation": "custom"}
    if formula and language:
        # The parser is built OUTSIDE the try so what it learned survives the exception. It
        # accumulates the named untranslatable constructs and the unknown functions as it
        # goes, and those are the whole point of a failed compile: a refusal that cannot say
        # WHAT it could not do teaches the review pass nothing and teaches the rule table
        # nothing either.
        p = _Parser(_tokens(formula, language), rules, language, alias, dialect, colmap,
                    measures if language in ("dax", "tableau-calc") else None,
                    inlining=(m["name"].lower(),))
        p.home = home
        p.ctx, p.slots, p.empty = ctx, slots, empty
        p.empty_aggs = empty_aggs
        if ctx is not None:
            ctx.table_calc = _over_of(tc, ctx) if tc else None
            p.lods = ctx.lods
        try:
            sql = p.parse()
            if p.calc_columns and unit is not None and ctx is None:
                clash = p.calc_columns & (
                    {g.lower() for g in unit.get("group_by", [])}
                    | {str(f.get("column") or "").lower() for f in unit.get("filters", [])})
                if clash:
                    # DAX's CALCULATE REPLACES the visual's own filter on that column; a CASE
                    # inside the aggregate intersects with it. Different numbers, so refused.
                    raise CompileError(f"CALCULATE filters {sorted(clash)}, which the visual "
                                       f"also groups or filters by; DAX overrides that filter "
                                       f"and one statement cannot")
            agg = (m.get("aggregation") or "").lower()
            if "__LODROW" in sql and agg in ("", "custom", "none") \
                    and not _AGGREGATE_CALL.search(sql) and "__LODAGG" not in sql \
                    and not re.search(r"\b(COUNT\(DISTINCT|MEDIAN|STDDEV|VAR_)", sql):
                # A level of detail read per row, with nothing aggregating it: the view's
                # rows would be the table's rows.
                raise _lod_refusal("FIXED", "a value per row, which no aggregate reads")
            if language == "tableau-calc" and agg and agg not in ("custom", "none"):
                # TRAP 1: a Tableau field's aggregation is the SHELF's. A row-level calc on a
                # SUM shelf is SUM(calc); emitting the calc bare yields a non-aggregate beside
                # a GROUP BY. A calc that aggregates itself is `usr`/custom and stays as is.
                rule = rules.aggregation(agg)
                if rule is None:
                    p.unknown.append(f"aggregation:{agg}")
                    raise CompileError(f"no rule for aggregation {agg!r}")
                p.used.append(rule["id"])
                sql = _apply(_agg_emit(rule, agg, dialect), [f"({sql})"], agg)
            if ctx is not None and "__LODROW" in sql and _AGGREGATE_CALL.search(sql):
                sql = _lods_at_their_grain(sql, ctx.lods, alias)
            return sql
        finally:
            if ctx is not None:
                ctx.table_calc = None
            res.rules_used += p.used
            res.provisional_rules += p.provisional
            res.untranslatable += p.untranslatable
            res.unknown += p.unknown
    if not m.get("aggregation"):
        # No formula and no aggregation chosen where it is used: a name the model does not
        # define (a measure missing from the extract), never a column to sum. Summed, it read
        # a column of that name that does not exist, and the gates stopped the whole run.
        raise CompileError(f"{m['name']!r} has no formula and no aggregation: the model does "
                           f"not define it")
    agg = str(m["aggregation"]).lower()
    rule = rules.aggregation(agg)
    if rule is None:
        res.unknown.append(f"aggregation:{agg}")
        raise CompileError(f"no rule for aggregation {agg!r}")
    res.rules_used.append(rule["id"])
    if rule.get("tier") == "provisional":
        res.provisional_rules.append(rule["id"])
    if colmap is not None:
        src = colmap.get(m["name"].lower())
        if src is None:
            # A named measure with no formula text is not a column. Emitting its name as one
            # gave `SUM(t.New Accounts (Latest))`, which does not parse.
            raise CompileError(f"{m['name']!r} has no formula and is not a column of the "
                               f"native query")
        return _apply(_agg_emit(rule, agg, dialect), [f"{alias}.{_ident(src)}"], agg)
    # The field's own name. Bare when it is a legal identifier (the warehouse folds its case
    # as it did when the column was created); quoted only when it cannot stand bare, so
    # `Unit Price` gives `t."Unit Price"` instead of `t.Unit Price`, which does not parse.
    name = m["name"]
    col = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) else _ident(name)
    return _apply(_agg_emit(rule, agg, dialect), [f"{alias}.{col}"], agg)


def _agg_call(pred: str) -> bool:
    """Whether a predicate tests an aggregate (a HAVING, not a WHERE)."""
    return bool(re.search(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", pred, re.I))


def _context_statements(aggs: list[dict], src: str, alias: str, joins: list[str],
                        where: list[str], rules, dialect: str
                        ) -> tuple[list[str], dict[str, str]]:
    """The statements a non-additive aggregate over a wider context reads (`_combine`).

    Each is the same aggregate over the SAME rows as the statement (its FROM, its joins, its
    row filters: ALLSELECTED keeps the visual's filters, and ALL of a grouped column the
    visual does not filter removes nothing else), grouped by only the columns the context
    keeps. With none kept it is one value, a scalar subquery; otherwise a derived table
    joined back on the kept columns, NULL matching NULL (the blank member is a member), one
    row per kept combination so the join never multiplies the statement's rows."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"its statement joins back on the kept columns, NULL matching "
                           f"NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    def body_of(src_: str, where_: list[str]) -> str:
        b = f"FROM   {src_} {alias}" + "".join(f"\n{j}" for j in joins)
        if where_:
            b += "\nWHERE  " + "\n  AND  ".join(where_)
        return b
    out_joins, sub = [], {}
    for a in aggs:
        mark, n = _CTX_MARK.format(n=a["n"]), a["n"]
        body = body_of(a.get("src") or src, a["where"] if "where" in a else where)
        if not a["keep"]:
            # One value: a one-row derived table beside every row, read back as an
            # aggregate so the statement keeps its own grouping (none on a card).
            out_joins.append(f"CROSS JOIN (\n    SELECT {a['sql']} AS v\n    "
                             + body.replace("\n", "\n    ") + f"\n  ) c{n}")
            sub[mark] = f"MAX(c{n}.v)"
            continue
        keys = ", ".join(f"{k} AS k{i}" for i, k in enumerate(a["keep"]))
        on = "\n   AND ".join(tmpl.replace("{0}", f"c{n}.k{i}").replace("{1}", k)
                              for i, k in enumerate(a["keep"]))
        out_joins.append(
            f"LEFT JOIN (\n    SELECT {keys}, {a['sql']} AS v\n    "
            + body.replace("\n", "\n    ")
            + f"\n    GROUP BY {', '.join(a['keep'])}\n  ) c{n}\n    ON {on}")
        sub[mark] = f"MAX(c{n}.v)"
    return out_joins, sub


def _lods_at_their_grain(sql: str, lods: list[dict], alias: str) -> str:
    """An aggregate of an expression that reads FIXED / INCLUDE levels of detail and no field
    of the rows themselves is evaluated once per combination of the view's dimensions and
    theirs, then aggregated (checked live: SUM(IIF({INCLUDE [order] : SUM(x)} > n, 1, 0))
    counts orders, not order lines; with a row field beside it, it is read on every row)."""
    marks = {r["n"]: _LOD_MARK["row"].format(n=r["n"]) for r in lods
             if r.get("mode") == "row" and r["kind"] in ("FIXED", "INCLUDE")}
    left = sql
    for m in marks.values():
        left = left.replace(m, "")
    if re.search(rf"\b{re.escape(alias)}\.", left) or re.search(r"__LODROW\d+__", left):
        return sql
    for r in lods:
        if r["n"] in marks and marks[r["n"]] in sql:
            r["mode"], r["outer"] = "grain", None
            if r["kind"] == "INCLUDE" and r.get("value_out") is None:
                return sql
            sql = sql.replace(marks[r["n"]], _LOD_MARK["grain"].format(n=r["n"]))
    return sql


def _lod_related_columns(unit: dict, source: str, columns: dict[str, set[str]],
                         measures: dict[str, str] | None, graph: list[dict] | None = None
                         ) -> list[tuple[str, str]]:
    """(column, table) of each field a level-of-detail expression of the unit reads that the
    statement's own table lacks and exactly one other table it relates to holds."""
    texts = [m.get("formula") or "" for m in unit.get("measures") or []
             if m.get("formula_language") in _LOD_LANGUAGES]
    texts += [measures[str(g).lower()] for g in unit.get("group_by") or []
              if measures and str(g).lower() in measures]
    own = {c.lower() for c in columns.get(source) or set()}
    others = list(dict.fromkeys(
        t for t in list(unit.get("warehouse_tables") or [])
        + [e.get(k) for e in graph or [] for k in ("from_table", "to_table")]
        if t and _bare(t) != _bare(source)))
    out: list[tuple[str, str]] = []
    for text in texts:
        if not _reaches_lod(text, measures or {}):
            continue
        try:
            toks = _expanded_tokens(text, measures)
        except CompileError:
            continue
        for k, v in toks:
            if k != "col":
                continue
            col = _column_ref(v).lower()
            if col in own or (measures and col in measures):
                continue
            holders = [t for t in others
                       if col in {c.lower() for c in columns.get(t) or columns.get(_bare(t)) or set()}]
            if len(holders) == 1 and (col, holders[0]) not in out:
                out.append((col, holders[0]))
    return out


def _padded_crosstab(unit: dict, select: list[str], src: str, alias: str, joins: list[str],
                     dim_preds: list[str], having: list[str], lods: list, windowed: bool,
                     rules, dialect: str, gkey) -> str | None:
    """The statement over a DENSIFIED crosstab, or None. A table calculation on a crosstab
    (`table_calc.pad`: the columns on its rows and on its columns) runs over every row tuple
    laid out against every column tuple, the empty cells included (checked live: a missing
    cell shows, the running value carried into it). The grouped rows are read from that
    domain (the row tuples crossed with the column tuples, over the rows the filters keep),
    each joined to its own rows, NULL matching NULL; an empty cell has NULL values."""
    pad = next(((m.get("table_calc") or {}).get("pad") for m in unit.get("measures") or []
                if (m.get("table_calc") or {}).get("pad")), None)
    groups = list(unit.get("group_by") or [])
    if not pad or not windowed or not groups:
        return None
    if having or lods:
        # Not left to a statement over the grouped rows only, which would run the calculation
        # without the empty cells (a silently different answer).
        raise CompileError("a table calculation on a densified crosstab beside "
                           + ("a filter on an aggregate" if having else
                              "a level of detail") + ": the empty cells it adds are not "
                           "compiled there")
    names = [str(g).lower() for g in groups]
    rows = [names.index(str(n).lower()) for n in pad.get("rows") or [] if str(n).lower() in names]
    cols = [names.index(str(n).lower()) for n in pad.get("cols") or [] if str(n).lower() in names]
    if not rows or not cols or sorted(rows + cols) != list(range(len(groups))):
        return None
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        return None
    emit = op.get("emit") or {}
    nse = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    keys = [gkey(g, i) for i, g in enumerate(groups)]
    # Over joins, the layout's members are those of the rows that hold a row of the
    # statement's own table (checked live: a product no sale names adds no blank status
    # column), so its rows carry a marker.
    own = f"(SELECT {alias}.*, 1 AS __own FROM {src} {alias})" if joins else src
    held = [f"{alias}.__own = 1"] if joins else []
    base = (f"{own} {alias}" + "".join("\n      " + j.replace("\n", "\n      ") for j in joins)
            + ("\n      WHERE  " + "\n        AND  ".join(dim_preds) if dim_preds else ""))
    domain = (f"{own} {alias}" + "".join("\n      " + j.replace("\n", "\n      ")
                                         for j in joins)
              + ("\n      WHERE  " + "\n        AND  ".join(dim_preds + held)
                 if dim_preds or held else ""))
    # Over joined tables each column the statement reads is carried by name (`<alias>__<col>`)
    # out of the joined rows, so one alias reads them all.
    carried: dict[str, str] = {}
    if joins:
        for x in list(select) + keys:
            for m in re.finditer(rf"\b({re.escape(alias)}|j\d+)\.(\w+)\b", x):
                carried.setdefault(m.group(0), f"{m.group(1)}__{m.group(2)}")

    def distinct(idx: list[int]) -> str:
        return ("SELECT DISTINCT " + ", ".join(f"{keys[i]} AS __pad{i}" for i in idx)
                + f"\n      FROM   {domain}")
    on = "\n     AND ".join(nse.replace("{0}", f"x.__key{i}").replace("{1}", f"d.__pad{i}")
                           for i in range(len(groups)))
    body = (f"  SELECT d.*, x.*\n  FROM   (\n    SELECT * FROM (\n      {distinct(rows)}\n"
            f"    ) pr\n    CROSS JOIN (\n      {distinct(cols)}\n    ) pc\n  ) d\n"
            f"  LEFT JOIN (\n    SELECT " + ", ".join(f"{k} AS __key{i}" for i, k in enumerate(keys))
            + (", " + ", ".join(f"{c} AS {n}" for c, n in carried.items()) if carried
               else f", {alias}.*") + f"\n    FROM   {base}\n  ) x\n    ON {on}")
    order = sorted(range(len(keys)), key=lambda i: -len(keys[i]))
    out = []
    for j, x in enumerate(select):
        bare = j < len(groups) and not re.search(r"\sAS\s+\S+$", x)
        name = x.rsplit(".", 1)[-1].strip() if bare else None
        for i in order:
            x = x.replace(keys[i], f"{alias}.__pad{i}")
        if carried:
            x = re.sub(rf"\b({re.escape(alias)}|j\d+)\.(\w+)\b",
                       lambda m: (f"{alias}.{carried[m.group(0)]}" if m.group(0) in carried
                                  else m.group(0)), x)
        out.append(f"{x} AS {name}" if bare else x)
    return ("SELECT " + ",\n       ".join(out) + f"\nFROM   (\n{body}\n) {alias}"
            + "\nGROUP BY " + ", ".join(f"{alias}.__pad{i}" for i in range(len(groups))))


def _inner(join: str) -> str:
    """A join clause as an inner join: only the rows that reach the joined table."""
    return re.sub(r"^\s*(FULL\s+OUTER|LEFT(\s+OUTER)?|RIGHT(\s+OUTER)?)\s+JOIN\b", "JOIN",
                  join, flags=re.I)


def _related(text: str, side: str, prefix: str, plan: "_JoinPlan | None",
             source_columns: set[str] | None) -> tuple[str, set[str]]:
    """`side.col` of a column the statement's own table does not hold, as the related table's
    alias the join plan reads it under (`prefix` + alias): (text, the aliases it needs)."""
    if plan is None or not source_columns:
        return text, set()
    used: set[str] = set()

    def place(m) -> str:
        col = m.group(1)
        if col.lower() in source_columns or col.startswith("__lodin") \
                or col.startswith(("__lodd", "__lodr")) or re.fullmatch(r"e\d+", col):
            return m.group(0)
        at = next((a for (c, _t), a in list(plan._at.items()) + list(plan.measure_at.items())
                   if c == col.lower() and a != plan.alias), None)
        if at is None:
            return m.group(0)
        used.add(at)
        return f"{prefix}{at}.{col}"
    return re.sub(rf"\b{re.escape(side)}\.(\w+)", place, text), used


def _lod_parts(lods: list[dict], src: str, alias: str, rules, dialect: str,
               source_columns: set[str] | None, dim_preds: list[str] | None = None,
               plan: "_JoinPlan | None" = None, plan_joins: list[str] | None = None
               ) -> tuple[list[str], dict[str, str]]:
    """The joins a statement's levels of detail need and what each placeholder reads.

    A FIXED level of detail is its own statement over the rows the data source and context
    filters keep (`src`, before the view's dimension filters), grouped by its dimensions and
    joined to the statement's rows on them, NULL matching NULL (a member Tableau shows as
    Null is a member). One with no dimension is one value for the whole table."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"a level-of-detail expression joins on its dimensions, NULL "
                           f"matching NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    joins, sub = [], {}
    for r in lods:
        if r["mode"] == "grain" and r["kind"] == "INCLUDE":
            # Read over the view's own rows: its fields where the statement reads them.
            r["dims_out"] = [_related(d, alias, "", plan, source_columns)[0]
                             for d in r["dims_out"]]
            if r.get("value_out"):
                r["value_out"] = _related(r["value_out"], alias, "", plan, source_columns)[0]
            continue
        # A level of detail over a related table's field reads the rows through the same
        # relationships as the statement: its own joins, under its own aliases (`s<alias>`).
        r["value_in"], need = _related(r["value_in"], "s", "s", plan, source_columns)
        dims = [_related(d, "s", "s", plan, source_columns) for d in r["dims_in"]]
        r["dims_in"] = [d for d, _u in dims]
        need |= {a for _d, u in dims for a in u}
        # (Read again, a record already placed names its related aliases itself.)
        need |= set(re.findall(r"\bs(j\d+)\.", " ".join([r["value_in"]] + r["dims_in"])))
        r["dims_out"] = [_related(d, alias, "", plan, source_columns)[0] for d in r["dims_out"]]
        if r.get("value_out"):
            r["value_out"] = _related(r["value_out"], alias, "", plan, source_columns)[0]
        text = " ".join([r["value_in"]] + r["dims_in"])
        text = re.sub(r"\bs\.(__lodin\d+|__lodr\d+|e\d+)\b", "", text) if r.get("from") \
            else text
        text = re.sub(r"\bsj\d+\.\w+", "", text)
        def renamed(text: str) -> str:
            return re.sub(r"\bj(\d+)\b", r"sj\1",
                          re.sub(rf"\b{re.escape(alias)}\.", "s.", text))
        preds = [renamed(p) for p in dim_preds or []] if r.get("after_filters") else []
        if any(re.search(r"\bsj\d+\.", p) for p in preds):
            need = need or {"*"}
        rows = f"{src} s"
        if need:
            # Only the rows that reach the related table count (checked live: a fact row with
            # no category is in no category's FIXED value, and that member's value is empty).
            rows += "".join(f"\n    {_inner(renamed(j))}" for j in plan_joins or []
                            if not j.startswith("__"))
        if r.get("pre"):
            # A level of detail among its dimensions: a column of the rows it reads.
            cols = ", ".join(f"{w} AS {c}" for c, w in r["pre"])
            where = f" WHERE {' AND '.join(preds)}" if preds and not r.get("from") else ""
            rows = f"(\n      SELECT s.*, {cols}\n      FROM {src} s{where}\n    ) s"
            preds = [] if where else preds
        if r.get("from"):
            # Nested levels of detail over the rows (after the view's dimension filters, for
            # one an INCLUDE or EXCLUDE holds).
            kept = (f"(SELECT s.* FROM {src} s WHERE {' AND '.join(preds)})" if preds
                    else src)
            rows = (f"(\n      {r['from'].replace('{src}', kept).replace('{src_all}', src)}"
                    f"\n    ) s")
        elif preds:
            # INCLUDE / EXCLUDE: after the view's dimension filters.
            rows += f"\n    WHERE {' AND '.join(preds)}"
        if source_columns:
            missing = sorted({c for c in re.findall(r"\bs\.(\w+)", text)
                              if c.lower() not in source_columns
                              and not c.startswith(("__lodd", "__lodr"))})
            if missing:
                raise CompileError(f"a level-of-detail expression reads {missing}, which are "
                                   f"not columns of the statement's own table")
        n = r["n"]
        if r["mode"] == "scalar":
            sub[_LOD_MARK["scalar"].format(n=n)] = f"(SELECT {r['value_in']} FROM {rows})"
            continue
        if r["mode"] == "members":
            # A Top N set: its members, ranked by the expression (an empty value last).
            d = r["dims_in"][0]
            sub[f"__LODSET{n}__"] = (
                f"(\n    SELECT k FROM (\n      SELECT {d} AS k, {r['value_in']} AS v\n"
                f"      FROM   {rows}\n"
                + (f"      WHERE  {r['among']}\n" if r.get("among") else "")
                + f"      GROUP BY {d}\n    ) m\n"
                f"    ORDER BY {_sort_term('v', r['direction'], 'LAST', dialect)}\n"
                f"    {_limit(int(r['limit']), dialect)}\n  )")
            continue
        keys = ", ".join(f"{d} AS k{i}" for i, d in enumerate(r["dims_in"]))
        on = "\n   AND ".join(tmpl.replace("{0}", f"l{n}.k{i}").replace("{1}", d)
                              for i, d in enumerate(r["dims_out"]))
        joins.append(f"LEFT JOIN (\n    SELECT {keys}, {r['value_in']} AS v\n    FROM   {rows}"
                     f"\n    GROUP BY {', '.join(r['dims_in'])}\n  ) l{n}\n    ON {on}")
        if r["mode"] == "row":
            sub[_LOD_MARK["row"].format(n=n)] = f"l{n}.v"
    return joins, sub


def _grain_statement(select: list[str], groups: list[str], grain: list[dict], src: str,
                     alias: str, joins: list[str], where: list[str],
                     lod_sub: dict[str, str], nse: str | None = None) -> str:
    """The statement for aggregates over FIXED / INCLUDE levels of detail: the view's rows at
    the grain of its own dimensions and every such level of detail's (`g`), then aggregated
    to the view. A plain SUM / COUNT / MIN / MAX beside it is computed at that grain and
    aggregated again. Any other aggregate of the view's rows beside it (an average, a
    distinct count, a median) is computed at the view's own grain in a statement of its own
    (`p`), joined to the view's rows on its dimensions, NULL matching NULL (`nse`)."""
    def name_of(entry: str) -> str:
        if re.search(r"\sAS\s+\w+$", entry):
            return entry.rsplit(" AS ", 1)[1].strip()
        return entry.rsplit(".", 1)[-1].strip()
    inner, outer = [], []
    direct: dict[str, str] = {}
    for i, g in enumerate(groups):
        inner.append(f"{g} AS g{i}")
        outer.append(f"g.g{i} AS {name_of(select[i])}")
    dims = list(dict.fromkeys(d for r in grain for d in r["dims_out"]))
    inner += [f"{d} AS d{j}" for j, d in enumerate(dims)]
    for r in grain:
        n = r["n"]
        inner.append(f"MAX(l{n}.v) AS x{n}" if r["kind"] == "FIXED"
                     else f"{r['value_out']} AS x{n}")
    atoms: dict[str, str] = {}
    simple = re.compile(r"\b(SUM|MIN|MAX|COUNT)\(([^()]*)\)")
    for entry in select[len(groups):]:
        expr, label = entry.rsplit(" AS ", 1)
        for r in grain:
            if r["outer"] is None:
                # An expression of the level of detail, evaluated at the grain (`g`) and
                # aggregated by the aggregate around it.
                expr = expr.replace(_LOD_MARK["grain"].format(n=r["n"]), f"g.x{r['n']}")
                continue
            fn = _LOD_OUTER[r["outer"]]
            close = ")" if "(" in fn else ""
            expr = expr.replace(_LOD_MARK["grain"].format(n=r["n"]),
                                f"{fn}{'' if close else '('}"
                                                    f"{' ' if close else ''}g.x{r['n']})")

        def atom(m) -> str:
            fn, arg = m.group(1).upper(), m.group(2)
            if arg.strip().upper().startswith("DISTINCT") or arg.strip().startswith("g."):
                return m.group(0)
            key = f"{fn}({arg})"
            if key not in atoms:
                atoms[key] = f"a{len(atoms)}"
            return f"{'SUM' if fn in ('SUM', 'COUNT') else fn}(g.{atoms[key]})"
        expr = simple.sub(atom, expr)
        expr = _view_grain_atoms(expr, alias, direct)
        for i in sorted(range(len(groups)), key=lambda i: -len(groups[i])):
            # A window beside it runs over the view's rows: its columns are the view's own.
            expr = expr.replace(groups[i], f"g.g{i}")
        if re.search(rf"\b{re.escape(alias)}\.|\bl\d+\.", expr):
            raise CompileError(f"{label.strip()} beside it reads the rows in a way that does "
                               f"not aggregate again from the grain of the level of detail")
        outer.append(f"{expr} AS {label.strip()}")
    inner += [f"{k} AS {v}" for k, v in atoms.items()]
    sql = "SELECT " + ",\n       ".join(inner) + f"\nFROM   {src} {alias}"
    # An INCLUDE over a related table's field reads only the rows that reach it (checked
    # live: the rows with no category are in no INCLUDE of a product, and no row shows them).
    related = any(r["kind"] == "INCLUDE" and re.search(r"\bj\d+\.", " ".join(r["dims_out"]))
                  for r in grain)
    for j in joins:
        lod_join = re.search(r"\)\s+l\d+\s+ON\b", j) is not None
        sql += f"\n{_inner(j) if related and not lod_join else j}"
    if where:
        sql += "\nWHERE  " + "\n  AND  ".join(where)
    keys = groups + dims
    if keys:
        sql += "\nGROUP BY " + ", ".join(keys)
    out = "SELECT " + ",\n       ".join(outer) + "\nFROM   (\n" + sql + "\n) g"
    if direct:
        if groups and not nse:
            raise CompileError("an aggregate beside it joins on the view's dimensions, NULL "
                               "matching NULL, which has no form for this dialect")
        body = ("SELECT " + ",\n       ".join([f"{g} AS g{i}" for i, g in enumerate(groups)]
                                              + [f"{v} AS {k}" for k, v in direct.items()])
                + f"\nFROM   {src} {alias}")
        for j in joins:
            if not re.match(r"(?s)LEFT JOIN \(\n    SELECT .* \) l\d+\n", j):
                body += f"\n{j}"
        if where:
            body += "\nWHERE  " + "\n  AND  ".join(where)
        if groups:
            body += "\nGROUP BY " + ", ".join(groups)
        on = "\n   AND ".join(nse.replace("{0}", f"p.g{i}").replace("{1}", f"g.g{i}")
                              for i in range(len(groups))) if groups else "1 = 1"
        out += f"\nLEFT JOIN (\n{body}\n) p\n    ON {on}"
    if groups:
        out += "\nGROUP BY " + ", ".join(f"g.g{i}" for i in range(len(groups)))
    for key, val in lod_sub.items():
        out = out.replace(key, val)
    return out


# An aggregate of the view's rows that does not aggregate again from a finer grain.
_VIEW_GRAIN_CALL = re.compile(r"\b(AVG|COUNT|MEDIAN|STDDEV_SAMP|STDDEV_POP|VAR_SAMP|VAR_POP|"
                              r"PERCENTILE_CONT|SUM|MIN|MAX)\s*\(", re.I)


def _view_grain_atoms(expr: str, alias: str, direct: dict[str, str]) -> str:
    """Each aggregate call in `expr` that still reads the view's rows (`alias.`) and not the
    grain's (`g.`) becomes a column of the view-grain statement: `MAX(p.b<k>)`, one value per
    view row."""
    out, i = "", 0
    while True:
        m = _VIEW_GRAIN_CALL.search(expr, i)
        if m is None:
            return out + expr[i:]
        depth, j = 0, m.end() - 1
        while j < len(expr):
            depth += {"(": 1, ")": -1}.get(expr[j], 0)
            if depth == 0:
                break
            j += 1
        call = expr[m.start():j + 1]
        after = expr[j + 1:].lstrip()
        if (re.search(rf"\b{re.escape(alias)}\.", call) and "g." not in call
                and not after.upper().startswith("OVER")):
            key = next((k for k, v in direct.items() if v == call), None)
            if key is None:
                key = f"b{len(direct)}"
                direct[key] = call
            out += expr[i:m.start()] + f"MAX(p.{key})"
            i = j + 1
        else:
            out += expr[i:m.end()]
            i = m.end()


_SHELF_LOD = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
              "count_distinct": "COUNTD", "median": "MEDIAN"}


def _reaches_lod(formula: str, measures: dict[str, str], depth: int = 0) -> bool:
    """Whether a formula holds a level-of-detail expression, itself or through the calcs it
    names: the shelf's aggregate is then put around it in the formula, where the parser reads
    an aggregate over a level of detail at its grain."""
    if "{" in _without_literals(formula or ""):
        return True
    if depth > 16:
        return False
    return any(_reaches_lod(measures[k], measures, depth + 1)
               for k in {m.lower() for m in re.findall(r"\[([^\]]+)\]", formula or "")}
               if k in measures)


def _over_of(tc: dict, ctx: "_UnitContext") -> dict:
    """A measure's table calculation as the window it runs in: the grouped columns' SQL it
    restarts on (partition) and runs along, in order (order). A column the statement does not
    group by is refused: the view's rows would not be the statement's."""
    by_name = {name: sql for name, _table, sql in ctx.groups}
    out: dict[str, list[str]] = {}
    for key, want in (("partition", "partitioning"), ("order", "addressing")):
        cols = []
        for c in tc.get(want) or []:
            sql = by_name.get(str(c).lower())
            if sql is None:
                raise CompileError(f"a table calculation runs over {c!r}, which the statement "
                                   f"does not group by")
            cols.append(sql)
        out[key] = cols
    # A sorted addressed column runs in the view's order: its SQL with the sort, and the SQL
    # of the columns a nested sort orders within.
    sorts = {}
    for c, rec in (tc.get("sort") or {}).items():
        sql = by_name.get(str(c).lower())
        if sql is None:
            continue
        within = [by_name[str(w).lower()] for w in rec.get("within") or []
                  if str(w).lower() in by_name]
        sorts[sql] = {**rec, "within_sql": within}
    if sorts:
        out["sorts"] = sorts
    if tc.get("nested"):
        # A calc the measure reaches runs by its own compute-using: {calc, lower: window}.
        out["nested"] = {str(k).lower(): _over_of(v, ctx) for k, v in tc["nested"].items()}
    return out


# The sibling modules' names, imported after this module's own definitions: the modules
# call each other, and a name is looked up only when a function runs.
from .parser import (  # noqa: E402
    _Parser,
    _column_ref,
)
from .expressions import (  # noqa: E402
    _AGGREGATE_CALL,
    _CTX_MARK,
    _DateChain,
    _LOD_LANGUAGES,
    _LOD_MARK,
    _LOD_OUTER,
    _SqlDate,
    _TimeIntelligence,
    _agg_emit,
    _apply,
    _lod_refusal,
    _tokens,
    _without_literals,
)
from .statements import (  # noqa: E402
    _compile_unit,
    _hoist_recursions,
    _ident,
    _measure_alias,
)
from .joins import (  # noqa: E402
    _JOIN_CLAUSE,
    _bare,
    _blank_member_test,
    _blank_rows_having,
    _expanded_tokens,
    _filter_reads_column,
    _hide_blank_rows_after,
    _limit,
    _plan_filters,
    _ranked_members,
    _sort_term,
)
