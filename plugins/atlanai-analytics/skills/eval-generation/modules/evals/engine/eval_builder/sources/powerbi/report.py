"""The Power BI report: pages, visuals, what each visual projects, and filters.

Two on-disk shapes, one result:

    PBIR     `definition/report.json`, `definition/pages/<id>/page.json`,
             `definition/pages/<id>/visuals/<id>/visual.json` (the enhanced
             report format: a PBIP `*.Report/definition/`, or the same tree
             under `Report/definition/` inside a newer .pbix)
    legacy   one `Layout` JSON (`Report/Layout` in a .pbix/.pbit, UTF-16, or
             `report.json` in an older PBIP), whose sections, visual configs
             and filters are JSON strings inside JSON

Every field reference is reduced to a `Field`: a column, a measure, an
implicit aggregation of a column, or one level of a date hierarchy. Filters
keep their semantic-query body untranslated; the tile translates it, because
only the tile knows which table a filter has to reach.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from eval_builder.sources.powerbi.model import read_json_part

# PBI QueryAggregateFunction
AGG_FUNCTIONS = {0: "SUM", 1: "AVG", 2: "COUNT_DISTINCT", 3: "MIN", 4: "MAX", 5: "COUNT",
                 6: "MEDIAN", 7: "STDEV", 8: "VARIANCE"}


@dataclass
class Field:
    kind: str                                   # column | measure | aggregation | datelevel
    table: str
    name: str
    func: str | None = None                     # aggregation: SUM, AVG, …; datelevel: Year|Quarter|Month|Day

    @property
    def ref(self) -> str:
        return f"{self.table}[{self.name}]"


@dataclass
class Projection:
    field: Field
    label: str
    query_ref: str = ""
    active: bool = True


@dataclass
class FilterDef:
    field: Field | None
    type: str                                   # Categorical | Advanced | RelativeDate | TopN | …
    body: dict | None                           # the semantic query filter {From, Where}; None when unset
    scope: str                                  # report | page | visual | slicer
    name: str = ""
    inverted: bool = False                      # isInvertedSelectionMode: NOT IN the listed values


@dataclass
class Visual:
    id: str
    page: str
    type: str
    title: str | None
    roles: dict[str, list[Projection]] = field(default_factory=dict)
    filters: list[FilterDef] = field(default_factory=list)
    field_parameter_roles: set[str] = field(default_factory=set)
    slicer_filter: FilterDef | None = None      # what a slicer's saved state selects


@dataclass
class Page:
    id: str
    name: str
    hidden: bool
    filters: list[FilterDef] = field(default_factory=list)
    visuals: list[Visual] = field(default_factory=list)


@dataclass
class Report:
    pages: list[Page] = field(default_factory=list)
    filters: list[FilterDef] = field(default_factory=list)
    format: str = ""                            # pbir | legacy


# ---------------------------------------------------------------- field references

def to_field(expr: dict, aliases: dict[str, str] | None = None) -> Field | None:
    aliases = aliases or {}

    def entity(src: dict) -> str | None:
        ref = (src or {}).get("SourceRef") or {}
        return ref.get("Entity") or aliases.get(ref.get("Source", ""))

    if "Column" in expr:
        e = entity(expr["Column"].get("Expression", {}))
        return Field("column", e, expr["Column"]["Property"]) if e else None
    if "Measure" in expr:
        e = entity(expr["Measure"].get("Expression", {}))
        return Field("measure", e, expr["Measure"]["Property"]) if e else None
    if "Aggregation" in expr:
        inner = to_field(expr["Aggregation"].get("Expression", {}), aliases)
        if inner is None or inner.kind != "column":
            return None
        return Field("aggregation", inner.table, inner.name,
                     AGG_FUNCTIONS.get(expr["Aggregation"].get("Function"), f"FUNC{expr['Aggregation'].get('Function')}"))
    if "HierarchyLevel" in expr:
        h = expr["HierarchyLevel"].get("Expression", {}).get("Hierarchy", {})
        var = h.get("Expression", {}).get("PropertyVariationSource")
        if var:                                 # an auto date hierarchy on a date column
            e = entity(var.get("Expression", {}))
            return Field("datelevel", e, var["Property"], expr["HierarchyLevel"].get("Level")) if e else None
    return None


def _label(p: dict, f: Field) -> str:
    return p.get("displayName") or p.get("nativeQueryRef") or f.name


def _title(objs: dict) -> str | None:
    try:
        v = objs["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        return v[1:-1].replace("''", "'") if v.startswith("'") else v
    except (KeyError, IndexError, TypeError):
        return None


def _filters(cfg: dict | list | None, scope: str) -> list[FilterDef]:
    items = cfg.get("filters", []) if isinstance(cfg, dict) else (cfg or [])
    out = []
    for f in items:
        fld = to_field(f.get("field") or f.get("expression") or {})
        inverted = False
        try:
            inverted = f["objects"]["general"][0]["properties"]["isInvertedSelectionMode"]["expr"]["Literal"]["Value"] == "true"
        except (KeyError, IndexError, TypeError):
            pass
        out.append(FilterDef(fld, f.get("type", ""), f.get("filter"), scope, f.get("name", ""), inverted))
    return out


def _slicer(objects: dict, roles: dict[str, list[Projection]]) -> FilterDef | None:
    """A slicer's saved selection: `general.filter` holds it as a semantic
    query filter (categorical and between slicers alike)."""
    try:
        body = objects["general"][0]["properties"]["filter"]["filter"]
    except (KeyError, IndexError, TypeError):
        return None
    fld = next((p.field for ps in roles.values() for p in ps), None)
    return FilterDef(fld, "Slicer", body, "slicer")


# ---------------------------------------------------------------- PBIR

def load_pbir(read: Callable[[str], bytes | None], list_dir: Callable[[str], list[str]], root: str) -> Report:
    """`read(path)` returns a file's bytes or None; `list_dir(prefix)` lists the
    files under it. `root` is the `definition/` folder."""
    rep = Report(format="pbir")
    doc = _json(read(f"{root}/report.json")) or {}
    rep.filters = _filters(doc.get("filterConfig"), "report")
    order = (_json(read(f"{root}/pages/pages.json")) or {}).get("pageOrder")
    files = list_dir(f"{root}/pages/")
    page_ids = order or sorted({p.split("/pages/")[1].split("/")[0] for p in files if p.endswith("/page.json")})
    for pid in page_ids:
        pdoc = _json(read(f"{root}/pages/{pid}/page.json"))
        if pdoc is None:
            continue
        page = Page(pid, pdoc.get("displayName", pid), pdoc.get("visibility") == "HiddenInViewMode",
                    _filters(pdoc.get("filterConfig"), "page"))
        for vf in sorted(p for p in files if p.startswith(f"{root}/pages/{pid}/visuals/") and p.endswith("/visual.json")):
            v = _json(read(vf))
            if v is None or "visual" not in v:
                continue                        # a visual group
            vis = v["visual"]
            roles: dict[str, list[Projection]] = {}
            fp: set[str] = set()
            for role, st in ((vis.get("query") or {}).get("queryState") or {}).items():
                for p in st.get("projections", []):
                    f = to_field(p.get("field", {}))
                    if f is not None:
                        roles.setdefault(role, []).append(Projection(f, _label(p, f), p.get("queryRef", ""),
                                                                     p.get("active", True)))
                if st.get("fieldParameters"):
                    fp.add(role)
            visual = Visual(v.get("name", vf), page.name, vis.get("visualType", ""),
                            _title(vis.get("visualContainerObjects") or {}) or _title(vis.get("objects") or {}),
                            roles, _filters(v.get("filterConfig"), "visual"), fp)
            if visual.type in ("slicer", "advancedSlicerVisual", "listSlicer", "textSlicer"):
                visual.slicer_filter = _slicer(vis.get("objects") or {}, roles)
            page.visuals.append(visual)
        rep.pages.append(page)
    return rep


# ---------------------------------------------------------------- legacy Layout

def load_legacy(layout: dict) -> Report:
    rep = Report(format="legacy")
    rep.filters = _filters(_jsonstr(layout.get("filters")), "report")
    for i, sec in enumerate(layout.get("sections", [])):
        page = Page(sec.get("name", str(i)), sec.get("displayName", f"Page {i + 1}"),
                    sec.get("visibility") == 1, _filters(_jsonstr(sec.get("filters")), "page"))
        for vc in sec.get("visualContainers", []):
            cfg = _jsonstr(vc.get("config")) or {}
            sv = cfg.get("singleVisual")
            if not sv:
                continue
            pq = sv.get("prototypeQuery") or {}
            aliases = {f["Name"]: f["Entity"] for f in pq.get("From", []) if "Entity" in f}
            by_ref = {}
            for s in pq.get("Select", []):
                f = to_field(s, aliases)
                if f is not None:
                    by_ref[s.get("Name", "")] = (f, s.get("NativeReferenceName"))
            props = sv.get("columnProperties") or {}
            roles: dict[str, list[Projection]] = {}
            for role, items in (sv.get("projections") or {}).items():
                for it in items:
                    hit = by_ref.get(it.get("queryRef", ""))
                    if hit:
                        f, native = hit
                        label = (props.get(it["queryRef"], {}) or {}).get("displayName") or native or f.name
                        roles.setdefault(role, []).append(Projection(f, label, it["queryRef"], it.get("active", True)))
            visual = Visual(cfg.get("name", ""), page.name, sv.get("visualType", ""),
                            _title(sv.get("vcObjects") or {}), roles,
                            _filters(_jsonstr(vc.get("filters")), "visual"))
            if visual.type in ("slicer", "advancedSlicerVisual"):
                visual.slicer_filter = _slicer(sv.get("objects") or {}, roles)
            page.visuals.append(visual)
        rep.pages.append(page)
    return rep


def _json(raw: bytes | None):
    if raw is None:
        return None
    try:
        return read_json_part(raw)
    except ValueError:
        return None


def _jsonstr(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return v
