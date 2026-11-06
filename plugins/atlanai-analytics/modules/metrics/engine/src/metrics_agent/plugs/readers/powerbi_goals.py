# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Power BI scorecards and goals (metrics) -> the `bi_facts` contract.

A scorecard lists goals. A goal's current value (and its target) is either typed in by hand or
CONNECTED to a data point of a report visual, and so to a measure of the dataset behind that
report. A connected goal is a person saying "this measure is the number we track, and this is
its name": the measure is the metric, the goal's title is the metric's name. This reader turns
a saved scorecard into one question unit per connected goal, over the connected dataset's own
definitions.

THE SHAPE, as the service returns it for `GET /groups/{groupId}/scorecards({scorecardId})
?$expand=goals` (checked against a live response; fields not listed are ignored):

    {"id", "name", "description", "groupId", "datasetId", "reportId",
     "goals": [{"id", "name", "description", "parentId", "level", "rank",
                "valuesFormatString", "goalValues": [{"timestamp", "value", "target"}] | null,
                "valueConnection": null | {
                    "type": "Current", "datasetId": "<the connected dataset>",
                    "reportUrl": "/groups/<ws>/reports/<report>?bookmarkGuid=<...>",
                    "query": "<a JSON STRING: {Commands: [{SemanticQueryDataShapeCommand:
                              {Query: {From: [{Name: alias, Entity: <model table>}],
                                       Select: [{Measure: {Expression: {SourceRef:
                                                 {Source: alias}}, Property: <measure>}}],
                                       Where: [{Condition: ...}]}}}]}>"},
                "targetConnection": null | {the same shape}}]}

`{"value": [scorecard, ...]}` (Scorecards - Get) and a bare list of scorecards are read the
same. The scorecard's own `datasetId` and `reportId` are its INTERNAL ones, never the
connected dataset. Microsoft's REST reference does not document `valueConnection` at all, so
this reader takes only what it needs from it and treats every part as optional; a connection
may also name its measure directly (`measure` / `measureName`, read as well).

The connected data point's `Where` conditions are the goal's filters: the goal's number is the
measure UNDER them, so they become the unit's filters (in, not in, comparisons, as the
`powerbi` reader reads a visual's), and a goal whose condition cannot be read is refused by
name rather than read without it.

A goal with no value connection is MANUAL: it produces nothing and is counted. A connection
that names no dataset or no measure, or a measure the dataset does not define, is counted by
reason too. Nothing is guessed.

THE CONNECTED DATASET'S DEFINITIONS are what make a goal compilable: the measure's DAX and the
tables it reads. They are read from files BESIDE the scorecard JSON: a saved scan result that
holds the dataset (matched by its id), or a `.bim`/`.tmsl` whose file name, `id` or `name` is
the dataset id. Without them the scorecard is refused, naming the dataset to supply.

