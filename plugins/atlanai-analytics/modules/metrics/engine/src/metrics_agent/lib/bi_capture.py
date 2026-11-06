# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A BI capture: the folder an operator's assistant fills from a signed-in browser or from the
BI vendor's own MCP server.

Neither route hands the agent an API credential, and no stage calls a browser or an MCP
server. The operator's own assistant does: in the operator's signed-in browser it saves what
the BI tool offers that person (its download of a report or a workbook, its export of a
widget's values); over MCP it calls the vendor's server and saves what its tools return (a
model or workbook definition, the values a view shows). Either way it lists every file in
`capture_manifest.json` (`bi_capture.schema.json`). The `fetch-bi-in-browser` and
`fetch-bi-over-mcp` skills say what to save, per tool and per role.

A capture is just another input form. Its definition files are the files an installed reader
already reads, so no reader changes for it; this module only decides WHICH files the run reads:

  * `definitions`, one file or one folder, is what the run's reader parses;
  * an `oracle` file holds the values the tool showed for one widget in one state. It is never
    a definition: it lies outside `definitions`, and the run never reads it. It is kept so a
    statement can be checked against the number the tool displayed (`read_oracle`).

Every file carries its SHA-256 and lies inside the folder; a file that changed after the
capture, a path that leaves the folder, a definition file outside `definitions`, or an oracle
file inside it is refused by name. A capture whose definitions could not be obtained says why
in `refused`, and the run is refused with those reasons rather than with "file not found".

The core never names a tool here: `tool` is the name of the installed reader, and every label
is the tool's own text, carried as data.

    python3 -m metrics_agent.lib.bi_capture seal <folder>    # write each listed file's hash
    python3 -m metrics_agent.lib.bi_capture check <folder>   # validate, as a run would
"""
from __future__ import annotations

import argparse
import csv
import decimal
import hashlib
import io
import json
import pathlib
import re
import sys
from typing import Any, NamedTuple

MANIFEST = "capture_manifest.json"
SCHEMA = "bi_capture.schema.json"

# A blank cell as tools display one: an empty cell, or the label a report puts on a blank group.
_BLANK_TEXT = frozenset({"", "(blank)"})
# A displayed number: an optional sign, digit groups split by commas, an optional decimal
# part, an optional percent sign. Anything else (a date, a code, a word) stays text.
_NUMBER = re.compile(r"^(?P<sign>[-+−]?)(?P<int>\d{1,3}(?:,\d{3})+|\d+)"
                     r"(?:\.(?P<frac>\d+))?(?P<pct>%?)$")


class CaptureError(ValueError):
    """The capture cannot be read as it is. The message names what is wrong and what to do."""


def find(path: pathlib.Path | str) -> pathlib.Path | None:
    """The capture folder `path` is, or None when it is not one."""
    p = pathlib.Path(path).expanduser()
    return p.resolve() if p.is_dir() and (p / MANIFEST).is_file() else None


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _inside(folder: pathlib.Path, rel: str, what: str) -> pathlib.Path:
    """`rel` under `folder`, resolved; refused when it leaves the folder (through `..` or a
    link), however the schema let it through."""
    path = (folder / rel).resolve()
    if path != folder and folder not in path.parents:
        raise CaptureError(f"{what} {rel!r} lies outside the capture folder {folder}; every "
                           "file of a capture is inside its folder.")
    return path


def _read_manifest(folder: pathlib.Path) -> dict[str, Any]:
    from metrics_agent.lib import artifacts  # noqa: PLC0415 - jsonschema only when used

    try:
        doc = json.loads((folder / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        raise CaptureError(f"{folder / MANIFEST} is not readable JSON "
                           f"({type(exc).__name__}).") from None
    try:
        artifacts.validate(doc, SCHEMA, what=MANIFEST)
    except artifacts.ArtifactError as exc:
        raise CaptureError(str(exc)) from None
    return doc


def _refusals(doc: dict[str, Any]) -> str:
    rows = doc.get("refused") or []
    if not rows:
        return "The manifest names no refusal for it."
    return "The capture recorded why:\n" + "\n".join(
        f"    {r['reason']}: {r['what']}" + (f" ({r['detail']})" if r.get("detail") else "")
        for r in rows)


def load(path: pathlib.Path | str, *, verify: bool = True) -> dict[str, Any]:
    """The capture's manifest, checked: it validates against its schema, every listed file
    is inside the folder and (with `verify`) still has the hash recorded, `definitions`
    exists, and definition and oracle files are kept apart."""
    folder = find(path)
    if folder is None:
        raise CaptureError(f"{path} is not a capture folder (no {MANIFEST}).")
    doc = _read_manifest(folder)
    defs = _inside(folder, doc["definitions"], "definitions")
    if not defs.exists():
        raise CaptureError(f"the capture's definitions ({doc['definitions']}) are not in "
                           f"{folder}. " + _refusals(doc))
    seen: set[pathlib.Path] = set()
    for f in doc["files"]:
        p = _inside(folder, f["path"], "file")
        if p in seen:
            raise CaptureError(f"{f['path']} is listed twice in {MANIFEST}.")
        seen.add(p)
        if p == (folder / MANIFEST).resolve():
            raise CaptureError(f"{MANIFEST} cannot list itself.")
        if not p.is_file():
            raise CaptureError(f"{f['path']} is listed in {MANIFEST} and is not in {folder} "
                               "(the download did not complete, or the file was moved).")
        under_defs = p == defs or defs in p.parents
        if f["role"] == "definition" and not under_defs:
            raise CaptureError(f"{f['path']} is a definition file outside the capture's "
                               f"definitions ({doc['definitions']}): the run would not read it.")
        if f["role"] == "oracle" and under_defs:
            raise CaptureError(f"{f['path']} is an oracle file inside the capture's "
                               f"definitions ({doc['definitions']}): a reader could take the "
                               "values for a definition. Keep oracle files beside it.")
        if f["role"] == "oracle" and "format" not in f:
            raise CaptureError(f"{f['path']} is an oracle file with no `format` (csv or tsv).")
        if verify and sha256_file(p) != f["sha256"]:
            raise CaptureError(f"{f['path']} changed after it was captured (its SHA-256 is "
                               f"not the one in {MANIFEST}). Capture it again, or seal the "
                               "capture again if the change was yours.")
    if not any(f["role"] == "definition" for f in doc["files"]):
        raise CaptureError(f"{MANIFEST} lists no definition file. " + _refusals(doc))
    if defs.is_dir():
        extra = sorted(p.relative_to(folder).as_posix() for p in defs.rglob("*")
                       if p.is_file() and p.resolve() not in seen)
        if extra:
            raise CaptureError(f"the capture's definitions hold files {MANIFEST} does not "
                               f"list: {', '.join(extra[:10])}. A reader parses everything "
                               "under its folder, so list them (and seal) or move them out.")
    return doc


def definition_root(path: pathlib.Path | str) -> pathlib.Path:
    """The path the run's reader parses: a capture's `definitions`; any other path as it is."""
    folder = find(path)
    if folder is None:
        return pathlib.Path(path).expanduser().resolve()
    return _inside(folder, load(folder)["definitions"], "definitions")


def definition_files(path: pathlib.Path | str) -> list[pathlib.Path]:
    folder = find(path)
    doc = load(folder)
    return sorted(_inside(folder, f["path"], "file") for f in doc["files"]
                  if f["role"] == "definition")


def routes(doc: dict[str, Any]) -> list[str]:
    """Every route a file of the capture came by: the manifest's own, and any a file names."""
    return sorted({doc["route"], *(f["route"] for f in doc["files"] if f.get("route"))})

def summary(doc: dict[str, Any]) -> dict[str, Any]:
    """What a run records about where its definitions came from (`run_inputs` bi source),
    with every gap the capture named, so the run says what it cannot verify."""
    out = {"route": doc["route"], "captured_at": doc["captured_at"],
           "role": doc["captured_by"]["role"],
           "row_filters_apply": doc["captured_by"].get("row_filters_apply"),
           "rows_on_disk": doc["rows_on_disk"],
           "oracle_files": sum(1 for f in doc["files"] if f["role"] == "oracle"),
           "refused": sorted({r["reason"] for r in doc.get("refused") or []})}
    if len(routes(doc)) > 1:
        out["routes"] = routes(doc)
    if doc.get("server"):
        out["server"] = {k: doc["server"][k] for k in ("name", "version", "transport")}
    gaps = gap_list(doc)
    if gaps:
        out["gaps"] = gaps
    return out

def gap_list(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The capture's gaps, plus the one it implies: a capture with no oracle file holds no
    displayed value, whether or not it said so."""
    gaps = [{k: g[k] for k in ("what", "cannot_verify", "detail", "objects", "access_code")
             if k in g}
            for g in doc.get("gaps") or []]
    if (not any(f["role"] == "oracle" for f in doc["files"])
            and not any(g["what"] == "displayed_values" for g in gaps)):
        gaps.append({"what": "displayed_values",
                     "cannot_verify": "no widget's number can be checked against the value "
                                      "the BI tool shows; only the warehouse witness checks "
                                      "the SQL",
                     "detail": "the capture holds no oracle file"})
    return sorted(gaps, key=lambda g: g["what"])

def gap_lines(summary_or_doc: dict[str, Any]) -> list[str]:
    """One line per gap, for a stage to print: what is missing and what the run cannot
    verify because of it."""
    gaps = summary_or_doc.get("gaps") or []
    return [f"{g['what']}: {g['cannot_verify']}" + (f" ({g['detail']})" if g.get("detail")
                                                     else "") for g in gaps]


# ── oracle files ────────────────────────────────────────────────────────────────────────
class Cell(NamedTuple):
    """One displayed value: its text, the number it shows (None when blank or not a number)
    and the decimals it is shown to (None when the file holds raw values)."""
    text: str
    number: decimal.Decimal | None
    decimals: int | None

    @property
    def blank(self) -> bool:
        return self.text.strip().lower() in _BLANK_TEXT


class Oracle(NamedTuple):
    columns: list[str]
    rows: list[list[Cell]]


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8")


def parse_cell(text: str, formatted: bool) -> Cell:
    """A displayed value. With `formatted`, `1,310,123.66` is 1310123.66 shown to 2 decimals
    and `12.5%` is 0.125 shown to 3; raw values keep every digit and carry no decimals."""
    t = text.strip()
    m = _NUMBER.match(t) if t else None
    if not m:
        try:
            raw = decimal.Decimal(t) if t and not formatted else None
        except decimal.InvalidOperation:
            raw = None
        if raw is not None and not raw.is_finite():
            raw = None
        return Cell(text, raw, None)
    digits = m["int"].replace(",", "") + ("." + m["frac"] if m["frac"] else "")
    value = decimal.Decimal(digits)
    if m["sign"] in ("-", "−"):
        value = -value
    places = len(m["frac"] or "")
    if m["pct"]:
        value, places = value / 100, places + 2
    return Cell(text, value, places if formatted else None)


def read_oracle(path: pathlib.Path | str, entry: dict[str, Any]) -> Oracle:
    """An oracle file's table, as the tool showed it. Its `format`, `header` and `formatted`
    come from its manifest entry; the encoding from the file's byte-order mark."""
    text = _decode(pathlib.Path(path).read_bytes())
    delimiter = "\t" if entry.get("format") == "tsv" else ","
    lines = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if r]
    formatted = bool(entry.get("formatted"))
    header = entry.get("header", True)
    columns = lines[0] if header and lines else []
    body = lines[1:] if header else lines
    return Oracle([c.strip() for c in columns],
                  [[parse_cell(c, formatted) for c in row] for row in body])


def matches(cell: Cell, value: Any, *, rel_tol: float = 0.0, abs_tol: float = 0.0) -> bool:
    """Whether a statement's value is the one the cell shows. A blank matches None; a number
    shown to n decimals matches a value that rounds to it (half away from zero, as tools
    display); a raw number matches within the tolerances; text matches exactly."""
    if value is None:
        return cell.blank
    if cell.number is None:
        return not cell.blank and str(value).strip() == cell.text.strip()
    try:
        v = decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        return False
    if cell.decimals is not None:
        q = decimal.Decimal(1).scaleb(-cell.decimals)
        return v.quantize(q, rounding=decimal.ROUND_HALF_UP) == cell.number.quantize(q)
    diff = abs(float(v) - float(cell.number))
    return diff <= max(abs_tol, rel_tol * max(abs(float(v)), abs(float(cell.number))))


# ── the command line ────────────────────────────────────────────────────────────────────
# ------------------------------------------------------------------ what an MCP session saved
# The `fetch-bi-over-mcp` skill has the assistant save, verbatim, what the vendor's MCP server
# returned, in one folder (the "raw" folder, never read by a run):
#     server.json      the `initialize` answer's serverInfo {name, version} and the transport
#     calls/*.json     one per tools/call: {"tool", "arguments", "result"} (+ optional
#                      "widget": which widget and state a value call is about, as shown)
#     files/           every file a tool wrote, copied as is (the path its result named)
# A vendor package's converter turns that folder into a capture (definitions + oracles + the
# manifest). These helpers are what every converter shares; none names a tool.
MCP_SERVER, MCP_CALLS, MCP_FILES = "server.json", "calls", "files"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

def redact_addresses(text: str) -> str:
    """`text` with every e-mail address replaced: a tool's message can name an owner."""
    return _EMAIL.sub("<address>", text)

class McpCall(NamedTuple):
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    widget: dict[str, Any] | None
    path: pathlib.Path

    @property
    def is_error(self) -> bool:
        return bool(self.result.get("isError"))

    def texts(self) -> list[str]:
        return [c.get("text") or "" for c in self.result.get("content") or []
                if isinstance(c, dict) and c.get("type") == "text"]

    def payload(self) -> Any:
        """The first text content, parsed as JSON (a string holding JSON is parsed once
        more); None when there is none or it is not JSON."""
        for text in self.texts():
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            return value
        return None

    def error_text(self) -> str:
        """The tool's error message, with any e-mail address removed (a message can name an
        owner to contact); never a name the capture keeps."""
        return redact_addresses(" ".join(self.texts()))[:400]

def read_mcp_raw(raw: pathlib.Path | str) -> tuple[dict[str, Any], list[McpCall]]:
    """The server and the calls an MCP session saved, in file-name order. Refused by name
    when the folder is not one."""
    raw = pathlib.Path(raw).expanduser().resolve()
    try:
        server = json.loads((raw / MCP_SERVER).read_text())
    except (OSError, ValueError):
        raise CaptureError(f"{raw} holds no readable {MCP_SERVER} (the MCP server's name and "
                           "version from its initialize answer, and the transport).") from None
    info = server.get("serverInfo") if isinstance(server.get("serverInfo"), dict) else server
    if not (isinstance(info, dict) and info.get("name") and info.get("version")
            and server.get("transport") in ("stdio", "http")):
        raise CaptureError(f"{raw / MCP_SERVER} must name the server (name, version) and the "
                           "transport (stdio or http).")
    calls = []
    for p in sorted((raw / MCP_CALLS).glob("*.json")):
        try:
            doc = json.loads(p.read_text())
        except ValueError:
            raise CaptureError(f"{p.name} is not readable JSON.") from None
        result = doc.get("result")
        if isinstance(result, dict) and "result" in result and "jsonrpc" in result:
            result = result["result"]            # the whole JSON-RPC answer was saved
        if not (isinstance(doc.get("tool"), str) and isinstance(result, dict)):
            raise CaptureError(f"{p.name} must hold {{tool, arguments, result}} as the server "
                               "answered a tools/call.")
        calls.append(McpCall(doc["tool"], dict(doc.get("arguments") or {}), result,
                             doc.get("widget") if isinstance(doc.get("widget"), dict) else None,
                             p))
    if not calls:
        raise CaptureError(f"{raw / MCP_CALLS} holds no saved tool call.")
    server_block = {"name": str(info["name"]), "version": str(info["version"]),
                    "transport": server["transport"],
                    "read_only": bool(server.get("read_only", False))}
    return server_block, calls

def raw_file(raw: pathlib.Path | str, named: str) -> pathlib.Path:
    """The copy, under the raw folder's `files/`, of a file a tool said it wrote at `named`
    (a path on the operator's machine): found by its base name, never by the path itself."""
    folder = (pathlib.Path(raw).expanduser().resolve() / MCP_FILES)
    base = pathlib.PurePath(str(named).replace("\\", "/")).name
    p = _inside(folder, base, "file") if base else None
    if p is None or not p.exists():
        raise CaptureError(f"the tool wrote {base or named!r}, and {folder} holds no copy of "
                           "it: copy the file the tool's answer names into files/.")
    return p

def oracle_csv(columns: list[str], rows: list[list[Any]]) -> bytes:
    """A table as an oracle file (UTF-8, comma-separated, a header line)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for row in rows:
        w.writerow(["" if v is None else v for v in row])
    return buf.getvalue().encode("utf-8")

def write_capture(dest: pathlib.Path | str, manifest: dict[str, Any],
                  payloads: dict[str, bytes | pathlib.Path]) -> dict[str, Any]:
    """Write a capture: each payload at its path (bytes, or a file or folder copied), the
    manifest beside them, then seal and check it. `dest` must not exist yet or be empty."""
    import shutil  # noqa: PLC0415

    dest = pathlib.Path(dest).expanduser()
    if dest.exists() and any(dest.iterdir()):
        raise CaptureError(f"{dest} is not empty; a capture is written into a new folder.")
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in payloads.items():
        target = _inside(dest.resolve(), rel, "file")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, pathlib.Path):
            if data.is_dir():
                shutil.copytree(data, target)
            else:
                shutil.copyfile(data, target)
        else:
            target.write_bytes(data)
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return seal(dest)

def seal(path: pathlib.Path | str) -> dict[str, Any]:
    """Write each listed file's SHA-256 into the manifest, then check the capture. The
    assistant lists the files; sealing records what they are now."""
    folder = find(path)
    if folder is None:
        raise CaptureError(f"{path} is not a capture folder (no {MANIFEST}).")
    try:
        doc = json.loads((folder / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        raise CaptureError(f"{folder / MANIFEST} is not readable JSON "
                           f"({type(exc).__name__}).") from None
    for f in doc.get("files") or []:
        if isinstance(f, dict) and isinstance(f.get("path"), str):
            p = _inside(folder, f["path"], "file")
            if p.is_file():
                f["sha256"] = sha256_file(p)
    (folder / MANIFEST).write_text(json.dumps(doc, indent=2) + "\n")
    return load(folder)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m metrics_agent.lib.bi_capture",
                                 description="Seal or check a BI capture folder.")
    ap.add_argument("action", choices=("seal", "check"))
    ap.add_argument("folder")
    a = ap.parse_args(argv)
    try:
        doc = seal(a.folder) if a.action == "seal" else load(a.folder)
    except CaptureError as exc:
        print(f"bi_capture: REFUSED — {exc}", file=sys.stderr)
        return 2
    s = summary(doc)
    print(f"bi_capture: {a.folder} is a {s['route']} capture for {doc['tool']}: "
          f"definitions {doc['definitions']}, {s['oracle_files']} oracle file(s), captured as "
          f"{s['role']}" + (f"; refused: {', '.join(s['refused'])}" if s["refused"] else ""))
    for line in gap_lines(s):
        print(f"bi_capture: gap: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
