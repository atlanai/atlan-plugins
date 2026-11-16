"""A dashboard tile -> what it shows, how it is filtered, and its SQL.

Tiles are the worksheets a dashboard places (a zone naming a worksheet, with
no type; device layouts are skipped) plus the worksheets no dashboard places.
A filter card is a zone too, but a control, not a tile: its `param` names the
field it filters, which marks that filter as a dashboard filter.

What a tile shows is read off the sheet, never inferred from SQL:

  shelves         table/rows, table/cols: `[ds].[instance]` joined by / * +
  encodings       pane/encodings: text, color, size, lod, tooltip, shape, path
  Measure Values  the members of the sheet's `[:Measure Names]` filter
  instances       column-instance/@derivation: an aggregation makes a measure;
                  None or a date part or truncation makes a dimension

A measure only on the tooltip or on Detail is not on the tile's face: it stays
in the element as a helper and out of the SQL, unless the tile shows no other
measure. A dimension anywhere, a tooltip included, sets the grain of the
marks, so it is always a GROUP BY key.

Filters apply at their saved state in Tableau's order: the published
datasource's extract and datasource filters, the workbook datasource's own,
the shared ones ("all sheets using this datasource"), then the sheet's.
Excluding values keeps NULL rows, as Tableau does; a filter on an aggregate
goes to HAVING. A filter built on USERNAME()/ISMEMBEROF() is row-level
security: recorded, never applied, because its effect depends on the viewer.
When the tile reads a FIXED expression the order matters: extract, datasource
and context filters (`context='true'`) are applied before it, the sheet's
other dimension filters after it (`sql.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.simplify import simplify

from eval_builder.elements import Dimension, FilterSpec, Measure, VisualElement, kind_from_tableau
from eval_builder.sources.tableau import sql as tile_sql
from eval_builder.sources.tableau.calc import (
    HOUSE, MEASURE_AGG, TEMPORAL, Ctx, Layer, Refusal, TExpr, Translator, ViewerDependent,
    apply_derivation, inline_lods, norm_type, parse_instance, tableau_literal,
)
from eval_builder.sources.tableau.model import PublishedIndex, Workbook

HELPER_CHANNELS = {"tooltip", "lod"}
AXIS = {"rows": "rows", "cols": "columns", "color": "color"}
AGG_LABEL = {"Sum": "SUM", "Avg": "AVG", "Count": "COUNT", "CountD": "COUNT_DISTINCT",
             "Min": "MIN", "Max": "MAX"}
MANY_MEMBERS = 200
_REF = re.compile(r"\[(?:[^\]]|\]\])*\](?:\.\[(?:[^\]]|\]\])*\])*")


def placements(wb: Workbook) -> list[tuple[str, str | None, dict]]:
    """(worksheet, dashboard or None, zone facts), in document order. A filter
    card controls its own sheet's filter on `param` and the filters other sheets
    share with it (same `filter-group`)."""
    out, seen = [], set()
    for db in wb.dashboards:
        zones = db.find("zones")            # the default layout; device layouts live elsewhere
        if zones is None:
            continue
        cards: dict[str, list] = {}
        for z in zones.iter("zone"):
            if z.get("type-v2") == "filter" and z.get("param"):
                owner = wb.worksheets.get(z.get("name"))
                group = next((f.get("filter-group") for f in (owner.findall("table/view/filter")
                              if owner is not None else []) if f.get("column") == z.get("param")), None)
                cards.setdefault(z.get("param"), []).append((z.get("name"), group))
        for z in zones.iter("zone"):
            name = z.get("name")
            if name in wb.worksheets and not z.get("type-v2") and not z.get("type") \
                    and (name, db.get("name")) not in seen:
                seen.add((name, db.get("name")))
                out.append((name, db.get("name"), {"cards": cards, "zone_id": z.get("id"),
                                                   "show_title": z.get("show-title") != "false"}))
    placed = {name for name, _, _ in out}
    return out + [(n, None, {}) for n in wb.worksheets if n not in placed]


def _split(ref: str, ds_names) -> tuple[str | None, str]:
    for d in ds_names:
        if ref.startswith(f"[{d}]."):
            return d, ref[len(d) + 3:]
    return None, ref


def _instance(inst: str, instances: dict) -> dict:
    info = parse_instance(inst)
    ci = instances.get(inst)
    if ci is not None:
        info["column"], info["derivation"] = ci.get("column"), ci.get("derivation")
        tc = ci.find("table-calc")
        if tc is not None:
            info["table_calc"] = info["table_calc"] or tc.get("type") or "quick table calculation"
        info["typekey"] = info["typekey"] or {"quantitative": "qk", "ordinal": "ok",
                                              "nominal": "nk"}.get(ci.get("type"))
    elif not info["decorated"]:
        info["column"], info["derivation"] = inst, None
    return info


def _title(wb: Workbook, ws, ds_names) -> str:
    """layout-options/title runs with <Sheet Name>, parameter and field insertions
    resolved; Tableau's default title is the sheet name."""
    ft = ws.find("layout-options/title/formatted-text")
    if ft is None:
        return ws.get("name")
    raw = "".join(r.text or "" for r in ft.iter("run")).replace("Æ", "\n")

    def sub(m):
        tok = m.group(1)
        if tok == "Sheet Name":
            return ws.get("name")
        if tok.startswith("[Parameters]."):
            p = wb.params.get(tok[len("[Parameters]."):]) or {}
            try:
                return str(tableau_literal(p["value"]).lit)
            except (KeyError, TypeError, Refusal):
                return f"<{p.get('caption') or tok}>"
        ds, inst = _split(tok, ds_names)
        fd = wb.datasources[ds].field(parse_instance(inst)["column"]) if ds in wb.datasources else None
        return f"<{fd.label if fd else tok}>"
    return " ".join(re.sub(r"<([^<>]+)>", sub, raw).split()) or ws.get("name")


