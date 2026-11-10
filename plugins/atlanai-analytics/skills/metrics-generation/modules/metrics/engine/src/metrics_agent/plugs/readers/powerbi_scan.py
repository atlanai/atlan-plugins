# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Power BI scanner API output (a saved scan result) -> the `bi_facts` contract.

The admin metadata-scanning API (`POST /admin/workspaces/getInfo`, then
`GET /admin/workspaces/scanResult/{scanId}`) returns, per workspace, the datasets with their
tables, columns, DAX measures, Power Query sources and shared expressions, and the reports and
dashboards bound to them. A saved scan result is one JSON document. This reader turns ONE
dataset of it into `bi_facts`.

THE ASSUMED SHAPE, from Microsoft's documented `WorkspaceInfoResponse` (field names verbatim;
everything not listed is ignored):

    {"workspaces": [{"id", "name",
                     "reports": [{"id", "name", "datasetId", "reportType"}],
                     "datasets": [{"id", "name", "description",
                                   "schemaRetrievalError",
                                   "tables": [{"name", "description", "isHidden",
                                               "columns": [{"name", "dataType", "isHidden",
                                                            "columnType", "expression",
                                                            "sourceColumn", "formatString",
                                                            "description"}],
                                               "measures": [{"name", "expression",
                                                             "description", "formatString",
                                                             "isHidden", "displayFolder"}],
                                               "source": [{"expression"}]}],
                                   "relationships": [{"name", "fromTable", "fromColumn",
                                                      "toTable", "toColumn",
                                                      "crossFilteringBehavior", "isActive",
                                                      "fromCardinality", "toCardinality"}],
                                   "expressions": [{"name", "expression", "description"}]}]}],
     "datasourceInstances": [...]}

`tables`, `relationships` and the expressions are present only when the scan asked for them
(`datasetSchema=true`, `datasetExpressions=true`); without them there is nothing to read and the
dataset is refused by name, saying which option to set. `columnType`, `expression` (on a
calculated column), `sourceColumn`, `displayFolder`, `isActive` and the cardinalities are read
when present and never required: the documented `Column` lists none of them, and real scans
carry some.

HOW. The dataset is converted to the TMSL `model` shape and read by `tabular.parse_model`, the
same code that reads a `.bim`, so one model read from either container is read identically.
Two things differ, and both are said out loud on the run (`_reader_notes`):

* **No report layer.** A scan result lists reports but never their pages or visuals, so there
  are no tiles: no widget titles, no visual number formats, no groupings or filters. Each
  VISIBLE measure becomes one curation unit (`container_kind: model`, no dashboards), titled by
  the measure's own name and carrying the measure's own `formatString` where the scan has one.
* **Cardinality is not in the documented shape.** A relationship the scan states no
  cardinality for is `unknown`, never the TMSL default: an absent key in a `.bim` means the
  default, an absent key here means the API did not say.

ONE DATASET PER RUN. A scan result usually holds many. The BI input's `bi_model` setting names
the one to read, by id or by name; without it a file holding several is refused, listing them.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from metrics_agent.plugs.readers import mquery as _mquery
from metrics_agent.plugs.readers import tabular as _tabular
from metrics_agent.plugs.readers.bicompile import FormulaCompiler

_CALC = {"calculated": "calculated", "calculatedtablecolumn": "calculatedTableColumn"}


class ScanError(ValueError):
    pass


def load_json(path: pathlib.Path) -> Any:
    """The document at `path`, or a refusal naming the file. utf-8-sig: a file saved by a
    Windows tool may carry a BOM."""
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReaderRefused(f"{pathlib.Path(path).name} is not readable JSON "
                            f"({type(exc).__name__}).") from None


def is_scan_result(doc: Any) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("workspaces"), list)
            and all(isinstance(w, dict) for w in doc["workspaces"]))


def datasets_of(doc: dict) -> list[tuple[dict, dict]]:
    """[(workspace, dataset)] for every dataset in the scan, in document order."""
    return [(ws, ds) for ws in doc.get("workspaces") or []
            for ds in ws.get("datasets") or [] if isinstance(ds, dict)]


def _label(ws: dict, ds: dict) -> str:
    return (f"{ds.get('name') or '(unnamed)'} (id {ds.get('id') or '?'}, workspace "
            f"{ws.get('name') or ws.get('id') or '?'})")


