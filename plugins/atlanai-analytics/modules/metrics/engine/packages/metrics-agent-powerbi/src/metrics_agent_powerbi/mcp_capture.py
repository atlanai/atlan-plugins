# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Turn what Microsoft's Power BI MCP servers returned into a capture folder the run reads.

The `fetch-bi-over-mcp` skill has the operator's assistant call Microsoft's own MCP servers and
save each answer verbatim in a raw folder (`metrics_agent.lib.bi_capture`: `server.json`,
`calls/*.json`, `files/`). This converter maps those answers onto the files the Power BI reader
already reads, writes `capture_manifest.json` (route `mcp`) and seals it. It calls nothing.

    python3 -m metrics_agent_powerbi.mcp_capture <raw folder> <new capture folder> \
        --role Contributor --custody customer

What each server gives (from Microsoft's documentation; not run live here, see the plug
README for why):

    Power BI Authoring (Modeling) MCP, local     `database_operations` exports the model as a
        (`@microsoft/powerbi-modeling-mcp`)      TMDL folder: the same TMDL a PBIP project or
                                                 Fabric's getDefinition holds. The assistant
                                                 copies that folder into files/
                                                 -> definitions/<model>/definition/*.tmdl
                                                 `dax_query_operations` runs a DAX query
                                                 -> an oracle file (raw values) for the widget
                                                    the assistant names in the saved call
    Power BI remote MCP                          Execute Query runs a DAX query (the
        (api.fabric.microsoft.com/v1/mcp/powerbi) executeQueries answer shape) -> an oracle
                                                 file, as above. Get Semantic Model Schema and
                                                 Get Report Metadata answer in shapes Microsoft
                                                 does not document as a definition format: not
                                                 mapped, so a remote-only session has no
                                                 definitions and is refused by name.

What neither gives, said in the manifest's `gaps` and printed by the run:

    report_definition   no report layout (pages, visuals, visual and page filters, report-level
                        measures): the run asks one question per measure of the model, and no
                        widget's filters are known. The report comes from another route (the
                        browser's .pbix download, or the powerbi-fabric plug).
    displayed_values    a DAX query answers with the model's raw values, not what a visual
                        displays: a query result is kept as a raw oracle (`formatted: false`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
from typing import Any

from metrics_agent.lib import bi_capture as BC
from metrics_agent_powerbi import access as _pbi_access

TOOL = "powerbi"
MODEL_MARKERS = ("model.tmdl", "database.tmdl")
REPORT_GAP_DETAIL = ("the Power BI Authoring MCP server has no report tools, and the remote "
                     "server's Get Report Metadata answer is not a report definition format "
                     "(not mapped)")


class ConvertError(BC.CaptureError):
    """The raw folder cannot be turned into a capture; the message says what to save."""


def _tmdl_folders(raw: pathlib.Path) -> list[pathlib.Path]:
    """Every exported TMDL folder under files/: a folder holding model.tmdl or
    database.tmdl (the `definition` folder itself)."""
    root = raw / BC.MCP_FILES
    found = {p.parent.resolve() for m in MODEL_MARKERS for p in root.rglob(m)}
    return sorted(found)


def _model_name(folder: pathlib.Path) -> str:
    """The model's name: `database.tmdl`'s `database <name>`, else the export folder's name
    (the folder above `definition`)."""
    db = folder / "database.tmdl"
    if db.is_file():
        m = re.search(r"^database[ \t]+(?:'([^']+)'|(\S+))[ \t]*$", db.read_text(encoding="utf-8-sig"),
                      re.M)
        if m:
            return m.group(1) or m.group(2)
    base = folder.parent.name if folder.name.lower() == "definition" else folder.name
    return re.sub(r"\.SemanticModel$", "", base, flags=re.I) or "model"


def _operation(args: dict[str, Any]) -> str:
    """The operation a tool's arguments name (any key that says `operation`), never a path."""
    stack: list[Any] = [args]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if "operation" in k.lower() and isinstance(v, str):
                    return v
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return ""


def table_of(payload: Any) -> tuple[list[str], list[list[Any]]] | None:
    """A DAX query answer as (columns, rows), from the shapes a server answers in:
    executeQueries' `{results: [{tables: [{rows: [{col: value}]}]}]}`, `{columns, rows}`, or a
    list of row objects. None for anything else."""
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        tables = [t for r in payload["results"] for t in (r.get("tables") or [])]
        if len(tables) != 1:
            return None
        payload = tables[0].get("rows")
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        cols = payload.get("columns")
        if isinstance(cols, list) and all(isinstance(r, list) for r in payload["rows"]):
            names = [c.get("name") if isinstance(c, dict) else c for c in cols]
            return [str(n) for n in names], [list(r) for r in payload["rows"]]
        payload = payload["rows"]
    if isinstance(payload, list) and all(isinstance(r, dict) for r in payload):
        cols: list[str] = []
        for r in payload:
            cols += [k for k in r if k not in cols]
        return cols, [[r.get(c) for c in cols] for r in payload]
    return None


def convert(raw: pathlib.Path | str, dest: pathlib.Path | str, *, role: str, custody: str,
            row_filters_apply: bool | None = None, model_name: str | None = None,
            captured_at: str | None = None) -> dict[str, Any]:
    """Write the capture at `dest` from the raw folder; returns its (sealed) manifest."""
    raw = pathlib.Path(raw).expanduser().resolve()
    server, calls = BC.read_mcp_raw(raw)
    payloads: dict[str, bytes | pathlib.Path] = {}
    files: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    for c in calls:
        if c.is_error:
            refused.append({"what": f"{c.tool} {_operation(c.arguments)}".strip(),
                            "reason": "tool_error", "detail": c.error_text()})
            code = _pbi_access.value_refusal_code(c.error_text())
            if c.widget is not None:
                # A widget's values the server did not give are a named gap the run says and
                # records, whatever the reason: an access limit as that blocker (no Build
                # permission, the MCP endpoint switched off), any other error as a capture
                # gap.
                gaps.append({"what": "displayed_values",
                             **({"access_code": code} if code else {}),
                             "objects": [str(c.widget.get("title") or c.tool)],
                             "cannot_verify": "the values this widget shows, so it is not "
                                              "checked against what the model answers",
                             "detail": BC.redact_addresses(c.error_text())[:400]})
    folders = _tmdl_folders(raw)
    if len(folders) > 1:
        raise ConvertError("files/ holds more than one exported TMDL model; a capture holds "
                           "one: convert each model in its own raw folder.")
    if not folders:
        tools = sorted({c.tool for c in calls})
        raise ConvertError("files/ holds no exported TMDL folder (model.tmdl or "
                           "database.tmdl): export the model with the Authoring MCP server's "
                           "database_operations and copy the folder it wrote. A schema answer "
                           "(Get Semantic Model Schema) is not a model definition. Saved tools: "
                           + ", ".join(tools) + ". "
                           + "; ".join(f"{r['reason']}: {r['what']}" for r in refused))
    folder = folders[0]
    name = model_name or _model_name(folder)
    # One folder level under definitions/, never `.` or `..` (a model named so would point
    # the reader at the capture's root, oracle files included).
    folder_name = re.sub(r'[^\w .-]+', '_', name).strip(' .') or 'model'
    defs = f"definitions/{folder_name}"
    exporter = next((c for c in calls if not c.is_error and "tmdl" in json.dumps(
        c.arguments).lower()), None)
    how = (f"{exporter.tool} ({_operation(exporter.arguments) or 'TMDL export'})"
           if exporter else "TMDL export")
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            rel = f"{defs}/definition/{p.relative_to(folder).as_posix()}"
            payloads[rel] = p
            files.append({"path": rel, "role": "definition", "sha256": "", "how": how})

    gaps.append({"what": "report_definition", "detail": REPORT_GAP_DETAIL,
                 "objects": [name],
                 "cannot_verify": "the report's pages, visuals, visual and page filters and "
                                  "report-level measures: questions come from the model's "
                                  "measures alone, and no widget's filters are checked; for "
                                  "the report, use another route (the browser's .pbix "
                                  "download, or the powerbi-fabric plug)"})

    taken: set[str] = set()
    for c in calls:
        if c.is_error or c.widget is None:
            continue
        table = table_of(c.payload())
        if table is None:
            raise ConvertError(f"{c.path.name}: a call marked with a widget holds no query "
                               "answer in a known shape (executeQueries results, {columns, "
                               "rows}, or a list of rows).")
        cols, rows = table
        stem = re.sub(r"[^\w.-]+", "_", str(c.widget.get("title") or c.tool)).strip("._")
        stem = stem or "query"
        out, i = stem, 2
        while out.casefold() in taken:
            out, i = f"{stem}__{i}", i + 1
        taken.add(out.casefold())
        rel = f"oracle/{out}.csv"
        payloads[rel] = BC.oracle_csv(cols, rows)
        widget = {"filters": [], **c.widget}
        files.append({"path": rel, "role": "oracle", "sha256": "", "format": "csv",
                      "header": True, "formatted": False, "widget": widget,
                      "how": f"{c.tool} ({_operation(c.arguments) or 'DAX query'})"})
    if any(f["role"] == "oracle" for f in files):
        gaps.append({"what": "displayed_values", "detail": "a DAX query answers with the "
                     "model's raw values, kept as raw oracles (formatted: false)",
                     "cannot_verify": "the rounding and formatting a visual displays"})

    manifest = {
        "contract_version": "1.0", "route": "mcp", "tool": TOOL, "custody": custody,
        "captured_at": captured_at or dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "captured_by": {"role": role, "row_filters_apply": row_filters_apply},
        "server": server, "source": {"item": name}, "definitions": defs,
        "rows_on_disk": False, "files": files, "refused": refused, "gaps": gaps,
    }
    return BC.write_capture(dest, manifest, payloads)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m metrics_agent_powerbi.mcp_capture",
                                 description="Turn saved Power BI MCP answers into a capture.")
    ap.add_argument("raw")
    ap.add_argument("dest")
    ap.add_argument("--role", required=True, help="the identity's workspace role or permission")
    ap.add_argument("--custody", required=True, choices=("customer", "third-party"))
    ap.add_argument("--row-filters", choices=("yes", "no", "unknown"), default="unknown")
    ap.add_argument("--model-name", default=None,
                    help="the model's name, when the export does not say it")
    a = ap.parse_args(argv)
    try:
        doc = convert(a.raw, a.dest, role=a.role, custody=a.custody,
                      row_filters_apply={"yes": True, "no": False}.get(a.row_filters),
                      model_name=a.model_name)
    except BC.CaptureError as exc:
        print(f"mcp_capture: REFUSED — {exc}", file=sys.stderr)
        return 2
    s = BC.summary(doc)
    print(f"mcp_capture: {a.dest}: {len(doc['files']) - s['oracle_files']} definition file(s), "
          f"{s['oracle_files']} oracle file(s) from {doc['server']['name']} "
          f"{doc['server']['version']}")
    for line in BC.gap_lines(s):
        print(f"mcp_capture: gap: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
