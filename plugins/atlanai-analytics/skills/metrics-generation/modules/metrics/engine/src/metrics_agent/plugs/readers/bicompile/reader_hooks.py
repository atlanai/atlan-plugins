# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What the tableau and powerbi readers call: FormulaCompiler, calculation items and formats. Part of bicompile; see __init__.py."""
from __future__ import annotations

from typing import Any
from metrics_agent.lib import bi_models as _bi_models
from metrics_agent.lib import declared as _declared
from metrics_agent.plugs.readers import tableau_params as _tparams
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

from .common import (  # noqa: F401
    CORPUS,
    CompileError,
    RULES,
    Result,
)

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


# The sibling modules' names, imported after this module's own definitions: the modules
# call each other, and a name is looked up only when a function runs.
from .parser import (  # noqa: E402
    _column_ref,
    _sql_string,
    tokenize,
)
from .expressions import (  # noqa: E402
    _call_args,
)
from .statements import (  # noqa: E402
    _leaf_names,
    _measure_alias,
    _text_of,
)
from .units import (  # noqa: E402
    compile_unit,
)
from .joins import (  # noqa: E402
    _bare,
)
