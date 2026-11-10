# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The BI model's own metadata about a measure, as the gate bundles and the metric card carry it.

A model says more about a measure than its formula: the author's description, the display
folder it sits in, the annotations and perspectives that include it, whether the model (or
the data source) is certified or promoted, and a KPI's target and status. A reader puts all
of it on `bi_facts` (`description`, `folder`, `tags`, `kpi`, the assets' `endorsement`);
this module turns it into the bounded block a widget's `bi_context` carries for each measure,
which travels with the statement to the verified layer and onto the metric card.

HINTS, NEVER SEMANTICS. Nothing here is read by a compiler: a description that says
"excludes returns" does not add a filter. It is context for whoever authors the question and
the card (the skills say so), and provenance for whoever reads it.

THE DEFINITION'S STYLE is how the BI tool defines the number, from what the reader declared:
`explicit` (a measure or calculated field of the model), `implicit` (a column aggregated
where a visual uses it, or a quick calculation defined there: `origin: visual`),
`report` (a report's own measure over a shared model), `kpi_target` / `kpi_status` (the target
or status the model declares for a KPI), `calculation_item` (a calculation group's item applied
to the measure by the widget, its formula in `calculation_items`). Vendor-neutral: it reads the
common format only.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

# The strongest endorsement first.
_ENDORSEMENTS = ("certified", "promoted")
# The assets a measure is defined in: its model or its data source (not a report or a view).
_MODEL_ROLES = ("semantic_model", "datasource", "workbook")


def endorsement(assets: list[dict] | None) -> tuple[str | None, str | None]:
    """The strongest endorsement any of the model's own assets carries, and its note."""
    best, note = None, None
    for a in assets or []:
        if a.get("role") not in _MODEL_ROLES:
            continue
        e = a.get("endorsement")
        if e in _ENDORSEMENTS and (best is None
                                   or _ENDORSEMENTS.index(e) < _ENDORSEMENTS.index(best)):
            best, note = e, a.get("endorsement_note")
    return best, note


def style(m: dict | None) -> str | None:
    """How the BI tool defines the measure; None when the model does not declare it."""
    if not m:
        return None
    part = (m.get("kpi_of") or {}).get("part")
    if part:
        return f"kpi_{part}"
    if m.get("origin") == "report":
        return "report"
    if m.get("origin") == "visual":
        return "implicit"
    return "explicit"


def _cut(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def calculation_items(unit: dict, tables: list[dict]) -> list[dict]:
    """The calculation items a widget applies to every measure it shows (bi_facts
    `calculation_group`): one kept by a filter on the group's column, or every item when the
    widget groups by that column (several numbers stacked in one statement, `stacked`).
    [{group, item, formula, stacked}] in the model's own words; [] for none."""
    out: list[dict] = []
    for t in tables or []:
        cg = t.get("calculation_group") or {}
        col = str(cg.get("column") or "").lower()
        if not col:
            continue
        items = {str(i["name"]): i.get("expression") for i in cg.get("items") or []}
        group = t.get("logical_name") or t.get("name")
        kept: list[str] = []
        for f in unit.get("filters") or []:
            if str(f.get("column") or "").lower() == col and f.get("op") == "in":
                vals = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
                kept += [str(v) for v in vals if str(v) in items]
        stacked = col in {str(c).lower() for c in unit.get("columns") or []}
        names = kept or (list(items) if stacked else [])
        out += [{"group": group, "item": n, "formula": items[n],
                 "stacked": stacked and len(names) > 1} for n in names]
    return out


def measure_context(name: str, m: dict | None, *, caption: str | None, aggregation: Any,
                    assets: list[dict] | None, max_chars: int,
                    items: list[dict] | None = None) -> dict[str, Any]:
    """One measure's entry in a widget's `bi_context.measures`. `items`: the calculation items
    the widget applies to it (`calculation_items`)."""
    m = m or {}
    tags: dict[str, Any] = {}
    own = m.get("tags") or {}
    if own.get("annotations"):
        tags["annotations"] = [{"name": str(a["name"]), "value": _cut(a.get("value"), max_chars)
                                or ""} for a in own["annotations"]]
    if own.get("perspectives"):
        tags["perspectives"] = sorted({str(p) for p in own["perspectives"]})
    labels = sorted({str(x) for a in assets or [] if a.get("role") in _MODEL_ROLES
                     for x in a.get("labels") or []})
    if labels:
        tags["labels"] = labels
    level, note = endorsement(assets)
    if level:
        tags["endorsement"] = level
        if note:
            tags["endorsement_note"] = _cut(note, max_chars)
    kpi = m.get("kpi") or {}
    if any(kpi.get(k) for k in ("target", "status")):
        tags["kpi"] = {k: _cut(kpi.get(k), max_chars)
                       for k in ("target", "status", "status_graphic")}
    out = {"name": name, "caption": caption, "aggregation": aggregation,
           "folder": m.get("folder"),
           "format_string": m.get("format_string"),
           "description": _cut(m.get("description"), max_chars),
           "formula": _cut(m.get("formula"), max_chars),
           "style": style(m) if m else None,
           "derived_from": list(m.get("uses") or [])}
    if items:
        # A calculation item applied to the measure: the number is the item's formula with
        # the measure in it, not the measure's own.
        out["calculation_items"] = [{k: (_cut(v, max_chars) if k == "formula" else v)
                                     for k, v in i.items()} for i in items]
        out["style"] = "calculation_item"
    if m.get("report"):
        out["report"] = m["report"]
    if m.get("kpi_of"):
        out["kpi_of"] = dict(m["kpi_of"])
    if tags:
        out["tags"] = tags
    return out


SIDECAR = "bi_tags.json"


class SidecarError(ValueError):
    """A bi_tags.json that is not one (schema `bi_tags`)."""


def load_sidecar(path: pathlib.Path) -> list[dict]:
    """The assets `bi_tags.json` beside a BI input (or in its folder) describes; [] without one.
    Untrusted data: validated against `bi_tags.schema.json`, values kept as text."""
    import jsonschema  # noqa: PLC0415 - only when a sidecar is present

    from metrics_agent.lib import artifacts as _artifacts  # noqa: PLC0415
    folder = path if path.is_dir() else path.parent
    f = folder / SIDECAR
    if not f.is_file():
        return []
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
        jsonschema.validate(doc, _artifacts.schema("bi_tags.schema.json"))
    except (ValueError, jsonschema.ValidationError) as exc:
        raise SidecarError(f"{f.name}: not a BI tags file ({str(exc)[:200]})") from exc
    return list(doc["assets"])


def apply_sidecar(assets: list[dict], tagged: list[dict]) -> None:
    """Copy each tagged asset's endorsement and description onto the matching asset (same
    role, and the same vendor id, else the same name ignoring case)."""
    for a in assets:
        for t in tagged:
            if t.get("role") != a.get("role"):
                continue
            same = (t.get("vendor_id") and t.get("vendor_id") == a.get("vendor_id")) or (
                t.get("name") and str(t["name"]).casefold() == str(a.get("name") or "").casefold())
            if not same:
                continue
            for k in ("endorsement", "endorsement_note", "description", "labels"):
                if t.get(k):
                    a[k] = t[k]
            break


_DEF_FIELDS = ("style", "formula", "folder", "description", "format_string",
               "calculation_items")


def _alias(name: str) -> str:
    from metrics_agent.lib import slugs  # noqa: PLC0415
    return slugs.column_alias(str(name or ""))


def _expr_key(expr: str | None) -> str:
    """Alias-blind, spacing-blind, case-blind: `SUM(t.x)` and `sum( o.x )` are one aggregate."""
    import re  # noqa: PLC0415
    return re.sub(r"\s+", "", re.sub(r"\b[A-Za-z_]\w*\.(?=\w)", "", str(expr or ""))).lower()


def aliases_for(expressions: list[str | None], aggregates: list[dict]) -> set[str]:
    """The output aliases of a statement's aggregates (D1's `metric_candidates`: alias, expr)
    that compute one of `expressions`: which of a statement's columns a metric reads."""
    want = {_expr_key(e) for e in expressions if e}
    return {str(a.get("alias")) for a in aggregates or []
            if a.get("alias") and _expr_key(a.get("expr") or a.get("expression")) in want}


def definitions(sources: list[tuple[str, dict]],
                aliases: dict[str, set[str]] | None = None) -> list[dict]:
    """Every BI definition a metric's statements were compiled from, one per (model, measure),
    with the statements compiled from it: the sources traced to where the number is defined.
    Read from each statement's own `bi_context` (A2), so nothing here opens a BI file; the
    model's metadata is carried as context and changes no SQL.

    A statement may show several measures (a table of sales and cost): with `aliases` (per
    statement, the output columns the metric reads, `aliases_for`), only the measures behind
    those columns are kept, matched on the column alias the compiler gives each measure; a
    statement showing one measure, or none matching, keeps all of its own."""
    out: dict[tuple, dict] = {}
    for vq_id, st in sources:
        bi = ((st.get("provenance") or {}).get("bi") or {})
        ctx_ = bi.get("bi_context") or {}
        shown = [ms for ms in ctx_.get("measures") or [] if ms.get("name")]
        want = (aliases or {}).get(vq_id)
        if want and len(shown) > 1:
            kept = [ms for ms in shown if _alias(ms["name"]) in want]
            shown = kept or shown
        for ms in shown:
            name = str(ms["name"])
            key = (ctx_.get("workbook"), name,
                   tuple(i.get("item") for i in ms.get("calculation_items") or []))
            d = out.get(key)
            if d is None:
                d = out[key] = {"model": ctx_.get("workbook"), "measure": name,
                                **{f: ms.get(f) for f in _DEF_FIELDS if f != "calculation_items"},
                                **({"calculation_items": ms["calculation_items"]}
                                   if ms.get("calculation_items") else {}),
                                "derived_from": list(ms.get("derived_from") or []),
                                "statements": []}
                tags = {k: v for k, v in (ms.get("tags") or {}).items()
                        if v not in (None, [], {})}
                if tags:
                    d["tags"] = tags
            if vq_id not in d["statements"]:
                d["statements"].append(vq_id)
    return [out[k] for k in sorted(out, key=lambda k: (str(k[0] or ""), k[1], k[2]))]
