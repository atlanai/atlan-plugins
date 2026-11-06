# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A table no relationship reaches, whose rows the model file fixes: a parameter.

A DAX what-if parameter is a calculated table of a number series (GENERATESERIES) with a
measure that reads the one value its filters leave, `SELECTEDVALUE(T[c], fallback)`. No
relationship reaches the table, so only a filter on the table itself (a slicer, a visual,
page or report filter) and a visual grouped by its column narrow it, and nothing else sees
those filters. Its rows are in the file (`bi_facts` `fixed_rows`, written by the reader), so
what a formula reads off it is known when the statement is compiled (seen live,
executeQueries over a Desktop-shaped what-if table):

* one value left: SELECTEDVALUE is that value; none or several (no selection, two picked, a
  value the series does not hold): the fallback, BLANK without one;
* MAX / MIN the largest / smallest value left (BLANK when none), HASONEVALUE whether one is
  left, COUNTROWS(T) how many rows, ISFILTERED / ISCROSSFILTERED whether a filter is on it,
  ISINSCOPE whether the visual groups by the column;
* a visual grouped by the column shows one row per value left (the cross product with its
  other grouping columns), each computed with that value.

So each formula is compiled with the value written in as a literal, the filters on the table
are taken off the statement (they filter nothing else), and a grouped column becomes one
statement per value, their rows put together. Every statement says which value it was
compiled for, since on the report a person can pick another.
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any, Callable

from metrics_agent.plugs.readers import bicompile as _bc

# The functions that read a fixed table's column (or the table) in a scalar context, and how.
_COLUMN_READERS = {"SELECTEDVALUE", "MAX", "MIN", "HASONEVALUE", "ISFILTERED",
                   "ISCROSSFILTERED", "ISINSCOPE"}
_TABLE_READERS = {"COUNTROWS", "ISFILTERED", "ISCROSSFILTERED"}
_OPS = {"in", "not_in", "eq", "filter", "neq", "gt", "lt", "gte", "lte", "range", "is_null",
        "not_null"}


def index(facts: dict, graph: list[dict]) -> dict[str, dict]:
    """Every table of the model with `fixed_rows` that no relationship touches (active or
    not), by each name a unit or a formula calls it. Each entry: the table's names, its
    columns (by model and statement name -> position), their types and its rows."""
    linked = {str(t).lower() for r in graph
              for t in (r.get("from_table"), r.get("to_table")) if t}
    out: dict[str, dict] = {}
    for t in facts.get("tables") or []:
        fr = t.get("fixed_rows")
        names = {str(t.get("logical_name") or ""), str(t["name"]), _bc._bare(t["name"])} - {""}
        if not fr or {n.lower() for n in names} & linked:
            continue
        cols = [str(c) for c in fr["columns"]]
        pos = {c.lower(): i for i, c in enumerate(cols)}
        types: dict[int, str] = {}
        shown = list(cols)
        for c in t.get("columns") or []:
            if c.get("is_calculated"):
                continue
            i = pos.get(str(c.get("source_column") or c["name"]).lower())
            if i is None:
                continue
            pos.setdefault(str(c["name"]).lower(), i)
            shown[i] = str(c["name"])
            if c.get("data_type"):
                types[i] = str(c["data_type"]).lower()
        entry = {"name": t["name"], "logical": str(t.get("logical_name") or t["name"]),
                 "names": {n.lower() for n in names}, "columns": shown, "pos": pos,
                 "types": types, "rows": _rows(fr, types)}
        for n in entry["names"]:
            out[n] = entry
    return out


def _rows(fr: dict, types: dict[int, str]) -> list[tuple]:
    if fr.get("series"):
        s = fr["series"]
        start, step = Decimal(repr(s["start"])), Decimal(repr(s["step"]))
        vals = [start + i * step for i in range(int(s["count"]))]
        if types.get(0) in ("int64", "integer", "int") or all(v == int(v) for v in vals):
            return [(int(v),) for v in vals]
        return [(v,) for v in vals]
    return [tuple(Decimal(repr(v)) if isinstance(v, float) else v for v in r)
            for r in fr.get("rows") or []]


