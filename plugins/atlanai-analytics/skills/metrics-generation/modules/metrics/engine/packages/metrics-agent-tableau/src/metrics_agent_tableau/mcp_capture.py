# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Turn what Tableau's own MCP server returned into a capture folder the run reads.

The `fetch-bi-over-mcp` skill has the operator's assistant call Tableau's official MCP server
(`tableau-mcp`, read-only tools only) and save each answer verbatim in a raw folder
(`metrics_agent.lib.bi_capture`: `server.json`, `calls/*.json`, `files/`). This converter maps
those answers onto the files the Tableau reader already reads, writes `capture_manifest.json`
(route `mcp`) and seals it. It calls nothing: no server, no network.

    python3 -m metrics_agent_tableau.mcp_capture <raw folder> <new capture folder> \
        --role Explorer --custody customer

What each tool gives, checked live against tableau-mcp 4.27.2 (Tableau Cloud, PAT and
connected app):

    download-workbook        the workbook's own .twb (or .twbx), written to a file whose path
                             the answer names                     -> definitions/<workbook>
    get-view-data            per sheet {sheetName, columns, rows}, numbers as DISPLAYED
                             ("1,310,123.66"); seen live: every row listed twice, so a list
                             that is two copies of itself is kept once -> oracle/<sheet>.csv
    get-workbook             the project and the workbook's web address -> manifest `source`

What it does not give, said in the manifest's `gaps` and printed by the run:

    data_source_definition   the server has no tool that downloads a PUBLISHED data source,
                             and get-datasource-metadata names logical tables only, not the
                             warehouse tables or joins. A workbook on a published data source
                             therefore needs that data source's .tds or .tdsx from another
                             route (the browser's Download Data Source, or the tableau-cloud
                             plug), listed in the raw folder's `extra.json`; without it the
                             reader refuses those sheets by name.

Never kept: an owner's name, user name or e-mail (none is written to the capture), a tool's
error text beyond 400 characters with addresses removed, any credential.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import zipfile
from typing import Any

from metrics_agent.lib import bi_capture as BC
from metrics_agent_tableau import access as _tab_access
from metrics_agent_tableau import fetch as F
from metrics_agent_tableau import rest as R

TOOL = "tableau"
EXTRA = "extra.json"
DOWNLOAD, VIEW_DATA, WORKBOOK = "download-workbook", "get-view-data", "get-workbook"
EXTRACT_SUFFIXES = (".hyper", ".tde")
DS_GAP_DETAIL = ("the MCP server exposes no tool that downloads a published data source, and "
                 "get-datasource-metadata names logical tables, not the warehouse tables or "
                 "joins")


class ConvertError(BC.CaptureError):
    """The raw folder cannot be turned into a capture; the message says what to save."""


def _split_values(raw: Any) -> list[str]:
    """A view filter's values as the tool takes them: comma-separated, `\\,` a literal
    comma."""
    parts = re.split(r"(?<!\\),", str(raw))
    return [p.replace("\\,", ",").strip() for p in parts]