def _member(v: str) -> TExpr:
    if v == "%null%":
        return TExpr("NULL", "null", const=True)
    return tableau_literal(v.replace("\\%", "%"))


def _groupfilter(gf) -> tuple[str, list[str]]:
    fn = gf.get("function")
    if gf.get("{http://www.tableausoftware.com/xml/user}ui-action-filter"):
        return "all", []                    # an action filter selects nothing at rest
    if fn == "level-members" or (fn == "crossjoin" and
                                 gf.get("{http://www.tableausoftware.com/xml/user}ui-enumeration") == "all"):
        return "all", []
    if fn == "member":
        return "in", [gf.get("member")]
    kids = list(gf)
    if fn == "union" and all(k.get("function") == "member" for k in kids):
        return "in", [k.get("member") for k in kids]
    if fn == "except" and kids and kids[0].get("function") == "level-members":
        rest = [_groupfilter(k) for k in kids[1:]]
        if all(mode == "in" for mode, _ in rest):
            return "not_in", [v for _, vals in rest for v in vals]
    why = {"end": "set: a top-N filter", "filter": "set: a condition filter"}.get(
        fn, f"unsupported filter: groupfilter {fn}")
    return "refused", [f"{why} is computed by Tableau"]


def _fold(pred: str) -> str | None:
    """'TRUE'/'FALSE' for a predicate with no column left after parameter
    substitution, else None."""
    try:
        e = sqlglot.parse_one(pred, read=HOUSE)
        if e.find(exp.Column):
            return None
        s = simplify(e).sql(dialect=HOUSE).upper()
        return s if s in ("TRUE", "FALSE") else None
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Pred:
    """One filter as the tile applies it."""

    spec: FilterSpec
    sql: str | None             # the predicate in the house dialect; None when not applied
    having: bool                # on an aggregate: HAVING
    early: bool                 # before FIXED expressions: extract, datasource, context
    reason: str | None          # the refusal, when the filter blocks the tile