def _num(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, Decimal)):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    try:
        return Decimal(str(v).strip().rstrip("MDLmdl"))
    except ArithmeticError:
        return None


def _equal(a: Any, b: Any) -> bool:
    """A cell and a filter's literal: numbers by value (a slicer may save the double it shows,
    0.30000000000000004 for 0.3), text without case (as the model compares it)."""
    if a is None or b is None:
        return a is None and b is None
    na, nb = (_num(a), _num(b)) if not isinstance(a, str) else (None, None)
    if na is not None and nb is not None:
        return math.isclose(float(na), float(nb), rel_tol=1e-12, abs_tol=1e-12)
    return str(a).casefold() == str(b).casefold()


def _order(a: Any, b: Any) -> int | None:
    if a is None or b is None:
        return None
    na, nb = (_num(a), _num(b)) if not isinstance(a, str) else (None, None)
    if na is not None and nb is not None:
        return (na > nb) - (na < nb)
    sa, sb = str(a).casefold(), str(b).casefold()
    return (sa > sb) - (sa < sb)


def _passes(cell: Any, f: dict) -> bool:
    op, value = str(f.get("op") or "in"), f.get("value")
    if op in ("in", "not_in") or (op in ("eq", "filter") and isinstance(value, list)):
        vals = value if isinstance(value, list) else [value]
        hit = any(_equal(cell, v) for v in vals)
        return hit if op != "not_in" else not hit
    if op == "is_null":
        return cell is None
    if op == "not_null":
        return cell is not None
    if op == "range" or isinstance(value, dict):
        lo, hi = (value or {}).get("min"), (value or {}).get("max")
        c1 = _order(cell, lo) if lo is not None else 1
        c2 = _order(cell, hi) if hi is not None else -1
        return c1 is not None and c2 is not None and c1 >= 0 and c2 <= 0
    c = _order(cell, value)
    if c is None:
        return False
    return {"eq": c == 0, "filter": c == 0, "neq": c != 0, "gt": c > 0, "lt": c < 0,
            "gte": c >= 0, "lte": c <= 0}[op]


def _dax_literal(v: Any) -> list:
    """A value as the DAX tokens of its literal."""
    if v is None:
        return [("func", "BLANK"), ("lparen", "("), ("rparen", ")")]
    if isinstance(v, bool):
        return [("func", "TRUE" if v else "FALSE"), ("lparen", "("), ("rparen", ")")]
    n = _num(v) if not isinstance(v, str) else None
    if n is not None:
        text = format(n.normalize(), "f") if n != n.to_integral_value() else str(int(n))
        return ([("op", "-"), ("num", text[1:])] if text.startswith("-")
                else [("num", text)])
    if isinstance(v, str):
        try:
            d = dt.date.fromisoformat(v)
            return [("func", "DATE"), ("lparen", "("), ("num", str(d.year)), ("comma", ","),
                    ("num", str(d.month)), ("comma", ","), ("num", str(d.day)),
                    ("rparen", ")")]
        except ValueError:
            return [("str", '"' + v.replace('"', '""') + '"')]
    return [("str", '"' + str(v).replace('"', '""') + '"')]


def _sql_literal(v: Any, dialect: str | None) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    n = _num(v) if not isinstance(v, str) else None
    if n is not None:
        return format(n.normalize(), "f") if n != n.to_integral_value() else str(int(n))
    try:
        return f"CAST('{dt.date.fromisoformat(str(v)).isoformat()}' AS DATE)"
    except ValueError:
        return _bc._literal(str(v), dialect)