def _once(rows: list[list[Any]]) -> tuple[list[list[Any]], bool]:
    """Rows a view-data answer listed twice over (seen live) kept once."""
    n = len(rows)
    if n and n % 2 == 0 and rows[: n // 2] == rows[n // 2:]:
        return rows[: n // 2], True
    return rows, False


def _safe_stem(name: str, taken: set[str]) -> str:
    stem = re.sub(r"[^\w.-]+", "_", name).strip("._")[:80] or "sheet"
    out, i = stem, 2
    while out.casefold() in taken:
        out, i = f"{stem}__{i}", i + 1
    taken.add(out.casefold())
    return out


def _published_names(data: bytes, label: str) -> list[str]:
    _, root = F.tableau_root(label, data, F.WORKBOOK_SUFFIXES)
    return [r["name"] or r["content_url"] for r in F.published_refs(root)]


def _ds_names(path: pathlib.Path) -> set[str]:
    """The names a data source file answers to: its file stem, its caption and its formatted
    name (casefolded)."""
    names = {path.stem.casefold()}
    try:
        _, root = F.tableau_root(path.name, path.read_bytes(), F.DATASOURCE_SUFFIXES)
    except R.TableauError:
        return names
    for key in ("caption", "formatted-name", "name"):
        if root.get(key):
            names.add(root.get(key).casefold())
    rl = root.find("repository-location")
    if rl is not None and rl.get("id"):
        names.add(rl.get("id").casefold())
    return names


def _has_extract(path: pathlib.Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as z:
        return any(n.lower().endswith(EXTRACT_SUFFIXES) for n in z.namelist())


def _refused(call: BC.McpCall, what: str) -> dict[str, Any]:
    return {"what": what, "reason": "tool_error", "detail": call.error_text()}


def convert(raw: pathlib.Path | str, dest: pathlib.Path | str, *, role: str, custody: str,
            row_filters_apply: bool | None = None,
            captured_at: str | None = None) -> dict[str, Any]:
    """Write the capture at `dest` from the raw folder; returns its (sealed) manifest."""
    raw = pathlib.Path(raw).expanduser().resolve()
    server, calls = BC.read_mcp_raw(raw)
    payloads: dict[str, bytes | pathlib.Path] = {}
    files: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    downloads = [c for c in calls if c.tool == DOWNLOAD]
    for c in downloads:
        if c.is_error:
            refused.append(_refused(c, f"{DOWNLOAD} {c.arguments.get('workbookId', '')}".strip()))
    good = [c for c in downloads if not c.is_error]
    if len(good) > 1:
        raise ConvertError("the raw folder holds more than one downloaded workbook; a capture "
                           "holds one: convert each workbook's calls in its own raw folder.")
    published: list[str] = []
    # The definitions folder is named after the workbook, as a fetch names it
    # (`workbooks/<workbook>/`): the reader names the model after the folder it reads.
    defs = "definitions"
    if good:
        c = good[0]
        answer = c.payload() or {}
        if not isinstance(answer, dict) or not answer.get("path"):
            raise ConvertError(f"{c.path.name}: the {DOWNLOAD} answer names no file (`path`). "
                               "Save the answer as the server gave it.")
        src = BC.raw_file(raw, answer["path"])
        data = src.read_bytes()
        name = F.safe_file_name(answer.get("filename") or src.name, "workbook",
                                F.WORKBOOK_SUFFIXES, data[:4] == b"PK\x03\x04")
        try:
            published = _published_names(data, name)
        except R.TableauError as exc:
            raise ConvertError(f"the downloaded workbook is not readable: {exc}") from None
        defs = f"definitions/{pathlib.PurePath(name).stem}"
        payloads[f"{defs}/{name}"] = src
        files.append({"path": f"{defs}/{name}", "role": "definition", "sha256": "",
                      "how": f"{DOWNLOAD} (workbookId {c.arguments.get('workbookId', '?')}"
                             + (", includeExtract" if c.arguments.get("includeExtract") else "")
                             + ")"})
    elif not downloads:
        refused.append({"what": DOWNLOAD, "reason": "tool_not_exposed"
                        if DOWNLOAD not in (server_tools(raw) or [DOWNLOAD]) else
                        "definitions_not_obtainable",
                        "detail": "no workbook download was saved"})

    # Data sources from another route, listed in extra.json: [{file, route, how}].
    supplied: list[set[str]] = []
    for entry in _extra(raw):
        src = BC.raw_file(raw, entry["file"])
        rel = f"{defs}/{src.name}"
        payloads[rel] = src
        files.append({"path": rel, "role": "definition", "sha256": "", "route": entry["route"],
                      "how": entry["how"]})
        supplied.append(_ds_names(src))
    missing = [n for n in published
               if not any(n.casefold() in names for names in supplied)]
    if missing:
        gaps.append({"what": "data_source_definition", "objects": missing,
                     "detail": DS_GAP_DETAIL,
                     "cannot_verify": "the sheets on these published data sources: their "
                                      "warehouse tables and joins are unknown, so A1 names an "
                                      "acquisition gap and their question units stay "
                                      "unresolved (no SQL); add each one's .tds (the "
                                      "browser's Download Data Source, or the tableau-cloud "
                                      "plug) in extra.json"})

    taken: set[str] = set()
    for c in calls:
        if c.tool != VIEW_DATA:
            continue
        if c.is_error:
            refused.append(_refused(c, f"{VIEW_DATA} {c.arguments.get('viewId', '')}".strip()))
            code = _tab_access.value_refusal_code(c.error_text())
            # Values the server did not give are a named gap the run says and records,
            # whatever the reason: an access limit as that blocker (the value service off, a
            # data source's credentials missing), any other error as a capture gap.
            gaps.append({"what": "displayed_values", **({"access_code": code} if code else {}),
                         "objects": [str(c.arguments.get("viewId") or VIEW_DATA)],
                         "cannot_verify": "the values this view shows, so no widget on it "
                                          "is checked against what a person sees",
                         "detail": BC.redact_addresses(c.error_text())[:400]})
            continue
        sheets = c.payload()
        if isinstance(sheets, str):                 # REST below 3.30: one CSV, first sheet
            raise ConvertError(f"{c.path.name}: the server returned view data as CSV text "
                               "(REST API below 3.30), which names no sheet; save get-view-data "
                               "from a site on REST 3.30 or later.")
        if not isinstance(sheets, list):
            raise ConvertError(f"{c.path.name}: the {VIEW_DATA} answer is not a list of "
                               "sheets.")
        filters = [{"field": k, "op": "in", "values": _split_values(v)}
                   for k, v in sorted((c.arguments.get("viewFilters") or {}).items())]
        for sheet in sheets:
            name = str(sheet.get("sheetName") or "")
            if sheet.get("sheetStatus") != "OK":
                detail = BC.redact_addresses(str(sheet.get("errorDetail") or ""))[:400]
                refused.append({"what": f"{VIEW_DATA} {name}", "reason": "tool_error",
                                "detail": detail})
                gaps.append({"what": "displayed_values", "objects": [name],
                             "cannot_verify": "the values this sheet shows, so it is not "
                                              "checked against what a person sees",
                             "detail": detail})
                continue
            rows, doubled = _once([list(r) for r in sheet.get("rows") or []])
            label = name + "".join(f"__{f['field']}_{'_'.join(f['values'])}" for f in filters)
            rel = f"oracle/{_safe_stem(label, taken)}.csv"
            payloads[rel] = BC.oracle_csv([str(x) for x in sheet.get("columns") or []], rows)
            widget = {"title": name, "sheet": name, "filters": filters, **(c.widget or {})}
            files.append({"path": rel, "role": "oracle", "sha256": "", "format": "csv",
                          "header": True, "formatted": True, "widget": widget,
                          "how": f"{VIEW_DATA} (viewId {c.arguments.get('viewId', '?')})"
                                 + ("; the answer listed every row twice, kept once"
                                    if doubled else "")})

    source: dict[str, Any] = {}
    for c in calls:
        if c.tool == WORKBOOK and not c.is_error:
            wb = (c.payload() or {}).get("data") or {}
            if wb.get("name"):
                source["item"] = str(wb["name"])
            if (wb.get("project") or {}).get("name"):
                source["container"] = str(wb["project"]["name"])
            url = str(wb.get("webpageUrl") or "").split("?")[0]
            if url.startswith("https://"):
                source["url"] = url
    if not any(f["role"] == "definition" for f in files):
        raise ConvertError("nothing in the raw folder is a workbook definition: save the "
                           f"{DOWNLOAD} answer and copy the file it wrote into files/. "
                           + "; ".join(f"{r['reason']}: {r['what']}" for r in refused))
    manifest = {
        "contract_version": "1.0", "route": "mcp", "tool": TOOL, "custody": custody,
        "captured_at": captured_at or dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "captured_by": {"role": role, "row_filters_apply": row_filters_apply},
        "server": server, **({"source": source} if source else {}),
        "definitions": defs,
        "rows_on_disk": any(_has_extract(p) for r, p in payloads.items()
                            if r.startswith("definitions/") and isinstance(p, pathlib.Path)),
        "files": files, "refused": refused, "gaps": gaps,
    }
    return BC.write_capture(dest, manifest, payloads)


def server_tools(raw: pathlib.Path) -> list[str] | None:
    """The tool names the server listed (`server.json` `tools`, from tools/list), or None."""
    try:
        tools = json.loads((raw / BC.MCP_SERVER).read_text()).get("tools")
    except (OSError, ValueError):
        return None
    return [str(t) for t in tools] if isinstance(tools, list) else None


def _extra(raw: pathlib.Path) -> list[dict[str, str]]:
    path = raw / EXTRA
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
    except ValueError:
        raise ConvertError(f"{path} is not readable JSON.") from None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not (isinstance(r, dict) and r.get("file") and r.get("how")
                and r.get("route") in ("browser", "api", "file")):
            raise ConvertError(f"{path}: each entry is {{file, route (browser, api or file), "
                               "how}}.")
        out.append({"file": str(r["file"]), "route": r["route"], "how": str(r["how"])})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m metrics_agent_tableau.mcp_capture",
                                 description="Turn saved Tableau MCP answers into a capture.")
    ap.add_argument("raw")
    ap.add_argument("dest")
    ap.add_argument("--role", required=True, help="the signed-in identity's site role")
    ap.add_argument("--custody", required=True, choices=("customer", "third-party"))
    ap.add_argument("--row-filters", choices=("yes", "no", "unknown"), default="unknown")
    a = ap.parse_args(argv)
    try:
        doc = convert(a.raw, a.dest, role=a.role, custody=a.custody,
                      row_filters_apply={"yes": True, "no": False}.get(a.row_filters))
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
