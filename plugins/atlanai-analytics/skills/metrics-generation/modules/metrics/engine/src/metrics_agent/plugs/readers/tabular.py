# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Analysis Services Tabular (`.bim` / TMSL) parser plugin -> the `bi_facts` contract.

PROVENANCE. A COPY of the Tabular parser of an earlier project (guardrail 6: ported,
never imported), rewritten onto THIS repo's contract. Deliberately left behind: the
earlier project's `filters` key, which this contract does not carry, and its blanket treatment of
every `model.expressions` entry as a parameter — see `_parameters` for why only the declared
ones qualify here.

A `.bim` is the semantic model on its own: tables, columns, DAX measures, relationships and
Power Query partitions, as JSON. Same vendor as `.pbix` and the same DAX, so nothing
downstream changes. Two things differ from every other Power BI input, and both matter.

**NO REPORT LAYER, SO `usage` IS CURATION AND SAYS SO.** A `.bim` has no pages and no visuals.
Leaving `usage` empty would drop a real signal — the model's own statement of which measures
belong together — so this emits one entry per `displayFolder` and one per `perspective`
instead. That is evidence, and it is honestly weaker evidence: each entry is
`container_kind: "display_folder"` or `"perspective"`, NEVER one of the observed-usage kinds,
and each carries an EMPTY `dashboards` list so `n_dashboards` stays 0. Curation must not be
counted as dashboard placement when `popularity_basis` falls back to BI, and the metric proposal
gate names that case separately as a result. A display folder is not proof a measure is on a
dashboard.

**PARTITIONS MAY NOT BE WAREHOUSE TABLES.** The shared miner records what the M expression
actually says. On an import ("extract") model every partition is a blob or CSV read, so
lineage comes back `object_kind: "file"` with no database and no schema — and that is the
honest answer. It means the BI file cannot supply warehouse table names; those come from the
bounded table set or the query log, never from inferring them here.

TWO TMSL SHAPES THAT BREAK A NAIVE READER, both handled:
  * `expression` is a STRING **or a list of strings** (129 of 186 measures in the model this
    was first written against are lists). A list is newline-joined, never `str()`-ed.
  * `isActive` is OMITTED when true and present only when false, so it defaults to True.
    Reading `r.get("isActive")` as falsy marks every active relationship inactive.
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any


from metrics_agent.plugs.readers import mquery as _mquery  # noqa: E402
from metrics_agent.plugs.readers.powerbi import (  # noqa: E402
    classify_choices, name_physically, relationship_semantics, resolve_entities,
    storage_semantics)
from metrics_agent.plugs.readers.powerbi import calculation_groups as _calculation_groups  # noqa: E402
from metrics_agent.plugs.readers.powerbi import TOOL_ANNOTATIONS as _TOOL_ANNOTATIONS  # noqa: E402
from metrics_agent.plugs.readers.powerbi import model_tags as _model_tags  # noqa: E402
from metrics_agent.plugs.readers.powerbi import field_choices as _powerbi_field_choices  # noqa: E402
from metrics_agent.plugs.readers.powerbi import storage_mode as _powerbi_storage_mode  # noqa: E402

class TabularError(ValueError):
    pass


def _expr(v: Any) -> str:
    """A TMSL expression is a string or a list of lines. Join, never `str()`."""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return "" if v is None else str(v)


def _curation(model: dict) -> list[dict]:
    """The model's OWN grouping of its measures, standing in for a report layer it lacks.

    Every entry here is curation, never observed usage — see the module docstring. The empty
    `dashboards` list is load-bearing, not an oversight: `a2_question_units` computes
    `n_dashboards` from it and the metric proposal gate reads that as the BI popularity substitute.
    """
    usage: list[dict] = []

    folders: dict[str, list[str]] = {}
    for t in model.get("tables") or []:
        for msr in t.get("measures") or []:
            fld = msr.get("displayFolder")
            if fld and msr.get("name"):
                folders.setdefault(fld, []).append(msr["name"])

    for fld, names in sorted(folders.items()):
        # TMSL nests folders with a backslash ("Period To Date\\WTD Measures"); the LAST
        # segment is the folder's own name and the first is the domain hint.
        leaf = fld.split("\\")[-1]
        usage.append({
            "unit_id": f"tmsl::display_folder::{fld}",
            "container": fld,
            "container_kind": "display_folder",
            "dashboards": [],
            "visual_id": fld,
            "visual_type": "display_folder",
            "title": leaf,
            "measures": sorted(set(names)),
            "columns": [],
            "filters": [],
            "sort": None, "top_n": None, "parameters": [],
            "source_fragments": {"widget": None, "dashboard": None,
                                 "prose": [p for p in fld.split("\\") if p]},
            "_aggregations": {},
            "_siblings": [],
        })

    for p in model.get("perspectives") or []:
        names: list[str] = []
        cols: list[str] = []
        for t in p.get("tables") or []:
            names += [m["name"] for m in (t.get("measures") or []) if m.get("name")]
            cols += [c["name"] for c in (t.get("columns") or []) if c.get("name")]
        if not (names or cols):
            continue
        pname = p.get("name") or "(unnamed)"
        usage.append({
            "unit_id": f"tmsl::perspective::{pname}",
            "container": pname,
            "container_kind": "perspective",
            "dashboards": [],
            "visual_id": pname,
            "visual_type": "perspective",
            "title": pname,
            "measures": sorted(set(names)),
            "columns": sorted(set(cols)),
            "filters": [],
            "sort": None, "top_n": None, "parameters": [],
            "source_fragments": {"widget": None, "dashboard": None, "prose": [pname]},
            "_aggregations": {},
            "_siblings": [],
        })
    return usage


