# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""bicompile, part 3 of 4: the BI→SQL compiler, split for the plugin directory's 256 KiB file limit. See __init__.py."""
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
    CompileError,
    Result,
    _AGGREGATE_CALL,
    _CONTEXT_FREE_FUNCS,
    _CTX_MARK,
    _DATE_LIT,
    _DATE_TYPES,
    _ISO_DAY,
    _LIMIT,
    _LOD_LANGUAGES,
    _LOD_MARK,
    _LOD_OUTER,
    _NOT_WALKED,
    _NUM_LIT,
    _TimeIntelligence,
    _apply,
    _bare,
    _call_args,
    _call_head,
    _calls_table_calc,
    _card_ends,
    _column_ref,
    _context_free,
    _emit_for,
    _expanded_tokens,
    _filtered_by,
    _foldable,
    _ident,
    _literal,
    _literal_list,
    _lod_refusal,
    _mod,
    _quote,
    _sort_term,
    _spec_edge,
    _split_top,
    _table_ref,
    _tokens,
    tokenize,
)
from ._part2 import (  # noqa: F401
    _Parser,
    _SHELF_LOD,
    _SORT,
    _agg_emit,
    _lods_at_their_grain,
    _measure_alias,
    _over_of,
    _reaches_lod,
    compile_expression,
)


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


_ITERATORS = ("SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX")


def _text_of(toks: list) -> str:
    return " ".join(v for _k, v in toks)


def _strip_parens(toks: list) -> list:
    while len(toks) >= 2 and toks[0][0] == "lparen" and toks[-1][0] == "rparen" \
            and _call_args([("func", "X")] + toks, 1)[0] == len(toks):
        toks = toks[1:-1]
    return toks


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


def _indent(sql: str, by: str = "    ") -> str:
    return "\n".join(by + ln for ln in sql.splitlines())


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


def _physical_edges(graph: list[dict], table_of: dict[str, str]) -> list[dict]:
    """Each relationship with an end named as a model table (`table_of`'s key, lower) named as
    the warehouse table that model table reads; ends already physical are kept."""
    def phys(t: Any) -> Any:
        return table_of.get(str(t or "").lower(), t) if t else t
    return [{**e, "from_table": phys(e.get("from_table")), "to_table": phys(e.get("to_table"))}
            for e in graph]


def _renamed_edges(graph: list[dict], renames: dict[tuple[str, str], str]) -> list[dict]:
    """Each relationship with its columns as the tables read them (`renames`: (table, bare,
    lower; model column, lower) -> the column read), where they differ."""
    def col(table: Any, c: Any) -> Any:
        return renames.get((_bare(table), str(c or "").lower()), c) if c else c
    out = []
    for e in graph:
        f, t = e.get("from_table"), e.get("to_table")
        new = {**e, "from_column": col(f, e.get("from_column")),
               "to_column": col(t, e.get("to_column"))}
        if e.get("columns"):
            new["columns"] = [{**p, "from_column": col(f, p.get("from_column")),
                               "to_column": col(t, p.get("to_column"))}
                              for p in e["columns"]]
        out.append(new)
    return out


def _edge_pairs(e: dict) -> list[tuple[str, str]]:
    pairs = [(str(p["from_column"]), str(p["to_column"])) for p in e.get("columns") or []]
    return pairs or [(str(e["from_column"]), str(e["to_column"]))]


class _Step:
    """One hop of a join path: along relationship `edge` (its index in the unit's graph),
    forwards (from end -> to end) or back, onto `table`."""

    def __init__(self, idx: int, edge: dict, forward: bool, table: str, distinct: bool,
                 keep: bool, keep_far: bool = False) -> None:
        self.idx, self.edge, self.forward, self.table = idx, edge, forward, table
        # keep: the near end's unmatched rows survive; keep_far: the reached end's too (a
        # relationship that keeps what does not match on either side).
        self.distinct, self.keep, self.keep_far = distinct, keep, keep_far
        pairs = _edge_pairs(edge)
        # (column on the table the hop leaves, column on the table it reaches)
        self.pairs = pairs if forward else [(b, a) for a, b in pairs]

    @property
    def key(self) -> tuple[int, bool]:
        return (self.idx, self.forward)

    @property
    def name(self) -> str:
        e = self.edge
        return str(e.get("name") or f"{_bare(e.get('from_table'))}.{e.get('from_column')} -> "
                                     f"{_bare(e.get('to_table'))}.{e.get('to_column')}")


