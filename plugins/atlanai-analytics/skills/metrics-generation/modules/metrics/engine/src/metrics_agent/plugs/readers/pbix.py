# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Power BI `.pbix` parser plugin -> the vendor-neutral `bi_facts` contract.

PROVENANCE. A COPY of the `.pbix` parser of an earlier project (guardrail 6: ported,
never imported), rewritten onto THIS repo's contract. Deliberately left behind, and each for a
reason:

  * **The earlier project's own `_walk_fields` / `_visual_fields` / `_report_usage`.** A `.pbix`
    carries its report layer as PBIR JSON inside the zip — the SAME format
    `readers/powerbi.py` already walks on disk. The earlier project has a second walker for it, and
    a second walker is a second set of answers: the earlier project's emits
    `{container_kind, measures, columns}` while the base emits the full `bi_context` block C3
    reads (guardrail 12). So the zip's report tree is unpacked and the ONE walker runs over it.
    This is the same argument that produced `readers/mquery`.
  * **The earlier project's title regex** — `re.search(r'"Title"[^}]*?"Value"…')` over a JSON dump
    of the objects blob. The shared walker reads the property properly.
  * **`filters: []` and `parameters: []`** — earlier-project contract keys this base does not carry,
    or carries only where a format actually declares them.

VERTIPAQ METADATA ONLY, NEVER DATA ROWS. `pbixray` can read the compressed column store; this
reads the schema, the DAX, the relationships and the Power Query expressions and nothing
else. A `.pbix` is a customer's data as well as their model, and the model is all this
pipeline is entitled to.

`pbixray` is imported INSIDE `parse`, so the offline suite and every other input path run
without the dependency installed.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys
import tempfile
import zipfile
from typing import Any


from metrics_agent.plugs.readers import mquery as _mquery  # noqa: E402
from metrics_agent.plugs.readers import powerbi as _powerbi  # noqa: E402

# Where the report layer sits inside the zip. The on-disk extract calls the same tree
# `definition/`, which is what lets one walker serve both.
_REPORT_PREFIX = "Report/definition/"
# A report saved in the LEGACY format (PBIR off, the long-standing default) is one JSON
# document, `Report/Layout`, written as UTF-16 little-endian: the same `sections` /
# `visualContainers` layout a PBIP folder's report.json holds, so the same walker reads it.
_LEGACY_LAYOUT = "Report/Layout"

# A .pbix is a zip from an untrusted source. Two limits, because a zip says how big it claims
# to be and a malicious one lies: refuse an entry whose declared size is absurd, and stop
# once the unpacked total passes the cap. Generous enough that no real report comes near it.
MAX_REPORT_ENTRY_BYTES = 64 * 1024 * 1024
MAX_REPORT_TOTAL_BYTES = 256 * 1024 * 1024


class PBIXError(ValueError):
    pass