class _State:
    """What one statement sees of each fixed table: the rows its filters (and the grouped
    value) leave, which columns are filtered, and which are grouped."""

    def __init__(self, entry: dict, rows: list[tuple], filtered: set[int],
                 grouped: set[int]) -> None:
        self.entry, self.rows, self.filtered, self.grouped = entry, rows, filtered, grouped

    def values(self, i: int) -> list:
        seen: list = []
        for r in self.rows:
            if not any(_equal(r[i], s) for s in seen):
                seen.append(r[i])
        return seen


def _ref(tok: tuple) -> tuple[str | None, str | None]:
    if tok[0] in ("qualified", "quoted_qualified"):
        return (str(_bc._table_ref(tok[1]) or "").lower(), _bc._column_ref(tok[1]).lower())
    if tok[0] in ("func", "str"):
        v = tok[1]
        return (v[1:-1].replace("''", "'").lower() if v.startswith("'") else v.lower()), None
    return None, None


def substitute(toks: list, states: dict[str, _State], said: list[str]) -> list:
    """`toks` with every read of a fixed table's value written in as its literal."""
    out: list = []
    i = 0
    while i < len(toks):
        k, v = toks[i]
        up = v.upper() if k == "func" else ""
        if up == "TREATAS" and i + 1 < len(toks) and toks[i + 1][0] == "lparen":
            # TREATAS(VALUES(T[c]), X[col]) of a fixed table: the values its filters leave,
            # as the value list TREATAS takes ({a, b}), so X[col] is filtered to them.
            end, args = _bc._call_args(toks, i + 1)
            head, inner = _bc._call_head(args[0]) if end is not None and len(args) == 2 \
                else (None, [])
            if head in ("VALUES", "DISTINCT") and len(inner) == 1:
                table, col = _ref(inner[0])
                st = states.get(table or "")
                pos = st.entry["pos"].get(col) if st is not None and col else None
                if pos is not None and st.values(pos):
                    vals = st.values(pos)
                    lst: list = [("brace", "{")]
                    for j, x in enumerate(vals):
                        if j:
                            lst.append(("comma", ","))
                        lst += _dax_literal(x)
                    lst.append(("brace", "}"))
                    said.append(f"{st.entry['logical']}[{st.entry['columns'][pos]}] in "
                                f"{_bc._text_of(lst)}")
                    out += [toks[i], toks[i + 1]] + lst + [("comma", ",")] \
                        + substitute(args[1], states, said) + [("rparen", ")")]
                    i = end + 1
                    continue
        if up in _COLUMN_READERS | _TABLE_READERS and i + 1 < len(toks) \
                and toks[i + 1][0] == "lparen":
            end, args = _bc._call_args(toks, i + 1)
            if end is not None and args and len(args[0]) == 1:
                table, col = _ref(args[0][0])
                st = states.get(table or "")
                if st is not None:
                    rep = _read(up, col, args, st, said)
                    if rep is not None:
                        # A fallback may read a fixed table too
                        # (SELECTEDVALUE(A[x], SELECTEDVALUE(B[y], 1))).
                        out.extend(substitute(rep, states, said))
                        i = end + 1
                        continue
        out.append(toks[i])
        i += 1
    return out