def _filter(fel, layer: Layer, instances: dict, tr: Translator, ctx: Ctx, ds_names,
            scope: str, origin: str, early: bool) -> Pred | None:
    """The filter element as a `Pred`; None for a no-op."""
    col_ref = fel.get("column") or ""
    _, inst = _split(col_ref, ds_names)
    if inst == "[:Measure Names]" or inst.startswith("[Action ("):
        return None
    info = _instance(inst, instances)
    fd = layer.lookup(info["column"])
    spec = FilterSpec(expr=None, label=fd.label if fd else info["column"].strip("[]"), origin=origin,
                      scope=scope)
    cls, members, mode = fel.get("class"), [], None
    if cls == "categorical":
        gf = fel.find("groupfilter")
        if gf is None:
            return None
        mode, members = _groupfilter(gf)
        if mode == "all":
            return None
        if mode == "refused":
            return Pred(spec, None, False, early, f'{members[0]} (filter "{spec.label}")')
        spec.kind = "categorical"
    elif cls == "quantitative":
        mode = fel.get("included-values")
        if mode == "all":
            return None
        spec.kind = "range" if mode in ("in-range", "in-range-or-null") else "predicate"
    elif cls == "relative-date":
        mode, spec.kind = "relative-date", "relative_date"
    else:
        return Pred(spec, None, False, early, f'unsupported filter: "{spec.label}" of class {cls}')
    ctx.chain, ctx.physical = [], []
    try:
        if info["table_calc"]:
            raise Refusal(f'table calculation {info["table_calc"]} in filter "{spec.label}"')
        t, _ = apply_derivation(tr.field(info["column"], layer), info["derivation"], ctx, spec.label)
        if mode in ("in", "not_in"):
            lits = [_member(v) for v in members]
            spec.values = ["NULL" if x.dtype == "null" else str(x.lit) for x in lits]
            if t.const:                     # a parameter-only field: membership is decided now
                if (t.lit in [x.lit for x in lits]) == (mode == "in"):
                    return None
                pred = "FALSE"
            else:
                vals, has_null = [x.sql for x in lits if x.dtype != "null"], len(lits) > sum(
                    x.dtype != "null" for x in lits)
                if mode == "in" and t.dtype == "bool" and len(lits) == 1 and isinstance(lits[0].lit, bool):
                    pred = t.sql if lits[0].lit else f"NOT ({t.sql})"   # same rows in WHERE/HAVING
                elif mode == "in":
                    parts = ([f"{t.sql} IN ({', '.join(vals)})"] if vals else []) + (
                        [f"{t.sql} IS NULL"] if has_null else [])
                    pred = " OR ".join(parts) if len(parts) < 2 else f"({' OR '.join(parts)})"
                elif has_null:              # excluding NULL itself
                    pred = f"({t.sql} IS NOT NULL" + (f" AND {t.sql} NOT IN ({', '.join(vals)}))" if vals else ")")
                else:                       # Tableau's exclude keeps NULL rows; NOT IN alone drops them
                    pred = f"({t.sql} NOT IN ({', '.join(vals)}) OR {t.sql} IS NULL)"
        elif mode in ("in-range", "in-range-or-null"):
            bounds = [(op, fel.findtext(tag)) for op, tag in ((">=", "min"), ("<=", "max"))]
            pred = " AND ".join(f"{t.sql} {op} {tableau_literal(v).sql}" for op, v in bounds if v is not None) or "TRUE"
            spec.values = [f"{op} {v}" for op, v in bounds if v is not None]
            if mode == "in-range-or-null":
                pred = f"(({pred}) OR {t.sql} IS NULL)"
        elif mode in ("non-null", "null"):
            pred = f"{t.sql} IS {'NOT ' if mode == 'non-null' else ''}NULL"
        elif mode == "relative-date":
            unit = (fel.get("period-type-v2") or fel.get("period-type") or "").lower()
            first, last = fel.get("first-period"), fel.get("last-period")
            if unit not in ("day", "month", "quarter", "year") or first is None or last is None \
                    or fel.get("anchor-date") or fel.get("anchor"):
                raise Refusal(f'unsupported filter: relative-date "{spec.label}" ({unit or "no unit"}, anchored or open)')
            base = "CURRENT_DATE" if unit == "day" else f"DATE_TRUNC('{unit}', CURRENT_DATE)"
            pred = f"{t.sql} >= DATEADD({unit}, {int(first)}, {base})"
            if fel.get("include-future") != "true":
                pred += f" AND {t.sql} < DATEADD({unit}, {int(last) + 1}, {base})"
            ctx.flags.add("needs_as_of")
            spec.values = [f"{first}..{last} {unit}(s) from the as-of date"]
        else:
            raise Refusal(f'unsupported filter: quantitative "{spec.label}" with included-values={mode}')
    except ViewerDependent:
        spec.scope, spec.applied, spec.kind, spec.values = "security", False, "predicate", []
        return Pred(spec, None, False, early, None)
    except Refusal as r:
        return Pred(spec, None, False, early, str(r))
    folded = _fold(pred)
    if folded == "TRUE":
        return None
    if folded == "FALSE":
        ctx.note(f'filter "{spec.label}" is FALSE at its saved state: the tile is empty')
    plain = len(set(ctx.physical)) == 1 and not any(h["hop"] == "calculation" for h in ctx.chain)
    spec.column = ctx.physical[0] if plain else None
    spec.physical_columns = sorted(set(ctx.physical))
    return Pred(spec, pred, t.agg, early and not t.agg, None)