def choose(items: list, model: str | None, *, key_id, key_name, what: str, where: str,
           label, setting: str = "bi_model") -> Any:
    """The ONE item `model` names (an id, or a name), or the only item; otherwise a refusal
    that lists every candidate. Never a guess: two items sharing a name are ambiguous."""
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    listing = "\n".join(f"    {label(x)}" for x in items[:50])
    more = f"\n    ... and {len(items) - 50} more" if len(items) > 50 else ""
    if model:
        want = str(model).strip()
        hits = [x for x in items if str(key_id(x) or "").lower() == want.lower()]
        if not hits:
            hits = [x for x in items if str(key_name(x) or "") == want]
        if not hits:
            hits = [x for x in items if str(key_name(x) or "").casefold() == want.casefold()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ReaderRefused(f"{where}: no {what} is named {want!r} (by id or name). It "
                                f"holds:\n{listing}{more}")
        raise ReaderRefused(f"{where}: {len(hits)} {what}s are named {want!r}; name one by "
                            "its id in `" + setting + "`:\n"
                            + "\n".join(f"    {label(x)}" for x in hits))
    if len(items) == 1:
        return items[0]
    if not items:
        raise ReaderRefused(f"{where} holds no {what}.")
    raise ReaderRefused(f"{where} holds {len(items)} {what}s, and a run reads ONE. Name it "
                        f"by id or name in the BI input's `{setting}` setting:\n"
                        f"{listing}{more}")


# What Power BI writes in place of a DAX or Power Query expression when the tenant setting
# "Enhance admin APIs responses with DAX and mashup expressions" is off (seen live): the
# expression is withheld, never a formula.
WITHHELD = {"not available"}


def _text(v: Any) -> str:
    """A scan expression is a string; a TMSL-style list of lines is joined, never `str()`-ed.
    A withheld expression is empty."""
    s = "\n".join(str(x) for x in v) if isinstance(v, list) else ("" if v is None else str(v))
    return "" if s.strip().lower() in WITHHELD else s


def _withheld(v: Any) -> bool:
    return isinstance(v, str) and v.strip().lower() in WITHHELD


# A Power Query expression starts with `let`, or is a single call to a namespaced function
# (`Sql.Database(...)`); a calculated table's source is DAX (`CALENDAR(...)`, `SUMMARIZE(...)`),
# which names no namespace.
_M_START = re.compile(r"^\s*(let\b|#\"|[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_.]+\s*\()", re.I)


def is_m(expr: str) -> bool:
    return bool(_M_START.match(_mquery.strip_m_comments(expr or "")))


def to_tmsl(ds: dict) -> tuple[dict, list[str]]:
    """One scan dataset -> a TMSL `model` object, and the notes on what it could not carry."""
    notes: list[str] = []
    shared = {str(e.get("name")): _text(e.get("expression"))
              for e in ds.get("expressions") or [] if isinstance(e, dict) and e.get("name")}
    tables, not_m = [], []
    for t in ds.get("tables") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        cols = []
        for c in t.get("columns") or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            ctype = str(c.get("columnType") or "").lower()
            # The engine's own row-number column: hidden, never a field anyone chose.
            if ctype == "rownumber" or str(c["name"]).startswith("RowNumber-"):
                continue
            col = {"name": c["name"], "dataType": c.get("dataType")}
            for k in ("sourceColumn", "description", "isHidden", "formatString"):
                if c.get(k) not in (None, ""):
                    col[k] = c[k]
            if ctype in _CALC:
                col["type"] = _CALC[ctype]
                if _text(c.get("expression")):
                    col["expression"] = _text(c["expression"])
            cols.append(col)
        entry: dict[str, Any] = {"name": t["name"], "columns": cols,
                                 "measures": [], "partitions": []}
        for k in ("description", "isHidden"):
            if t.get(k):
                entry[k] = t[k]
        for msr in t.get("measures") or []:
            if not isinstance(msr, dict) or not msr.get("name"):
                continue
            entry["measures"].append({k: msr[k] for k in (
                "name", "expression", "description", "formatString", "displayFolder",
                "isHidden") if msr.get(k) not in (None, "")
                and not (k == "expression" and _withheld(msr.get(k)))})
        for s in t.get("source") or []:
            expr = _text((s or {}).get("expression"))
            if not expr.strip():
                continue
            if is_m(expr):
                entry["partitions"].append({"name": "source", "source": {
                    # A table read through a shared query (`Source = #"Staging"`) keeps its
                    # own M: the reference, and every step after it, is read with the model's
                    # expressions by `mquery.read_power_query`, as in every other container.
                    "type": "m", "expression": expr}})
                break
            # A DAX calculated table: its rows are a statement (`daxtables`), read like a
            # .bim's calculated partition.
            entry["partitions"].append({"name": "source", "source": {
                "type": "calculated", "expression": expr}})
            not_m.append(t["name"])
            break
        tables.append(entry)
    if not_m:
        notes.append(f"{len(not_m)} table(s) have a source that is not Power Query (a DAX "
                     f"calculated table): {', '.join(sorted(not_m)[:8])}. Each is read as "
                     "the statement of its rows where its DAX is one the reader reads.")
    rels, no_card = [], 0
    for r in ds.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        rel = {k: r[k] for k in ("name", "fromTable", "fromColumn", "toTable", "toColumn",
                                 "crossFilteringBehavior", "isActive", "fromCardinality",
                                 "toCardinality", "relyOnReferentialIntegrity") if k in r}
        if "fromCardinality" not in r and "toCardinality" not in r:
            no_card += 1
        rels.append(rel)
    model = {"name": ds.get("name"), "tables": tables, "relationships": rels,
             "expressions": [{"name": k, "expression": v} for k, v in shared.items()]}
    return model, notes + ([f"{no_card} relationship(s) carry no cardinality in the scan "
                            "result; recorded as unknown."] if no_card else [])


def _measure_units(ds_name: str, facts: dict) -> tuple[list[dict], int]:
    """One CURATION unit per visible measure: what a model with no report layer and no grouping
    of its own can honestly offer. `dashboards` is empty, so it never counts as placement."""
    units, hidden = [], 0
    hidden_names = facts.pop("_hidden_measures", set())
    for m in facts["measures"]:
        if m["name"] in hidden_names:
            hidden += 1
            continue
        home = m.get("home_table") or ""
        units.append({
            "unit_id": f"scan::measure::{home}::{m['name']}",
            "container": home or ds_name,
            "container_kind": "model",
            "dashboards": [],
            "visual_id": m["name"],
            "visual_type": "measure",
            "title": m["name"],
            "measures": [m["name"]],
            "columns": [], "filters": [],
            "sort": None, "top_n": None, "parameters": [],
            "number_format": m.get("format_string"),
            "source_fragments": {"widget": None, "dashboard": None,
                                 "prose": [p for p in (ds_name, home) if p]},
            "_aggregations": {},
            "_siblings": [],
        })
    return units, hidden


def dataset_facts(ws: dict, ds: dict, source_file: str, *, where: str) -> dict[str, Any]:
    """`bi_facts` for one scan dataset, with no usage: the caller says what the units are."""
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    label = _label(ws, ds)
    if not ds.get("tables"):
        why = (f" The scan reports: {ds['schemaRetrievalError']}"
               if ds.get("schemaRetrievalError") else "")
        raise ReaderRefused(
            f"{where}: dataset {label} carries no tables, so there is no model to read.{why}\n"
            "  Re-run the scan with datasetSchema=true and datasetExpressions=true "
            "(POST /admin/workspaces/getInfo?datasetSchema=true&datasetExpressions=true), "
            "then save GET /admin/workspaces/scanResult/{scanId}.")
    model, notes = to_tmsl(ds)
    measures = [m for t in model["tables"] for m in t["measures"]]
    if measures and not any(m.get("expression") for m in measures):
        withheld = any(_withheld(m.get("expression"))
                       for t in ds.get("tables") or [] for m in t.get("measures") or [])
        raise ReaderRefused(
            f"{where}: dataset {label} lists {len(measures)} measure(s) with no DAX "
            "expression. " + (
                "Power BI withheld them (\"Not available\"): the tenant setting \"Enhance "
                "admin APIs responses with DAX and mashup expressions\" (Admin portal → Tenant "
                "settings → Admin API settings) is off, or on only for security groups this "
                "identity is not in (seen live). A Fabric administrator turns it on for a group "
                "holding the identity that ran the scan, then the scan is run again."
                if withheld else
                "The scan was run without datasetExpressions=true; re-run it with it."))
    hidden = {m["name"] for m in measures if m.get("isHidden")}
    facts = _tabular.parse_model(model, source_file, str(ds.get("name") or ds.get("id")),
                                 usage=[])
    for rel, src in zip(facts["relationships"],
                        [r for r in model["relationships"]
                         if r.get("fromTable") and r.get("toTable")]):
        if "fromCardinality" not in src and "toCardinality" not in src:
            rel["cardinality"] = "unknown"
    facts["assets"] = [{"role": "semantic_model", "name": ds.get("name") or None,
                        "vendor_id": ds.get("id") or None, "path": None,
                        "site": ws.get("id") or None,
                        "catalog_id": None, "catalog_qualified_name": None}]
    # The dataset's endorsement and description, as the scan states them (never who endorsed).
    level = str(((ds.get("endorsementDetails") or {}).get("endorsement") or "")).lower()
    if level in ("certified", "promoted"):
        facts["assets"][0]["endorsement"] = level
    if ds.get("description"):
        facts["assets"][0]["description"] = str(ds["description"])
    facts["_reader_notes"] = notes
    facts["_hidden_measures"] = hidden
    return facts


def scan_file(path: pathlib.Path) -> pathlib.Path:
    """`path` itself, or the ONE scan result in a folder."""
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    if not path.is_dir():
        return path
    found = []
    for f in sorted(path.rglob("*.json")):
        try:
            if is_scan_result(json.loads(f.read_text(encoding="utf-8-sig"))):
                found.append(f)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    if len(found) == 1:
        return found[0]
    raise ReaderRefused(f"{path.name} holds {len(found)} scan result file(s); give the ONE "
                        "saved scan result" + (":\n" + "\n".join(f"    {f.name}" for f in found)
                                               if found else "."))


def parse(path: pathlib.Path, model: str | None = None) -> dict[str, Any]:
    path = scan_file(pathlib.Path(path))
    doc = load_json(path)
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    if not is_scan_result(doc):
        raise ReaderRefused(f"{path.name} is not a Power BI scan result: it has no "
                            "`workspaces` list (save the body of GET "
                            "/admin/workspaces/scanResult/{scanId}).")
    ws, ds = choose(datasets_of(doc), model, key_id=lambda x: x[1].get("id"),
                    key_name=lambda x: x[1].get("name"), what="dataset", where=path.name,
                    label=lambda x: _label(*x))
    facts = dataset_facts(ws, ds, str(path), where=path.name)
    units, hidden = _measure_units(facts["model_name"], facts)
    facts["usage"] = units
    facts["_reader_notes"] = [
        f"scan result: read dataset {_label(ws, ds)}. A scan result has no report pages or "
        f"visuals, so there are NO tiles: each of the {len(units)} visible measure(s) is one "
        "curation unit titled by the measure's own name. Widget titles and visual number "
        "formats are ABSENT (a measure's own formatString is kept where the scan has one)."
        + (f" {hidden} hidden measure(s) get no unit." if hidden else ""),
        *facts["_reader_notes"]]
    facts["vendor"] = PowerBIScanReader.name
    return facts


class PowerBIScanReader(FormulaCompiler):
    """The `powerbi-scan` BI reader (`sockets.BIReader`): one dataset of a saved Power BI
    scan result. Its formulas are DAX and Power Query, compiled by the shared engine exactly
    as the `powerbi` reader's are."""

    name = "powerbi-scan"
    languages = ("dax", "m-query")
    suffixes = (".json",)

    def detect(self, path: pathlib.Path) -> bool:
        path = pathlib.Path(path)
        if not path.is_file() or path.suffix.lower() != ".json":
            return False
        try:
            return is_scan_result(json.loads(path.read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeDecodeError, ValueError):
            return False

    def form(self, path: pathlib.Path) -> str:
        from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

        path = pathlib.Path(path)
        if not path.is_dir() and path.suffix.lower() != ".json":
            raise ReaderRefused(f"the powerbi-scan reader reads ONE saved scan result (.json), "
                                f"or a folder holding one; {path.name} is not one.")
        return "scan-result"

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        # A table whose Power Query changes its rows is read as a statement in the run's
        # dialect (`mquery.read_power_query`).
        with _mquery.run_dialect(opts.get("dialect")):
            return parse(pathlib.Path(path), model=opts.get("model"))
