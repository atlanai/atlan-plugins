# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Looker (LookML) parser plugin -> the vendor-neutral `bi_facts` contract.

PROVENANCE. A COPY of the LookML parser of an earlier project (rule 6: copied, never
imported), rewritten onto this repo's contract: the field names, the usage entry and the
lineage block are `bi_facts`' own. Deliberately left behind: the earlier project's `filters`
top-level key, which `bi_facts.schema.json` does not carry, and its bare `parameters: []`, which
this repo populates only for Power BI.

STATUS: NOT EXERCISED. It is present, it imports cleanly and it parses the shapes below; it is
not a supported BI source yet:

  * there is no LookML vocabulary in `bi_compiler_rules.yml` and no LookML row in
    `compiler_corpus/`. A LookML measure is not DAX and not a Tableau pill, so compiling it
    through another tool's vocabulary would emit plausible-wrong SQL, the worst failure this
    pipeline has, because it looks right;
  * it is therefore not a registered reader, and `bi_vendor` in run.yml cannot name it.
    Reaching this parser is a deliberate act, not something a run can wander into.

Making it supported means registering it as a reader, with its own compiler rows and corpus
entries in the same change.

Parses a LookML repo (or one `.lkml` file):
  views      -> tables (+ `sql_table_name` lineage, dimensions -> columns)
  measures   -> measures (`formula_language: lookml`)
  explores   -> relationships (joined on `sql_on`)
  `*.dashboard.lookml` -> usage
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

# The `${view.field}` reference form, which is how LookML names a column anywhere it is not
# declaring one.
_REF = re.compile(r"\$\{([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\}")
# `${TABLE}.col` — a dimension that is just the physical column, not a calculation.
_PASSTHROUGH = re.compile(r"^\s*\$\{TABLE\}\.(?P<col>[A-Za-z0-9_\"`\[\]]+)\s*$")


class LookMLError(ValueError):
    pass


def _lineage(view: dict) -> dict[str, Any]:
    """`sql_table_name` -> the base's `source_lineage` block.

    A derived table has no warehouse object to name, so it is recorded as `derived` with its
    SQL as the expression rather than being given a fabricated object name.
    """
    stn = (view.get("sql_table_name") or "").strip()
    derived = view.get("derived_table") or {}
    parts = [p.strip("`\"[] ") for p in stn.split(".") if p.strip()]
    return {
        "connection": None,
        "database": parts[0] if len(parts) == 3 else None,
        "schema": parts[-2] if len(parts) >= 2 else None,
        "object": parts[-1].lower() if parts else None,
        "object_kind": "table" if stn else ("derived" if derived else None),
        "raw_expression": (derived.get("sql") if derived else stn) or None,
        "published_datasource": None,
    }


def _columns(view: dict) -> list[dict]:
    out: list[dict] = []
    for kind in ("dimensions", "dimension_groups"):
        for d in view.get(kind) or []:
            sql = (d.get("sql") or "").strip()
            m = _PASSTHROUGH.match(sql)
            col = {
                "name": d["name"],
                "data_type": d.get("type"),
                # A dimension whose SQL is just `${TABLE}.col` is the column itself; anything
                # else is a calculation, and calling it a plain column would lose that.
                "is_calculated": bool(sql) and m is None,
                "formula": sql or None,
                "formula_language": "lookml" if sql else None,
                "source_column": (m.group("col").strip("`\"[]").lower() if m
                                  else d["name"].lower()),
                "role": "dimension",
                "caption": d.get("label"),
            }
            # `description` is typed as a string on the contract, not a nullable one, so an
            # absent one is OMITTED rather than written as null.
            if d.get("description"):
                col["description"] = d["description"]
            out.append(col)
    return out


def _is_dashboard(path: pathlib.Path) -> bool:
    """LookML dashboards are `*.dashboard.lookml` — and they are YAML documents."""
    return path.name.endswith(".dashboard.lookml")


def _load_dashboards(path: pathlib.Path) -> list[dict]:
    """A `*.dashboard.lookml` file -> its dashboard definitions.

    `yaml.safe_load` deliberately: these are customer-authored files and are data, never
    instruction (guardrail 9). `safe_load` cannot construct arbitrary Python objects, which
    is the only thing standing between a hostile dashboard file and code execution.
    """
    import yaml  # noqa: PLC0415 — lazy, beside the lkml import

    doc = yaml.safe_load(path.read_text(errors="ignore"))
    if isinstance(doc, dict):
        doc = [doc]
    return [d for d in (doc or []) if isinstance(d, dict)]