def _read(fn: str, col: str | None, args: list, st: _State, said: list[str]) -> list | None:
    e = st.entry
    if col is None:
        if fn == "COUNTROWS" and len(args) == 1:
            return [("num", str(len(st.rows)))]
        if fn in ("ISFILTERED", "ISCROSSFILTERED") and len(args) == 1:
            return _dax_literal(bool(st.filtered or st.grouped))
        return None
    i = e["pos"].get(col)
    if i is None:
        return None
    vals = st.values(i)
    if fn == "SELECTEDVALUE" and len(args) in (1, 2):
        if len(vals) == 1:
            said.append(f"{e['logical']}[{e['columns'][i]}] = {_shown(vals[0])}")
            return _dax_literal(vals[0])
        why = "no value" if not vals else "no single value"
        said.append(f"{e['logical']}[{e['columns'][i]}]: {why} selected, so its fallback "
                    f"{_bc._text_of(args[1]) if len(args) == 2 else 'BLANK()'}")
        return ([("lparen", "(")] + list(args[1]) + [("rparen", ")")]
                if len(args) == 2 else _dax_literal(None))
    if fn in ("MAX", "MIN") and len(args) == 1:
        present = [x for x in vals if x is not None]
        if not present:
            return _dax_literal(None)
        best = present[0]
        for x in present[1:]:
            c = _order(x, best)
            if c is not None and ((fn == "MAX" and c > 0) or (fn == "MIN" and c < 0)):
                best = x
        said.append(f"{fn}({e['logical']}[{e['columns'][i]}]) = {_shown(best)}")
        return _dax_literal(best)
    if fn == "HASONEVALUE" and len(args) == 1:
        return _dax_literal(len(vals) == 1)
    if fn in ("ISFILTERED", "ISCROSSFILTERED") and len(args) == 1:
        return _dax_literal(i in st.filtered or i in st.grouped
                            if fn == "ISFILTERED" else bool(st.filtered or st.grouped))
    if fn == "ISINSCOPE" and len(args) == 1:
        return _dax_literal(i in st.grouped)
    return None


def _shown(v: Any) -> str:
    return _bc._text_of(_dax_literal(v))


def _reads_fixed(unit: dict, fixed: dict[str, dict], measures: dict[str, str]) -> set[str]:
    """The fixed tables (by entry name) a unit filters, groups by or whose values its
    formulas read."""
    hit: set[str] = set()
    for f in unit.get("filters") or []:
        e = fixed.get(str(f.get("table") or "").lower())
        if e is not None:
            hit.add(e["name"])
    for t in unit.get("group_by_tables") or []:
        e = fixed.get(str(t or "").lower())
        if e is not None:
            hit.add(e["name"])
    texts = [str(m.get("formula") or "") for m in unit.get("measures") or []]
    texts += list(measures.values())
    for text in texts:
        low = text.lower()
        for n, e in fixed.items():
            if e["name"] not in hit and (f"{n}[" in low or f"'{n}'" in low):
                hit.add(e["name"])
    return hit