def _decode_layout(raw: bytes) -> str:
    """The legacy `Report/Layout` text. Desktop writes UTF-16 LE with no byte-order mark; a
    BOM, or plain UTF-8 from another writer, is read as what it says it is."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if len(raw) >= 2 and raw[1:2] == b"\x00":
        return raw.decode("utf-16-le")
    return raw.decode("utf-8")


def _unpack_report(pbix: pathlib.Path, dest: pathlib.Path) -> str | None:
    """Unpack the zip's report layer under `dest`: the PBIR tree to `<dest>/definition/…`
    ("pbir"), or else the legacy `Report/Layout` to `<dest>/report.json` ("legacy"). None
    when the file holds neither (a model-only file).

    Path traversal is refused rather than sanitised: a `.pbix` naming `../` in a member has
    no legitimate reading, and quietly rewriting it would hide that someone tried.
    """
    found = False
    total = 0
    try:
        z = zipfile.ZipFile(pbix)
    except zipfile.BadZipFile as exc:
        raise PBIXError(
            f"{pbix.name} is not a readable zip ({type(exc).__name__}). A .pbix is a zip; a "
            "truncated download or a file that is not one at all lands here.") from None
    with z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith(_REPORT_PREFIX):
                continue
            rel = info.filename[len("Report/"):]
            target = (dest / rel).resolve()
            # `is_relative_to` rather than a "/"-prefixed string compare: the latter hardcodes
            # the POSIX separator, so on Windows it failed CLOSED and refused every entry.
            if not target.is_relative_to(dest.resolve()):
                raise PBIXError(
                    f"{pbix.name} contains a report entry that escapes the archive: "
                    f"{info.filename!r}. Refused.")
            if info.file_size > MAX_REPORT_ENTRY_BYTES:
                raise PBIXError(
                    f"{pbix.name}: report entry {info.filename!r} declares "
                    f"{info.file_size} bytes, over the {MAX_REPORT_ENTRY_BYTES} cap.")
            total += info.file_size
            if total > MAX_REPORT_TOTAL_BYTES:
                raise PBIXError(
                    f"{pbix.name}: the report layer unpacks to more than "
                    f"{MAX_REPORT_TOTAL_BYTES} bytes. Refused rather than held in memory.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            found = True
        if found:
            return "pbir"
        try:
            info = z.getinfo(_LEGACY_LAYOUT)
        except KeyError:
            return None
        if info.file_size > MAX_REPORT_ENTRY_BYTES:
            raise PBIXError(
                f"{pbix.name}: report entry {info.filename!r} declares {info.file_size} "
                f"bytes, over the {MAX_REPORT_ENTRY_BYTES} cap.")
        with z.open(info) as src:
            # Read one byte past the cap: a zip can lie about its declared size.
            raw = src.read(MAX_REPORT_ENTRY_BYTES + 1)
        if len(raw) > MAX_REPORT_ENTRY_BYTES:
            raise PBIXError(
                f"{pbix.name}: report entry {info.filename!r} unpacks to more than "
                f"{MAX_REPORT_ENTRY_BYTES} bytes. Refused rather than held in memory.")
        try:
            text = _decode_layout(raw)
        except UnicodeDecodeError:
            raise PBIXError(
                f"{pbix.name}: {info.filename!r} is neither UTF-16 nor UTF-8 text, so its "
                "visuals cannot be read.") from None
        (dest / "report.json").write_text(text, encoding="utf-8")
        return "legacy"


def report_usage(pbix: pathlib.Path, tables: list[dict]) -> list[dict]:
    """The usage entries of a `.pbix`'s report layer, in either format, read by the same
    walkers as a report on disk. [] for a file with no report layer."""
    with tempfile.TemporaryDirectory(prefix="pbix-report-") as tmp:
        # The report is named by the directory it is unpacked into, and that name becomes part
        # of every unit id. The temp directory's own name is random, so ids changed on every
        # run and a review answered against one run attached to nothing in the next. Unpacked
        # under the file's stem instead: the same .pbix always yields the same ids.
        dest = pathlib.Path(tmp) / pbix.stem
        dest.mkdir()
        kind = _unpack_report(pbix, dest)
        if kind == "pbir":
            return _powerbi._parse_pbir_report(dest, tables)
        if kind == "legacy":
            return _powerbi._parse_report(dest, tables)
    return []


# A parameter's M body: a text literal followed by its `meta [IsParameterQuery=true, …]`
# record. Only a plain string literal can name an object; a number, a date or an expression
# is a knob of another kind and is never substituted.
_TEXT_PARAM = re.compile(r'^\s*"((?:[^"]|"")*)"\s+meta\s*\[', re.S)


def _parameters(model) -> list[dict]:
    """The model's M parameters, in the shape the TMDL reader writes (`name`, scrubbed
    `value`, `kind`). A `.pbix` stores them in its mashup, which `pbixray` exposes."""
    df = getattr(model, "m_parameters", None)
    if df is None:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        expr = str(r.get("Expression") or "")
        literal = expr.split("meta", 1)[0].strip()
        out.append({"name": str(r["ParameterName"]),
                    "value": _mquery.scrub_m(literal) or None,
                    "kind": "parameter"})
    return out


def _text_parameters(model) -> dict[str, str]:
    """Parameter name -> its string literal, for the parameters that hold one."""
    df = getattr(model, "m_parameters", None)
    if df is None:
        return {}
    out: dict[str, str] = {}
    for _, r in df.iterrows():
        m = _TEXT_PARAM.match(str(r.get("Expression") or ""))
        if m:
            out[str(r["ParameterName"])] = m.group(1).replace('""', '"')
    return out


def _resolve_object_param(lineage: dict, params: dict[str, str]) -> dict:
    """`Name=@param` names its object through a parameter. Where the model declares that
    parameter as a text literal, the literal IS the object's name: substitute it. The
    parameter itself stays visible on `bi_facts.parameters`. An undeclared parameter is
    left as the token: a guessed name is worse than an unexplained one."""
    obj = lineage.get("object")
    if not (isinstance(obj, str) and obj.startswith("@")):
        return lineage
    value = params.get(obj[1:])
    if value:
        lineage = dict(lineage, object=value)
    return lineage


def _source_columns(model) -> dict[tuple[str, str], str]:
    """(table, model column name) -> the column Power Query delivers it as (`SourceColumn`).

    A column renamed in the model (in the field list or the model view) keeps its old name as
    its source column and gets no Power Query step: `Call Date` reads `CALL_DATE`. Missing from
    a file that does not record it, where the model name is the only answer."""
    try:
        df = model.tmschema_columns
    except Exception:                                            # noqa: BLE001
        return {}
    if df is None or "SourceColumn" not in getattr(df, "columns", ()):
        return {}
    return {(str(r["TableName"]), str(r["Name"])): str(r["SourceColumn"])
            for _, r in df.iterrows() if isinstance(r["SourceColumn"], str)
            and r["SourceColumn"].strip()}


def _tables(model) -> tuple[list[dict], list[str]]:
    params = _text_parameters(model)
    sources = _source_columns(model)
    pq = {}
    if model.power_query is not None:
        pq = {r["TableName"]: r["Expression"] for _, r in model.power_query.iterrows()}

    calc: dict[tuple[str, str], str] = {}
    if model.dax_columns is not None:
        for _, r in model.dax_columns.iterrows():
            calc[(r["TableName"], r["ColumnName"])] = r["Expression"]

    schema = model.schema
    out: list[dict] = []
    for tname in model.tables:
        # A column Power Query renamed is still read from the warehouse by its OLD name, and a
        # column the model renamed is read by its source column: the two compose. The renames
        # (and every other step) are read by `mquery.read_power_query`, shared with the other
        # containers; here each column carries the name Power Query delivers it as.
        cols = []
        for _, r in schema[schema["TableName"] == tname].iterrows():
            key = (tname, r["ColumnName"])
            delivered = sources.get((tname, str(r["ColumnName"])), str(r["ColumnName"]))
            source = str(r["ColumnName"]) if key in calc else delivered
            col = {
                "name": r["ColumnName"],
                "data_type": str(r["PandasDataType"]),
                "is_calculated": key in calc,
                "source_column": source.lower(),
                "_source_exact": source,
            }
            if key in calc:
                col["formula"] = calc[key]
                col["formula_language"] = "dax"
            cols.append(col)
        entry: dict[str, Any] = {"name": tname, "columns": cols}
        if tname in pq:
            # The SHARED miner, so a target this names from a .pbix is the same target it
            # names from the identical expression in a TMDL partition.
            entry["source_lineage"] = _resolve_object_param(
                _mquery.source_lineage(_mquery.substitute_parameters(pq[tname], params)),
                params)
            entry["_m"] = pq[tname]
        out.append(_powerbi.name_physically(entry))
    # The shared queries a table may read (load disabled: no table of their own) and the
    # parameters, by name, as the mashup holds them.
    shared = {k: v for k, v in pq.items() if k not in set(model.tables)}
    shared.update(_parameter_expressions(model))
    return out, _mquery.read_power_query(out, shared)


def _parameter_expressions(model) -> dict[str, str]:
    """Parameter name -> its M (`"x" meta [IsParameterQuery=true, …]`)."""
    df = getattr(model, "m_parameters", None)
    if df is None:
        return {}
    return {str(r["ParameterName"]): str(r.get("Expression") or "") for _, r in df.iterrows()}


def _measures(model) -> list[dict]:
    out: list[dict] = []
    if model.dax_measures is None:
        return out
    for _, r in model.dax_measures.iterrows():
        m: dict[str, Any] = {
            "name": r["Name"],
            "home_table": r["TableName"],
            "formula": r["Expression"],
            "formula_language": "dax",
            # A DAX measure is declared once and is already a closure, the same as in TMDL.
            "aggregation_at_use_site": False,
        }
        if r.get("Description"):
            m["description"] = str(r["Description"])
        if r.get("DisplayFolder"):
            m["folder"] = str(r["DisplayFolder"])
        out.append(m)
    return out


def _relationships(model) -> list[dict]:
    out: list[dict] = []
    if model.relationships is None:
        return out
    for _, r in model.relationships.iterrows():
        # pbixray states the cardinality as `<M|1>:<M|1>` and the cross filter as
        # Single/Both; the ends are split so the shared reading applies.
        card = str(r.get("Cardinality") or "")
        ends = card.split(":") if card.count(":") == 1 else ["", ""]
        rel = {
            "from_table": r["FromTableName"], "from_column": r["FromColumnName"],
            "to_table": r["ToTableName"], "to_column": r["ToColumnName"],
            "is_active": bool(r["IsActive"]),
            "kind": "relationship",
            **_powerbi.relationship_semantics(ends[0] or None, ends[1] or None,
                                              r.get("CrossFilteringBehavior"),
                                              r.get("RelyOnReferentialIntegrity")),
        }
        if not card:
            rel["cardinality"] = "unknown"
        out.append(rel)
    return out


def parse(path: pathlib.Path, report_index: dict | None = None) -> dict[str, Any]:
    """`report_index` is accepted and ignored: a `.pbix` carries its own report layer inside
    the file, so there is no sibling tree to join against."""
    try:
        from pbixray import PBIXRay  # noqa: PLC0415 — deliberately lazy
    except ImportError:
        raise PBIXError(
            "reading a .pbix needs the optional `pbix` feature, which this runtime was set up "
            "without. Set up a new runtime with `--feature pbix` (a runtime keeps the features "
            "it was set up with), or supply the model as a TMDL folder or .bim instead."
        ) from None

    pbix = pathlib.Path(path)
    if not pbix.is_file():
        raise PBIXError(f"not a file: {pbix}")

    try:
        model = PBIXRay(str(pbix))
    except Exception as exc:                                     # noqa: BLE001
        if "LiveConnection" in type(exc).__name__:
            raise PBIXError(
                f"{pbix.name} is a live-connection report: it holds the report and no semantic "
                "model, so there are no measure formulas to read. Supply the model (its TMDL "
                "folder or .bim, a fetch with the model, or the full .pbix download)."
            ) from None
        raise PBIXError(
            f"pbixray could not read {pbix.name}: {type(exc).__name__}. A .pbix saved by a "
            "very old Desktop, or one whose model is encrypted by a sensitivity label, is "
            "not readable here — that is an acquisition gap, not a finding about the model."
        ) from None

    tables, notes = _tables(model)

    usage = report_usage(pbix, tables)
    measures = _measures(model)
    notes += _powerbi.finish_usage(usage, tables, measures)

    return {
        "contract_version": "1.0",
        "vendor": "powerbi",
        "source_file": str(pbix),
        "model_name": pbix.stem,
        "tables": _mquery.attach_native_statements(tables),
        "measures": measures,
        "relationships": _relationships(model),
        "usage": usage,
        "parameters": _parameters(model),
        "_unresolved_published": [],
        **({"_reader_notes": notes} if notes else {}),
    }