def parse(path: pathlib.Path, report_index: dict | None = None) -> dict[str, Any]:
    """`report_index` is accepted and ignored: it is the Power BI crawler-extract join, and
    LookML has no equivalent. Kept so A1 can call every parser the same way."""
    import lkml  # noqa: PLC0415 — lazy: the offline suite must not need the dependency

    root = pathlib.Path(path)
    if root.is_file():
        files = [root]
    else:
        # BOTH extensions, and this is a bug fix rather than a port detail. The earlier project globs
        # `*.lkml` and then asks whether a file ends in `.dashboard.lookml` — which the glob can
        # never have found, because that name ends in `.lookml`. So the earlier project's LookML
        # dashboards produced NO usage at all, silently. Caught here by a test with a dashboard
        # in it.
        files = sorted(set(root.rglob("*.lkml")) | set(root.rglob("*.lookml")))
    if not files:
        raise LookMLError(
            f"no .lkml or .lookml files under {root}. A LookML input is a repository of "
            "those files, or one such file.")

    tables: list[dict] = []
    measures: list[dict] = []
    relationships: list[dict] = []
    usage: list[dict] = []
    unreadable: list[str] = []

    for f in files:
        # A LookML DASHBOARD is YAML, not LookML. `lkml.load` raises on it outright, which is
        # the second half of why the earlier project's dashboard path produced nothing: even had its glob
        # found these files, its parser could not read one. Both formats are handled here so
        # that the "dashboards -> usage" line in this module's docstring is actually true.
        try:
            if _is_dashboard(f):
                doc = {"dashboards": _load_dashboards(f)}
            else:
                doc = lkml.load(f.read_text(errors="ignore")) or {}
        except Exception:                                        # noqa: BLE001
            # Named, not swallowed: a file that will not parse is reported by the caller
            # rather than silently reducing the model.
            unreadable.append(str(f))
            continue

        for view in doc.get("views") or []:
            vname = view["name"]
            table = {"name": vname, "columns": _columns(view),
                     "source_lineage": _lineage(view)}
            if view.get("description"):
                table["description"] = view["description"]
            tables.append(table)
            for m in view.get("measures") or []:
                meas = {
                    "name": m["name"],
                    "home_table": vname,
                    "formula": f"type: {m.get('type', '')}; sql: {m.get('sql', '')}".strip(),
                    "formula_language": "lookml",
                    # A LookML measure declares its own aggregation (`type: sum`), so it is
                    # a closure like a DAX measure, not a Tableau pill.
                    "aggregation_at_use_site": False,
                }
                if m.get("description"):
                    meas["description"] = m["description"]
                measures.append(meas)

        for exp in doc.get("explores") or []:
            for j in exp.get("joins") or []:
                pair = _REF.findall(j.get("sql_on") or "")
                if len(pair) >= 2:
                    relationships.append({
                        "from_table": pair[0][0], "from_column": pair[0][1],
                        "to_table": pair[1][0], "to_column": pair[1][1],
                        "is_active": True,
                        "cardinality": j.get("relationship") or "unknown",
                    })

        for dash in (doc.get("dashboards") or []):
            dname = dash.get("title") or dash.get("dashboard") or f.stem
            elements = dash.get("elements") or []
            siblings = [e.get("title") or e.get("name") or f"tile-{i}"
                        for i, e in enumerate(elements)]
            for ei, el in enumerate(elements):
                flds = el.get("fields") or []
                if isinstance(flds, str):
                    flds = [flds]
                title = el.get("title") or el.get("name") or f"tile-{ei}"
                usage.append({
                    "unit_id": f"lookml::{dname}::{title}",
                    "container": dname,
                    "container_kind": "dashboard",
                    "dashboards": [{"name": dname, "title": dname, "position": ei}],
                    "visual_id": str(el.get("name") or title),
                    "visual_type": str(el.get("type") or "tile"),
                    "title": str(title),
                    # LookML names a field `view.field` and does not say which are
                    # measures. Resolved against the measures actually declared above
                    # rather than guessed from the name.
                    "measures": sorted({x.split(".")[-1] for x in flds}),
                    "columns": [],
                    "filters": [],
                    "sort": None,
                    "top_n": el.get("limit"),
                    "parameters": [],
                    "source_fragments": {"widget": None, "dashboard": None,
                                         "prose": [str(x) for x in (title, dname) if x]},
                    "_aggregations": {},
                    "_siblings": siblings,
                })

    # A field named on a tile is a measure only if a view declared it as one; the rest are
    # dimensions. Doing this after the whole repo is read is what makes it possible at all —
    # a dashboard file routinely references views defined in another file.
    declared = {m["name"] for m in measures}
    for u in usage:
        named = u["measures"]
        u["measures"] = sorted(n for n in named if n in declared)
        u["columns"] = sorted(n for n in named if n not in declared)

    return {
        "contract_version": "1.0",
        "vendor": "lookml",
        "source_file": str(root),
        "model_name": root.stem if root.is_file() else root.name,
        "tables": tables,
        "measures": measures,
        "relationships": relationships,
        "usage": usage,
        "parameters": [],
        "_unresolved_published": unreadable,
    }