ONE SCORECARD, ONE DATASET PER RUN. The BI input's `bi_model` setting names the scorecard (id or
name) when the file holds several, or the connected dataset (id or name) when the goals connect
to several; ambiguity is refused with the candidates listed.
"""
from __future__ import annotations

import pathlib
from typing import Any

from metrics_agent.plugs.readers import powerbi as _powerbi
from metrics_agent.plugs.readers import powerbi_scan as _scan
from metrics_agent.plugs.readers import tabular as _tabular
from metrics_agent.plugs.readers.bicompile import FormulaCompiler

_VALUE_KEYS = ("valueConnection", "currentValueConnection")
_TARGET_KEYS = ("targetConnection", "targetValueConnection")


def scorecards_of(doc: Any) -> list[dict]:
    """The scorecards a saved response holds: one object, `{"value": [...]}`, or a list."""
    if isinstance(doc, dict) and isinstance(doc.get("goals"), list):
        return [doc]
    items = doc.get("value") if isinstance(doc, dict) else doc
    if isinstance(items, list) and items and all(
            isinstance(x, dict) and isinstance(x.get("goals"), list) for x in items):
        return items
    return []


def _connection(goal: dict, keys: tuple[str, ...]) -> dict | None:
    for k in keys:
        if isinstance(goal.get(k), dict) and goal[k]:
            return goal[k]
    return None


def _semantic_query(conn: dict) -> dict | None:
    """The `SemanticQueryDataShapeCommand.Query` a connection carries, from its `query` (a
    JSON STRING in the service's response, or already an object), or None."""
    import json  # noqa: PLC0415

    raw = conn.get("query")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    for cmd in raw.get("Commands") or [raw]:
        q = ((cmd or {}).get("SemanticQueryDataShapeCommand") or {}).get("Query") \
            if isinstance(cmd, dict) else None
        if isinstance(q, dict):
            return q
    return raw.get("Query") if isinstance(raw.get("Query"), dict) else None


def _entity(node: dict, aliases: dict[str, str]) -> str | None:
    ref = ((node.get("Expression") or {}).get("SourceRef") or {})
    return ref.get("Entity") or aliases.get(str(ref.get("Source") or ""))


def _column_of(cond: dict) -> str | None:
    """The model column a semantic-query condition restricts."""
    if "Not" in cond:
        return _column_of((cond["Not"] or {}).get("Expression") or {})
    if "In" in cond:
        exprs = (cond["In"] or {}).get("Expressions") or []
        col = (exprs[0] or {}).get("Column") if len(exprs) == 1 else None
        return (col or {}).get("Property")
    if "Comparison" in cond:
        return (((cond["Comparison"] or {}).get("Left") or {}).get("Column") or {}).get(
            "Property")
    if "And" in cond:
        left = _column_of((cond["And"] or {}).get("Left") or {})
        right = _column_of((cond["And"] or {}).get("Right") or {})
        return left if left and left == right else None
    return None


def _where(query: dict) -> tuple[list[dict], str | None]:
    """The visual data point's own filters (its `Where`) in the common filter shape. A goal's
    number is the measure UNDER these filters, so one that cannot be read refuses the goal
    (named) rather than be dropped: dropped, it would compute a different number."""
    from metrics_agent.plugs.readers.powerbi import _condition  # noqa: PLC0415

    out = []
    for w in query.get("Where") or []:
        cond = (w or {}).get("Condition") or {}
        col = _column_of(cond)
        op, value = _condition(cond)
        if not col or value is None:
            return [], "a filter on the connected data point that has no column and value"
        out.append({"scope": "visual", "stage": "dimension", "column": str(col).lower(),
                    "op": op, "value": value,
                    "evidence": "powerbi goal connection filter"})
    return out, None


def measure_of(conn: dict) -> tuple[str | None, str | None, list[dict], str | None]:
    """(table, measure name, filters, problem) a connection names.

    The service's shape: `query` is a JSON string of a `SemanticQueryDataShapeCommand` whose
    `From[]` maps an alias to the model table (`Entity`) and whose `Select[]` holds the
    measure (`Measure.Property`). Also read: `measure` / `measureName` given directly."""
    for k in ("measure", "measureName"):
        v = conn.get(k)
        if isinstance(v, str) and v.strip():
            return None, v.strip(), [], None
        if isinstance(v, dict) and (v.get("name") or v.get("property")):
            return (v.get("table") or v.get("entity"),
                    str(v.get("name") or v.get("property")), [], None)
    query = _semantic_query(conn)
    if query is None:
        return None, None, [], "no query naming the measure"
    aliases = {str(f.get("Name")): f.get("Entity") for f in query.get("From") or []
               if isinstance(f, dict) and f.get("Name")}
    found = [(_entity(sel["Measure"], aliases), str(sel["Measure"]["Property"]))
             for sel in query.get("Select") or []
             if isinstance(sel, dict) and isinstance(sel.get("Measure"), dict)
             and sel["Measure"].get("Property")]
    if len(found) != 1:
        other = [sel for sel in query.get("Select") or [] if isinstance(sel, dict)
                 and ("Aggregation" in sel or "Column" in sel)]
        why = (f"{len(found)} measures in the connected data point" if found else
               "an aggregated column, not a measure" if other else "no measure")
        return None, None, [], why
    filters, problem = _where(query)
    if problem:
        return None, None, [], problem
    return found[0][0], found[0][1], filters, None


def _definitions(folder: pathlib.Path, dataset_id: str, skip: pathlib.Path) -> dict | None:
    """The connected dataset's `bi_facts` (no usage), from a file beside the scorecard."""
    want = dataset_id.lower()
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.resolve() == skip.resolve():
            continue
        suffix = f.suffix.lower()
        if suffix == ".json":
            try:
                doc = _scan.load_json(f)
            except Exception:  # noqa: BLE001 - an unreadable neighbour is not ours to judge
                continue
            if not _scan.is_scan_result(doc):
                continue
            for ws, ds in _scan.datasets_of(doc):
                if str(ds.get("id") or "").lower() == want:
                    return _scan.dataset_facts(ws, ds, str(f), where=f.name)
        elif suffix in (".bim", ".tmsl"):
            try:
                doc = _scan.load_json(f)
            except Exception:  # noqa: BLE001
                continue
            ids = {f.stem.lower(), str(doc.get("id") or "").lower(),
                   str(doc.get("name") or "").lower()}
            if want in ids:
                facts = _tabular.parse(f)
                facts["usage"] = []
                facts["_reader_notes"] = []
                return facts
    # A semantic model as the Fabric fetch writes it (`semanticModels/<workspace>/<id>/` with
    # its TMDL under `definition/`), beside the scorecard or under an extract beside it.
    for d in sorted(folder.glob("*")) + sorted(folder.glob("semanticModels/*/*")):
        if d.is_dir() and d.name.lower() == want and (d / "definition").is_dir():
            facts = _powerbi.parse(d)
            facts["usage"] = []
            facts["_reader_notes"] = []
            return facts
    return None


def _latest(values: list, key: str) -> Any:
    rows = [v for v in values or [] if isinstance(v, dict) and v.get(key) is not None]
    rows.sort(key=lambda v: str(v.get("timestamp") or ""))
    return rows[-1][key] if rows else None


def _scorecard_file(path: pathlib.Path) -> pathlib.Path:
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    if path.is_file():
        return path
    found = []
    for f in sorted(path.rglob("*.json")):
        try:
            if scorecards_of(_scan.load_json(f)):
                found.append(f)
        except Exception:  # noqa: BLE001
            continue
    if len(found) == 1:
        return found[0]
    raise ReaderRefused(f"{path.name} holds {len(found)} scorecard file(s); give the ONE "
                        "scorecard JSON (its connected dataset's definitions beside it)"
                        + (":\n" + "\n".join(f"    {f.name}" for f in found) if found else "."))


def parse(path: pathlib.Path, model: str | None = None) -> dict[str, Any]:
    from metrics_agent.sockets import ReaderRefused  # noqa: PLC0415

    path = _scorecard_file(pathlib.Path(path))
    cards = scorecards_of(_scan.load_json(path))
    if not cards:
        raise ReaderRefused(f"{path.name} is not a saved scorecard: no `goals` list (save "
                            "GET /groups/{groupId}/scorecards({scorecardId})?$expand=goals,"
                            "goalValues).")
    card_ids = {str(c.get("id") or "").lower() for c in cards} | {
        str(c.get("name") or "") for c in cards}
    pick_card = model if (model and (str(model).lower() in card_ids or model in card_ids)) \
        else None
    card = _scan.choose(cards, pick_card, key_id=lambda c: c.get("id"),
                        key_name=lambda c: c.get("name"), what="scorecard", where=path.name,
                        label=lambda c: f"{c.get('name') or '(unnamed)'} (id {c.get('id')})")
    card_name = str(card.get("name") or card.get("id") or path.stem)
    goals = [g for g in card.get("goals") or [] if isinstance(g, dict)]

    manual, no_dataset, no_measure = [], [], []
    connected: list[tuple[dict, dict, str, str | None, str]] = []
    for g in goals:
        conn = _connection(g, _VALUE_KEYS)
        title = str(g.get("name") or g.get("id") or "(unnamed goal)")
        if conn is None:
            manual.append(title)
            continue
        ds_id = str(conn.get("datasetId") or "").strip()
        if not ds_id:
            no_dataset.append(title)
            continue
        table, measure, filters, problem = measure_of(conn)
        if not measure:
            no_measure.append(f"{title} ({problem})")
            continue
        connected.append((g, conn, ds_id, table, measure, filters))
    if not connected:
        raise ReaderRefused(
            f"{path.name}: scorecard {card_name!r} has no goal whose value is connected to a "
            f"measure ({len(goals)} goal(s): {len(manual)} manual, {len(no_dataset)} name no "
            f"dataset, {len(no_measure)} name no measure). A manual goal produces nothing.")

    datasets = sorted({c[2] for c in connected}, key=str.lower)
    pick_ds = None if pick_card else model
    ds_id = _scan.choose(datasets, pick_ds, key_id=lambda d: d, key_name=lambda d: d,
                         what="connected dataset", where=f"{path.name} ({card_name})",
                         label=lambda d: f"{d} ({sum(1 for c in connected if c[2] == d)} "
                                         "goal(s))")
    facts = _definitions(path.parent, ds_id, path)
    if facts is None:
        raise ReaderRefused(
            f"{path.name}: {sum(1 for c in connected if c[2] == ds_id)} goal(s) of scorecard "
            f"{card_name!r} are connected to dataset {ds_id}, and its definitions are not "
            "beside the scorecard. Put one of these in the same folder:\n"
            f"    a saved scan result that holds dataset {ds_id} "
            "(GET /admin/workspaces/scanResult/{scanId}, scanned with datasetSchema=true and "
            "datasetExpressions=true)\n"
            f"    the dataset's model as {ds_id}.bim (Tabular JSON, e.g. scripted over XMLA)\n"
            f"    the dataset's TMDL folder as the Fabric fetch writes it "
            f"(semanticModels/<workspace>/{ds_id}/)")
    other = [c for c in connected if c[2] != ds_id]
    defined = {m["name"].lower(): m["name"] for m in facts["measures"]}
    facts.pop("_hidden_measures", None)
    notes = list(facts.pop("_reader_notes", []))

    usage, undefined = [], []
    for g, conn, _ds, table, measure, filters in (c for c in connected if c[2] == ds_id):
        title = str(g.get("name") or g.get("id"))
        name = defined.get(measure.lower())
        if name is None:
            undefined.append(f"{title} -> {measure}")
            continue
        refs = []
        tconn = _connection(g, _TARGET_KEYS)
        if tconn is not None:
            _tt, tmeasure, _tf, _tp = measure_of(tconn)
            if tmeasure:
                refs.append({"label": "target", "measure": defined.get(tmeasure.lower(),
                                                                       tmeasure),
                             "kind": "goal-target"})
        else:
            target = _latest(g.get("goalValues") or [], "target")
            if target is not None:
                refs.append({"label": "target", "value": str(target), "kind": "goal-target"})
        visual = conn.get("visualName") or conn.get("visualId")
        usage.append({
            "unit_id": f"goal::{card.get('id') or card_name}::{g.get('id') or title}",
            "container": card_name,
            "container_kind": "scorecard",
            "dashboards": [{"name": card_name, "title": card_name,
                            **({"position": int(g["rank"])}
                               if isinstance(g.get("rank"), int) else {})}],
            "visual_id": str(g.get("id") or title),
            "visual_type": "goal",
            "title": title,
            "measures": [name],
            "columns": [], "filters": filters,
            "sort": None, "top_n": None, "parameters": [],
            "description": g.get("description") or None,
            "number_format": g.get("valuesFormatString") or None,
            "reference_lines": refs,
            "source_fragments": {"widget": None, "dashboard": None,
                                 "prose": [p for p in (card.get("description"), card_name,
                                                       str(visual) if visual else None) if p]},
            "_aggregations": {},
            "_siblings": [],
        })
    if not usage:
        raise ReaderRefused(f"{path.name}: no connected goal names a measure dataset {ds_id} "
                            f"defines: {'; '.join(undefined[:10])}.")
    notes.insert(0, f"scorecard {card_name!r}: {len(usage)} connected goal(s) read over "
                    f"dataset {ds_id}, each titled by the goal's name; {len(manual)} manual "
                    "goal(s) produce nothing.")
    for what, items in (("connect no dataset", no_dataset), ("name no measure", no_measure),
                        ("name a measure the dataset does not define", undefined),
                        ("are connected to another dataset (one dataset per run; name it in "
                         "bi_model to read those)", [str(c[0].get("name")) for c in other])):
        if items:
            notes.append(f"{len(items)} goal(s) {what}: {'; '.join(items[:10])}.")
    facts.update(vendor=PowerBIGoalsReader.name, source_file=str(path), model_name=card_name,
                 usage=usage, _reader_notes=notes)
    facts["assets"] = [*(facts.get("assets") or []),
                       {"role": "dashboard", "name": card_name,
                        "vendor_id": card.get("id") or None, "path": None,
                        "site": card.get("groupId") or None,
                        "catalog_id": None, "catalog_qualified_name": None}]
    facts.setdefault("_unresolved_published", [])
    return facts


class PowerBIGoalsReader(FormulaCompiler):
    """The `powerbi-goals` BI reader (`sockets.BIReader`): one saved scorecard, its connected
    goals read over the connected dataset's definitions. Its formulas are the dataset's DAX
    and Power Query, compiled by the shared engine as the `powerbi` reader's are."""

    name = "powerbi-goals"
    languages = ("dax", "m-query")
    suffixes = (".json", ".bim", ".tmsl")

    def detect(self, path: pathlib.Path) -> bool:
        path = pathlib.Path(path)
        if not path.is_file() or path.suffix.lower() != ".json":
            return False
        try:
            return bool(scorecards_of(_scan.load_json(path)))
        except Exception:  # noqa: BLE001 - detection never raises
            return False

    def form(self, path: pathlib.Path) -> str:
        return "scorecard"

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        return parse(pathlib.Path(path), model=opts.get("model"))