def compile_with_fixed(unit: dict, model: dict, compile_fn: Callable[[dict, dict], Any],
                       dialect: str | None):
    """The unit compiled with each fixed table it reads as a parameter, or None when it reads
    none. `compile_fn(unit, model)` compiles a unit that no longer names them."""
    fixed = model.get("fixed") or {}
    if not fixed:
        return None
    measures = model.get("measures") or {}
    hit = _reads_fixed(unit, fixed, measures)
    if not hit:
        return None
    entries = {e["name"]: e for e in fixed.values() if e["name"] in hit}
    kept_filters, rows, filtered = [], {n: list(e["rows"]) for n, e in entries.items()}, {
        n: set() for n in entries}
    for f in unit.get("filters") or []:
        e = fixed.get(str(f.get("table") or "").lower())
        if e is None or e["name"] not in entries:
            kept_filters.append(f)
            continue
        i = e["pos"].get(str(f.get("column") or "").lower())
        op = str(f.get("op") or "in")
        if i is None or op not in _OPS or f.get("negated"):
            return _refused(f"a filter on the parameter table {e['logical']!r} "
                            f"({f.get('column')!r}, {op}) that is not read off its rows")
        rows[e["name"]] = [r for r in rows[e["name"]] if _passes(r[i], f)]
        filtered[e["name"]].add(i)
    group_by = list(unit.get("group_by") or [])
    gbt = list(unit.get("group_by_tables") or [])
    grouped: list[tuple[int, str, int]] = []      # (position in group_by, table, column)
    for k, g in enumerate(group_by):
        e = fixed.get(str(gbt[k] if k < len(gbt) else "").lower())
        if e is not None and e["name"] in entries:
            i = e["pos"].get(str(g).lower())
            if i is None:
                return _refused(f"grouping column {g!r} is not a column of the parameter "
                                f"table {e['logical']!r}")
            grouped.append((k, e["name"], i))
    keep = [k for k in range(len(group_by)) if k not in {p for p, _t, _i in grouped}]
    base = {**unit, "filters": kept_filters,
            "group_by": [group_by[k] for k in keep],
            "warehouse_tables": [t for t in unit.get("warehouse_tables") or []
                                 if str(t).lower() not in {e["name"].lower()
                                                           for e in entries.values()}]}
    if gbt:
        base["group_by_tables"] = [gbt[k] for k in keep if k < len(gbt)]
    if unit.get("totals"):
        base["totals"] = [{**t, "group_by": [g for g in t.get("group_by") or []
                                             if g not in {group_by[p] for p, _t, _i in grouped}]}
                          for t in unit["totals"]]

    def one(row_of: dict[str, list[tuple]], groups: dict[str, set[int]], u: dict):
        states: dict[str, _State] = {}
        for n, e in entries.items():
            st = _State(e, row_of[n], filtered[n], groups.get(n, set()))
            for name in e["names"]:
                states[name] = st
        said: list[str] = []
        lang = "dax"

        def sub(text: str | None) -> str | None:
            if not text:
                return text
            try:
                toks = _bc._tokens(text, lang)
            except _bc.CompileError:
                return text
            new = substitute(toks, states, said)
            return text if new == toks else _bc._text_of(new)
        u2 = {**u, "measures": [{**m, "formula": sub(m.get("formula"))
                                 if m.get("formula_language") in (None, "dax") else m.get("formula")}
                                for m in u.get("measures") or []]}
        m2 = {**model, "fixed": {},
              "measures": {k: sub(f) for k, f in (model.get("measures") or {}).items()}}
        res = compile_fn(u2, m2)
        return res, said

    if not grouped:
        res, said = one(rows, {}, base)
        return _with_gotcha(res, list(dict.fromkeys(said)))
    # One statement per combination of the grouped values left, each with them written in.
    by_table: dict[str, list[int]] = {}
    for _p, t, i in grouped:
        by_table.setdefault(t, []).append(i)
    per_table: list[list[tuple[str, tuple, list[tuple]]]] = []
    for t, cols in by_table.items():
        seen: list[tuple] = []
        for r in rows[t]:
            key = tuple(r[i] for i in cols)
            if not any(all(_equal(a, b) for a, b in zip(key, s)) for s in seen):
                seen.append(key)
        per_table.append([(t, key, [r for r in rows[t]
                                    if all(_equal(r[i], key[j]) for j, i in enumerate(cols))])
                          for key in seen])
    combos: list[list] = [[]]
    for options in per_table:
        combos = [c + [o] for c in combos for o in options]
    if not combos or any(not opts for opts in per_table):
        return _refused("the filters on the parameter table keep none of its values, so the "
                        "visual shows no row")
    cap = unit.get("max_values_grouped")
    capped: list = []
    if isinstance(cap, int) and not isinstance(cap, bool) and len(combos) > cap:
        # As many values as a widget's variants may be (policy bi.max_variants_per_widget):
        # the rest are left out of the statement, each named (variant_cap).
        capped, combos = combos[cap:], combos[:cap]
    branches, said_all, first = [], [], None
    total_branches: dict[int, list[str]] = {}
    for combo in combos:
        row_of = dict(rows)
        values: dict[int, Any] = {}
        for t, key, kept in combo:
            row_of[t] = kept
            for j, i in enumerate(by_table[t]):
                for p, tt, ii in grouped:
                    if tt == t and ii == i:
                        values[p] = key[j]
        groups = {t: set(cols) for t, cols in by_table.items()}
        res, said = one(row_of, groups, base)
        if res.coverage == _bc.REFUSED or not res.sql:
            return res
        first = first or res
        said_all.extend(said)
        branches.append(_wrap(res.sql, values, group_by, keep, dialect))
        for ti, tot in enumerate(res.totals or []):
            orig = (unit.get("totals") or [])[ti] if ti < len(unit.get("totals") or []) else {}
            if tot.get("sql") and any(group_by[p] in (orig.get("group_by") or [])
                                       for p, _t, _i in grouped):
                kept_g = [g for g in group_by if g in (orig.get("group_by") or [])]
                total_branches.setdefault(ti, []).append(_wrap(
                    tot["sql"], {kept_g.index(group_by[p]): v for p, v in values.items()
                                 if group_by[p] in kept_g},
                    kept_g, [kept_g.index(g) for g in kept_g if g not in
                             {group_by[p] for p, _t, _i in grouped}], dialect))
    out = first
    out.sql = "\nUNION ALL\n".join(branches)
    if unit.get("totals"):
        # A total that keeps the grouped column: one row per value, as the visual's rows. One
        # that does not: the table's values left by its filters only (no value is grouped).
        flat, _s = one(rows, {}, base)
        totals = []
        for ti, orig in enumerate(unit["totals"]):
            if ti in total_branches:
                totals.append({**orig, "sql": "\nUNION ALL\n".join(total_branches[ti]),
                               "reason": None})
            elif ti < len(flat.totals or []):
                totals.append({**flat.totals[ti], "kind": orig.get("kind"),
                               "group_by": list(orig.get("group_by") or [])})
        out.totals = totals
    said_all = list(dict.fromkeys(said_all))
    if capped:
        left = ["/".join(_shown(v) for v in key) for combo in capped for _t, key, _k in combo]
        why = (f"variant_cap: the visual shows {len(combos) + len(capped)} values of the "
               f"parameter it is grouped by; the statement holds the first {len(combos)} "
               f"(policy bi.max_variants_per_widget) and leaves out {len(capped)}: "
               + ", ".join(left[:12]) + (", ..." if len(left) > 12 else ""))
        out.coverage = _bc.PARTIAL
        out.untranslatable = list(out.untranslatable or []) + [
            {"construct": "parameter values", "why": why}]
        out.reason = "; ".join(x for x in (out.reason, why) if x)
    return _with_gotcha(out, said_all if len(said_all) <= 6 else said_all[:6] + ["..."])