class _JoinPlan:
    """The joins a statement needs, and where each column it groups or filters by is read.

    `at(column, table)` is the alias a column is read under: the statement's own alias, or the
    alias of the table a declared relationship path reaches."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.clauses: list[str] = []
        self.unreachable: set[str] = set()
        self.reasons: list[str] = []
        self.full = False          # a hop keeps the reached table's unmatched rows too
        self._at: dict[tuple[str, str], str] = {}
        # Each joined alias -> the key columns it is joined on, so an expression over a
        # joined table's row can tell an unmatched row (all NULL) from a matched one.
        self.keys: dict[str, list[str]] = {}
        # (column, warehouse table) a MEASURE reads through a joined table -> its alias, and
        # why one could not be reached (a measure that needs it is refused, not the unit).
        self.measure_at: dict[tuple[str, str], str] = {}
        self.unplaced: dict[tuple[str, str], str] = {}
        # Each joined alias -> the warehouse table it reads (bare).
        self.table_at: dict[str, str] = {}
        # Columns on a table the relationships reach only against their filter direction or
        # along an inactive relationship: (column -> (table, hint)). Such a table does not
        # filter the statement's rows; the visual lists its members beside every row.
        self.detached: dict[str, tuple[str, str | None]] = {}
        # A path through an end that is not one row (many-to-many, or one-to-many along a
        # relationship that filters both ways): its root alias -> how it is read
        # (`_expand_branches`), and the row predicates on its tables, which belong inside it.
        self.branches: dict[str, dict] = {}
        self.branch_preds: dict[str, list[str]] = {}
        self.graph: list[dict] = []

    def at(self, column: str, table: str | None = None) -> str:
        col = str(column).lower()
        return (self._at.get((col, _bare(table))) or self._at.get((col, ""))
                or self.alias)


def _blank_rows_having(values: list[str]) -> str | None:
    """`(a IS NOT NULL OR b IS NOT NULL ...)` over the statement's value projections (each
    `expr AS alias`); None when there is none, or one is a window (a HAVING cannot test it)."""
    exprs = [x.rsplit(" AS ", 1)[0] for x in values]
    if not exprs or any(" OVER (" in e for e in exprs):
        return None
    return "(" + "\n       OR ".join(f"{e} IS NOT NULL" for e in exprs) + ")"


def _hide_blank_rows_after(sql: str, select: list[str], n_groups: int) -> str:
    """The statement's rows without those whose every value is empty, where a value is a
    window (computed across the visual's rows), which a HAVING cannot test. Power BI leaves a
    row out of a visual when every measure on it is BLANK, whatever the measure reads
    (SUMMARIZECOLUMNS keeps a row only where at least one of its expressions is not blank:
    learn.microsoft.com/dax/summarizecolumns-function-dax). The window is computed first, over
    every grouped row: a total over a wider context reads the model's rows, not the rows the
    visual goes on to show (seen against Power BI: a city with no red sales has no share of
    the red total and no row)."""
    names = [_out_name(x) for x in select]
    cols = ",\n       ".join(f"b_.{n}" for n in names)
    shown = "\n    OR ".join(f"b_.{n} IS NOT NULL" for n in names[n_groups:])
    return f"SELECT {cols}\nFROM   (\n{sql}\n) b_\nWHERE  ({shown})"


_JOIN_CLAUSE = re.compile(r"^\s*(?:LEFT\s+|FULL\s+(?:OUTER\s+)?|INNER\s+)?JOIN\s+(.+?)\s+(\w+)"
                          r"\s+ON\s+(.*)$", re.S | re.I)


def _with_every_member(leaf: str, select: list[str], n_groups: int, gexprs: list[str],
                       src: str, alias: str, joins: list[str], preds: list[str], rules,
                       dialect: str, graph: list[dict] | None = None,
                       members: list[str] | None = None,
                       folded: set[int] | frozenset = frozenset()) -> str:
    """"Show items with no data": every member of the grouping columns, with the leaf
    statement's values where it has a row (NULL elsewhere).

    The members of the columns read on ONE table are the combinations that table holds
    (under the filters on it, and on the tables it reaches along its own many-to-one hops,
    which filter it); columns of different tables are crossed. A filter on a table only
    REACHED FROM a grouped table (the fact, when a dimension is grouped) does not narrow that
    dimension's members. The leaf's own rows are kept too (a FULL JOIN), so a member only the
    rows have (the blank member of unmatched keys) stays."""
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"it joins the members to the rows, NULL matching NULL, which has "
                           f"no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    # The join tree: each joined alias -> (its table, its ON clause, the alias it hangs off).
    tree: dict[str, tuple[str, str, str]] = {}
    for j in joins:
        m = _JOIN_CLAUSE.match(j)
        if not m:
            raise CompileError("a join of the statement this cannot read")
        parents = [a for a in re.findall(r"\b(\w+)\.", m.group(3)) if a != m.group(2)]
        if len(set(parents)) != 1:
            raise CompileError("a join of the statement that hangs off no single table")
        tree[m.group(2)] = (m.group(1), j, parents[0])

    def below(a: str) -> list[str]:
        out, todo = [], [a]
        while todo:
            cur = todo.pop(0)
            kids = [k for k, (_t, _j, par) in tree.items() if par == cur]
            out += kids
            todo += kids
        return out

    def aliases_in(text: str) -> set[str]:
        return {a for a in re.findall(r"\b(\w+)\.", re.sub(r"'(?:[^']|'')*'", "", text))
                if a == alias or a in tree}

    owner: list[str] = []
    for e in gexprs:
        on = aliases_in(e)
        if len(on) != 1:
            raise CompileError(f"a grouping column read on {len(on)} tables at once")
        owner.append(next(iter(on)))
    outs = []
    for x in select[:n_groups]:
        name = x.rsplit(" AS ", 1)[1] if " AS " in x else x.rsplit(".", 1)[-1]
        outs.append(name.strip())
    parts = []
    for k, a in enumerate(dict.fromkeys(owner)):
        reach = [a] + below(a)
        # Only a filter on the table itself narrows its members (seen live: SUMMARIZECOLUMNS
        # crosses a dimension's every member with the others under a filter on a table its
        # rows reach, e.g. every product beside the categories a department filter keeps);
        # the fact, which reaches every dimension, is narrowed by all of them.
        mine = [p for p in preds if aliases_in(p) and (
            aliases_in(p) <= {a} or (a == alias and aliases_in(p) <= set(reach)))]
        # A member filter (the members a Top N keeps) is a predicate on its key's table,
        # whatever its ranking subquery reads.
        for p in members or []:
            key = re.match(r"\(?\s*(\w+)\.\w+\s+IN\s*\(", p)
            if key and (key.group(1) == a or (a == alias and key.group(1) in reach)):
                mine.append(p)
        # A text column the model compares case- and trailing-space-insensitively is one
        # member per folded value (seen live: 'north' and 'North' are one region), shown by
        # the spelling the rows show.
        mine_idx = [i for i, o in enumerate(owner) if o == a]
        fold = [i for i in mine_idx if i in folded]
        cols = [f"MIN({gexprs[i]}) AS g{i}" if i in folded else f"{gexprs[i]} AS g{i}"
                for i in mine_idx]
        frm = (f"{src} {alias}" if a == alias else f"{tree[a][0]} {a}")
        body = (("SELECT " if fold else "SELECT DISTINCT ")
                + ",\n                   ".join(cols)
                + f"\n            FROM   {frm}"
                + "".join(f"\n            {tree[b][1]}" for b in below(a)))
        if mine:
            body += "\n            WHERE  " + "\n              AND  ".join(mine)
        if fold:
            body += "\n            GROUP BY " + ", ".join(
                _Parser._text_key(gexprs[i]) if i in folded else gexprs[i] for i in mine_idx)
        if not mine and a != alias:
            # The table's BLANK member: a row reaching it with no match (an unmatched key) is
            # a member of its own, with no filter on the table to leave it out.
            path, cur = [], a
            while cur in tree:
                path.insert(0, tree[cur][1])
                cur = tree[cur][2]
            key = re.search(rf"\b{re.escape(a)}\.(\w+)", tree[a][1].split(" ON ", 1)[-1])
            nulls = ", ".join(f"NULL AS g{i}" for i, o in enumerate(owner) if o == a)
            if cur == alias and key:
                body = (f"{body}\n            UNION\n            SELECT {nulls}"
                        f"\n            FROM   {src} {alias}"
                        + "".join(f"\n            {j}" for j in path)
                        + f"\n            WHERE  {a}.{key.group(1)} IS NULL")
            # Another relationship into the table (an inactive one too) whose keys it lacks
            # makes the same blank member (seen live: the ship date's empty keys give the
            # date table a blank year).
            other = _blank_member_test(tree[a][0], [e for e in graph or []
                                                    if e.get("is_active") is False])
            if other:
                body = (f"{body}\n            UNION\n            SELECT {nulls}"
                        f"\n            FROM   (SELECT 1 AS one) z\n            WHERE  {other}")
        parts.append(f"(\n            {body}\n        ) m{k}")
    idx = {a: k for k, a in enumerate(dict.fromkeys(owner))}
    on = " AND ".join(tmpl.format(_Parser._text_key(f"m{idx[o]}.g{i}"),
                                  _Parser._text_key(f"v.{outs[i]}")) if i in folded
                      else tmpl.format(f"m{idx[o]}.g{i}", f"v.{outs[i]}")
                      for i, o in enumerate(owner))
    keys = [f"COALESCE(m{idx[o]}.g{i}, v.{outs[i]}) AS {outs[i]}" for i, o in enumerate(owner)]
    vals = [f"v.{x.rsplit(' AS ', 1)[1].strip()}" for x in select[n_groups:]]
    inner = "\n".join("    " + ln for ln in leaf.splitlines())
    members = "\n    CROSS JOIN ".join(parts)
    return ("SELECT " + ",\n       ".join(keys + vals)
            + f"\nFROM   {members}\nFULL JOIN (\n{inner}\n) v\n  ON  {on}")


_REF = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def _branch_split(preds: list[str], plan: _JoinPlan) -> list[str]:
    """The row predicates that stay on the statement; the ones over a branch's tables only go
    inside it (`plan.branch_preds`): outside, a member that passes would repeat a row."""
    if not plan.branches:
        return preds
    owner = {al: root for root, br in plan.branches.items() for al in br["aliases"]}
    out = []
    for p in preds:
        text = re.sub(r"'(?:[^']|'')*'", "", p)
        hit = {owner.get(m.group(1)) for m in _REF.finditer(text)
               if m.group(1) in owner or m.group(1) == plan.alias
               or m.group(1) in plan.table_at}
        roots = {h for h in hit if h is not None}
        if not roots:
            out.append(p)
            continue
        if len(roots) > 1 or None in hit:
            raise CompileError("a filter over a many-to-many (or both-ways) path and another "
                               "table at once, which one distinct set of members cannot hold")
        plan.branch_preds.setdefault(next(iter(roots)), []).append(p)
    return out


def _blank_in(f: dict) -> bool | None:
    """Whether a filter keeps a blank member: an `in` listing the blank, a `not_in` not
    listing it; a test of the empty value (`is_null`), a text test (a blank holds no text:
    `contains` fails it, `not_contains` keeps it) and a comparison read with the filter's
    stand-in for an empty cell (`null_as`: 0, "" or the zero date). None for a filter whose
    test of a blank is not read here (a Top N, a relative date)."""
    op, v = str(f.get("op") or "").lower(), f.get("value")
    vals = v if isinstance(v, list) else [v]
    if op in ("in", "=") or (op == "eq" and isinstance(v, list)):
        return any(x is None for x in vals)
    if op in ("not_in", "ne", "<>"):
        return not any(x is None for x in vals)
    if op in ("is_null", "not_null"):
        return op == "is_null"
    if op in _TEXT_OPS:
        return op.startswith("not_")
    if op in ("eq", "filter", "gt", "lt", "neq", "lte", "gte", "range") and v is not None:
        return _empty_passes("eq" if op == "filter" else op, v, f.get("null_as"))
    if op in ("any_of", "all_of") and isinstance(v, list) and v:
        parts = [_blank_in({**part, "null_as": f.get("null_as")}) for part in v
                 if isinstance(part, dict)]
        if None in parts or len(parts) != len(v):
            return None
        return any(parts) if op == "any_of" else all(parts)
    return None


def _blank_member_test(table: str, graph: list[dict], _seen: frozenset = frozenset()
                       ) -> str | None:
    """When `table` has a BLANK member: the condition that some relationship into its one end
    holds a key it lacks (or no key), or that the table on its many side has a blank member
    of its own (whose key is blank: seen live, a category lists a blank when a sale's product
    is unknown, every product's category known). None when no relationship ends on it as
    one."""
    tests = []
    for e in graph or []:
        fs, ts = _card_ends(e.get("cardinality"))
        if str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        for many_t, one_t, many_end, one_end, flip in (
                (e.get("from_table"), e.get("to_table"), fs, ts, False),
                (e.get("to_table"), e.get("from_table"), ts, fs, True)):
            if _bare(one_t) != _bare(table) or one_end != "one" or many_end != "many":
                continue
            pairs = _edge_pairs(e)
            pairs = [(b, a) for a, b in pairs] if flip else pairs
            on = " AND ".join(f"y_.{oc} = x_.{mc}" for mc, oc in pairs)
            tests.append(f"EXISTS (SELECT 1 FROM {many_t} x_ "
                         f"WHERE NOT EXISTS (SELECT 1 FROM {one_t} y_ WHERE {on}))")
            if _bare(many_t) not in _seen:
                farther = _blank_member_test(many_t, graph, _seen | {_bare(table)})
                if farther:
                    tests.append(farther)
    return " OR ".join(dict.fromkeys(tests)) or None


def _expand_branches(sql: str, plan: _JoinPlan, rules, dialect: str) -> str:
    """Each branch placeholder -> `INNER JOIN (SELECT DISTINCT member, columns ...) a ON
    member = key`, NULL matching NULL (a blank member is a member). The statement's references
    to the branch's tables are read off the distinct set."""
    if not plan.branches:
        return sql
    op = rules.operator("null_safe_eq", dialect)
    if op is None:
        raise CompileError(f"a many-to-many (or both-ways) path joins on its members, NULL "
                           f"matching NULL, which has no form for the {dialect} dialect")
    emit = op.get("emit") or {}
    tmpl = (emit.get("dialect_templates") or {}).get(str(dialect).lower()) or emit["template"]
    for a, br in plan.branches.items():
        mine = set(br["aliases"])
        holder = f"__BRANCH_{a}__"
        head, _, tail = sql.partition(holder)
        if not _:
            continue
        # Each column the statement reads on the branch's tables is projected under its own
        # name, or `<alias>_<column>` where two of its tables share that name.
        found = {(m.group(1), m.group(2)) for m in _REF.finditer(head + "\n" + tail)
                 if m.group(1) in mine}
        count: dict[str, int] = {}
        for _al, col in found:
            count[col.lower()] = count.get(col.lower(), 0) + 1
        refs = {(al, col): (col if count[col.lower()] == 1 and not col.lower().startswith(
            f"{a}_m") else f"{al}_{col}") for al, col in found}

        def sub(m: re.Match, a: str = a, mine: set = mine, refs: dict = refs) -> str:
            al, col = m.group(1), m.group(2)
            if al not in mine:
                return m.group(0)
            return f"{a}.{refs[(al, col)]}"
        head, tail = _REF.sub(sub, head), _REF.sub(sub, tail)
        step = br["step"]
        e = step.edge
        near_table = e["from_table"] if step.forward else e["to_table"]
        pairs = step.pairs                    # (column on the table left, column reached)
        if br["near_one"]:
            # The member is the row of the one end each row reaches, or its blank member.
            member = [f"{a}_m.{sc}" for sc, _dc in pairs]
            via = (f"\n    LEFT JOIN {near_table} {a}_m ON "
                   + " AND ".join(f"{a}_m.{sc} = {a}.{dc}" for sc, dc in pairs))
        else:
            member = [f"{a}.{dc}" for _sc, dc in pairs]
            via = ""
        base = str(step.table)
        blank = _blank_member_test(base, plan.graph)
        if blank:
            base = (f"(\n      SELECT * FROM {step.table}\n      UNION ALL\n      SELECT b0_.*"
                    f"\n      FROM   (SELECT 1 AS one_) o_\n      LEFT JOIN {step.table} b0_ "
                    f"ON 1 = 0\n      WHERE  {blank}\n    )")
        cols = [f"{m} AS {a}_m{i}" for i, m in enumerate(member)]
        cols += [f"{al}.{col} AS {name}" for (al, col), name in refs.items()
                 if name not in {f"{a}_m{i}" for i in range(len(member))}]
        body = ("SELECT DISTINCT " + ",\n           ".join(dict.fromkeys(cols))
                + f"\n    FROM   {base} {a}" + via
                + "".join(f"\n    {c}" for c in br["clauses"]))
        preds = plan.branch_preds.get(a) or []
        if preds:
            body += "\n    WHERE  " + "\n      AND  ".join(preds)
        on = " AND ".join(tmpl.format(f"{a}.{a}_m{i}", f"{br['prev']}.{sc}")
                          for i, (sc, _dc) in enumerate(pairs))
        sql = head + f"INNER JOIN (\n    {body}\n  ) {a} ON {on}" + tail
    return sql


def _branch_many_hop(step: "_Step", a: str, prev: str, on: str, root: str,
                     prev_step: "_Step") -> str:
    """A hop inside a branch onto an end that is NOT one row (one-to-many filtering both ways,
    or many-to-many), as an INNER JOIN. A row with no row of the reached table reaches no
    member of the tables beyond it; kept by a LEFT JOIN its NULLs read as their BLANK member
    (seen against Power BI: a store with no sales counted its country for the blank customer).

    From a ONE end, the reached table's rows whose key that end lacks belong to its blank
    member, the row whose key is NULL here (the branch's own blank row, or a row a LEFT JOIN
    left unmatched): they are joined to it. Where the end left was itself reached as many
    rows, its blank member is no row of the join, so those rows would be lost: refused."""
    fs, ts = _card_ends(step.edge.get("cardinality"))
    near = fs if step.forward else ts
    if near != "one":
        return f"INNER JOIN {step.table} {a} ON {on}"
    if prev != root and prev_step.distinct:
        raise CompileError("a both-ways path that leaves a table reached as many rows for "
                           "its own many side: the rows its blank member holds are not "
                           "placed")
    near_table = step.edge["from_table"] if step.forward else step.edge["to_table"]
    lost = " AND ".join(f"{prev}.{sc} IS NULL" for sc, _dc in step.pairs)
    match = " AND ".join(f"y_.{sc} = {a}.{dc}" for sc, dc in step.pairs)
    return (f"INNER JOIN {step.table} {a} ON ({on} OR ({lost} AND NOT EXISTS "
            f"(SELECT 1 FROM {near_table} y_ WHERE {match})))")


def _tuple_columns(f: dict) -> list[dict]:
    """The columns a `tuple` filter tests together ({column, table} each), else none."""
    v = f.get("value")
    if str(f.get("op") or "").lower() != "tuple" or not isinstance(v, dict):
        return []
    return [c for c in v.get("columns") or [] if isinstance(c, dict) and c.get("column")]


def _filter_reads_column(f: dict) -> bool:
    """A filter entry the compiler turns into a predicate on its column."""
    if (f.get("stage") or "").lower() in ("measure", "table-calc"):
        return False
    if str(f.get("op") or "").lower() == "topn":
        return False
    return not (f.get("value") is None and f.get("value_kind") == "absent")


def _plan_joins(unit: dict, source: str, alias: str, columns: dict[str, set[str]],
                extra: list[tuple[str, str]] | tuple = (), graph: list[dict] | None = None,
                fields: list[tuple[str, str]] | tuple = (), whole_model: bool = False
                ) -> _JoinPlan:
    """The joins that reach every column the unit groups or filters by.

    THE SEMANTICS ARE THE RELATIONSHIP'S, read off the common relationship fields, never the
    tool's name:

    * Only an ACTIVE relationship is walked (`is_active`), and never a blend, a range or an
      as-of one. A table only those reach is refused by name.
    * A hop never multiplies the statement's rows. From the many end to the one end (or across
      a one-to-one) it is a plain join. Onto an end that is not one (many-to-many, or a
      multiplicity the model does not state) the reached table is read as its DISTINCT rows
      over the columns the statement needs, so each row of the statement's table is counted
      once per member, as a relationship that aggregates each table at its own grain does.
      From the one end to the many end it is walked only when the relationship filters both
      ways (`cross_filter: both`), and then read the same distinct way; otherwise refused.
    * Unmatched rows follow `join_type`: a `left` relationship keeps a many-end row whose key
      the one end lacks (a NULL member, the tool's blank one), a `full` one keeps an unmatched
      row of either end, an `inner` one neither. A row of the statement's table is kept
      (LEFT JOIN) exactly when its end is one of those the relationship keeps: for `left`, the
      many end, however the model stored the relationship round. Under `full` the reached
      table's unmatched rows are kept too (FULL OUTER JOIN: a member with no fact rows shows,
      with NULL measures). A filter still applies after the join: one on the statement's
      table removes such a member, as Tableau's own answer does (seen live).
    * Any number of hops (a snowflaked dimension two tables away). When two different paths
      of the same length reach a table, which one the model means is not knowable here: the
      column is refused by name, never joined along the first.
    * A column is read on the table the unit says it is on (`group_by_tables`, a filter's
      `table`); otherwise on the statement's own table when that holds it, else on the one
      unit table that does. Two candidate tables and no word on which is refused by name.
    * `extra` are columns a MEASURE reads on another table (a CALCULATE filter on a
      dimension's column, RELATED): reached only along hops that never repeat a row of the
      statement's table, and always kept by a LEFT JOIN, since the measure reading them must
      not change which rows the statement has. One that cannot be reached is recorded in
      `unplaced`; the measure is refused, not the unit.
    """
    plan = _JoinPlan(alias)
    gb = list(unit.get("group_by") or [])
    gbt = list(unit.get("group_by_tables") or [])
    # (column, table, is a grouping, is a measure's)
    requests: list[tuple[str, str | None, bool, bool]] = []
    for i, g in enumerate(gb):
        requests.append((str(g).lower(), gbt[i] if i < len(gbt) else None, True, False))
    for f in unit.get("filters") or []:
        if f.get("column") and _filter_reads_column(f):
            requests.append((str(f["column"]).lower(), f.get("table"), False, False))
        for c in _tuple_columns(f):
            requests.append((str(c["column"]).lower(), c.get("table"), False, False))
    for col, table in extra:
        requests.append((str(col).lower(), table, False, True))
    for col, table in fields:
        requests.append((str(col).lower(), table, False, False))
    for mj in unit.get("member_joins") or []:
        # A table whose members the statement always joins (a TREATAS of its values): read
        # like a filter's column, so the join keeps only the rows that match a member.
        requests.append((str(mj["column"]).lower(), mj["table"], False, False))
    src = _bare(source)
    plan.table_at[alias] = src
    if not requests:
        return plan

    tables = unit.get("warehouse_tables") or []
    graph = unit.get("join_graph") or [] if graph is None else graph

    def _locate(col: str, hint: str | None, grouping: bool) -> tuple[str | None, str | None]:
        if hint:
            return (None if _bare(hint) == src else str(hint)), None
        if col in _columns_of(source, columns):
            return None, None
        cands = list(dict.fromkeys(t for t in tables if _bare(t) != src
                                   and col in _columns_of(t, columns)))
        if len(cands) == 1:
            return cands[0], None
        if not cands:
            # A filter whose column no table is known to hold stays on the statement's table
            # (the compiler cannot place it elsewhere, and EXPLAIN says so); a grouping column
            # with no home is refused, as it always was.
            return None, ("" if grouping else None)
        return None, (f"column {col!r} is on {sorted(cands)} and the BI file does not say "
                      f"which")

    blocked: dict[str, list[str]] = {}
    # Tables blocked only by a relationship's filter direction or by its being inactive: the
    # relationship is there, it filters nothing towards the statement's rows.
    soft: set[str] = set()
    hard: set[str] = set()

    def _steps_from(cur: str):
        for i, e in enumerate(graph):
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            if cur not in (a, b) or a == b:
                continue
            other_end = b if cur == a else a
            other_table = str(e["to_table"] if cur == a else e["from_table"])
            label = str(e.get("name") or f"{a} -> {b}")
            kind = str(e.get("kind") or "relationship")
            if kind in _NOT_WALKED:
                blocked.setdefault(other_end, []).append(f"{_NOT_WALKED[kind]} ({label})")
                hard.add(other_end)
                continue
            if e.get("is_active") is False:
                blocked.setdefault(other_end, []).append(
                    f"an inactive relationship ({label}), which the model uses only where a "
                    f"measure activates it")
                soft.add(other_end)
                continue
            fs, ts = _card_ends(e.get("cardinality"))
            jt = str(e.get("join_type") or "left").lower()
            both = str(e.get("cross_filter") or "").lower() in ("both", "bothdirections")
            forward = cur == a
            near, far = (fs, ts) if forward else (ts, fs)
            # Is a row of the end the statement stands on kept when it matches nothing? A
            # `left` relationship keeps its MANY end's (its from end's, when that says
            # nothing): the blank member is always on the other side, whichever way round
            # the model stored the relationship.
            primary_is_from = not (fs == "one" and ts == "many")
            keep = jt == "full" or (jt == "left" and forward == primary_is_from)
            if fs == "one" and ts == "one" and jt == "left":
                # One-to-one: each end has a blank member for the other's unmatched rows, so
                # a row of the end the statement stands on is kept whichever way it is walked.
                keep = True
            far_kept = jt == "full"
            if far == "one":
                yield other_end, _Step(i, e, forward, other_table, False, keep, far_kept)
            elif far is None or (far == "many" and near == "many"):
                if forward or both:
                    yield other_end, _Step(i, e, forward, other_table, True, keep, far_kept)
                else:
                    blocked.setdefault(other_end, []).append(
                        f"relationship {label}, which filters only towards {cur}")
                    soft.add(other_end)
            elif both:
                yield other_end, _Step(i, e, forward, other_table, True, keep, far_kept)
            else:
                blocked.setdefault(other_end, []).append(
                    f"relationship {label} from its one end to its many end, which repeats "
                    f"rows and does not filter that way")
                soft.add(other_end)

    # Breadth first, keeping up to two shortest paths per table: a second is an ambiguity.
    dist: dict[str, int] = {src: 0}
    paths: dict[str, list[tuple[_Step, ...]]] = {src: [()]}
    frontier = [src]
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            for other, step in _steps_from(cur):
                if other == src:
                    continue
                if other not in dist:
                    dist[other] = dist[cur] + 1
                    paths[other] = []
                    nxt.append(other)
                if dist[other] != dist[cur] + 1:
                    continue
                for p in paths[cur]:
                    if len(paths[other]) < 2 and all(s.key != step.key for s in p):
                        paths[other].append(p + (step,))
        frontier = nxt

    # Every table some relationship (other than a blend or a condition) connects to the
    # statement's table, whichever way it filters and whether it is active.
    relaxed, todo = {src}, [src]
    while todo:
        cur = todo.pop()
        for e in graph:
            if str(e.get("kind") or "relationship") in _NOT_WALKED:
                continue
            a, b = _bare(e.get("from_table")), _bare(e.get("to_table"))
            for x, y in ((a, b), (b, a)):
                if x == cur and y not in relaxed:
                    relaxed.add(y)
                    todo.append(y)

    alias_of_path: dict[tuple, str] = {}
    hops: list[tuple[str, _Step, str]] = []               # (alias, step, alias it leaves)
    needs: dict[str, list[str]] = {}                       # alias -> columns read on it

    def _need(a: str, col: str) -> None:
        if col.lower() not in [c.lower() for c in needs.setdefault(a, [])]:
            needs[a].append(col)

    structural: set[tuple] = set()
    for col, hint, grouping, measure in requests:
        if measure:
            target = str(hint)
            tb = _bare(target)
            found = paths.get(tb) or []
            if tb == src:
                continue
            why = None
            if len(found) != 1:
                why = (f"two relationship paths of the same length reach {target}"
                       if len(found) > 1 else
                       "; ".join(dict.fromkeys(blocked.get(tb) or []))
                       or "no declared relationship reaches it")
            elif any(st.distinct for st in found[0]):
                why = (f"a relationship on the way to {target} does not end on one row, so "
                       f"joining it would repeat the measure's rows")
            if why:
                plan.unplaced.setdefault((col, tb), why)
                continue
            prev = alias
            for n in range(1, len(found[0]) + 1):
                key = tuple(s.key for s in found[0][:n])
                step = found[0][n - 1]
                if key not in alias_of_path:
                    a = f"j{len(alias_of_path) + 1}"
                    alias_of_path[key] = a
                    hops.append((a, step, prev))
                prev = alias_of_path[key]
            _need(prev, col)
            plan.measure_at[(col, tb)] = prev
            continue
        target, why = _locate(col, hint, grouping)
        if why is not None:
            plan.unreachable.add(col)
            if why and why not in plan.reasons:
                plan.reasons.append(why)
            continue
        if target is None:
            # On the statement's own table. Recorded, so a same-named column another request
            # reads on a joined table does not take this one's place.
            plan._at[(col, _bare(hint))] = alias
            plan._at.setdefault((col, ""), alias)
            continue
        tb = _bare(target)
        found = paths.get(tb) or []
        if len(found) != 1:
            if len(found) > 1:
                why = (f"two relationship paths of the same length reach {target} from "
                       f"{source} ({' / '.join(' -> '.join(s.name for s in p) for p in found)})"
                       f"; which one the model means is not knowable here")
            elif blocked.get(tb):
                why = (f"{target} is reached only through "
                       + "; ".join(dict.fromkeys(blocked[tb])))
            else:
                why = ""
            # A table no relationship of the model links to the statement's table at all
            # (`whole_model`: the graph is every relationship the model has) filters nothing
            # either: seen live, a target table beside the fact lists its members beside
            # every row, and a filter on the fact leaves the target's measure as it is.
            unlinked = whole_model and tb not in relaxed
            if (not found and tb not in hard
                    and (unlinked or (tb in relaxed and (tb in soft or not blocked.get(tb))))):
                # The relationships exist and filter nothing towards these rows (against
                # their direction, or inactive): the column is listed beside them, not
                # joined (`_detached_statement`).
                plan.detached[col] = (str(target), hint)
                continue
            # A filter too: read off the statement's table under the same name, it would
            # narrow a different column, or none.
            plan.unreachable.add(col)
            if why and why not in plan.reasons:
                plan.reasons.append(why)
            continue
        prev = alias
        for n in range(1, len(found[0]) + 1):
            key = tuple(s.key for s in found[0][:n])
            step = found[0][n - 1]
            structural.add(key)
            if key not in alias_of_path:
                a = f"j{len(alias_of_path) + 1}"
                alias_of_path[key] = a
                hops.append((a, step, prev))
            prev = alias_of_path[key]
        orig = next((g for g in gb if str(g).lower() == col), col)
        _need(prev, orig if grouping else col)
        plan._at[(col, _bare(hint))] = prev
        plan._at.setdefault((col, ""), prev)

    for a, step, prev in hops:
        for sc, dc in step.pairs:
            _need(a, dc)
            # The key the hop leaves from: a table read as its distinct rows must carry it
            # for the next table to join on.
            if prev != alias:
                _need(prev, sc)
    key_of = {a: k for k, a in alias_of_path.items()}
    # A BRANCH: a hop onto an end that is not one row, along a relationship whose
    # multiplicity the model states (many-to-many, or one-to-many filtering both ways). The
    # tables from it on are read as ONE distinct set of (member of the table it leaves, the
    # columns needed beyond), filtered inside, and joined on the member: each row of the
    # statement's table is counted once per member however many paths lead to it.
    children: dict[str, list[str]] = {}
    for a, _step, prev in hops:
        children.setdefault(prev, []).append(a)
    in_branch: dict[str, str] = {}
    for a, step, prev in hops:
        fs, ts = _card_ends(step.edge.get("cardinality"))
        if (step.distinct and fs and ts and prev not in in_branch
                and key_of[a] in structural):
            todo = [a]
            while todo:
                cur = todo.pop()
                in_branch[cur] = a
                todo += children.get(cur, [])
            near = fs if step.forward else ts
            plan.branches[a] = {"step": step, "prev": prev, "near_one": near == "one",
                                "aliases": [], "clauses": []}
    for a, step, prev in hops:
        plan.keys[a] = [dc for _sc, dc in step.pairs]
        plan.table_at[a] = _bare(step.table)
        on = " AND ".join(f"{a}.{dc} = {prev}.{sc}" for sc, dc in step.pairs)
        kw = {(True, True): "FULL OUTER JOIN", (True, False): "LEFT JOIN",
              (False, True): "RIGHT JOIN", (False, False): "INNER JOIN"}[
            (step.keep, step.keep_far)]
        if key_of[a] not in structural:
            kw = "LEFT JOIN"                # read by a measure only: changes no row
        root = in_branch.get(a)
        if root is not None:
            plan.branches[root]["aliases"].append(a)
            if root == a:
                plan.clauses.append(f"__BRANCH_{a}__")
            else:
                # Inside the branch every hop is a plain join: the distinct set it is read
                # as already counts each member once. A hop to a ONE end keeps a row with no
                # match (LEFT JOIN: its NULL is that table's blank member).
                clause = f"LEFT JOIN {step.table} {a} ON {on}"
                if step.distinct:
                    clause = _branch_many_hop(step, a, prev, on, root,
                                              next(s for x, s, _p in hops if x == prev))
                plan.branches[root]["clauses"].append(clause)
            continue
        plan.full = plan.full or step.keep_far
        if step.distinct:
            body = ", ".join(needs.get(a, []))
            plan.clauses.append(f"{kw} (\n    SELECT DISTINCT {body}\n    FROM   {step.table}"
                                f"\n  ) {a} ON {on}")
        else:
            plan.clauses.append(f"{kw} {step.table} {a} ON {on}")
    if plan.branches:
        plan.graph = graph
    return plan


def _edge_key(e: dict) -> tuple:
    return (_bare(e.get("from_table")), str(e.get("from_column") or "").lower(),
            _bare(e.get("to_table")), str(e.get("to_column") or "").lower())


def _measure_graph(unit: dict, source: str, measures: dict[str, str] | None,
                   table_of: dict[str, str] | None, model_graph: list[dict] | None
                   ) -> tuple[list[dict], list[tuple[str, str]], set, bool]:
    """The relationships a statement may join along, the columns its DAX measures read on
    other tables, the relationship changes its measures make, and whether its measures need
    statements of their own.

    The graph is the unit's own plus the model's, on warehouse tables. A measure that is a
    CALCULATE with USERELATIONSHIP(a, b) joins along that relationship instead of the active
    one between the same two tables; CROSSFILTER(a, b, Both | None | OneWay) makes that
    relationship filter both ways, not at all, or one way. When every measure makes the same
    changes they apply to the whole statement. When they differ, each set of measures is a
    statement of its own (`_joined_statements`), except a USERELATIONSHIP whose far table the
    visual reads nothing of: it changes no row, and the measures share one statement."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}

    def phys(name: Any) -> Any:
        return tmap.get(str(name or "").lower(), name)

    graph: list[dict] = []
    seen: set[tuple] = set()
    for e in list(unit.get("join_graph") or []) + list(model_graph or []):
        e = {**e, "from_table": phys(e.get("from_table")), "to_table": phys(e.get("to_table"))}
        if _edge_key(e) in seen:
            continue
        seen.add(_edge_key(e))
        graph.append(e)
    src = _bare(source)
    extra: list[tuple[str, str]] = []
    specs = [_relationship_spec(m, measures, tmap, src, extra)
             for m in unit.get("measures") or []]
    named = {x for x in specs if x}
    if not named:
        return graph, extra, set(), False
    used = {k for sp in named for k in _spec_keys(sp)}
    if len(named) == 1 and all(x == next(iter(named)) for x in specs):
        return _apply_spec(graph, next(iter(named))), extra, used, False
    spec = next(iter(named))
    if len(named) == 1 and all(k[0] == "use" for k in spec):
        ends = set()
        for k in spec:
            hit = _spec_edge(graph, k[1])
            if hit is not None:
                ends |= {_bare(graph[hit].get("from_table")), _bare(graph[hit].get("to_table"))}
        read = {_bare(t) for t in (unit.get("warehouse_tables") or [])[1:]}
        read |= {_bare(t) for t in unit.get("group_by_tables") or [] if t}
        read |= {_bare(f.get("table")) for f in unit.get("filters") or [] if f.get("table")}
        if not ((ends - {src}) & read):
            return graph, extra, used, False
    return graph, extra, used, True


def _measure_homes(unit: dict, source: str, measures: dict[str, str] | None,
                   table_of: dict[str, str] | None) -> dict[str, str]:
    """Each DAX measure's own table: the statement's table when its formula reads it (or no
    table at all), else the one other warehouse table it reads, when there is exactly one.
    A measure reading several other tables keeps the statement's table (and is refused by
    name there, as before)."""
    tmap = {k.lower(): v for k, v in (table_of or {}).items()}
    src = _bare(source)
    out: dict[str, str] = {}
    if not tmap:
        return out
    for m in unit.get("measures") or []:
        if m.get("formula_language") != "dax" or not m.get("formula"):
            continue
        # A context test of another table (SELECTEDVALUE(Article[Brand])) reads none of its
        # rows: the measure's own table is the one it aggregates. A measure that aggregates
        # no table at all (ISFILTERED(Customer[City]) alone) is a statement over the table
        # its test names: on any other, a grouping column that does not filter that table
        # is listed beside the statement, out of the context the test reads.
        expanded = _expanded_tokens(m["formula"], measures)
        named: dict[str, str] = {}
        for toks in (_row_tokens(expanded), expanded):
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
                    named[_bare(phys)] = phys
            if named:
                break
        if not named or src in named:
            continue
        if len(named) == 1:
            out[m["name"]] = next(iter(named.values()))
    return out


def _spec_keys(spec: frozenset) -> set:
    """The relationship keys (frozenset of (column, table) ends) a spec names."""
    return {k[1] for k in spec}


def _relationship_spec(m: dict, measures: dict[str, str] | None, tmap: dict, src: str,
                       extra: list) -> frozenset | None:
    """The relationship changes one DAX measure makes: {("use", ends)} for USERELATIONSHIP,
    {("cross", ends, direction)} for CROSSFILTER. None for a measure that is not DAX; empty
    for one that changes nothing. Also appends to `extra` the columns it reads on other
    tables."""
    if m.get("formula_language") != "dax" or not m.get("formula"):
        return None
    toks = _expanded_tokens(m["formula"], measures)
    out = set()
    for i, (k, v) in enumerate(toks):
        if k in ("qualified", "quoted_qualified"):
            t = _table_ref(v)
            physical = tmap.get(str(t).lower()) if t else None
            if physical and _bare(physical) != src:
                req = (_column_ref(v).lower(), physical)
                if req not in extra:
                    extra.append(req)
        up = v.upper() if k == "func" else ""
        if up in ("USERELATIONSHIP", "CROSSFILTER") and i + 5 < len(toks) \
                and toks[i + 1][0] == "lparen" and toks[i + 3][0] == "comma":
            ends = []
            for tk in (toks[i + 2], toks[i + 4]):
                t = _table_ref(tk[1]) if tk[0] != "col" else None
                ends.append((_column_ref(tk[1]).lower(), _bare(tmap.get(str(t).lower(), t))))
            if up == "USERELATIONSHIP":
                out.add(("use", frozenset(ends)))
            elif i + 6 < len(toks) and toks[i + 5][0] == "comma":
                out.add(("cross", frozenset(ends), str(toks[i + 6][1]).strip("'\"").lower()))
    return frozenset(out)


def _apply_spec(graph: list[dict], spec: frozenset) -> list[dict]:
    """The graph a measure's relationship changes leave: USERELATIONSHIP activates its
    relationship and deactivates the others between the same two tables; CROSSFILTER Both
    filters both ways, None filters nothing (the relationship is not walked), OneWay one way."""
    out = [dict(e) for e in graph]
    for k in sorted(spec, key=lambda x: x[0] != "use"):
        hit = _spec_edge(out, k[1])
        if hit is None:
            continue
        if k[0] == "use":
            ends = {_bare(out[hit].get("from_table")), _bare(out[hit].get("to_table"))}
            for i, e in enumerate(out):
                if i == hit:
                    e["is_active"] = True
                elif {_bare(e.get("from_table")), _bare(e.get("to_table"))} == ends:
                    e["is_active"] = False
        else:
            how = k[2]
            if how == "none":
                out[hit]["is_active"] = False
            elif how == "both":
                out[hit]["cross_filter"] = "both"
            elif how.startswith("oneway"):
                out[hit]["cross_filter"] = "single"
    return out


def _date_keys(graph: list[dict], source: str, types: dict[tuple[str, str], str]
               ) -> dict[tuple[str, str], dict]:
    """(date column, date table) -> how the statement's table reaches it: every active
    relationship from the measure's table to a table whose ONE end is that column. A column
    the model types as other than a date is not one."""
    src = _bare(source)
    out: dict[tuple[str, str], dict] = {}
    for e in graph:
        if e.get("is_active") is False or str(e.get("kind") or "relationship") in _NOT_WALKED:
            continue
        fs, ts = _card_ends(e.get("cardinality"))
        for near, far, ncol, fcol, far_end in (
                (e.get("from_table"), e.get("to_table"), e.get("from_column"),
                 e.get("to_column"), ts),
                (e.get("to_table"), e.get("from_table"), e.get("to_column"),
                 e.get("from_column"), fs)):
            if _bare(near) != src or far_end != "one" or _bare(far) == src:
                continue
            dtype = types.get((_bare(far), str(fcol).lower())) \
                or types.get((str(far).lower(), str(fcol).lower()))
            if dtype and not _DATE_TYPES.match(dtype):
                continue
            out[(str(fcol).lower(), _bare(far))] = {
                "table": far, "column": str(fcol), "fact_column": str(ncol)}
    return out


def _measure_elsewhere(m: dict, unit: dict, source: str,
                       columns: dict[str, set[str]]) -> str | None:
    """Why a shelf-aggregated column measure cannot be computed over `source`, or None.

    Only a measure with no formula (a column plus an aggregation), only when the schema
    evidence says the column is not on the statement's table and is on another of the unit's
    tables. A blend link names the post-aggregation join that table needs."""
    if m.get("formula") or not columns or not _columns_of(source, columns):
        return None
    col = str(m.get("name") or "").lower()
    if col in _columns_of(source, columns):
        return None
    homes = [t for t in unit.get("warehouse_tables") or []
             if _bare(t) != _bare(source) and col in _columns_of(t, columns)]
    if not homes:
        return None
    blend = any(str(e.get("kind")) == "blend" and {_bare(e.get("from_table")),
                                                   _bare(e.get("to_table"))}
                == {_bare(source), _bare(homes[0])} for e in unit.get("join_graph") or [])
    if blend:
        return (f"aggregates a column of {homes[0]}, which a data blend links to {source}: "
                f"a blend aggregates the secondary at the link grain and left-joins it after "
                f"aggregation, which is not compiled")
    return (f"aggregates a column of {homes[0]}, not of {source}: a statement over {source} "
            f"does not aggregate another table at that table's grain")


def _columns_of(table: str, columns: dict[str, set[str]]) -> set[str]:
    """What the parser resolved as reachable on a table. Absent evidence means the compiler
    cannot PLACE the column, which is a refusal rather than an assumption."""
    hit = columns.get(table)
    if hit is None:
        hit = next((v for k, v in columns.items() if _bare(k) == _bare(table)), None)
    return set(hit or ())


def _strip_alias(pred: str, alias: str, to: str = "") -> str:
    """`pred` with every `alias.` qualifier removed (or replaced by `to`), OUTSIDE quotes
    only. A context filter runs inside a subquery that has no alias; a plain text replace
    also rewrote literals (`'gift.card'` lost its `t.`)."""
    out, i, n, q = [], 0, len(pred), None
    head = alias + "."
    while i < n:
        ch = pred[i]
        if q:
            out.append(ch)
            if ch == q:
                if i + 1 < n and pred[i + 1] == q:      # a doubled quote is an escape
                    out.append(pred[i + 1])
                    i += 1
                else:
                    q = None
            i += 1
            continue
        if ch in ("'", '"'):
            q = ch
            out.append(ch)
            i += 1
            continue
        if pred.startswith(head, i) and (i == 0 or not (pred[i - 1].isalnum()
                                                        or pred[i - 1] in "_.")):
            i += len(head)
            if to:
                out.append(to + ".")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# A filter that keeps the MEMBERS of a field passing a test over the population (a Top N by
# an expression, a condition): not a row predicate, compiled by the statement around it.
MEMBER_OPS = ("top_n", "condition")


_RELATIVE_PERIODS = ("day", "month", "quarter", "year")


def _relative_date(target: str, v: dict, rules, dialect: str | None, res: Result
                   ) -> tuple[str | None, str | None]:
    """A relative date filter as a range of whole periods: from the start of the period
    `first` periods from today (or from the anchor) to the end of the period `last` periods
    from it. A week starts on the value's `week_start`. "To date" (`include_future` false)
    also ends the range at today, or at the moment the query runs for a time column (the
    current time, read as the session's wall clock). The date arithmetic per dialect is rule
    data (`op.relative_date`). -> (predicate, None) or (None, why it is refused)."""
    period = str(v.get("period") or "").lower()
    if v.get("unknown"):
        return None, (f"a relative date filter with setting(s) {v['unknown']} the compiler "
                      f"does not read, which may move its window")
    rule = rules.operator("relative_date", dialect or "")
    emit = (rule or {}).get("emit") or {}
    d = str(dialect or "").lower()
    shift = (emit.get("shift") or {}).get(d)
    if rule is None or not shift:
        return None, f"no relative date rule for the {dialect or 'run'} dialect"
    start = str(v.get("week_start") or "").lower()
    iso = (emit.get("weekday_iso") or {}).get(d)
    if period == "week" and not (start in _ISO_DAY and iso):
        return None, ("a relative date filter by week whose first day of the week the file "
                      "does not state" if start not in _ISO_DAY else
                      f"a relative date filter by week, which has no weekday form for the "
                      f"{dialect or 'run'} dialect")
    if period not in _RELATIVE_PERIODS and period != "week":
        return None, (f"a relative date filter by {period or 'an unnamed period'}: only "
                      f"{', '.join(_RELATIVE_PERIODS + ('week',))} are compiled")
    # The time zone the BI tool reads the current moment in (the viewer file's): "today" and
    # "now" are both read there, so the window's periods and a "to date" end agree with what
    # the tool shows across the warehouse session's midnight.
    tz, zoned = v.get("time_zone"), None
    if tz:
        if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)*", str(tz)) or len(str(tz)) > 64:
            return None, f"a relative date filter in a time zone {tz!r} that is not one"
        zoned = (emit.get("now_in_zone") or {}).get(d)
        if not zoned:
            return None, (f"a relative date filter in the time zone {tz}, which has no form "
                          f"for the {dialect or 'run'} dialect")
        names = (emit.get("zone_names") or {}).get(d)
        if names is not None:
            if str(tz) not in names:
                return None, (f"a relative date filter in the time zone {tz}, which the "
                              f"{dialect} dialect names by its own names, and the rule lists "
                              f"no name for it")
            tz = names[str(tz)]
        zoned = zoned.replace("{tz}", str(tz))
        res.rules_used.append("engine.relative_date_time_zone")
    if v.get("anchor"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v["anchor"])):
            return None, f"a relative date anchor {v['anchor']!r} that is not a date"
        anchor = f"CAST('{v['anchor']}' AS DATE)"
    else:
        # Today as the dialect spells it (`today`), else the standard CURRENT_DATE.
        anchor = (f"CAST({zoned} AS DATE)" if zoned
                  else (emit.get("today") or {}).get(d) or "CURRENT_DATE")
    # The start of a period (`trunc`, {p} the period, {d} the date), else DATE_TRUNC.
    cut = (emit.get("trunc") or {}).get(d) or "DATE_TRUNC('{p}', {d})"

    def trunc(expr: str) -> str:
        if period != "week":
            return cut.format(p=period, d=expr)
        # The week's first day: the day moved back to the weekday the data source starts on.
        return shift.format(p="day", n="-" + _mod(f"{iso.format(expr)} - {_ISO_DAY[start]} + 7",
                                                  "7", dialect),
                            d=expr)

    def at(n: int) -> str:
        return trunc(shift.format(p=period, n=int(n), d=anchor) if n else anchor)
    res.rules_used.append(rule["id"])
    pred = f"{target} >= {at(int(v['first']))} AND {target} < {at(int(v['last']) + 1)}"
    if not v.get("include_future", True):
        # "To date": nothing after today (the anchor), or after now on a time column.
        # The current moment in the time zone the BI tool reads it in (the viewer's), else
        # the session's wall clock.
        now = zoned or (emit.get("now") or {}).get(d)
        if not now:
            return None, (f"a relative date filter to date, which has no current-time form "
                          f"for the {dialect or 'run'} dialect")
        end = (f"{shift.format(p='day', n=1, d=anchor)}" if v.get("anchor") else now)
        pred += (f" AND {target} < {end}" if v.get("anchor") else f" AND {target} <= {end}")
        res.rules_used.append("engine.relative_date_to_date")
    pred = f"({pred})"
    if v.get("include_null"):
        pred = f"({pred} OR {target} IS NULL)"
    return pred, None


# Tests on a column's text (the common format's `contains` family), each an operator row.
_TEXT_OPS = {"contains": "contains", "not_contains": "contains", "starts_with": "starts_with",
             "not_starts_with": "starts_with"}
# The comparison of each op, for deciding whether an empty cell's stand-in (`null_as`) passes.
_CMP = {"eq": lambda a, b: a == b, "gt": lambda a, b: a > b, "lt": lambda a, b: a < b,
        "neq": lambda a, b: a != b}


def _stand_in(literal: Any, null_as: dict):
    """(empty cell's stand-in, literal) as two comparable values, by the literal's type."""
    if isinstance(literal, bool):
        return None, None
    if isinstance(literal, (int, float)) or _NUM_LIT.fullmatch(str(literal)):
        n = null_as.get("number")
        return (None, None) if n is None else (float(n), float(literal))
    if _DATE_LIT.fullmatch(str(literal)):
        d = null_as.get("date")
        return (None, None) if d is None else (str(d), str(literal)[:10])
    t = null_as.get("text")
    return (None, None) if t is None else (str(t).casefold(), str(literal).casefold())


def _empty_passes(op: str, value: Any, null_as: dict | None) -> bool:
    """Whether an EMPTY cell passes the comparison, with the empty value read as the
    filter's stand-in (`null_as`, the tool's comparison rule). False without one."""
    if not null_as or value is None:
        return False
    if op in ("range", "lte") and isinstance(value, dict):
        ok = True
        for bound, cmp in (("min", lambda a, b: a >= b), ("max", lambda a, b: a <= b)):
            if value.get(bound) is None:
                continue
            a, b = _stand_in(value[bound], null_as)
            ok = ok and a is not None and cmp(a, b)
        return ok
    if op in _CMP and not isinstance(value, (list, dict)):
        a, b = _stand_in(value, null_as)
        return a is not None and _CMP[op](a, b)
    return False


def _test_pred(target: str, op: str, value: Any, f: dict, rules, dialect: str | None,
               res: "Result", text_key: bool = False, force: bool = False
               ) -> tuple[str | None, str | None]:
    """The predicates of the common format's tests that the generic path does not write:
    a membership with the EMPTY member (a JSON null in the list), the text tests, `any_of` /
    `all_of`, and a comparison an empty cell passes under the filter's `null_as`.
    -> (predicate, None); (None, why) when it cannot be written; (None, None) when the
    generic path writes it (unless `force`, which writes every comparison here)."""
    null_as = f.get("null_as")
    if op in ("any_of", "all_of"):
        if not isinstance(value, list) or not value:
            return None, f"{op} with no tests"
        parts = []
        for part in value:
            if not isinstance(part, dict) or part.get("value") is None:
                return None, f"a part of {op} with no value"
            pred, why = _test_pred(target, str(part.get("op") or ""), part["value"], f,
                                   rules, dialect, res, text_key, force=True)
            if pred is None:
                return None, why or f"a part of {op} ({part.get('op')}) has no rule"
            parts.append(pred)
        res.rules_used.append(f"engine.{op}")
        return "(" + (" OR " if op == "any_of" else " AND ").join(parts) + ")", None
    if op in _TEXT_OPS:
        rule = rules.operator(_TEXT_OPS[op], dialect or "")
        if rule is None:
            return None, f"no operator rule for {_TEXT_OPS[op]}"
        res.rules_used.append(rule["id"])
        lit = str(value).upper() if text_key else str(value)
        col = _Parser._text_key(target) if text_key else target
        pred = _apply(_emit_for(rule, dialect), [col, _literal(lit, dialect)], op)
        # An empty cell holds no text: it fails the test, and passes its negation.
        return ((f"NOT COALESCE(({pred}), FALSE)" if op.startswith("not_") else f"({pred})"),
                None)
    if op in ("is_null", "not_null"):
        if not force:
            return None, None
        return f"{target} IS {'NOT ' if op == 'not_null' else ''}NULL", None
    if op in ("in", "not_in") or (op in ("eq", "filter") and isinstance(value, list)):
        vals = value if isinstance(value, list) else [value]
        if None not in vals and not force:
            return None, None
        rest = [v for v in vals if v is not None]
        keyed = text_key and rest and all(_foldable(v) for v in rest)
        col = _Parser._text_key(target) if keyed else target
        lits = _literal_list([v.upper() for v in rest] if keyed else rest, dialect)
        if op == "not_in":
            res.rules_used.append("engine.empty_member")
            if not rest:
                return f"{target} IS NOT NULL", None
            if None in vals:
                return f"({target} IS NOT NULL AND {col} NOT IN ({lits}))", None
            return f"({target} IS NULL OR {col} NOT IN ({lits}))", None
        if not rest:
            return f"{target} IS NULL", None
        member = f"{col} IN ({lits})" if len(rest) > 1 else f"{col} = {lits}"
        if None in vals:
            res.rules_used.append("engine.empty_member")
            return f"({target} IS NULL OR {member})", None
        return member, None
    if op in ("eq", "filter", "gt", "lt", "neq", "lte", "range"):
        op = "eq" if op == "filter" else op
        passes = _empty_passes(op, value, null_as)
        if not passes and not force:
            return None, None
        if isinstance(value, dict):
            preds = []
            for bound, row in (("min", "range"), ("max", "lte")):
                if value.get(bound) is None:
                    continue
                rule = rules.operator(row)
                if rule is None:
                    return None, f"no operator rule for {row}"
                res.rules_used.append(rule["id"])
                preds.append(_apply(_emit_for(rule, dialect),
                                    [target, _literal(value[bound], dialect)], row))
            if not preds:
                return None, "a range with no bound"
            pred = " AND ".join(preds)
        else:
            rule = rules.operator(op, dialect or "")
            if rule is None:
                return None, f"no operator rule for {op}"
            res.rules_used.append(rule["id"])
            if text_key and op in ("eq", "neq") and isinstance(value, str) \
                    and not _NUM_LIT.fullmatch(value) and not _DATE_LIT.fullmatch(value):
                pred = _apply(_emit_for(rule, dialect), [_Parser._text_key(target),
                                                       _literal(value.upper(), dialect)], op)
            else:
                pred = _apply(_emit_for(rule, dialect), [target, _literal(value, dialect)], op)
        if passes:
            # An empty cell compares as its stand-in and passes: the rows with no value stay.
            res.rules_used.append("engine.empty_compares_as")
            return f"({pred} OR {target} IS NULL)", None
        return (f"({pred})" if " AND " in pred else pred), None
    return None, None


def _tuple_pred(f: dict, rules, dialect: str | None, res: "Result", text_key: bool,
                target_of) -> tuple[str | None, str | None]:
    """A `tuple` filter -> an OR of one AND per selected combination ((c1 = a AND c2 = b)
    OR ...), a NULL member as IS NULL; `not` (the value's `negated`) keeps the rows matching
    none. -> (predicate, None) or (None, why)."""
    v = f["value"]
    cols = _tuple_columns(f)
    rows = [r for r in v.get("rows") or [] if isinstance(r, list)]
    if not cols or not rows or any(len(r) != len(cols) for r in rows):
        return None, (f"a filter over {len(cols)} column(s) together whose selected "
                      f"combinations do not each name every column")
    rule = rules.operator("eq", dialect or "")
    if rule is None:
        return None, "no operator rule for eq"
    res.rules_used.append(rule["id"])
    targets = [target_of(c["column"], c.get("table")) for c in cols]
    alts = []
    for r in rows:
        parts = []
        for tgt, val in zip(targets, r):
            if val is None:
                parts.append(f"{tgt} IS NULL")
            elif text_key and isinstance(val, str) and not _NUM_LIT.fullmatch(val) \
                    and not _DATE_LIT.fullmatch(val):
                parts.append(_apply(_emit_for(rule, dialect), [_Parser._text_key(tgt),
                                                             _literal(val.upper(), dialect)],
                                    "eq"))
            else:
                parts.append(_apply(_emit_for(rule, dialect), [tgt, _literal(val, dialect)],
                                    "eq"))
        alts.append("(" + " AND ".join(parts) + ")")
    res.rules_used.append("engine.tuple_filter")
    pred = "(" + "\n       OR ".join(alts) + ")"
    if v.get("negated"):
        pred = f"NOT COALESCE({pred}, FALSE)"
    return pred, None


def _relative_range(target: str, v: dict, rules, dialect: str | None, res: "Result"
                    ) -> tuple[str | None, str | None]:
    """A range whose bounds are computed from the moment the query runs (in UTC): each bound
    a list of steps from now, a move (`add`) or a truncation to the start of a unit (`span`).
    A lower bound is `>=`; an upper bound ending in a span covers that span to its end
    (`<` the start of the next one), else `<=`. Per-dialect SQL is rule data
    (`op.relative_range`). -> (predicate, None) or (None, why)."""
    rule = rules.operator("relative_range", dialect or "")
    emit = (rule or {}).get("emit") or {}
    d = str(dialect or "").lower()
    now, shift, trunc = ((emit.get(k) or {}).get(d) for k in ("now", "shift", "trunc"))
    if rule is None or not (now and shift and trunc):
        return None, f"no relative range rule for the {dialect or 'run'} dialect"
    units = set(emit.get("units") or ())
    # A WEEK starts on the day the filter states (`week_start`, the reader's word for the
    # tool's rule): its start is the day truncation moved back to that weekday, the ISO day
    # number per dialect being rule data (`weekday_iso`).
    start = str(v.get("week_start") or "").lower()
    iso = (emit.get("weekday_iso") or {}).get(d)

    def week_start(expr: str) -> str:
        day = trunc.format(p="day", d=expr)
        back = "-" + _mod(f"{iso.format(day)} - {_ISO_DAY[start]} + 7", "7", dialect)
        return shift.format(p="day", n=back, d=day)
    preds = []
    for side in ("lower", "upper"):
        b = v.get(side)
        if b is None:
            continue
        expr, last = now, None
        for st in (b or {}).get("steps") or []:
            unit = str(st[-1]).lower()
            if unit == "week" and st[0] == "span" and not (start in _ISO_DAY and iso):
                return None, ("a relative date bound cut to a week, whose first day the "
                              "filter does not state" if start not in _ISO_DAY else
                              f"a relative date bound cut to a week, which has no weekday "
                              f"form for the {dialect or 'run'} dialect")
            if unit not in units and unit != "week":
                return None, (f"a relative date or time bound moved or cut by {unit}: only "
                              f"{', '.join(sorted(units | {'week'}))} are compiled")
            if st[0] == "add":
                expr = shift.format(p=unit, n=int(st[1]), d=expr)
            elif unit == "week":
                expr = week_start(expr)
            else:
                expr = trunc.format(p=unit, d=expr)
            last = st
        if side == "lower":
            preds.append(f"{target} >= {expr}")
        elif last is not None and last[0] == "span":
            preds.append(f"{target} < {shift.format(p=str(last[-1]).lower(), n=1, d=expr)}")
        else:
            preds.append(f"{target} <= {expr}")
    if not preds:
        return None, "a relative range with no bound"
    res.rules_used.append(rule["id"])
    return "(" + " AND ".join(preds) + ")", None


_AGG_SQL = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|STDDEV\w*|VAR\w*)\s*\(", re.I)


def _limit(n: int, dialect: str | None) -> str:
    """`LIMIT n`, or the dialect's own form (`limit` in data/reader_dialects.yml) after an
    ORDER BY."""
    return _LIMIT.get(str(dialect or "").lower(), "LIMIT {n}").format(n=int(n))


def _member_filters(members: list[dict], unit: dict, rules, alias: str, dialect: str,
                    res: Result, *, src: str, plan, measures, calc, calc_language,
                    dim_preds: list[str], joins: list[str] | tuple = (),
                    compile_by=None, limit_ok: bool = True) -> tuple[list[str], bool]:
    """Filters that keep the MEMBERS of a field passing a test, as row predicates:
    (predicates, whether a Top N among them replaces the statement's LIMIT).

    A Top N by an expression and a condition on an aggregate are computed over the
    population the data source and context filters leave, independently of the other
    dimension filters, and then intersected with them: the field's value is in the members
    a subquery over that population keeps (a NULL member included, which `IN` alone would
    drop). Ranking inside the filtered rows instead answers the context-filter question. A
    Top N on the one field the sheet groups by, with nothing else filtering, is the same
    rows as the statement's own ORDER BY … LIMIT and is left to it. A condition with no
    aggregate is a test of each row's own value.

    A Top N whose `population` is `filtered` ranks the members within EVERY other filter of
    the view (the statement's own rows, its joins and its row predicates), by the ranking
    measure as the statement computes it (`compile_by`); `empty_members: excluded` leaves out
    a member whose measure is empty, and `ties` keeps every member tied with the Nth (a rank,
    not a row count)."""
    if not members:
        return [], False
    if not calc_language:
        raise CompileError(
            f"filter(s) on {sorted({str(f['column']) for f in members})} keep the members an "
            f"expression selects, and the unit names no language to compile it in")
    gb = [str(g).lower() for g in unit.get("group_by") or []]
    preds: list[str] = []
    top_done = False

    def compile_over(expr: str, on: str) -> str:
        p = _Parser(_tokens(expr, calc_language), rules, calc_language, on, dialect, None,
                    measures, None, ())
        sql = p.parse()
        res.rules_used += p.used
        res.provisional_rules += p.provisional
        res.unknown += p.unknown
        return sql

    for f in members:
        col, op, v = str(f["column"]), str(f["op"]).lower(), f["value"]
        what = "Top N" if op == "top_n" else "condition"
        key_t = calc(col, f.get("table")) or f"{plan.at(col, f.get('table'))}.{col}"
        if op == "top_n" and str(v.get("population") or "") == "filtered":
            preds.append(_ranked_members(f, key_t, v, alias, dialect, res, src=src,
                                         joins=joins, dim_preds=dim_preds,
                                         compile_by=compile_by))
            top_done = top_done or bool(unit.get("top_n"))
            continue
        if re.search(r"\bj\d+\.", key_t):
            # A column of a joined table: its members ranked (or tested) over the statement's
            # own rows through the same joins, before the view's dimension filters (a Top N
            # or a condition is computed over every row that reaches the members).
            if op == "top_n":
                # Its members are the joined table's: a fact row that reaches no row of it
                # (the blank member the view shows for it) is not ranked (checked live: the
                # bottom department by quantity is a department, never that blank).
                held = _joined_row_test(key_t, joins)
                preds.append(_ranked_members(f, key_t, v, alias, dialect, res, src=src,
                                             joins=joins, dim_preds=[held] if held else [],
                                             compile_by=compile_by))
                top_done = top_done or bool(unit.get("top_n"))
                continue
            preds.append(_tested_members(f, key_t, v, alias, res, src=src, joins=joins,
                                         compile_by=compile_by))
            continue
        key_m = _strip_alias(key_t, alias, "m")
        try:
            if op == "top_n":
                by = compile_over(str(v["by"]), "m")
                n = int(v["n"])
                if (unit.get("top_n") == n and gb == [col.lower()] and not dim_preds
                        and len(members) == 1 and limit_ok):
                    continue        # the statement's own ORDER BY … LIMIT is these rows
                order = _sort_term(by, "ASC" if v.get("direction") == "asc" else "DESC",
                                   "LAST", dialect)
                sub = (f"      SELECT {key_m} AS member_key\n      FROM   {src} m\n"
                       f"      GROUP BY {key_m}\n      ORDER BY {order}\n"
                       f"      {_limit(n, dialect)}")
                res.rules_used.append("engine.top_n_members")
                top_done = top_done or bool(unit.get("top_n"))
            else:
                cond = compile_over(str(v["expression"]), "m")
                negate = bool(v.get("exclude"))
                if not _AGG_SQL.search(cond):
                    row = _strip_alias(cond, "m", alias)
                    preds.append(f"NOT COALESCE(({row}), FALSE)" if negate else f"({row})")
                    res.rules_used.append("engine.condition_rows")
                    continue
                test = f"NOT COALESCE(({cond}), FALSE)" if negate else f"({cond})"
                sub = (f"      SELECT {key_m} AS member_key\n      FROM   {src} m\n"
                       f"      GROUP BY {key_m}\n      HAVING {test}")
                res.rules_used.append("engine.condition_members")
        except CompileError as exc:
            raise CompileError(f"the {what} filter on {col!r} does not compile ({exc}); "
                               f"dropping it would return other members") from exc
        preds.append(f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q\n  )\n"
                     f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
                     f"    ) q WHERE q.member_key IS NULL\n  )))")
    return preds, top_done


def _joined_row_test(key_t: str, joins: list[str]) -> str | None:
    """A test that the row reached a row of the joined table `key_t` reads (`j<n>.col`): a
    column its join matches on is not NULL. None where the join names none."""
    m = re.match(r"^(j\d+)\.", key_t)
    if not m:
        return None
    for j in joins:
        if re.search(rf"\)\s*{m.group(1)}\s+ON\b|\s{m.group(1)}\s+ON\b", j):
            on = j.split(" ON ", 1)[-1]
            col = re.search(rf"\b{m.group(1)}\.(\w+)\b", on)
            if col:
                return f"{m.group(1)}.{col.group(1)} IS NOT NULL"
    return None


def _tested_members(f: dict, key_t: str, v: dict, alias: str, res: Result, *, src: str,
                    joins, compile_by) -> str:
    """The members of a joined table's column a condition filter keeps: the statement's own
    rows through its joins, grouped by the column, those whose aggregate passes (Exclude:
    those whose test is not true)."""
    col = str(f["column"])
    if compile_by is None:
        raise CompileError(f"the condition on {col!r} names no way to compile its expression")
    try:
        cond = compile_by(str(v["expression"]))
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        raise CompileError(f"the condition filter on {col!r} does not compile ({exc}); "
                           f"dropping it would return other members") from exc
    test = f"NOT COALESCE(({cond}), FALSE)" if v.get("exclude") else f"({cond})"
    if not _AGG_SQL.search(cond):
        res.rules_used.append("engine.condition_rows")
        return test
    sub = f"      SELECT {key_t} AS member_key\n      FROM   {src} {alias}"
    for j in joins:
        sub += "\n      " + j.replace("\n", "\n      ")
    sub += f"\n      GROUP BY {key_t}\n      HAVING {test}"
    res.rules_used.append("engine.condition_members")
    return (f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q\n  )\n"
            f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
            f"    ) q WHERE q.member_key IS NULL\n  )))")


def _ranked_members(f: dict, key_t: str, v: dict, alias: str, dialect: str, res: Result, *,
                    src: str, joins, dim_preds: list[str], compile_by) -> str:
    """The members a Top N keeps when it ranks within every other filter of the view: the
    statement's own rows grouped by the field, ranked by the measure, those ranked N or
    better kept (with ties when the filter says so; a member with an empty measure left out
    when it says so). A NULL member is kept by its own test: `IN` never matches a NULL."""
    col = str(f["column"])
    if compile_by is None:
        raise CompileError(f"the Top N on {col!r} names no way to compile its measure")
    try:
        by = compile_by(str(v["by"]))
        n = int(v["n"])
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        raise CompileError(f"the Top N filter on {col!r} does not compile ({exc}); dropping "
                           f"it would return other members") from exc
    if " OVER (" in by:
        raise CompileError(f"the Top N on {col!r} ranks by a window, which a ranking over "
                           f"the members does not compute")
    order = _sort_term(by, "ASC" if str(v.get("direction")).lower() == "asc" else "DESC",
                       "LAST", dialect)
    rank = "RANK()" if v.get("ties") else "ROW_NUMBER()"
    rows = [p for p in dim_preds if not _AGG_SQL.search(p)]
    sub = (f"      SELECT {key_t} AS member_key,\n"
           f"             {rank} OVER (ORDER BY {order}) AS member_rank\n"
           f"      FROM   {src} {alias}")
    for j in joins:
        sub += "\n      " + j.replace("\n", "\n      ")
    if rows:
        sub += "\n      WHERE  " + "\n        AND  ".join(rows)
    sub += f"\n      GROUP BY {key_t}"
    if str(v.get("empty_members") or "") == "excluded":
        sub += f"\n      HAVING {by} IS NOT NULL"
    res.rules_used.append("engine.top_n_ranked_members")
    return (f"({key_t} IN (\n    SELECT member_key FROM (\n{sub}\n    ) q "
            f"WHERE q.member_rank <= {n}\n  )\n"
            f"   OR ({key_t} IS NULL AND EXISTS (\n    SELECT 1 FROM (\n{sub}\n"
            f"    ) q WHERE q.member_key IS NULL AND q.member_rank <= {n}\n  )))")


def _plan_filters(unit: dict, rules, alias: str, res: Result,
                  colmap: dict[str, str] | None = None, resolve=None,
                  dialect: str | None = None, place=None, text_key: bool = False,
                  special: dict | None = None):
    """The unit's filters as predicates: (context, dimension, deferred). With `text_key` (a
    model that compares text without case and without a column's trailing spaces), a filter
    on text values compares the column's key with each value's.

    With `special`, a caller that places them itself receives the `datasource`-stage
    predicates (applied to every row before any context filter) in `special["datasource"]`
    and the member filters (`MEMBER_OPS`) in `special["members"]`; without it the former are
    context predicates and the latter are deferred (refused by name)."""
    ctx, dim, deferred = [], [], []
    for f in unit.get("filters", []):
        column = f.get("column")
        op = str(f.get("op") or "").lower()
        if op == "top_rank" and unit.get("top_n"):
            # The rank filter of a Top N, already recovered into `top_n`: emitting it as a
            # predicate yields `WHERE rank = NULL`, which runs and returns nothing.
            continue
        if op in MEMBER_OPS:
            if f.get("value") is None:
                deferred.append({**f, "_why": "no-value"})
            elif special is not None and colmap is None:
                special["members"].append(f)
            elif (special is not None and op == "top_n" and isinstance(f.get("value"), dict)
                  and str(f["value"].get("population") or "") == "filtered"
                  and str(column).lower() in colmap):
                # Over a native query: a Top N ranked within the view's own filters ranks by
                # the measure as the statement computes it (`compile_by`, which reads the
                # query's columns), so only the member column needs the query's spelling.
                # Other member filters compile their expression without that map: deferred.
                special["members"].append({**f, "column": _ident(colmap[str(column).lower()]),
                                           "table": None})
            else:
                deferred.append({**f, "_why": "member-filter"})
            continue
        if colmap is not None and f.get("value") is not None and (
                (f.get("stage") or "").lower() not in ("measure", "table-calc")):
            if str(column).lower() not in colmap:
                # A calc over the query's columns (Tableau custom SQL) resolves below; only a
                # name that is neither is deferred.
                if not (resolve and resolve(column, f.get("table"))):
                    deferred.append({**f, "_why": "not-a-column"})
                    continue
            else:
                column = _ident(colmap[str(column).lower()])
        stage = (f.get("stage") or "").lower()
        if (stage in ("measure", "table-calc") and f.get("value") is None
                and f.get("value_kind") == "absent"):
            # A filter card on a value with nothing set (seen in a service-saved report: an
            # `Advanced` filter on the visual's measure, no condition): it narrows nothing.
            continue
        if stage == "measure" and f.get("measure") and special is not None and colmap is None:
            # A test of a MEASURE's value on each row of the view: placed by the caller, as a
            # HAVING over the compiled measure.
            if f.get("value") is None:
                deferred.append({**f, "_why": "no-value"})
            else:
                special.setdefault("measure_tests", []).append(f)
            continue
        if stage in ("measure", "table-calc"):
            # A measure-stage filter is applied after aggregation. Skipping it would ship a
            # statement missing a filter the widget applies (it used to be skipped whenever
            # the sheet had a Top N, which silently dropped a date range), so it is named.
            deferred.append({**f, "_why": "measure-stage"})
            continue
        # Where this filter's predicates go: a data source filter's own list when the caller
        # places it, else the context tier for it and for a context filter; a filter of what
        # the view SHOWS (`display`) to its own list, applied after every calculation.
        if stage == "datasource" and special is not None:
            into = special["datasource"]
        elif stage == "display" and special is not None:
            into = special.setdefault("display", [])
        elif stage in ("context", "datasource"):
            into = ctx
        else:
            into = dim
        if op == "relative_date" and isinstance(f.get("value"), dict):
            on = place(column, f.get("table")) if place and colmap is None else alias
            target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
            pred, why = _relative_date(target, f["value"], rules, dialect, res)
            if why:
                res.untranslatable.append({"construct": "relative date filter", "why": why})
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if op in ("not_null", "is_null"):
            on = place(column, f.get("table")) if place and colmap is None else alias
            target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
            into.append(f"{target} IS {'NOT ' if op == 'not_null' else ''}NULL")
            res.rules_used.append("engine.null_filter")
            continue
        if op == "tuple" and isinstance(f.get("value"), dict):
            # Several columns tested together (a hierarchy slicer's selected paths): a row
            # passes when its columns equal one selected combination, an empty member read
            # as NULL. One column's members alone would keep combinations never selected.
            pred, why = _tuple_pred(f, rules, dialect, res, text_key,
                                    lambda c, t: ((resolve(c, t) if resolve else None)
                                                  or f"{(place(c, t) if place and colmap is None else alias)}.{c}"))
            if pred is None:
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if f.get("value") is None and f.get("value_kind") == "absent":
            continue        # a filter that narrows nothing (nothing selected): no predicate
        if f.get("value") is None or (isinstance(f.get("value"), dict)
                                      and op not in ("relative_range",)
                                      and f["value"].get("min") is None
                                      and f["value"].get("max") is None):
            # The widget applies this filter, but the file recorded no value for it. Dropping
            # it would compute a different number that still runs; it is named instead.
            deferred.append({**f, "_why": "no-value"})
            continue
        # The alias the column is read under: a joined table's for a column of another
        # table (`_plan_joins`), else the statement's own.
        on = place(column, f.get("table")) if place and colmap is None else alias
        target = (resolve(column, f.get("table")) if resolve else None) or f"{on}.{column}"
        if op == "relative_range" and isinstance(f["value"], dict):
            pred, why = _relative_range(target, f["value"], rules, dialect, res)
            if why:
                res.untranslatable.append({"construct": "relative date filter", "why": why})
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
            into.append(pred)
            continue
        if f.get("null_as") is not None or op in _TEXT_OPS or op in ("any_of", "all_of") \
                or (isinstance(f["value"], list) and None in f["value"]):
            pred, why = _test_pred(target, op, f["value"], f, rules, dialect, res, text_key)
            if pred is not None:
                into.append(pred)
                continue
            if why:
                deferred.append({**f, "_why": "relative-date", "_detail": why})
                continue
        if isinstance(f["value"], dict):
            # A RANGE carries both bounds, either of which may be open. Each bound is its own
            # operator row: `range` (>=) for the lower, `lte` for the upper.
            preds, missing = [], []
            for bound, op in (("min", "range"), ("max", "lte")):
                if f["value"].get(bound) is None:
                    continue
                rule = rules.operator(op)
                if rule is None:
                    missing.append(op)
                    continue
                res.rules_used.append(rule["id"])
                if rule.get("tier") == "provisional":
                    res.provisional_rules.append(rule["id"])
                preds.append(_apply(_emit_for(rule, dialect),
                                    [target, _literal(f["value"][bound], dialect)], op))
            if missing:
                res.unknown += [f"operator:{op}" for op in missing]
                deferred.append({**f, "op": ",".join(missing)})
                continue
            if f["value"].get("or_null"):
                # The range, and the rows with no value ("include null values").
                preds = [f"({' AND '.join(preds)} OR {target} IS NULL)"]
                res.rules_used.append("engine.range_or_null")
            into.extend(preds)
            continue
        vals = f["value"] if isinstance(f["value"], (list, tuple, set)) else [f["value"]]
        if resolve and not re.search(rf"\b({re.escape(alias)}|j\d+)\.", target):
            # A filter on a calc that reads NO column (a parameter-driven sheet switch) is a
            # display control, not a data predicate: emitting `'Trend' IN ('Bar')` returns
            # nothing. Declared, never emitted.
            res.untranslatable.append({"construct": "parameter-only filter",
                                       "why": f"filter on {column!r} controls which view is "
                                              f"shown, not which rows"})
            continue
        if text_key and vals and all(_foldable(v) and v != "%null%" for v in vals) \
                and str(f.get("op") or "eq").lower() in ("in", "eq", "filter", "not_in"):
            # Text as the model holds it (seen live: 'North' and 'north' are one value): the
            # column's key against each value's, upper-cased here so the value stays a
            # literal a reader of the statement can still see.
            keyed = _literal_list([v.upper() for v in vals], dialect)
            res.rules_used.append("engine.text_key_filter")
            key = _Parser._text_key(target)
            if str(f.get("op") or "").lower() == "not_in":
                pred = f"({target} IS NULL OR {key} NOT IN ({keyed}))"
            elif len(vals) == 1:
                pred = f"{key} = {keyed}"
            else:
                pred = f"{key} IN ({keyed})"
            into.append(pred)
            continue
        if str(f.get("op") or "").lower() == "not_in":
            # A Tableau EXCLUDE filter. It keeps NULL rows unless `%null%` is itself excluded;
            # a bare SQL NOT IN would silently drop them too.
            excl_null = "%null%" in [str(v) for v in vals]
            rest = [v for v in vals if str(v) != "%null%"]
            if rest:
                pred = (f"({target} IS NOT NULL AND {target} NOT IN ({_literal_list(rest, dialect)}))"
                        if excl_null else
                        f"({target} IS NULL OR {target} NOT IN ({_literal_list(rest, dialect)}))")
            else:
                pred = f"{target} IS NOT NULL"
            res.rules_used.append("engine.tableau_exclude")
            into.append(pred)
            continue
        if "%null%" in [str(v) for v in vals]:
            # Tableau's member `%null%` IS NULL; `IN ('%null%')` matches a literal string.
            rest = [v for v in vals if str(v) != "%null%"]
            pred = (f"({target} IS NULL OR {target} IN ({_literal_list(rest, dialect)}))"
                    if rest else f"{target} IS NULL")
            into.append(pred)
            continue
        rule = rules.operator(str(f.get("op") or "eq").lower(), dialect or "")
        if rule is None:
            res.unknown.append(f"operator:{f.get('op')}")
            deferred.append(f)
            continue
        emit = rule.get("emit") or {}
        multi = isinstance(f.get("value"), (list, tuple, set))
        if multi and not emit.get("list_arg"):
            # A Tableau filter on several members carries a LIST. Only a membership operator
            # can express it; anything else would have to flatten it, and a flattened list is
            # a literal nobody wrote.
            res.unknown.append(f"operator:{f.get('op')}:multi-value")
            deferred.append({**f, "_why": "multi-value"})
            continue
        res.rules_used.append(rule["id"])
        if rule.get("tier") == "provisional":
            res.provisional_rules.append(rule["id"])
        value = (_literal_list(f["value"], dialect) if multi
                 else _literal(f.get("value"), dialect))
        pred = _apply(_emit_for(rule, dialect), [target, value], str(f.get("op")))
        into.append(pred)
    return ctx, dim, deferred