def _parameters(model: dict) -> list[dict]:
    """Shared M expressions that are actually PARAMETERS.

    The earlier project recorded every `model.expressions` entry as a parameter. They are not: most
    are shared queries, and the base's contract types a parameter as exactly that. The test is
    the same one `readers/powerbi.py` applies to `expressions.tmdl` — `IsParameterQuery=true` —
    so one definition of "parameter" holds across TMDL and TMSL rather than two that disagree.
    """
    out: list[dict] = []
    for e in model.get("expressions") or []:
        body = _expr(e.get("expression"))
        if "isparameterquery=true" not in body.replace(" ", "").lower():
            continue
        out.append({"name": e.get("name") or "(unnamed)",
                    "value": _mquery.scrub_m(body.split("meta", 1)[0].strip()) or None,
                    "kind": "parameter"})
    return out


def parse(path: pathlib.Path, report_index: dict | None = None,
          _chain: tuple = ()) -> dict[str, Any]:
    """`report_index` is accepted and ignored: a `.bim` has no report layer to join to.

    Entity partitions (Direct Lake; a composite model's tables from another semantic model)
    resolve as in the TMDL reader (`powerbi.resolve_entities`); `_chain` is the cycle check."""
    src = pathlib.Path(path)
    try:
        # utf-8-sig: TMSL written by Visual Studio carries a BOM.
        doc = json.loads(src.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TabularError(f"{src} is not readable TMSL JSON: {type(exc).__name__}") from None
    model = doc.get("model") or doc
    return parse_model(model, str(src), model.get("name") or src.stem, path=src, _chain=_chain)


def parse_model(model: dict, source_file: str, model_name: str, *,
                usage: list[dict] | None = None, path: pathlib.Path | None = None,
                _chain: tuple = ()) -> dict[str, Any]:
    """A TMSL-shaped `model` object -> `bi_facts`. The `.bim` reader's whole body, shared with
    every reader whose source arrives in (or converts to) this shape — a scan result's dataset
    is one — so a model read from either container is read by one piece of code. `usage`
    replaces the model's own curation (display folders, perspectives) when the caller has
    better evidence, or other curation, to offer. `path` is where an upstream model of a
    composite chain is looked for (beside the file); `_chain` is the cycle check."""
    tables: list[dict] = []
    measures: list[dict] = []
    params = _mquery.text_parameters({str(e.get("name")): _expr(e.get("expression"))
                                      for e in model.get("expressions") or []
                                      if e.get("name")})
    for t in model.get("tables") or []:
        tname = t["name"]
        cols = []
        for c in t.get("columns") or []:
            calc = c.get("type") in ("calculated", "calculatedTableColumn")
            col: dict[str, Any] = {
                "name": c["name"],
                "data_type": c.get("dataType"),
                "is_calculated": bool(calc),
                "source_column": str(c.get("sourceColumn") or c["name"]).lower(),
                # The exact spelling: Power Query names columns case-sensitively.
                "_source_exact": str(c.get("sourceColumn") or c["name"]),
            }
            if calc and _expr(c.get("expression")):
                col["formula"] = _expr(c["expression"])
                col["formula_language"] = "dax"
            if c.get("description"):
                col["description"] = c["description"]
            cols.append(col)

        entry: dict[str, Any] = {"name": tname, "columns": cols}
        if t.get("description"):
            entry["description"] = t["description"]
        if t.get("isHidden"):
            entry["is_hidden"] = True
        # The FIRST M partition is the table's source. Many partitions per table is normal on
        # an import model (one per month of CSV); they share a source shape, so the first is
        # representative and 400 near-identical lineages would only bloat the artifact.
        modes = {_powerbi_storage_mode(p.get("mode")) for p in t.get("partitions") or []
                 if p.get("mode")}
        if len(modes) == 1 and None not in modes:
            entry["storage_mode"] = modes.pop()
        for part in t.get("partitions") or []:
            source = part.get("source") or {}
            if source.get("type") == "m":
                entry["source_lineage"] = _mquery.source_lineage(
                    _mquery.substitute_parameters(_expr(source.get("expression")), params))
                entry["_m"] = _expr(source.get("expression"))
                break
            if source.get("type") == "entity" and source.get("entityName"):
                # Direct Lake, or a table of another semantic model (a composite model):
                # resolved below, from the shared expression the partition names.
                entry["_entity"] = {"entity": source["entityName"],
                                    "schema": source.get("schemaName") or None,
                                    "expression": source.get("expressionSource")}
                break
        # The table's hierarchies (each level a column) and, for a calculated table of
        # (label, NAMEOF(field), order) rows, the field switch it is.
        hier = [{"name": str(h["name"]), "levels": [
            {"name": str(lv["name"]), "column": str(lv["column"])}
            for lv in sorted(h.get("levels") or [], key=lambda x: x.get("ordinal", 0))
            if lv.get("name") and lv.get("column")]}
            for h in t.get("hierarchies") or [] if h.get("name")]
        if hier:
            entry["hierarchies"] = hier
        cg = t.get("calculationGroup") or {}
        if cg.get("calculationItems"):
            entry["_calc_items"] = [{"name": str(i.get("name")),
                                     "formula": _expr(i.get("expression")),
                                     "ordinal": i.get("ordinal"),
                                     "format_string": _expr((i.get("formatStringDefinition")
                                                             or {}).get("expression")) or None}
                                    for i in cg["calculationItems"] if i.get("name")]
            if cg.get("precedence") is not None:
                entry["_calc_precedence"] = int(cg["precedence"])
        for part in t.get("partitions") or []:
            source = part.get("source") or {}
            if source.get("type") == "calculated":
                choices = _powerbi_field_choices(_expr(source.get("expression")))
                if choices:
                    entry["field_choices"] = choices
                else:
                    # Its rows as a statement (`daxtables`, from `read_power_query`).
                    entry["_dax_table"] = _expr(source.get("expression"))
        tables.append(name_physically(entry))

        for msr in t.get("measures") or []:
            m: dict[str, Any] = {
                "name": msr["name"],
                "home_table": tname,
                "formula": _expr(msr.get("expression")),
                "formula_language": "dax",
                "aggregation_at_use_site": False,
            }
            if msr.get("description"):
                m["description"] = msr["description"]
            if msr.get("formatString"):
                m["format_string"] = msr["formatString"]
            if msr.get("displayFolder"):
                m["folder"] = msr["displayFolder"]
            # The model's own metadata, kept as tags (TOM: `annotations`, a measure's `kpi`).
            notes_ = [{"name": str(a["name"]), "value": str(a.get("value") or "")}
                      for a in msr.get("annotations") or []
                      if isinstance(a, dict) and a.get("name")
                      and not _TOOL_ANNOTATIONS.match(str(a["name"]))]
            if notes_:
                m["tags"] = {"annotations": notes_}
            kpi = msr.get("kpi") if isinstance(msr.get("kpi"), dict) else None
            if kpi and (kpi.get("targetExpression") or kpi.get("statusExpression")):
                m["kpi"] = {"target": _expr(kpi.get("targetExpression")) or None,
                            "status": _expr(kpi.get("statusExpression")) or None,
                            "status_graphic": kpi.get("statusGraphic") or None}
            measures.append(m)

    relationships = [{
        "from_table": r.get("fromTable"), "from_column": r.get("fromColumn"),
        "to_table": r.get("toTable"), "to_column": r.get("toColumn"),
        # TMSL omits isActive when the relationship IS active.
        "is_active": bool(r.get("isActive", True)),
        "kind": "relationship",
        **({"name": str(r["name"])} if r.get("name") else {}),
        **relationship_semantics(r.get("fromCardinality"), r.get("toCardinality"),
                                 r.get("crossFilteringBehavior"),
                                 r.get("relyOnReferentialIntegrity")),
    } for r in model.get("relationships") or []
        if r.get("fromTable") and r.get("toTable")]

    # A KPI's target and status as measures, and the measures each formula names.
    _model_tags([], tables, measures)
    _calculation_groups(tables)
    storage_semantics(tables, relationships)
    classify_choices(tables)
    expressions = {str(e.get("name")): _expr(e.get("expression"))
                   for e in model.get("expressions") or [] if e.get("name")}
    notes = _mquery.read_power_query(tables, expressions)
    notes += resolve_entities(tables, measures, relationships, expressions,
                              path or pathlib.Path(source_file), _chain)

    out = {
        "contract_version": "1.0",
        "vendor": "powerbi",
        "source_file": source_file,
        "model_name": model_name,
        "tables": _mquery.attach_native_statements(tables),
        "measures": measures,
        "relationships": relationships,
        "usage": _curation(model) if usage is None else usage,
        "parameters": _parameters(model),
        "_unresolved_published": [],
    }
    if notes:
        out["_reader_notes"] = notes
    return out