def _snake(caption: str, used: set[str]) -> str:
    base = re.sub(r"[^0-9a-zA-Z]+", "_", caption).strip("_").lower() or "value"
    base = f"c_{base}" if base[0].isdigit() else base
    name, i = base, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def resolve(wb: Workbook, pidx: PublishedIndex, ws_name: str, dashboard: str | None,
            zone: dict, dialect: str | None = None) -> VisualElement:
    ws = wb.worksheets[ws_name]
    view = ws.find("table/view")
    ds_names = set(wb.datasources)
    deps: dict[str, dict] = {}
    for dd in view.findall("datasource-dependencies"):
        d = deps.setdefault(dd.get("datasource"), {"columns": {}, "instances": {}})
        d["columns"].update({c.get("name"): c for c in dd.findall("column")})
        d["instances"].update({c.get("name"): c for c in dd.findall("column-instance")})
    used = [d.get("name") for d in view.findall("datasources/datasource") if d.get("name") != "Parameters"]
    blocking: list[tuple[str, str]] = []            # (what, reason); the first reason is the refusal
    notes: list[str] = []
    layers: dict[str, Layer] = {}
    for d in used:
        wds = wb.datasources.get(d)
        if wds is None:
            blocking.append(("tile", f"unresolved field: datasource {d} is not defined in the workbook"))
            continue
        pub = pidx.pair(wds) if wds.kind == "sqlproxy" else None
        if wds.kind == "sqlproxy" and pub is None:
            blocking.append(("tile", f"published datasource {wds.repo_id} was not provided"))
        layers[d] = Layer(wds, wb.params, pub, deps.get(d, {}).get("columns"))
    if len(used) > 1:
        blocking.append(("tile", f"blend: {len(used)} datasources on one worksheet"))
    first = layers[used[0]] if used and used[0] in layers else None
    source = (first.published or first.ds) if first else None
    if source is not None and source.dialect() is None and not (first.ds.kind == "sqlproxy" and first.published is None):
        classes = sorted({c for c in source.warehouses.values() if c} or {source.conn_class or "none"})
        blocking.append(("tile", f"non-warehouse source ({', '.join(classes)})"))
    from eval_builder import dialect as D

    out_dialect = dialect or (source.dialect() if source else None) or D.run()

    # -- what the tile shows ----------------------------------------------------
    shelves = {s: [_split(m.group(), ds_names) for m in _REF.finditer(ws.findtext(f"table/{s}") or "")]
               for s in ("rows", "cols")}
    on_shelf = set(shelves["rows"] + shelves["cols"])
    marks, channels = [], {}
    for ref in shelves["rows"] + shelves["cols"]:
        channels.setdefault(ref, []).append("rows" if ref in shelves["rows"] else "cols")
    for p in ws.findall("table/panes/pane"):
        axis = p.get("x-axis-name") or p.get("y-axis-name")
        if axis and _split(axis, ds_names) not in on_shelf:
            continue                        # a stale pane for a field no longer on the axis
        if p.find("mark") is not None:
            marks.append(p.find("mark").get("class"))
        for e in p.find("encodings") if p.find("encodings") is not None else []:
            if e.get("column"):
                channels.setdefault(_split(e.get("column"), ds_names), []).append(e.tag)
    if any(inst == "[Multiple Values]" for _, inst in channels):
        mvf = next((f for f in view.findall("filter") if (f.get("column") or "").endswith("[:Measure Names]")), None)
        if mvf is None:
            blocking.append(("tile", "unresolved field: Measure Values without a Measure Names filter"))
        for g in (mvf.iter("groupfilter") if mvf is not None else []):
            if g.get("function") == "member" and g.get("member"):
                # Inside member strings Tableau writes % as \%.
                ref = _split(g.get("member").strip('"').replace("\\%", "%"), ds_names)
                channels.setdefault(ref, []).append("measure values")

    # -- fields ---------------------------------------------------------------
    ctx = Ctx(week_start=(first.ds.week_start or (first.published.week_start if first.published else None))
              if first else None, dialect=out_dialect)
    tr = Translator(ctx)
    fields = []
    for (dsn, inst), chans in channels.items():
        if inst in ("[:Measure Names]", "[Multiple Values]"):
            continue
        layer = layers.get(dsn)
        if layer is None:
            blocking.append(("tile", f"unresolved field: {inst} of a datasource the sheet does not use"))
            continue
        info = _instance(inst, deps.get(dsn, {}).get("instances", {}))
        fd = layer.lookup(info["column"])
        fields.append({
            "label": fd.label if fd else info["column"].strip("[]"), "inst": f"[{dsn}].{inst}", "layer": layer,
            "channels": chans, "info": info, "t": None, "bucket": None, "reason": None,
            "chain": [], "physical": [], "reads": False,
            "role": "measure" if info["derivation"] in MEASURE_AGG or info["derivation"] == "User" else "dimension",
            "dtype": norm_type(fd.datatype if fd else None),
            "geo": fd is not None and fd.element is not None
            and (fd.element.get("semantic-role") or "") in ("[Geographical].[Latitude]", "[Geographical].[Longitude]")})

    def translate(f: dict) -> None:
        info = f["info"]
        ctx.chain, ctx.physical = [], []
        try:
            if info["table_calc"]:
                raise Refusal(f'table calculation {info["table_calc"]} in "{f["label"]}"')
            f["t"], f["bucket"] = apply_derivation(tr.field(info["column"], f["layer"]), info["derivation"],
                                                   ctx, f["label"])
            if f["t"].agg:
                f["role"] = "measure"
        except Refusal as r:
            f["reason"] = str(r)
        f["chain"], f["physical"] = list(ctx.chain), sorted(set(ctx.physical))
        f["reads"] = any(h["hop"] in ("column", "row count") for h in f["chain"])

    # Dimensions first: a measure that aggregates a FIXED value is only sound at
    # the view's grain, which the dimensions define.
    for f in fields:
        if f["role"] == "dimension":
            translate(f)
    ctx.view_dims = [f["t"].sql for f in fields
                     if f["role"] == "dimension" and f["t"] is not None and not f["t"].const]
    for f in fields:
        if f["role"] == "measure" and f["t"] is None and f["reason"] is None:
            translate(f)
    measures = [f for f in fields if f["role"] == "measure"]
    dims = [f for f in fields if f["role"] == "dimension"]
    shown = [m for m in measures if not set(m["channels"]) <= HELPER_CHANNELS] or measures
    for f in dims + measures:
        f["helper"] = f["role"] == "measure" and f not in shown
        if f["reason"] and f["helper"]:
            notes.append(f'helper measure "{f["label"]}" ({"/".join(f["channels"])}) left out: {f["reason"]}')
        elif f["reason"]:
            blocking.append((f'{f["role"]} "{f["label"]}"', f["reason"]))
        elif not f["helper"] and any(h["hop"] == "column" and not h["warehouse_columns"] for h in f["chain"]):
            blocking.append((f'{f["role"]} "{f["label"]}"', f'unresolved field: cannot trace "{f["label"]}" to warehouse columns'))
    data = [f for f in dims + shown if f["reason"] or f["reads"]]
    if not data:
        blocking.insert(0, ("tile", "decorative tile (no data field)"))

    # -- filters, in Tableau's order -------------------------------------------
    preds = []
    cards = zone.get("cards", {})

    def card(f, sheet_filter: bool) -> bool:
        return f.get("column") in cards and (not sheet_filter or any(
            owner == ws_name or (group and f.get("filter-group") == group)
            for owner, group in cards[f.get("column")]))

    def context(f) -> bool:
        return f.get("context") == "true"

    for d, layer in layers.items():
        stages = []                         # (filter, layer, scope, origin, before FIXED expressions)
        if layer.published is not None:
            pl = layer.published_layer()
            stages += [(f, pl, "extract", "tableau-tds", True) for f in layer.published.extract_filters]
            stages += [(f, pl, "datasource", "tableau-tds", True) for f in layer.published.filters]
        stages += [(f, layer, "extract", "tableau-xml", True) for f in layer.ds.extract_filters]
        stages += [(f, layer, "workbook", "tableau-xml", True) for f in layer.ds.filters]
        for f, sdeps in wb.shared_filters.get(d, []):     # applied to every sheet on the datasource
            shared = Layer(layer.ds, wb.params, layer.published, {**layer.deps, **sdeps})
            stages.append((f, shared, "dashboard" if card(f, False) else "workbook", "tableau-xml", context(f)))
        insts = deps.get(d, {}).get("instances", {})
        for f, lay, scope, origin, early in stages:
            preds.append(_filter(f, lay, insts if lay.label == "workbook" else {}, tr, ctx, ds_names,
                                 scope, origin, early))
    for f in view.findall("filter"):
        dsn, _ = _split(f.get("column") or "", ds_names)
        if dsn in layers:
            preds.append(_filter(f, layers[dsn], deps.get(dsn, {}).get("instances", {}), tr, ctx,
                                 ds_names, "dashboard" if card(f, True) else "element", "tableau-xml", context(f)))
    preds = [p for p in preds if p is not None]
    for p in preds:
        spec = p.spec
        if p.reason:
            blocking.append((f'filter "{spec.label}"', p.reason))
        elif not spec.applied:
            notes.append(f'filter "{spec.label}" ({spec.origin}) is row-level security: recorded, not applied')
        elif len(spec.values) > MANY_MEMBERS:
            notes.append(f'filter "{spec.label}" lists {len(spec.values)} members frozen at save time: '
                         "applied, but review before using this tile")
        if p.sql is not None:
            spec.expr = tile_sql.render(inline_lods(p.sql, ctx), out_dialect)

    # -- SQL ------------------------------------------------------------------
    used_alias: set[str] = set()
    sql_text, tables = None, []
    if not blocking:
        items = [(f["t"].sql, _snake(f["label"], used_alias)) for f in dims + shown]
        rows: dict[str, bool] = {}          # predicate -> applied before FIXED expressions; first one wins
        for p in preds:
            if p.sql is not None and not p.having:
                rows[p.sql] = rows.get(p.sql, False) or p.early
        try:
            sql_text, prune_notes, tables = tile_sql.assemble(
                ctx, items, [f["t"].sql for f in dims if not f["t"].const], list(rows.items()),
                list(dict.fromkeys(p.sql for p in preds if p.sql is not None and p.having)), out_dialect)
            notes += prune_notes
            for m in shown:                 # SUM(1) and the like count the rows of what the tile reads
                if not m["physical"] and not m["t"].const:
                    m["physical"] = [f"{t}.*" for t in tables]
        except Refusal as r:
            blocking.append(("tile", str(r)))
    if not tables:
        tables = sorted({t for ds, rel in ctx.relations.values() for t in rel.tables(ds.dialect())})
    customs = [rel.sql for _, rel in ctx.relations.values() if rel.kind == "text"]

    # -- the element ----------------------------------------------------------
    if ctx.params:
        notes.append("parameters at their saved values: " + "; ".join(
            f'{p["caption"]} = {p["value"]}' for p in ctx.params.values()))
    if "needs_as_of" in ctx.flags:
        notes.append("needs an as-of date: the SQL reads the current date")
    if not zone.get("show_title", True):
        notes.append("the dashboard hides this tile's title")
    notes += ctx.notes + [f"blocked: {what}: {why}" for i, (what, why) in enumerate(blocking)
                          if what != "tile" or i > 0]
    mark = next((m for m in marks if m and m != "Automatic"), "Automatic")
    geo = any(f["geo"] for f in fields if set(f["channels"]) & {"rows", "cols"})
    continuous = [s for s in ("rows", "cols") if any(
        f["info"]["typekey"] == "qk" for f in fields if s in f["channels"])]
    kind = kind_from_tableau("Automatic" if geo else mark, continuous_axes=len(continuous),
                             date_axis=any(f["dtype"] in TEMPORAL and f["role"] == "dimension"
                                           and f["info"]["typekey"] == "qk" for f in dims),
                             has_dimensions=bool(dims), is_map=geo)

    def render(t: TExpr | None) -> str:
        return tile_sql.render(inline_lods(t.sql, ctx), out_dialect) if t is not None else ""

    return VisualElement(
        tool="tableau", workbook=wb.name, page=dashboard, element_id=f"{wb.id}:{ws_name}",
        title=_title(wb, ws, ds_names), kind=kind, raw_kind=mark, workbook_id=wb.id,
        measures=[Measure(label=m["label"], expr=render(m["t"]), source_ref=m["inst"],
                          agg=AGG_LABEL.get(m["info"]["derivation"], "CUSTOM"),
                          physical_columns=m["physical"], role="helper" if m["helper"] else "value")
                  for m in measures],
        dimensions=[Dimension(label=d["label"], expr=render(d["t"]), physical_columns=d["physical"],
                              bucket=d["bucket"], source_ref=d["inst"],
                              axis=next((AXIS[c] for c in d["channels"] if c in AXIS), "detail"))
                    for d in dims],
        filters=[p.spec for p in preds], source_relation=tables, dialect=out_dialect,
        original_sql="\n;\n".join(customs) or None, sql=None if blocking else sql_text,
        shape="shelves", refusal=blocking[0][1] if blocking else None, notes=notes,
        provenance={"lineage": [{"field": f["label"], "instance": f["inst"], "hops": f["chain"]} for f in fields],
                    "parameters": dict(ctx.params), "needs_as_of": "needs_as_of" in ctx.flags,
                    "fixed_lods": [{"column": l.name, "expression": l.text, "depth": l.depth,
                                    "partition": [tile_sql.render(k, out_dialect) for k in l.partition],
                                    "sql": tile_sql.render(inline_lods(l.sql, ctx), out_dialect)}
                                   for l in ctx.lods],
                    "zone_id": zone.get("zone_id"), "source_dialect": source.dialect() if source else None,
                    "datasources": [{"workbook_datasource": lay.ds.caption, "published": lay.ds.repo_id,
                                     "published_file": lay.published.doc.label if lay.published else None}
                                    for lay in layers.values()]},
    )