def _wrap(sql: str, values: dict[int, Any], group_by: list[str], keep: list[int],
          dialect: str | None) -> str:
    """`SELECT` the inner statement's columns with each grouped value written in at its place
    among the grouping columns."""
    import sqlglot  # noqa: PLC0415

    try:
        names = sqlglot.parse_one(sql, read=dialect or None).named_selects
    except Exception:  # noqa: BLE001 - a statement sqlglot cannot read keeps the plain order
        names = []
    inner_groups = names[:len(keep)]
    rest = names[len(keep):]
    cols, gi = [], 0
    for k, g in enumerate(group_by):
        if k in values:
            cols.append(f"{_sql_literal(values[k], dialect)} AS {g}")
        elif gi < len(inner_groups):
            cols.append(f"b.{inner_groups[gi]}")
            gi += 1
    cols += [f"b.{n}" for n in rest]
    body = "\n".join("    " + ln if ln.strip() else ln for ln in sql.splitlines())
    return "SELECT " + ",\n       ".join(cols) + f"\nFROM   (\n{body}\n) b"


def _refused(reason: str):
    return _bc.Compiled(coverage=_bc.REFUSED, reason=reason)


def _with_gotcha(res, said: list[str]):
    if res.sql and said:
        res.gotchas.append(
            "Parameter value(s) as the report is saved: " + "; ".join(said)
            + ". On the report a person can pick another value; this statement is that "
              "state only.")
    return res
