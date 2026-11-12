# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Tableau parser — .twb / .twbx / .tds / .tdsx -> the vendor-neutral bi_facts contract.

PROVENANCE. Ported from the Tableau parsing stage of an earlier project, which
already carried the four traps below. Deliberately left behind: its metric-group assembly and
wikilink serving (a different pipeline's output), its `objectid_base`/`repo_id` Tableau-server
identifiers, and its blend-key resolution against a live catalog.

WHAT MAKES TABLEAU DIFFERENT FROM POWER BI, and why this is its own parser rather than a
branch: a Power BI measure is declared once in DAX and is already a closure. A Tableau field
is a PILL ON A SHELF — the aggregation is chosen at the USE SITE, so the same field is SUM on
one worksheet and AVG on another. The metric does not exist until field and shelf are
combined, which is why this lane yields more candidates per field and why D2 does most of its
work here.

THE FOUR TRAPS, each of which silently changes the number rather than failing:

  1. AGGREGATION AT THE USE SITE — recorded per usage entry, never assumed from the field.
  2. DATA BLENDING IS NOT A JOIN — a left join executed AFTER aggregation at the primary's
     grain. Emitted as kind="blend" so A3 declares it rather than writing a SQL join.
  3. TOP-N HIDES behind a rank calc plus a "Top 1-N" bin filter. Missed, you generate SQL
     with no LIMIT that runs, passes the witness, and answers a different question.
  4. PARAMETER-DRIVEN BRANCHES — `CASE [Parameters].[P] WHEN ...`. Recorded with the declared
     default; a branch is never picked silently.

AND ONE ABSENCE THAT LOOKS LIKE SUCCESS: a .twb referencing a PUBLISHED datasource carries no
warehouse connection at all — the .tds does. Hand this parser a workbook without its
datasource files and every worksheet parses cleanly and resolves to nothing, so that case is
reported as a named acquisition gap rather than as "the estate has no lineage".

AND THE INPUTS AROUND A WORKBOOK, each of which also parsed "cleanly" into nothing:

  * A PLAIN FIELD IS A MEASURE TOO. `SUM(Quantity)` on a shelf or the Marks card is a measure
    of that sheet with no calculated field anywhere; it is emitted as a measure with no
    formula (a column, `aggregation_at_use_site`), aggregated as the sheet says. A date pill
    (`YEAR(Order Date)`, `MONTH-TRUNC(…)`) is a derived DIMENSION, declared on its table as
    the calc it is, never the raw date (a different grain) and never an aggregation.
  * PUBLISHED CHAINS. A published datasource's own file may itself be a sqlproxy to another
    published datasource. The chain is walked to the physical connection, at any depth;
    a link missing after the first, or a cycle, is refused naming the chain.
  * VIRTUAL CONNECTIONS. A datasource on one (`class='publishedConnection'`) names only the
    virtual connection's tables, as `[<table id>].[NAME]`; its `.tvcx` beside the input (the
    JSON `.tvc` Tableau saves) says which warehouse object each is.
  * PULSE. A saved response of the Pulse definitions API (`<datasource>.pulse.json`, beside the
    datasource's .tds) is one question unit per definition, like a worksheet.
  * MODELS THAT LIVE ELSEWHERE. A cube (Analysis Services, SAP BW, Essbase), Salesforce Data
    Cloud or Tableau Semantics holds its measures outside Tableau; refused by name, saying
    where the model lives and what to supply.
  * CUSTOM SQL names no object of its own; every table it reads is in `source_lineage.reads`.
"""
from __future__ import annotations

import json
import pathlib
import sys
import re
import xml.etree.ElementTree as ET
import urllib.parse
import zipfile
from xml.parsers import expat
from typing import Any

from metrics_agent.plugs.readers.bicompile import FormulaCompiler  # noqa: E402
from metrics_agent.lib import bi_tags as _bi_tags  # noqa: E402
from metrics_agent.plugs.readers import mquery as _mquery  # noqa: E402
from metrics_agent.lib import sqlshape as _sqlshape  # noqa: E402

# Tableau's order of operations. A CONTEXT filter narrows the population BEFORE a later
# TOP N or LOD is computed; a DIMENSION filter does not. Collapsing them into one WHERE
# produces SQL that runs, passes the witness and returns a different number.
FILTER_STAGE = {
    "datasource": "datasource",
    "context": "context",
    "dimension": "dimension",
    "measure": "measure",
    "table-calc": "table-calc",
    "display": "display",
}
AGG_PREFIX = re.compile(r"^\[?(sum|avg|min|max|cnt|countd|median|attr|stdev|var)\]?:", re.I)
AGG_NAME = {"sum": "sum", "avg": "avg", "min": "min", "max": "max", "cnt": "count",
            "countd": "count_distinct", "median": "median", "attr": "single_value",
            "stdev": "stdev", "var": "var"}
PILL = re.compile(r"\[([^\]]+)\]\.\[([^\]]+)\]")
PARAM_CASE = re.compile(r"CASE\s+\[Parameters\]\.\[([^\]]+)\]", re.I)
RANK_FN = re.compile(r"\b(RANK|INDEX|RANK_DENSE|RANK_UNIQUE)\s*\(", re.I)


class TableauError(ValueError):
    """A Tableau input the reader refuses, in words an operator can act on; a stage reports
    it by name (`<stage>: TableauError: …`), never as a traceback."""


class UnsafeXMLError(TableauError):
    """A document declares a DTD, so it is refused before it is parsed."""


# --------------------------------------------------------------------------- files
SUFFIXES = (".twb", ".tds", ".twbx", ".tdsx")
# A virtual connection's own file: a package (`.tvcx`, a zip) or its loose XML (`.tvc`).
# Read only to resolve a datasource that connects through one; never a model of its own.
VC_SUFFIXES = (".tvcx", ".tvc")
# A saved response of the Pulse metric-definitions REST endpoint, named for the published
# datasource it is built on (`<datasource>.pulse.json`).
PULSE_SUFFIX = ".pulse.json"


def _is_pulse(p: pathlib.Path) -> bool:
    return p.name.lower().endswith(PULSE_SUFFIX)


def _tableau_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """The Tableau files under a directory, by suffix in ANY case. A `*.twb` glob is
    case-sensitive on POSIX, so a `REVIEW.TWB` copied off Windows was never read and the
    directory reported "no Tableau XML found"."""
    found = [p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES]
    return sorted(found, key=lambda p: (SUFFIXES.index(p.suffix.lower()), str(p)))


def _pulse_files(path: pathlib.Path) -> list[pathlib.Path]:
    """The Pulse definition files this input carries: every `*.pulse.json` under a
    directory, or the file itself. A workbook file reads none (pass the folder)."""
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.is_file() and _is_pulse(p))
    return [path] if _is_pulse(path) else []


def _roots(path: pathlib.Path) -> list[tuple[str, ET.Element]]:
    """Every XML root in the input, whether loose or packaged."""
    out: list[tuple[str, ET.Element]] = []
    files: list[pathlib.Path] = []
    if path.is_dir():
        files = _tableau_files(path)
    elif _is_pulse(path):
        # Pulse definitions are built on a PUBLISHED datasource: its .tds/.tdsx beside the
        # file is the model the definitions are read against.
        files = sorted(p for p in path.parent.iterdir()
                       if p.is_file() and p.suffix.lower() in (".tds", ".tdsx"))
        if not files:
            raise TableauError(
                f"{path.name} holds Pulse metric definitions, but no .tds/.tdsx is beside it. "
                f"A definition names only fields of its published datasource: download that "
                f"datasource (.tds or .tdsx) into {path.parent} and re-run.")
    elif path.suffix.lower() not in SUFFIXES:
        # Parsed as XML anyway, a spreadsheet or a Power BI file failed with a bare
        # ParseError that named neither the file's kind nor what is supported.
        raise TableauError(
            f"the tableau reader does not know how to read {path.name}. Supported: a "
            f".twb/.tds, a .twbx/.tdsx package, a Pulse definitions file "
            f"(<datasource>{PULSE_SUFFIX}) beside its .tds/.tdsx, or a directory holding "
            f"them.")
    else:
        files = [path]
    for f in files:
        out += _read_xml(f)
    if not out:
        raise TableauError(
            f"no Tableau XML found under {path}. Expected .twb/.tds, or a .twbx/.tdsx "
            f"package containing one.")
    # ONE WORKBOOK PER RUN. A run builds one model, and everything per workbook — its
    # worksheets, dashboards, parameters and shared filters — is read from ONE workbook root.
    # Given a directory of several, the first silently won and the others' sheets vanished
    # while their datasources were still read. Refused by name instead; a datasource file
    # (.tds/.tdsx) beside the workbook is not a workbook and is still welcome.
    workbooks = [name for name, root in out if root.tag == "workbook"]
    if len(workbooks) > 1:
        raise TableauError(
            f"{path} holds {len(workbooks)} workbooks ({', '.join(workbooks[:6])}). A run "
            f"reads ONE workbook: pass one .twb/.twbx (with its published .tds/.tdsx beside "
            f"it), or a directory holding only that workbook.")
    return out


# A packaged workbook's XML is a few megabytes. A member declaring far more is a zip bomb or
# a mistake, and is refused rather than inflated into memory.
MAX_XML_MEMBER_BYTES = 64 * 1024 * 1024


def _refuse_declarations(label: str, data: bytes) -> None:
    """Refuse a document that declares a DTD, before the tree parser sees it.

    `xml.etree` is not hardened: an internal entity can expand exponentially (billion laughs)
    and a DOCTYPE is where every external-entity trick starts. Tableau never writes one — a
    .twb/.tds is `<?xml …?>`, a build comment and the root element — so a DOCTYPE or an
    ENTITY declaration is refused outright rather than defused. The check is expat's own
    tokeniser, not a byte search, so an encoding declaration cannot hide the declaration; it
    stops at the first one, before any entity is expanded. Malformed XML is left to the tree
    parser, which reports it as it always has.
    """
    def refuse(what: str):
        def handler(*_args) -> None:
            raise UnsafeXMLError(
                f"{label}: declares {what}. A Tableau workbook or datasource never carries "
                "one; refused before parsing (entity expansion and external entities start "
                "there).")
        return handler

    p = expat.ParserCreate()
    p.StartDoctypeDeclHandler = refuse("a DOCTYPE")
    p.EntityDeclHandler = refuse("an ENTITY")
    p.ExternalEntityRefHandler = refuse("an external entity reference")
    try:
        p.Parse(data, True)
    except expat.ExpatError:
        return


def _parse_bytes(label: str, data: bytes) -> ET.Element:
    _refuse_declarations(label, data)
    return ET.fromstring(data)


def _read_xml(f: pathlib.Path) -> list[tuple[str, ET.Element]]:
    if f.suffix.lower() in (".twbx", ".tdsx"):
        try:
            z = zipfile.ZipFile(f)
        except zipfile.BadZipFile as exc:
            raise TableauError(f"{f.name} is not a readable package ({type(exc).__name__}); "
                               "a .twbx/.tdsx is a zip.") from None
        out = []
        with z:
            for info in z.infolist():
                if not info.filename.lower().endswith((".twb", ".tds")):
                    continue
                if info.file_size > MAX_XML_MEMBER_BYTES:
                    raise TableauError(
                        f"{f.name}: {info.filename!r} declares {info.file_size} bytes, over "
                        f"the {MAX_XML_MEMBER_BYTES} cap. Refused rather than held in memory.")
                with z.open(info) as src:
                    data = src.read(MAX_XML_MEMBER_BYTES + 1)
                if len(data) > MAX_XML_MEMBER_BYTES:
                    raise TableauError(f"{f.name}: {info.filename!r} inflates past the "
                                       f"{MAX_XML_MEMBER_BYTES} cap. Refused.")
                out.append((info.filename,
                            _parse_bytes(f"{f.name}: {info.filename!r}", data)))
        return out
    # A loose file is held to the same cap: it is read whole before it is parsed.
    with f.open("rb") as src:
        data = src.read(MAX_XML_MEMBER_BYTES + 1)
    if len(data) > MAX_XML_MEMBER_BYTES:
        raise TableauError(f"{f.name} is over the {MAX_XML_MEMBER_BYTES} cap. Refused.")
    return [(f.name, _parse_bytes(f.name, data))]


def _sibling_datasources(path: pathlib.Path) -> list[tuple[str, ET.Element]]:
    """The .tds/.tdsx files beside a single workbook file.

    A workbook on a PUBLISHED datasource carries no connection; the datasource's own file
    does, and an operator who downloaded both puts them in one folder and passes the
    workbook. These are read only to RESOLVE the workbook's references — never emitted on
    their own, so a stray .tds in the folder adds nothing to the facts.
    """
    # A published datasource can itself be built on another published datasource, so a
    # single .tds file resolves against its siblings too (never against itself).
    if path.is_dir() or _is_pulse(path) or path.suffix.lower() not in SUFFIXES:
        return []
    out: list[tuple[str, ET.Element]] = []
    for ext in (".tds", ".tdsx"):
        for f in sorted(p for p in path.parent.iterdir()
                        if p.is_file() and p.suffix.lower() == ext and p != path):
            out += _read_xml(f)
    return out


# ---------------------------------------------------------------- virtual connections
def _vc_files(path: pathlib.Path) -> list[pathlib.Path]:
    """The virtual-connection files the input carries: under a directory, or beside a file."""
    where = path if path.is_dir() else path.parent
    walk = where.rglob("*") if path.is_dir() else where.iterdir()
    return sorted(p for p in walk if p.is_file() and p.suffix.lower() in VC_SUFFIXES)


def _read_vc(f: pathlib.Path) -> list[tuple[str, ET.Element]]:
    """A virtual connection as Tableau saves it: a `.tvcx` (a zip holding `<Name>.tvc`, as
    `GET /api/<v>/sites/<site>/virtualConnections/<id>/content` returns it) or the loose `.tvc`.
    The `.tvc` is JSON. Held to the same size caps as every other Tableau file, and turned into
    the element tree `VirtualConnection` reads."""
    if f.suffix.lower() == ".tvc":
        with f.open("rb") as src:
            data = src.read(MAX_XML_MEMBER_BYTES + 1)
        if len(data) > MAX_XML_MEMBER_BYTES:
            raise TableauError(f"{f.name} is over the {MAX_XML_MEMBER_BYTES} cap. Refused.")
        return [(f.name, _vc_tree(f.name, data))]
    try:
        z = zipfile.ZipFile(f)
    except zipfile.BadZipFile as exc:
        raise TableauError(f"{f.name} is not a readable package ({type(exc).__name__}); "
                           "a .tvcx is a zip.") from None
    out = []
    with z:
        for info in z.infolist():
            if not info.filename.lower().endswith(".tvc"):
                continue
            if info.file_size > MAX_XML_MEMBER_BYTES:
                raise TableauError(f"{f.name}: {info.filename!r} declares {info.file_size} "
                                   f"bytes, over the {MAX_XML_MEMBER_BYTES} cap. Refused.")
            with z.open(info) as src:
                data = src.read(MAX_XML_MEMBER_BYTES + 1)
            if len(data) > MAX_XML_MEMBER_BYTES:
                raise TableauError(f"{f.name}: {info.filename!r} inflates past the "
                                   f"{MAX_XML_MEMBER_BYTES} cap. Refused.")
            label = f"{f.name}:{info.filename}"
            out.append((label, _vc_tree(label, data)))
    if not out:
        raise TableauError(f"{f.name} holds no .tvc member; it is not a virtual connection.")
    return out


def _vc_tree(label: str, data: bytes) -> ET.Element:
    """The `.tvc` JSON as an element tree: `<virtual-connection name>` with one `<connection>`
    per connection specification (its class, dbname and schema) and one `<table>` per table,
    carrying its id (`luid`, which a datasource's relation names) and a `<relation>` written as
    in a .tds: the physical table, or the table's custom SQL."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
        props = doc["revision"]["revisableProperties"]
        tables = props["tables"]
    except (ValueError, KeyError, TypeError):
        raise TableauError(
            f"{label}: not a Tableau virtual connection (expected the JSON Tableau saves, "
            f"with revision.revisableProperties.tables).") from None
    root = ET.Element("virtual-connection", {"name": str(doc.get("name") or "")})
    for spec in props.get("connectionSpecifications") or []:
        attrs = {str(a.get("attributeName")): str(a.get("value"))
                 for a in spec.get("unsecuredConnectionAttributes") or []
                 if a.get("attributeName") in ("class", "dbname", "schema")
                 and a.get("value") is not None}
        ET.SubElement(root, "connection", {"name": str(spec.get("id") or ""), **attrs})
    for tb in tables:
        rp = tb.get("revisableProperties") or {}
        pid = rp.get("physicalIdentifier") or {}
        attrs = {"name": str(pid.get("caption") or tb.get("name") or ""),
                 "luid": str(tb.get("luid") or ""),
                 "connection": str(rp.get("connectionSpecificationId") or "")}
        el = ET.SubElement(root, "table", attrs)
        sql = rp.get("customSql")
        sql = sql.get("query") if isinstance(sql, dict) else sql
        if isinstance(sql, str) and sql.strip():
            rel = ET.SubElement(el, "relation", {"type": "text"})
            rel.text = sql
            continue
        parts = re.findall(r"\[([^\]]+)\]", str(pid.get("qualifiedName") or ""))
        name = parts[-1] if parts else str(tb.get("name") or "")
        # The table's own database and schema win over its connection's defaults.
        if pid.get("databaseName"):
            el.set("dbname", str(pid["databaseName"]))
        if pid.get("schemaName"):
            el.set("schema", str(pid["schemaName"]))
        ET.SubElement(el, "relation", {"type": "table", "table": f"[{name}]"})
    for tb in tables:
        for col in (tb.get("revisableProperties") or {}).get("columns") or []:
            phys = (col.get("revisableProperties") or {}).get("physicalColumnName")
            if col.get("luid") and phys:
                ET.SubElement(root, "column", {"luid": str(col["luid"]), "physical": str(phys)})
    return root


_FIELD_ID = re.compile(r"\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{12})\]")


def _name_vc_columns(root: ET.Element, columns: dict[str, str]) -> None:
    """A datasource on a virtual connection names each column by the virtual connection's
    column id (`[df912aa4-…]`, captioned for display) instead of the warehouse column. Every
    `[id]` the virtual connections know becomes `[physical name]`, in attributes and text
    alike (columns, formulas, shelves, metadata), so the rest of the reader reads ordinary
    fields. Ids are unique, and a table id (`[<table id>].[NAME]`) is not a column id."""
    if not columns:
        return
    def sub(text: str) -> str:
        return _FIELD_ID.sub(lambda m: f"[{columns[m.group(1).lower()]}]"
                             if m.group(1).lower() in columns else m.group(0), text)
    for el in root.iter():
        for k, v in el.attrib.items():
            if "[" in v:
                el.set(k, sub(v))
        if el.text and "[" in el.text:
            el.text = sub(el.text)


def _norm_class(cls: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (cls or "").lower())


def _is_vc_class(cls: str | None) -> bool:
    # `publishedConnection` (resourceId / resourceName) is what Tableau writes, verified on a
    # live site; the others are older or alternative spellings.
    return _norm_class(cls) in ("publishedconnection", "virtualconnection",
                                "virtualconnections", "vconn")


# A physical location Tableau appends to a virtual-connection table's name:
# `ORDERS (RETAIL.MERCH.ORDERS)`. Where it is present it names the warehouse object outright.
_VC_LOCATION = re.compile(r"^\s*(?P<name>.*?)\s*\((?P<loc>[^()]+)\)\s*$")
# How a datasource names a virtual-connection table: `[<table id>].[NAME]`.
_VC_TABLE_ID = re.compile(r"^\[([0-9a-fA-F-]{36})\]\.\[([^\]]+)\]$")


class VirtualConnection:
    """One virtual connection, read from its file: its tables, each with the warehouse object
    (or custom SQL) behind it and the connection that object is reached through.

    Built from the `.tvc` JSON (`_vc_tree`). A datasource on a virtual connection names a
    table as `[<table id>].[NAME]`, so tables are found by their id first and by name second;
    a table name carrying its location in parentheses resolves from the name.
    """

    def __init__(self, label: str, root: ET.Element) -> None:
        self.label = label
        self.root = root
        self.name = root.get("name") or root.get("caption") or ""
        self.connections = {(c.get("name") or ""): c for c in root.iter("connection")}
        self.tables: dict[str, ET.Element] = {}
        self.by_id: dict[str, ET.Element] = {}
        for t in root.iter("table"):
            nm = (t.get("name") or t.get("caption") or "").strip()
            if nm:
                self.tables[_vc_table_key(nm)] = t
            if t.get("luid"):
                self.by_id[t.get("luid").lower()] = t

    def keys(self, file_name: str) -> set[str]:
        rl = self.root.find("repository-location")
        stem = pathlib.PurePosixPath(file_name.split(":")[0]).stem
        names = [self.name, self.root.get("caption"), stem,
                 rl.get("id") if rl is not None else None]
        return {k for k in (_ds_name_key(n) for n in names) if k}

    def table(self, name: str) -> ET.Element | None:
        ref = _VC_TABLE_ID.match(name.strip())
        if ref and ref.group(1).lower() in self.by_id:
            return self.by_id[ref.group(1).lower()]
        return self.tables.get(_vc_table_key(ref.group(2) if ref else name))

    def connection_of(self, table: ET.Element) -> ET.Element | None:
        ref = table.get("connection")
        if ref and ref in self.connections:
            return self.connections[ref]
        rel = table.find("relation")
        if rel is not None and rel.get("connection") in self.connections:
            return self.connections[rel.get("connection")]
        conns = [c for c in self.connections.values() if c.get("class")]
        return conns[0] if len(conns) == 1 else None


def _vc_table_key(name: str) -> str:
    """A virtual-connection table, matched by its name with any location suffix dropped."""
    raw = _bracket_join(name) if name.strip().startswith("[") else name
    m = _VC_LOCATION.match(raw)
    return (m.group("name") if m else raw).strip().lower()


# ------------------------------------------------------- models that live outside Tableau
# A datasource whose connection is one of these reads a model that is DEFINED ELSEWHERE: the
# measures, hierarchies and calculations are the cube's or the semantic layer's, and the file
# Tableau saves holds only a pointer to them. Read as a datasource it parses "cleanly" into
# nothing; refused by name instead, saying where the model lives and what to supply.
_EXTERNAL_MODELS: tuple[tuple[frozenset[str], str, str], ...] = (
    (frozenset({"msolap", "msas", "ssas", "analysisservices"}),
     "a Microsoft Analysis Services cube",
     "The measures and calculations are defined in the cube, not in Tableau. Supply the cube's "
     "model instead: a tabular model's .bim (read with bi_vendor: powerbi), or the query "
     "history and tables of the warehouse the cube is built on."),
    (frozenset({"sapbw", "sapbusinesswarehouse", "sapnetweaverbw"}),
     "SAP BW (a BEx query or InfoProvider)",
     "The key figures and their aggregation are defined in SAP BW. There is no reader for BW "
     "queries: supply the query history and tables of the database BW loads from."),
    (frozenset({"essbase", "oracleessbase"}),
     "an Oracle Essbase cube",
     "The measures, members and calculation scripts are defined in Essbase. There is no reader "
     "for Essbase outlines: supply the query history and tables of the source the cube is "
     "loaded from."),
    (frozenset({"salesforcecdp", "salesforcedatacloud", "datacloud", "sfdatacloud",
                "salesforcedatacloudconnector"}),
     "Salesforce Data Cloud",
     "The data model objects and calculated insights are defined in Data Cloud. Supply the "
     "query history and tables of the warehouse Data Cloud shares its data to (zero-copy), or "
     "a model a reader here reads."),
    (frozenset({"tableausemantics", "semantics", "semanticmodel", "salesforcesemantics",
                "tableausemanticmodel"}),
     "a Tableau Semantics model",
     "The metrics, calculated fields and relationships are defined in the semantic model, not "
     "in this file. Supply the warehouse tables and query history the semantic model reads, or "
     "its definitions in a format a reader here reads."),
)


def _live_connections(ds: ET.Element) -> list[ET.Element]:
    """Every connection of the LIVE source, outer first: an extract's own `hyper` connection
    describes a copy, not where the data comes from."""
    extract = {id(c) for e in ds.iter("extract") for c in e.iter("connection")}
    return [c for c in ds.iter("connection") if id(c) not in extract]


def _refuse_external_model(ds: ET.Element, label: str) -> None:
    for conn in _live_connections(ds):
        cls = _norm_class(conn.get("class"))
        for classes, what, supply in _EXTERNAL_MODELS:
            if cls in classes:
                caption = ds.get("caption") or ds.get("formatted-name") or ds.get("name") or ""
                raise TableauError(
                    f"{label}: datasource {caption!r} connects to {what} (connection class "
                    f"{conn.get('class')!r}). Its model lives outside Tableau, so this file "
                    f"holds no measure definitions to read. {supply}")


def _bracket_join(raw: str) -> str:
    """'[DB].[SCHEMA].[TABLE]' -> 'db.schema.table'."""
    parts = re.findall(r"\[([^\]]+)\]", raw or "")
    return ".".join(parts).lower() if parts else (raw or "").strip().lower()


# ---------------------------------------------------------------------- datasources
def _physical(ds: ET.Element, *, vcs: list[tuple[str, "VirtualConnection"]] | None = None,
              dialect: str = "") -> dict[str, Any]:
    """The physical warehouse object behind a datasource, and whether it is published.

    `vcs` are the virtual connections read beside the input: a datasource that connects
    through one resolves its tables to the warehouse objects the virtual connection names.
    `dialect` (the run's) is what a custom-SQL statement is parsed in to record the tables it
    reads; without one, `reads` holds only the tables the relation tree names."""
    out: dict[str, Any] = {"connection": None, "database": None, "schema": None,
                           "object": None, "object_kind": None, "raw_expression": None,
                           "published_datasource": False}
    conn = ds.find(".//connection")
    if conn is not None:
        cls = (conn.get("class") or "").lower()
        out["connection"] = cls or None
        out["database"] = conn.get("dbname")
        out["schema"] = conn.get("schema")
        if cls == "sqlproxy":
            # A published datasource: the real connection lives in a separate .tds. The
            # workbook alone parses fine and resolves to nothing.
            out["published_datasource"] = True

    if out["published_datasource"]:
        # Its relation names Tableau's proxy (`[sqlproxy]`), never a warehouse object: the
        # table is known only from the published data source's own file. Read as a table it
        # compiled `FROM sqlproxy` (seen live when that file could not be fetched).
        return out

    vc_conn = next((c for c in _live_connections(ds) if _is_vc_class(c.get("class"))), None)
    if vc_conn is not None:
        return _vc_physical(ds, vc_conn, vcs or [], dialect, out)

    rel = _primary_relation(ds)
    if rel is not None:
        kind = (rel.get("type") or "").lower()
        if kind == "union":
            union = _union_statement(ds, rel, dialect)
            if isinstance(union, str):
                out["refusal"] = union
            else:
                out.update(union)
            rel = None
        elif kind == "text" and (rel.text or "").strip():
            out["object_kind"] = "custom-sql"
            # SCRUBBED like every other raw_expression. This was the one caller that
            # was not, which falsified the shared miner's "every caller gets scrub_m
            # whether it remembers to ask or not" — a custom SQL relation can carry a
            # credential in a connection string just as an M expression can.
            out["raw_expression"] = _mquery.scrub_m(_unescape_custom_sql(rel.text.strip()))
        else:
            table = rel.get("table") or rel.get("name")
            if table:
                # QUALIFIED FROM THE CONNECTION THE RELATION IS READ THROUGH: `[SCHEMA].[T]`
                # under a connection naming a database is that database's table. Left as
                # `schema.t`, the compiled SQL ran only where the session's default database
                # happened to be right. A federated datasource names the database on its
                # named connection, not on the outer one. Never invented when none is named.
                leaf = _leaf_connection(ds, rel)
                db = out["database"] or (leaf.get("dbname") if leaf is not None else None)
                schema = out["schema"] or (leaf.get("schema") if leaf is not None else None)
                if out["database"] is None and out["schema"] is None and leaf is not None:
                    out["database"], out["schema"] = db, schema
                out["object_kind"] = "table"
                out["object"] = _qualify(_bracket_join(table), db, schema)
                agg = _aggregated_extract(ds, out["object"])
                if isinstance(agg, str):
                    out["refusal"] = agg
                elif agg:
                    out.update(agg)
    if not out["published_datasource"]:
        reads = _reads(ds, dialect)
        if reads:
            out["reads"] = reads
        why = out.get("refusal") or _source_refusal(ds, rel, dialect)
        if why:
            out["refusal"] = why
            if why.startswith(_NOT_A_WAREHOUSE):
                out["object"] = None        # a sheet or a file path is no warehouse object
    return out


# An aggregated extract's `<aggregation derivation>` (Tableau's twb schema: `<extract>` holds
# `<aggregation column derivation>` beside its filters) -> the aggregate its rows hold.
_EXTRACT_AGG = {"sum": "SUM({c})", "min": "MIN({c})", "max": "MAX({c})", "avg": "AVG({c})",
                "count": "COUNT({c})", "countd": "COUNT(DISTINCT {c})"}


# A metadata record's local type -> the field's data type.
_EXTRACT_TYPES = {"integer": "integer", "real": "real", "string": "string", "date": "date",
                  "datetime": "datetime", "boolean": "boolean"}


def _aggregated_extract(ds: ET.Element, table: str) -> dict | str | None:
    """An extract made with "Aggregate data for visible dimensions": its rows are one per
    combination of the data source's visible dimension columns, each listed measure
    aggregated as its `<aggregation derivation>` says (Tableau's documented semantics; the
    element is its twb schema's). A sheet's SUM over it is a sum of those aggregates, an AVG
    an average of them. -> lineage fields of the statement that holds those rows, None when
    the extract is not aggregated, or the reason it cannot be written.

    Not live-verified: a refreshed aggregated extract needs the warehouse (step 26); the
    sample that would confirm it is a Desktop-saved .twb with an aggregated extract."""
    ex = ds.find("extract")
    if ex is None or (ex.get("enabled") or "true").lower() == "false":
        return None
    aggs = [a for a in ex if a.tag == "aggregation"]
    if not aggs:
        return None
    remote, local_type, order = {}, {}, []
    for rec in ds.iter("metadata-record"):
        if (rec.get("class") or "") == "column":
            local = (rec.findtext("local-name") or "").strip().strip("[]")
            if local:
                remote[local.lower()] = (rec.findtext("remote-name") or local).strip()
                local_type[local.lower()] = (rec.findtext("local-type") or "").strip().lower()
                order.append(local)

    def ident(local: str) -> str:
        name = remote.get(local.lower(), local)
        return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else \
            '"' + name.replace('"', '""') + '"'
    measured: dict[str, str] = {}
    measured_out: list[tuple[str, str, str | None]] = []
    for a in aggs:
        col = (a.get("column") or "").strip().strip("[]")
        form = _EXTRACT_AGG.get((a.get("derivation") or "").strip().lower())
        if not col or form is None:
            return (f"an aggregated extract that rolls {a.get('column')!r} up as "
                    f"{a.get('derivation')!r}: only Sum, Min, Max, Avg, Count and CountD "
                    "are written as a statement (a date rolled up to a level is not)")
        measured[col.lower()] = form.format(c=ident(col)) + f" AS {ident(col)}"
        # COUNT and COUNTD hold a whole number whatever the column's type.
        kind = ("integer" if form.startswith("COUNT") else local_type.get(col.lower()))
        measured_out.append((col, "measure", kind))
    # THE VISIBLE DIMENSIONS ARE THE TABLE'S, NOT ONLY THE DECLARED ONES. A file declares a
    # `<column>` only for a field whose properties were changed (a caption, a role, hidden);
    # every other column of the table is in its metadata records alone, with the role its
    # type gives it (a number a measure, anything else a dimension). Read from the `<column>`
    # elements only, the extract's grain lost every undeclared dimension.
    declared = {(c.get("name") or "").strip().strip("[]").lower(): c
                for c in ds.findall("column")}
    keys, seen, outputs = [], set(), []
    for name in order + [(c.get("name") or "").strip().strip("[]")
                         for c in ds.findall("column")]:
        low = name.lower()
        c = declared.get(low)
        role = ((c.get("role") if c is not None else None) or
                ("measure" if local_type.get(low) in ("integer", "real") else
                 "dimension" if low in local_type else "")).lower()
        if (not name or low in seen or low in measured
                or (c is not None and c.find("calculation") is not None)
                or (c is not None and (c.get("hidden") or "").lower() == "true")
                or role != "dimension"
                or name.startswith(":") or low.startswith("number of records")):
            continue
        seen.add(low)
        keys.append(ident(name))
        outputs.append((name, "dimension", local_type.get(low)))
    sel = ",\n       ".join(keys + list(measured.values()))
    group = ("\nGROUP BY " + ", ".join(keys)) if keys else ""
    return {"object": None, "object_kind": "statement",
            "statement": f"SELECT {sel}\nFROM   {table}{group}", "reads": [table],
            "name_hint": f"{table}__aggregated_extract",
            # The statement's columns, for the fields no `<column>` declares.
            "_extract_columns": outputs + measured_out}


# The fields a union generates beside its tables' columns: the table a row came from (a
# database table's name; a file's sheet and path are files, refused by their class).
_UNION_GENERATED = {"table name"}


def _union_output(name: str) -> str:
    """A union column's name in the statement's output: a plain lowercase identifier."""
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "col"


def _union_statement(ds: ET.Element, rel: ET.Element, dialect: str) -> dict | str:
    """A UNION of tables as one statement: each table's rows with every column of the union
    (NULL where the table has no such column) and the generated Table Name (the table's own
    name, seen live), then UNION ALL. -> lineage fields, or the reason it cannot be written.

    The statement's output names each column as `_union_output` spells it; the reader's
    columns read those names (`source_column`)."""
    own = rel.find("columns")
    names = [c.get("name") for c in own.findall("column")] if own is not None else []
    leaves = [r for r in rel.findall("relation")]
    if not names or not leaves:
        return ("a union whose file lists no columns or no tables: which column of each "
                "table it reads is not stated")
    parts, reads, db0, schema0 = [], [], None, None
    for leaf in leaves:
        if (leaf.get("type") or "").lower() != "table" or not leaf.get("table"):
            return (f"a union of {(leaf.get('type') or 'unnamed')!r} relations: only a union "
                    f"of tables is written as one statement")
        lc = _leaf_connection(ds, leaf)
        db = lc.get("dbname") if lc is not None else None
        schema = lc.get("schema") if lc is not None else None
        db0, schema0 = db0 or db, schema0 or schema
        q = _qualify(_bracket_join(leaf.get("table")), db, schema)
        reads.append(q)
        have = {(c.get("name") or "").lower(): c.get("name")
                for c in (leaf.find("columns") or ET.Element("x")).findall("column")}
        if not have:
            return (f"a union whose table {leaf.get('name')!r} lists no columns: which of "
                    f"the union's columns it holds is not stated")
        sel = []
        for n in names:
            out_name = _union_output(n)
            if n.lower() in _UNION_GENERATED:
                text = (leaf.get("name") or "").replace("'", "''")
                sel.append(f"'{text}' AS {out_name}")
            elif n.lower() in have:
                src = have[n.lower()]
                ident = src if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", src) else \
                    '"' + src.replace('"', '""') + '"'
                sel.append(f"{ident} AS {out_name}")
            else:
                sel.append(f"NULL AS {out_name}")
        parts.append("SELECT " + ",\n       ".join(sel) + f"\nFROM   {q}")
    return {"object": None, "object_kind": "statement", "database": db0, "schema": schema0,
            "statement": "\nUNION ALL\n".join(parts), "reads": sorted(set(reads)),
            "_outputs": {n.lower(): _union_output(n) for n in names},
            "_union": rel.get("name") or "union"}


# Connection classes whose data does not live in a warehouse the run can query: files,
# spreadsheets, web and SaaS connectors, and an extract with no live connection behind it.
_NON_WAREHOUSE = {
    "excel-direct": "an Excel workbook", "excel": "an Excel workbook",
    "textscan": "a text or CSV file", "google-sheets": "a Google Sheet",
    "googlesheets": "a Google Sheet", "semistructpassivestore": "a JSON file",
    "json": "a JSON file", "pdf": "a PDF file", "stat-direct": "a statistical file",
    "spatial": "a spatial file", "hyper": "a Tableau extract with no live connection",
    "dataengine": "a Tableau extract with no live connection",
    "webdata-direct": "a web data connector", "odata": "an OData feed",
    "salesforce": "Salesforce (a SaaS connector)",
    "google-analytics": "Google Analytics (a SaaS connector)",
    "box": "a file in Box", "dropbox": "a file in Dropbox", "onedrive": "a file in OneDrive",
}
_NOT_A_WAREHOUSE = "the datasource reads "


def _source_refusal(ds: ET.Element, rel: ET.Element | None, dialect: str = "") -> str | None:
    """Why no warehouse statement reproduces this datasource's rows, or None.

    * a leaf on a file, spreadsheet, web or SaaS connection (not a warehouse);
    * leaves on connections of different classes or servers (a cross-database join or
      union): one warehouse statement reads one warehouse;
    * Initial SQL on the connection, which runs before every query Tableau sends and can
      change what the tables return (session settings, temporary tables); Initial SQL that
      only sets what cannot change a result (`_initial_sql_effect`) is read without it."""
    caption = ds.get("caption") or ds.get("formatted-name") or ds.get("name") or ""
    leaves = _leaves(ds) or ([rel] if rel is not None else [])
    conns = [c for c in (_leaf_connection(ds, leaf) for leaf in leaves) if c is not None]
    if not conns:
        conns = [c for c in _live_connections(ds)
                 if (c.get("class") or "").lower() not in ("federated", "")]
    for c in conns:
        cls = (c.get("class") or "").lower()
        if cls in _NON_WAREHOUSE:
            return (f"{_NOT_A_WAREHOUSE}{_NON_WAREHOUSE[cls]} (connection class {cls!r}), "
                    f"not a warehouse table: no SQL over it can be verified")
    ends = sorted({((c.get("class") or "").lower(), (c.get("server") or "").lower())
                   for c in conns})
    if len(ends) > 1:
        shown = " + ".join(f"{k}{' at ' + sv if sv else ''}" for k, sv in ends)
        return (f"datasource {caption!r} joins or unions tables across connections ({shown}): "
                f"no single warehouse statement reproduces it")
    ex = ds.find("extract")
    if ex is not None and (ex.get("enabled") or "true").lower() != "false":
        # An extract of the first N rows, or of a sample: which rows it holds is the
        # source's order at the last refresh (or a random draw), which no statement states.
        count, units = (ex.get("count") or "-1").strip(), (ex.get("units") or "").lower()
        if units == "percent" or (count.lstrip("-").isdigit() and int(count) >= 0):
            return (f"datasource {caption!r} reads an extract of "
                    f"{'a sample' if units == 'percent' else 'its first ' + count + ' rows'}, "
                    f"in an order no statement states")
    for c in _live_connections(ds):
        why = _initial_sql_effect((c.get("one-time-sql") or "").strip(), dialect)
        if why:
            return (f"datasource {caption!r} runs Initial SQL on its connection before every "
                    f"query ({why}), which can change what its tables return; a statement "
                    f"run on its own does not run it")
    return None


_DIALECT_DATA = pathlib.Path(__file__).parent / "data" / "reader_dialects.yml"


def _initial_sql_effect(sql: str, dialect: str) -> str | None:
    """Why a connection's Initial SQL may change what a statement returns, or None when every
    statement in it is one the dialect's session runs without effect on results (a query
    tag, a timeout, the compute: `session_without_effect` in `data/reader_dialects.yml`)."""
    if not sql:
        return None
    import yaml  # noqa: PLC0415 - read only when a connection carries Initial SQL

    known = ((yaml.safe_load(_DIALECT_DATA.read_text()) or {}).get("session_without_effect")
             or {}).get(str(dialect or "").lower())
    if not known:
        return f"no list of settings without effect for the {dialect or 'run'} dialect"
    text = re.sub(r"--[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", sql, flags=re.S))
    if "[" in text:
        # A Tableau parameter in the Initial SQL ([TableauServerUser], ...): per viewer.
        return "it names a Tableau parameter, whose value depends on who views the workbook"
    settings = {x.upper() for x in known.get("alter_session_set") or []}
    uses = {x.upper() for x in known.get("use") or []}
    for stmt in (x.strip() for x in re.split(r";(?=(?:[^']*'[^']*')*[^']*$)", text)):
        if not stmt:
            continue
        m = re.fullmatch(r"(?is)ALTER\s+SESSION\s+SET\s+(.+)", stmt)
        if m:
            names = re.findall(r"(?i)([A-Z_][A-Z0-9_]*)\s*=\s*(?:'(?:[^']|'')*'|[^\s,']+)",
                               m.group(1))
            bad = [n for n in names if n.upper() not in settings]
            if not names or bad:
                return f"it sets {', '.join(bad) or 'a session setting'}"
            continue
        m = re.fullmatch(r"(?is)USE\s+([A-Z_]+)\s+\S+", stmt)
        if m and m.group(1).upper() in uses:
            continue
        return f"it runs {' '.join(stmt.split()[:2]).upper()}"
    return None


def _leaf_connection(ds: ET.Element, rel: ET.Element) -> ET.Element | None:
    """The connection a relation is read through: the named connection it names, else the
    first live connection that names a database."""
    named = {nc.get("name"): nc.find("connection") for nc in ds.iter("named-connection")
             if nc.find("connection") is not None}
    if rel.get("connection") in named:
        return named[rel.get("connection")]
    return next((c for c in _live_connections(ds) if c.get("dbname")), None)


def _qualify(name: str, database: str | None, schema: str | None) -> str:
    """A table name the source spells with fewer than three parts, completed from the
    connection it is read through: `orders` over `RETAIL`/`MERCH` is `retail.merch.orders`."""
    parts = [p for p in name.lower().split(".") if p]
    if len(parts) == 1 and schema:
        parts = [schema.lower(), *parts]
    if len(parts) == 2 and database:
        parts = [database.lower(), *parts]
    return ".".join(parts)


def _sql_tables(sql: str, dialect: str) -> list[str]:
    """Every REAL table a custom-SQL statement reads — joins, subqueries and set operations
    included, CTE names excluded — parsed in the run's declared dialect. None parsed (no
    dialect, or a statement the parser cannot read) is an empty list, never a guess."""
    if not dialect or not sql.strip():
        return []
    try:
        tree = _sqlshape.parse(sql, dialect)
    except _sqlshape.ParseError:
        return []
    return _sqlshape.extract_tables(tree)


def _leaves(ds: ET.Element) -> list[ET.Element]:
    """Every leaf relation of the live relation tree: the tables and custom-SQL statements a
    datasource's join (or single relation) reads."""
    conn = ds.find("connection")
    root = conn.find("relation") if conn is not None else None
    if root is None:
        root = ds.find(".//relation")
    if root is None:
        return []
    return [r for r in root.iter("relation")
            if (r.get("type") or "").lower() in ("text", "table")]


def _reads(ds: ET.Element, dialect: str) -> list[str]:
    """Every warehouse object the datasource reads: each table leaf of its relation tree, and
    each table its custom SQL names, qualified from the connection the leaf is read through.

    A custom-SQL datasource names no physical object of its own, so without this the tables
    its statement reads were in no lineage at all: the run's bounded table set never held
    them, and a query of the same tables in the history was out of scope."""
    named = {nc.get("name"): nc.find("connection")
             for nc in ds.iter("named-connection") if nc.find("connection") is not None}
    live = [c for c in _live_connections(ds) if c.get("dbname")]
    default = live[0] if live else None
    out: list[str] = []
    for leaf in _leaves(ds):
        # An element with no children is FALSE: `named.get(...) or default` read every leaf
        # through the first connection, its own named connection ignored.
        conn = named.get(leaf.get("connection"))
        if conn is None:
            conn = default
        db = conn.get("dbname") if conn is not None else None
        schema = conn.get("schema") if conn is not None else None
        if (leaf.get("type") or "").lower() == "text":
            sql = _unescape_custom_sql((leaf.text or "").strip())
            out += [_qualify(t, db, schema) for t in _sql_tables(sql, dialect)]
        else:
            table = leaf.get("table") or leaf.get("name")
            if table:
                out.append(_qualify(_bracket_join(table), db, schema))
    return sorted(set(out))


def _vc_physical(ds: ET.Element, conn: ET.Element, vcs: list[tuple[str, "VirtualConnection"]],
                 dialect: str, out: dict[str, Any]) -> dict[str, Any]:
    """A datasource on a VIRTUAL CONNECTION: its relation names a table of the virtual
    connection, and the virtual connection's own file says which warehouse object (or custom
    SQL) that table is. Without the file the datasource names no warehouse object at all,
    unless the table's name carries its location (`ORDERS (RETAIL.MERCH.ORDERS)`)."""
    caption = ds.get("caption") or ds.get("formatted-name") or ds.get("name") or ""
    names = [conn.get(a) for a in ("resourceName", "dbname", "server-ds-friendly-name",
                                   "caption", "name", "virtual-connection", "vconn-name")]
    want = {k for k in (_ds_name_key(n) for n in names) if k}
    vc = next((v for f, v in vcs if v.keys(f) & want), None)
    if vc is None and len(vcs) == 1:
        vc = vcs[0][1]
    vc_name = (conn.get("resourceName") or conn.get("dbname")
               or conn.get("server-ds-friendly-name") or "?")
    reads: list[str] = []
    primary = _primary_relation(ds)
    for leaf in _leaves(ds) or ([primary] if primary is not None else []):
        tname = (leaf.get("table") or leaf.get("name") or "").strip()
        if not tname:
            continue
        lin = _vc_table_lineage(vc, tname, dialect, caption, vc_name)
        reads += lin.pop("_reads", [])
        if leaf is primary or out["object_kind"] is None:
            out.update(lin)
    if out["object_kind"] is None:
        raise TableauError(
            f"datasource {caption!r} connects through the virtual connection {vc_name!r} and "
            f"names no table of it. Supply the virtual connection's .tvcx beside the "
            f"datasource.")
    if reads:
        out["reads"] = sorted(set(reads))
    return out


def _vc_table_lineage(vc: "VirtualConnection | None", tname: str, dialect: str, caption: str,
                      vc_name: str) -> dict[str, Any]:
    raw = _bracket_join(tname) if tname.startswith("[") else tname
    loc = _VC_LOCATION.match(raw)
    table = vc.table(tname) if vc is not None else None
    if table is None:
        if loc and loc.group("loc").count(".") >= 1:
            # Tableau's own spelling names the object: resolved from the name, as a table.
            obj = loc.group("loc").strip().lower()
            return {"object": obj, "object_kind": "table", "_reads": [obj]}
        if vc is None:
            raise TableauError(
                f"datasource {caption!r} connects through the virtual connection {vc_name!r}, "
                f"and no virtual-connection file for it is beside the input. Its tables, "
                f"columns and connections live in the virtual connection: save it as a .tvcx "
                f"(see docs/getting-started.md, \"Tableau files to supply\") beside the "
                f"datasource and re-run.")
        raise TableauError(
            f"datasource {caption!r} reads table {tname!r} of the virtual connection "
            f"{vc.name or vc.label!r}, which holds no such table "
            f"({', '.join(sorted(vc.tables)[:8]) or 'none'}).")
    c = vc.connection_of(table)
    if c is not None:
        for classes, what, supply in _EXTERNAL_MODELS:
            if _norm_class(c.get("class")) in classes:
                raise TableauError(
                    f"datasource {caption!r} reads {tname!r} of the virtual connection "
                    f"{vc.name!r}, which connects to {what}. Its model lives outside "
                    f"Tableau. {supply}")
    db = table.get("dbname") or (c.get("dbname") if c is not None else None)
    schema = table.get("schema") or (c.get("schema") if c is not None else None)
    lin: dict[str, Any] = {"connection": (c.get("class") or "").lower() or None
                           if c is not None else None,
                           "database": db, "schema": schema}
    rel = table.find("relation")
    if rel is not None and (rel.get("type") or "").lower() == "text" and (rel.text or "").strip():
        sql = _unescape_custom_sql(rel.text.strip())
        lin.update(object=None, object_kind="custom-sql",
                   raw_expression=_mquery.scrub_m(sql),
                   _reads=[_qualify(t, db, schema) for t in _sql_tables(sql, dialect)])
        return lin
    phys = (rel.get("table") or rel.get("name")) if rel is not None else None
    if phys:
        obj = _qualify(_bracket_join(phys), db, schema)
    elif loc and loc.group("loc").count(".") >= 1:
        obj = loc.group("loc").strip().lower()
    else:
        raise TableauError(
            f"virtual connection {vc.name!r}: table {tname!r} names no warehouse object (no "
            f"<relation> and no location in its name).")
    lin.update(object=obj, object_kind="table", _reads=[obj])
    return lin


def _logical_tables(ds: ET.Element) -> dict[str, Any] | None:
    """Tableau's RELATIONSHIPS model (2020.2+): a data source whose live relation is a
    `collection` of logical tables. Returns each table's leaf relation by its name, every
    column's table (`<metadata-record>` `parent-name`) and remote (warehouse) name, and the
    column pairs its `<object-graph>` relates; None for any other data source."""
    conn = ds.find("connection")
    if conn is None or (conn.get("class") or "").lower() == "sqlproxy":
        return None             # a published data source's proxy: its file holds the model
    root = conn.find("relation")
    if root is None or (root.get("type") or "").lower() != "collection":
        return None
    leaves = [r for r in root if r.tag == "relation"
              and (r.get("type") or "").lower() in ("table", "text")]
    if len(leaves) < 2:
        return None
    # A column's `parent-name` names its logical table by the object graph's object id
    # (`DIM_CUSTOMER (DB.DIM_CUSTOMER)_695E…`) in a published file, by the relation's name in
    # others: both resolve to the relation's name.
    object_of = {}
    for obj in ds.iter("object"):
        rel = obj.find("properties/relation")
        if obj.get("id") and rel is not None and rel.get("name"):
            object_of[obj.get("id")] = rel.get("name")
    parent, remote = {}, {}
    for rec in ds.iter("metadata-record"):
        if (rec.get("class") or "") != "column":
            continue
        local = (rec.findtext("local-name") or "").strip().strip("[]")
        if local:
            owner = (rec.findtext("parent-name") or "").strip().strip("[]")
            parent[local.lower()] = object_of.get(owner, owner)
            remote[local.lower()] = (rec.findtext("remote-name") or local).strip()
    pairs = []
    for rel in ds.iter("relationship"):
        expr = rel.find("expression")
        if expr is None or expr.get("op") != "=":
            continue            # a range or another condition is not a column pair
        sides = [e.get("op") for e in expr.findall("expression")]
        if len(sides) == 2 and all(s and s.startswith("[") for s in sides):
            pairs.append(tuple(s.strip("[]") for s in sides))
    return {"leaves": {(r.get("name") or "").strip(): r for r in leaves},
            "parent": parent, "remote": remote, "pairs": pairs}


def _primary_relation(ds: ET.Element) -> ET.Element | None:
    """The relation the datasource's rows come from, read off the LIVE connection.

    Two things defeated a plain `.//relation`. A datasource whose root relation is a JOIN
    (`type='join'`) has no table and no SQL of its own — the first leaf under it is the
    primary side, the one Tableau's join starts from. And an extracted datasource carries a
    second relation tree under its extract connection, naming `[Extract].[…]` tables that
    exist only in the .hyper file; the live connection's tree comes first, and only it is read.
    """
    conn = ds.find("connection")
    root = conn.find("relation") if conn is not None else None
    if root is None:
        root = ds.find(".//relation")
    if root is None:
        return None
    # A JOIN, or a COLLECTION: Tableau's relationships model (2020.2+) lists a data source's
    # logical tables in one `<relation type='collection'>`, related in its object graph, and
    # names no table of its own (seen live: a data source read as no table at all).
    if (root.get("type") or "").lower() not in ("join", "collection"):
        return root
    for leaf in root.iter("relation"):
        if (leaf.get("type") or "").lower() in ("text", "table"):
            return leaf
    return None


def _unescape_custom_sql(sql: str) -> str:
    """Tableau doubles `<` and `>` inside custom SQL (`<<>>` is `<>`); undone, or the
    statement does not parse."""
    return sql.replace("<<", "<").replace(">>", ">")


_PROJECT_SUFFIX = re.compile(r"\s*\|\s*project\s*:.*$", re.I)


def _ds_name_key(name: str | None) -> str:
    """A datasource name normalised for matching a workbook's reference to its .tds.

    A published datasource is spelled several ways: the .tds's `formatted-name` or file stem
    (`sales_ds`), the workbook's `caption` and `server-ds-friendly-name` (`sales_ds | Project
    : Finance`), and the sqlproxy connection's `dbname`. Case, separators and the project
    suffix are not part of the identity.
    """
    s = _PROJECT_SUFFIX.sub("", (name or "").strip())
    return re.sub(r"[\s_\-]+", " ", s).strip().lower()


def _published_keys(ds: ET.Element, file_name: str = "") -> set[str]:
    """Every name a datasource answers to, normalised. Used on BOTH sides of the match."""
    rl = ds.find("repository-location")
    conn = ds.find(".//connection")
    names = [rl.get("id") if rl is not None else None,
             ds.get("formatted-name"), ds.get("caption")]
    if conn is not None and (conn.get("class") or "").lower() == "sqlproxy":
        names += [conn.get("dbname"), conn.get("server-ds-friendly-name")]
    names += _file_stems(file_name)
    return {k for k in (_ds_name_key(n) for n in names) if k}


_LOCAL_COPY = re.compile(r"\s*\(local copy\)\s*$", re.I)


def _file_stems(file_name: str) -> list[str]:
    """The names a datasource FILE gives it. Tableau names the .tds inside a downloaded
    .tdsx `<Name> (local copy).tds`, and a REST download is URL-encoded (`Sales+DS.tdsx`):
    both spell the datasource's name, so both are tried bare."""
    if not file_name or not file_name.lower().endswith(".tds"):
        return []
    stem = pathlib.PurePosixPath(file_name).stem
    bare = _LOCAL_COPY.sub("", urllib.parse.unquote_plus(stem))
    return list(dict.fromkeys([stem, bare]))


def _qualified(lineage: dict[str, Any]) -> str | None:
    obj = lineage.get("object")
    if not obj:
        return None
    if obj.count(".") >= 2:
        return obj
    # A `schema.table` takes only the database; a bare table the database and the schema.
    # Prepending both to `schema.table` repeated the schema.
    return _qualify(obj, lineage.get("database"), lineage.get("schema"))


def _folders(ds: ET.Element) -> dict[str, str]:
    """The folder each field sits in, by internal name: `<folder name=…><folder-item name=…/>`
    on the data source (and its newer `<folders-common>` wrapper); a folder inside a folder is
    joined with a backslash, as Power BI writes a nested display folder."""
    out: dict[str, str] = {}

    def walk(el: ET.Element, path: list[str]) -> None:
        for f in el.findall("folder"):
            here = path + [(f.get("name") or "").strip()]
            for it in f.findall("folder-item"):
                if (it.get("type") or "field") == "field" and it.get("name"):
                    out.setdefault(it.get("name"), "\\".join(p for p in here if p))
            walk(f, here)

    walk(ds, [])
    for fc in ds.findall("folders-common"):
        walk(fc, [])
    return out


def _desc(c: ET.Element) -> str:
    """A field's comment (Edit Comment): the text of its `<desc><formatted-text><run>`s."""
    d = c.find("desc")
    if d is None:
        return ""
    return " ".join(" ".join((r.text or "").split()) for r in d.iter("run")
                    if (r.text or "").strip()).strip()


def _columns(ds: ET.Element) -> tuple[list[dict], dict[str, dict]]:
    """Real columns and calculated fields. The internal name is what pills reference."""
    cols: list[dict] = []
    index: dict[str, dict] = {}
    folder_of = _folders(ds)
    # A union's relations list their own columns (`<relation type='union'><columns>`): the
    # tables' columns, not the data source's fields.
    in_relation = {id(c) for r in ds.iter("relation") for c in r.iter("column")}
    for c in ds.findall(".//column"):
        if id(c) in in_relation:
            continue
        internal = c.get("name") or ""
        calc = c.find("calculation")
        formula = calc.get("formula") if calc is not None else None
        if calc is not None and (calc.get("class") or "") == "categorical-bin" and calc.get("column"):
            # A Tableau GROUP: named bins over a base field. Its definition IS a CASE over the
            # base column's members — written so filters and group-bys on it compile. A member
            # no bin holds keeps its own value (checked live), and the Null member is a member.
            whens, null_label, base = [], None, calc.get("column")
            for b in calc.findall("bin"):
                label = (b.get("value") or "").strip()
                for v in b.findall("value"):
                    text = (v.text or "").strip()
                    if text == "%null%":
                        null_label = null_label or label
                    elif text:
                        whens.append(f"WHEN {text} THEN {label}")
            # `default` names the Other bin (Include Other): every member no bin holds, the
            # Null member too (seen in a web-editor sample), is in it.
            other = (calc.get("default") or "").strip() or None
            rest = other or base
            if null_label is not None:
                formula = (f"CASE WHEN ISNULL({base}) THEN {null_label} "
                           + " ".join(f"WHEN {base} = {w[5:]}" for w in whens)
                           + f" ELSE {rest} END")
            elif whens:
                formula = f"CASE {base} " + " ".join(whens) + f" ELSE {rest} END"
            else:
                # A group with no bins yet: every member keeps its own value.
                formula = rest if other else f"{base}"
        if calc is not None and (calc.get("class") or "") == "bin" and calc.get("formula"):
            # A numeric BIN: each value's bin is named by its lower bound, `size` wide from
            # `peg`. A size a parameter sets (`size-parameter='[Parameters].[X]'`, which
            # Tableau uses over `size`, seen live) is that parameter's current value,
            # substituted where the calc is compiled as every parameter is.
            size = (calc.get("size-parameter") or calc.get("size") or "").strip()
            peg = calc.get("peg") or "0"
            sized = (bool(re.fullmatch(r"-?\d+(\.\d+)?", size)) and float(size) > 0) \
                or (bool(_PARAM_REF.fullmatch(size)) and bool(calc.get("size-parameter")))
            if sized and re.fullmatch(r"-?\d+(\.\d+)?", peg):
                formula = (f"FLOOR(({calc.get('formula')} - {peg}) / {size}) * {size} + {peg}"
                           if float(peg) else f"FLOOR({calc.get('formula')} / {size}) * {size}")
            else:
                formula = "BIN_SIZE_UNREAD()"
        rec = {
            "name": (c.get("caption") or internal.strip("[]")).strip(),
            # The author's own label, kept SEPARATE from the internal name: "Units Sold" is
            # what a person searches for, `units_sold` is what the SQL uses.
            "caption": (c.get("caption") or "").strip() or None,
            "default_format": c.get("default-format"),
            "data_type": c.get("datatype"),
            # The author's comment on the field, and the folder the data source shows it in.
            "description": _desc(c),
            "is_calculated": formula is not None,
            "formula": formula,
            "formula_language": "tableau-calc" if formula else None,
            "source_column": (c.get("name") or "").strip("[]").lower() if not formula else None,
            "role": ("measure" if (c.get("role") or "").lower() == "measure"
                     else "dimension" if c.get("role") else None),
        }
        if folder_of.get(internal):
            rec["folder"] = folder_of[internal]
        labels = [{"value": None if (a.get("key") or "").strip() == "%null%"
                   else (a.get("key") or "").strip().strip('"'), "label": a.get("value") or ""}
                  for a in c.findall("aliases/alias") if a.get("key") is not None]
        if labels:
            # ALIASES: the label the view shows for a member (`<aliases><alias key='"A"'
            # value='Label'/>`), read off the column itself.
            rec["value_labels"] = labels
        cols.append(rec)
        index[internal] = rec
    sets = {g.get("name") or "": g for g in ds.findall("group")}
    for g in ds.findall("group"):
        # A SET: In where its definition holds, Out elsewhere (an unknown test is Out). Its
        # <groupfilter> tree is read as a condition (`_set_condition`): members, a range, a
        # condition on the member's aggregate, a Top N by one, and their union, intersection
        # and difference.
        internal = g.get("name") or ""
        if not internal or internal in index:
            continue
        top = g.find("groupfilter")
        try:
            cond = _set_condition(top, sets, 0) if top is not None else "FALSE"
            formula = f"IF {cond} THEN \"In\" ELSE \"Out\" END"
        except ValueError as exc:
            formula = None
            rec_refusal = str(exc)
        else:
            rec_refusal = None
        rec = {"name": (g.get("caption") or internal.strip("[]")).strip(),
               "caption": (g.get("caption") or "").strip() or None, "default_format": None,
               "data_type": "string", "description": "", "is_calculated": True,
               "formula": formula or f"SET_NOT_READ({json.dumps(rec_refusal)})",
               "formula_language": "tableau-calc", "source_column": None,
               "role": "dimension"}
        cols.append(rec)
        index[internal] = rec
    return cols, index


def _set_condition(gf: ET.Element, sets: dict, depth: int) -> str:
    """A set's <groupfilter> tree as a condition in the calc language. Each function as Tableau
    saves it: `member` (one member; `%null%` the Null one), `union` / `intersection` /
    `except` of their children, `level-members` (every member), `empty-level` (none),
    `range` (from / to), `filter` (the child's members whose aggregate passes `expression`,
    computed per member before the dimension filters, as a FIXED level of detail is) and
    `end` over `order` (the Top or Bottom `count` members by the order's expression; the count
    a number, a parameter or an expression of them), and `order` alone (its child's members,
    ordered: the order changes no membership, checked live: every member In)."""
    fn = gf.get("function") or ""
    kids = [c for c in gf if c.tag == "groupfilter"]
    level = _set_field(gf.get("level") or "")
    if fn == "member":
        m = gf.get("member") or ""
        return f"ISNULL({level})" if m == "%null%" else f"({level} = {m})"
    if fn in ("union", "intersection"):
        if not kids:
            return "FALSE" if fn == "union" else "TRUE"
        joiner = " OR " if fn == "union" else " AND "
        return "(" + joiner.join(_set_condition(k, sets, depth + 1) for k in kids) + ")"
    if fn == "except" and kids:
        rest = [_set_condition(k, sets, depth + 1) for k in kids[1:]]
        return ("(" + _set_condition(kids[0], sets, depth + 1)
                + "".join(f" AND NOT IFNULL({r}, FALSE)" for r in rest) + ")")
    if fn == "level-members":
        return "TRUE"
    if fn == "empty-level":
        return "FALSE"
    if fn == "range" and level:
        lo, hi = gf.get("from"), gf.get("to")
        parts = ([f"{level} >= {lo}"] if lo else []) + ([f"{level} <= {hi}"] if hi else [])
        return "(" + " AND ".join(parts or ["TRUE"]) + ")"
    if fn == "filter" and gf.get("expression") and len(kids) == 1:
        lev = _set_level(kids[0])
        return (f"({_set_condition(kids[0], sets, depth + 1)} AND "
                f"{{FIXED {lev} : {gf.get('expression')}}})")
    if fn == "order" and len(kids) == 1:
        return _set_condition(kids[0], sets, depth + 1)
    if fn == "end" and len(kids) == 1 and kids[0].get("function") == "order":
        order = kids[0]
        inner = [c for c in order if c.tag == "groupfilter"]
        count = (gf.get("count") or "").strip()
        # The count is a number, a parameter (`[Parameters].[X]`, at its current value) or an
        # expression of them; the compiler folds it to a whole number.
        if not count or len(inner) != 1 or not order.get("expression"):
            raise ValueError("a Top N set with no count, or whose order is not one expression "
                             "over one set of members")
        desc = (order.get("direction") or "DESC").upper() == "DESC"
        if (gf.get("end") or "top").lower() == "bottom":
            desc = not desc
        lev = _set_level(inner[0])
        among = _set_condition(inner[0], sets, depth + 1)
        # The Top N ranks only the members its child keeps (checked live: the top 2 of a
        # union of four members are the top 2 of those four), so the child's condition is
        # also the ranking's own (its fifth argument).
        return (f"({among} AND TOPN_SET_MEMBER({lev}, {count}, "
                f"'{'desc' if desc else 'asc'}', {order.get('expression')}"
                + (f", {among}" if among != "TRUE" else "") + "))")
    raise ValueError(f"a set defined by a <groupfilter function={fn!r}> this reader does "
                     f"not read")


def _set_level(gf: ET.Element) -> str:
    """The field a set's groupfilter ranges over (`level` or `member`), or, on a combination
    that names none itself (a `union` of members), the field its members name."""
    for el in gf.iter("groupfilter"):
        lev = _set_field(el.get("level") or "") or (
            _set_field(el.get("member") or "") if el.get("function") == "empty-level" else "")
        if lev:
            return lev
    raise ValueError("a set condition over a groupfilter naming no field")


def _set_field(ref: str) -> str:
    """`[none:X:nk]` (a pill) or `[X]` as the field `[X]`."""
    m = re.fullmatch(r"\[none:(.+):[a-z]+\]", ref or "")
    return f"[{m.group(1)}]" if m else ref


# ------------------------------------------------------------------------ worksheets
def _pill_field(raw: str) -> tuple[str | None, str | None]:
    """'[dsid].[sum:Sales:qk]' -> (dsid, 'sum:Sales:qk')."""
    m = PILL.search(raw or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def _aggregation_of(field: str) -> tuple[str | None, str]:
    """Split the shelf's aggregation prefix off the field name.

    Tableau encodes a pill as `<agg>:<FieldName>:<typecode>`, where `<agg>` is `none` for a
    dimension. Taking segment 0 unconditionally yields the literal string "none" as a column
    name — which then appears in every downstream filter and shape key.

    This is trap 1: the aggregation is on the SHELF, not on the field, so the same field
    yields a different metric depending on where it was dropped.
    """
    kind, value, bare = _derivation(field)
    return (value if kind == "agg" else None), bare


# A date pill's derivation: a DATE PART (`yr:`, `mn:` — discrete, the month across years) or a
# TRUNCATION (`tmn:` — the continuous month). Neither is an aggregation, and neither is the
# raw date: grouping by the raw date is a different grain.
DATE_PART = {"yr": "year", "qr": "quarter", "mn": "month", "wk": "week", "dy": "day",
             "wd": "weekday", "hr": "hour", "mi": "minute", "sc": "second"}
DATE_TRUNC = {"tyr": "year", "tqr": "quarter", "tmn": "month", "twk": "week", "tdy": "day",
              "thr": "hour", "tmi": "minute", "tsc": "second",
              # Month / Year and Month / Day / Year are a truncation shown as two parts.
              "my": "month", "mdy": "day"}
# `<column-instance derivation=…>`: the same choice, spelled out.
_DERIVATION_ATTR = {
    "sum": ("agg", "sum"), "avg": ("agg", "avg"), "min": ("agg", "min"),
    "max": ("agg", "max"), "count": ("agg", "count"), "countd": ("agg", "count_distinct"),
    "median": ("agg", "median"), "attribute": ("agg", "single_value"),
    "user": ("agg", "custom"),
    "stdev": ("agg", "stdev"), "stdevp": ("agg", "stdevp"), "var": ("agg", "var"),
    "varp": ("agg", "varp"), "none": ("none", None),
    "year": ("date_part", "year"), "quarter": ("date_part", "quarter"),
    "month": ("date_part", "month"), "week": ("date_part", "week"),
    "day": ("date_part", "day"), "weekday": ("date_part", "weekday"),
    "hour": ("date_part", "hour"), "minute": ("date_part", "minute"),
    "second": ("date_part", "second"),
    "year-trunc": ("date_trunc", "year"), "quarter-trunc": ("date_trunc", "quarter"),
    "month-trunc": ("date_trunc", "month"), "week-trunc": ("date_trunc", "week"),
    "day-trunc": ("date_trunc", "day"), "hour-trunc": ("date_trunc", "hour"),
    "minute-trunc": ("date_trunc", "minute"), "second-trunc": ("date_trunc", "second"),
    "my": ("date_trunc", "month"), "mdy": ("date_trunc", "day"),
}
# `DATEPART('weekday' | 'week', <field>)`, `DATENAME('week', <field>)`, `DATETRUNC('week',
# <field>)` or `DATEDIFF('week', <field>, <field>)` without its start-of-week argument; and
# `WEEK(<field>)`, which is `DATEPART('week', <field>)`.
_WEEK_ARG = r"(?:\[[^\]]+\]|#[^#]+#)"
_WEEKDAY_NO_START = re.compile(r"((?:DATEPART\(\s*'(?:weekday|week)'|DATENAME\(\s*'week'|"
                               r"DATETRUNC\(\s*'week'|DATEDIFF\(\s*'week'\s*,\s*" + _WEEK_ARG
                               + r")\s*,\s*" + _WEEK_ARG + r")\s*\)", re.I)
_WEEK_FN = re.compile(r"\bWEEK\(\s*(\[[^\]]+\])\s*\)", re.I)
# An aggregation as the Tableau function that computes it.
_AGG_FN = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
           "count_distinct": "COUNTD", "median": "MEDIAN", "stdev": "STDEV",
           "stdevp": "STDEVP", "var": "VAR", "varp": "VARP"}
_AGG_HEADS = {**AGG_NAME, "ctd": "count_distinct", "cntd": "count_distinct",
              "med": "median", "usr": "custom", "stdevp": "stdevp", "varp": "varp"}


def _derivation(field: str, instances: dict[str, tuple[str, str]] | None = None
                ) -> tuple[str, str | None, str]:
    """(kind, value, field) for one pill: kind is `agg` (value: the aggregation), `date_part`
    or `date_trunc` (value: the part), or `none` (a plain dimension).

    The worksheet's own `<column-instance derivation=…>` is read first where it names the
    pill; the name's prefix otherwise. A table calculation stacked on an aggregate
    (`pcto:sum:Sales:qk`) is `custom` over the field, never a column called `sum`."""
    raw = (field or "").strip("[]")
    inst = (instances or {}).get(raw)
    if inst is not None:
        column, derivation = inst
        hit = _DERIVATION_ATTR.get(derivation.lower())
        if hit is not None:
            return hit[0], hit[1], column
    parts = raw.split(":")
    if len(parts) < 2:
        return "none", None, parts[0].strip("[]")
    head = parts[0].lower()
    if head in ("none", "io"):
        # `io`: a set shown as In / Out of it.
        return "none", None, parts[1].strip("[]")
    if head in _AGG_HEADS:
        return "agg", _AGG_HEADS[head], parts[1].strip("[]")
    if head in DATE_PART:
        return "date_part", DATE_PART[head], parts[1].strip("[]")
    if head in DATE_TRUNC:
        return "date_trunc", DATE_TRUNC[head], parts[1].strip("[]")
    if len(parts) >= 4 and parts[1].lower() in _AGG_HEADS:
        # A table calculation over an aggregate: post-aggregation, named as `custom`.
        return "agg", "custom", parts[2].strip("[]")
    # A user-defined aggregation prefix we do not recognise: the field is still segment 1
    # when the pill is segmented, and `custom` is the admitted gap.
    if len(parts) >= 3:
        return "agg", "custom", parts[1].strip("[]")
    return "none", None, parts[0].strip("[]")


def _instances(ws: ET.Element) -> dict[str, tuple[str, str]]:
    """The worksheet's column instances: pill name -> (field, derivation)."""
    out: dict[str, tuple[str, str]] = {}
    for ci in ws.iter("column-instance"):
        name, column = (ci.get("name") or "").strip("[]"), (ci.get("column") or "").strip("[]")
        if name and column and ci.get("derivation"):
            out[name] = (column, ci.get("derivation"))
    return out


# ----------------------------------------------------------------- table calculations
# A pill with a <table-calc> child is computed over the sheet's AGGREGATED rows (a quick table
# calculation on an aggregate, or the compute-using of a calc that calls a table-calculation
# function). Compiled as its bare aggregate it is silently wrong, so each one is either turned
# into its formula plus the columns it runs along, or named as a refusal.
#
# Checked live (hand-built sheets, the view's own data): `ordering-type` Rows is Table
# (across), Columns is Table (down), Table runs along every row dimension, Pane along the
# innermost row dimension, CellInPane along none, Field along its <order> fields; a calc pill
# with no <table-calc> runs as Rows. Rows sort ascending with an empty value first. A moving
# window (`WindowTotal`) leaves the current row out unless `window-options` says
# IncludeCurrent. Percentile ranks ascending from 0. A quick Difference / Percent Difference /
# Percent From (`PctValue`) is from the member its <address> names: relative (-1), a fixed
# member's literal (`Fixed`), or a parameter (`Fixed,Parameter`, the address a quoted
# `[Parameters].[X]`); `level-break` is the twb schema's Restarting every.
_TC_ATTRS = {"type", "ordering-type", "aggregation", "rank-options", "from", "to",
             "window-options", "diff-options", "ordering-field", "level-address",
             "level-break", "restart-every", "field"}
# The children a <table-calc> may hold: its addressing fields, and a difference's address.
_TC_CHILDREN = {"address"}
_TC_AGG = {"sum": ("SUM", "Sum"), "avg": ("AVG", "Average"), "min": ("MIN", "Minimum"),
           "max": ("MAX", "Maximum"), "count": ("COUNT", "Count")}
_PILL_AGG_LABEL = {"sum": "", "avg": "Avg. ", "min": "Min. ", "max": "Max. ",
                   "count": "Count of ", "count_distinct": "Distinct count of ",
                   "median": "Median "}
_RANK_FN = {"competition": "RANK", "modified": "RANK_MODIFIED", "dense": "RANK_DENSE",
            "unique": "RANK_UNIQUE"}
# The table-calculation functions of the calc language: a calc that calls one runs over the
# sheet's rows by its pill's compute-using.
TABLE_CALC_FN = re.compile(
    r"\b(RUNNING_(SUM|AVG|MIN|MAX|COUNT)|WINDOW_\w+|TOTAL|INDEX|FIRST|LAST|SIZE|LOOKUP|"
    r"PREVIOUS_VALUE|RANK(_DENSE|_MODIFIED|_PERCENTILE|_UNIQUE)?|SCRIPT_\w+)\s*\(", re.I)


# A quick table calculation's pill name when the file has no <table-calc> beside it.
_QUICK_PREFIX = re.compile(r"^(pcto|cum|diff|pcdf|rank|pctrank|win|ytd|cgr):", re.I)
_SORT_TAGS = {"computed-sort", "manual-sort", "natural-sort", "shelf-sort", "alphabetic-sort"}


def _is_sort_tag(tag: str) -> bool:
    """A sort element of a <view>; `shelf-sort` also comes in versioned forms (`shelf-sort-*`)."""
    return tag in _SORT_TAGS or tag.startswith("shelf-sort-")


def _tc_record(tc: ET.Element) -> dict:
    """One <table-calc> element: its attributes, `order` the fields of its <order> children
    (a pill reference each, or its one `ordering-field`), `address` its <address> values."""
    rec = {k: v for k, v in tc.attrib.items()}
    rec["order"] = [o.get("field") or "" for o in tc.findall("order")]
    if not rec["order"] and tc.get("ordering-field"):
        # One addressing field, as an attribute.
        rec["order"] = [tc.get("ordering-field") or ""]
    rec["address"] = [(v.text or "").strip() for a in tc.findall("address")
                      for v in a.findall("value")]
    rec["_children"] = sorted({c.tag for c in tc if c.tag != "order"} - _TC_CHILDREN)
    return rec


def _table_calc_instances(ws: ET.Element) -> dict[str, dict]:
    """Pill name -> its <table-calc> attributes (`_tc_record`), for every column instance
    that has one. A pill may hold several, as Desktop saves them (checked live): the first
    with no `field` is the pill's own; one whose `field` names another calc is the
    compute-using of that NESTED calc (`nested`, by the field it names); a further one with
    no `field` is a SECONDARY calculation run over the result of the one before it
    (`secondary`, in order: a quick Running Total, then a Percent Difference of it)."""
    out: dict[str, dict] = {}
    for ci in ws.iter("column-instance"):
        name = (ci.get("name") or "").strip("[]")
        own_column = ci.get("column") or ""
        els = ci.findall("table-calc")
        if not els or not name:
            continue
        own, nested, secondary = None, {}, []
        for el in els:
            field = el.get("field") or ""
            if field and not (own_column and field.endswith(own_column)):
                nested[field] = _tc_record(el)
            elif own is None:
                own = _tc_record(el)
            else:
                secondary.append(_tc_record(el))
        rec = own if own is not None else {"ordering-type": "Rows", "order": [],
                                           "address": [], "_children": []}
        if nested:
            rec["nested"] = nested
        if secondary:
            rec["secondary"] = secondary
        out[name] = rec
    return out


def _quick_calc(tc: dict, agg: str, field: str, inner: str, member_of=None,
                over: tuple[str, str] | None = None) -> tuple[str, str | None, str | None]:
    """(label, formula, refusal) of a quick table calculation over `agg` of a field (`inner`
    is the field or its formula, bracketed). The formula is the calc language's own.
    `member_of(ref)` is an addressed pill's dimension as the calc language writes it (for a
    difference from a fixed member); `over` (label, calc reference) is the calculation a
    SECONDARY calculation runs over, in place of the aggregate."""
    kind = tc.get("type") or ""
    fn = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
          "count_distinct": "COUNTD", "median": "MEDIAN"}.get(agg)
    lab = f"{_PILL_AGG_LABEL.get(agg, '')}{field}"
    if over is not None:
        lab, fn, inner = over[0], "", over[1]
    if fn is None:
        return f"{kind} of {field}", None, (f"a quick table calculation ({kind}) over a "
                                            f"{agg} aggregation")
    base = f"{fn}({inner})" if fn else inner
    second = (tc.get("aggregation") or "").lower()
    if kind == "PctTotal":
        return f"% of Total {lab}", f"{base} / TOTAL({base})", None
    if kind == "CumTotal" and second in _TC_AGG:
        word = _TC_AGG[second][1]
        return f"Running {word} of {lab}", f"RUNNING_{_TC_AGG[second][0]}({base})", None
    if kind in ("Rank", "PctRank"):
        opts = [o.strip().lower() for o in (tc.get("rank-options") or "").split(",") if o.strip()]
        method = next((o for o in opts if o in _RANK_FN), "competition")
        desc = "ascending" not in opts if kind == "Rank" else "descending" in opts
        order = "'desc'" if desc else "'asc'"
        if kind == "PctRank":
            return f"Percentile of {lab}", f"RANK_PERCENTILE({base}, {order})", None
        return f"Rank of {lab}", f"{_RANK_FN[method]}({base}, {order})", None
    if kind == "WindowTotal" and second in _TC_AGG and tc.get("from") is not None \
            and tc.get("to") is not None:
        try:
            lo, hi = int(tc["from"]), int(tc["to"])
        except ValueError:
            return f"Moving calculation of {lab}", None, (
                f"a moving calculation from {tc.get('from')!r} to {tc.get('to')!r}")
        word = _TC_AGG[second][1]
        label = f"Moving {word} of {lab}"
        if "includecurrent" not in (tc.get("window-options") or "").lower():
            # The current row is left out of the window (checked live).
            if lo < 0 < hi:
                return label, None, ("a moving calculation that leaves out the current row "
                                     "from the middle of its window")
            hi = -1 if hi == 0 else hi
            lo = 1 if lo == 0 else lo
        return label, f"WINDOW_{_TC_AGG[second][0]}({base}, {lo}, {hi})", None
    if kind in ("Difference", "PctDiff", "PctValue"):
        # A difference from another member along the compute-using, in the formula Tableau
        # writes for it (checked live: the first member's is empty; Percent Difference divides
        # by the absolute value of the member it is computed from; Percent From is the value
        # over that member's). The member is relative (`diff-options='Relative'`, address
        # -1: the previous one), the first or last, or FIXED: the member the address names
        # (one value per addressed field, a literal of the calc language), or the current
        # value of the parameter it names (`Fixed,Parameter`, the address a quoted
        # `[Parameters].[X]`). Checked live: a fixed member is found in each partition, a
        # member no row holds (a text '2024' for a number, the Null member) leaves every row
        # empty, and the member's own row is 0 (100% for Percent From).
        opts = {o.strip().lower() for o in (tc.get("diff-options") or "Relative").split(",")}
        addr = [a for a in tc.get("address") or [] if a != ""]
        ref = None
        if "relative" in opts and len(addr) <= 1 and "parameter" not in opts:
            # A RELATIVE address is the editor's "Relative to" choice, not an offset: -1 the
            # previous member, 1 the next, -2 the FIRST and 2 the LAST (seen in an
            # editor-saved file; its view shows the first member's difference 0 and the
            # last's 0).
            try:
                n = int(addr[0]) if addr else -1
            except ValueError:
                n = None
            ref = {-1: f"LOOKUP(ZN({base}), -1)", 1: f"LOOKUP(ZN({base}), 1)",
                   -2: f"LOOKUP(ZN({base}), FIRST())", 2: f"LOOKUP(ZN({base}), LAST())"
                   }.get(n)
            if ref is None:
                return f"{kind} of {lab}", None, (
                    f"a quick {kind} relative to address {addr[0] if addr else '?'!s}: the "
                    f"editor writes only -1, 1, -2 and 2 (previous, next, first, last)")
            if "compounded" in opts and kind == "PctDiff":
                # Compound Growth Rate: the growth per step from the member it is computed
                # from, as the calc language writes the quick calculation (rendered live:
                # (value / first) ^ (1 / (INDEX() - 1)) - 1, the first member's 0%).
                return (f"% Difference in {lab}",
                        f"POWER(ZN({base}) / {ref}, ZN(1 / (INDEX() - 1))) - 1", None)
        elif "fixed" in opts and len(addr) == 1 and \
                addr[0].strip("%").lower() in ("first", "last"):
            ref = f"LOOKUP(ZN({base}), {addr[0].strip('%').upper()}())"
        elif "fixed" in opts and addr and member_of is not None:
            fields = list(tc.get("order") or [])
            if "parameter" in opts:
                addr = [a.strip('"') for a in addr]
                if not all(re.fullmatch(r"\[Parameters\]\.\[[^\[\]]+\]", a) for a in addr):
                    addr = []
            if len(addr) == len(fields) and fields:
                tests = []
                for f, v in zip(fields, addr):
                    dim = member_of(f, v)
                    if dim is None:
                        tests = []
                        break
                    tests.append("FALSE" if v == "%null%" else f"MIN({dim}) = {v}")
                if tests:
                    ref = (f"WINDOW_MAX(IIF({' AND '.join(tests)}, ZN({base}), NULL))")
        if ref is not None:
            if kind == "Difference":
                return f"Difference in {lab}", f"ZN({base}) - {ref}", None
            if kind == "PctValue":
                return f"% of {lab}", f"ZN({base}) / {ref}", None
            return f"% Difference in {lab}", f"(ZN({base}) - {ref}) / ABS({ref})", None
        return f"{kind} of {lab}", None, (
            f"a quick {kind} from {', '.join(addr) or '?'} ({tc.get('diff-options')}): the "
            f"member it is computed from is not read")
    return f"{kind or 'table calculation'} of {lab}", None, (
        f"quick table calculation {kind or '?'!s}"
        + (f" ({second})" if second else "") + " is not compiled")


def _addressing(tc: dict | None, rows: list[str], cols: list[str], marks: list[str],
                label_of, expand=None) -> dict:
    """How a table calculation runs over the sheet: {addressing, partitioning} over the
    grouped columns, or {refusal}. `rows` / `cols` are the dimensions on each shelf, outer
    first; `marks` those on the Marks card; `label_of` maps an <order> pill to its column.

    Checked live on a crosstab (two dimensions on Rows, one on Columns): Rows runs along the
    Columns shelf (Table across), Columns along the Rows shelf (Table down), Table along both,
    rows first (across then down), Pane along the innermost dimension of each shelf,
    RowInPane along the innermost column dimension (Pane across), ColumnInPane along the
    innermost row dimension (Pane down), CellInPane along none. A dimension on the Marks card
    partitions (checked live: a running sum down a channel restarts per status on Color).
    Field runs along its fields; `level-address` keeps the fields to that level (the deeper
    ones partition, checked live) and `restart-every` restarts on a field (the ones after it
    are addressed). `expand(ref)` lists the sheet's pills of a field named bare."""
    tc = tc or {}
    odd = sorted(set(tc) - _TC_ATTRS - {"order", "_children", "address", "nested",
                                        "nested_calcs", "secondary"})
    if odd or tc.get("_children"):
        return {"refusal": "a table calculation with settings the compiler does not read ("
                           + ", ".join(odd + list(tc.get("_children") or [])) + ")"}
    how = tc.get("ordering-type") or "Rows"
    every = rows + cols + [m for m in marks if m not in rows + cols]
    if how == "Rows":
        along = list(cols)
    elif how == "Columns":
        along = list(rows)
    elif how == "Table":
        along = list(rows) + list(cols)
    elif how == "Pane":
        along = rows[-1:] + cols[-1:]
    elif how == "RowInPane":
        along = cols[-1:]
    elif how == "ColumnInPane":
        along = rows[-1:]
    elif how == "CellInPane":
        along = []
    elif how == "Field":
        along = []
        for f in tc.get("order") or []:
            lab = label_of(f)
            if lab in every or expand is None:
                along.append(lab)
            else:
                # The field itself, not one of its pills (`ordering-field='[ds].[Order
                # Date]'`, as the web editor saves a date's quick calculation): every pill of
                # that field the sheet shows, in shelf order (seen in an editor-saved file:
                # Year and Quarter of the date, the calc runs along both).
                along += expand(f) or [lab]
        if not along or any(a is None or a not in every for a in along):
            return {"refusal": "a table calculation computed along a field the sheet does not "
                               "group by"}
        for key in ("level-address", "level-break", "restart-every"):
            if tc.get(key):
                at = label_of(tc[key])
                if at not in along and key != "level-address" and at in every:
                    # Restarting on a field it does not run along: that field partitions
                    # it already.
                    continue
                if at not in along:
                    return {"refusal": f"a table calculation's {key} names a field it does "
                                       f"not run along"}
                k = along.index(at)
                # `level-address` keeps the fields down to it; `level-break` (Restarting
                # every, as the editor saves it) names the FIRST field addressed after the
                # restart, so the fields above it partition (seen in an editor-saved YTD
                # Total: Year / Quarter with level-break on the quarter restarts every year,
                # rendered so live); `restart-every` names the field it restarts on.
                along = (along[:k + 1] if key == "level-address" else
                         along[k:] if key == "level-break" else along[k + 1:])
    else:
        return {"refusal": f"a table calculation computed using {how!r}, an ordering this "
                           f"reader has not seen"}
    out = {"addressing": along, "partitioning": [c for c in every if c not in along]}
    if rows and cols and how in _PADDED:
        # On a crosstab the view lays every row out against every column and a calculation
        # along it runs over the empty cells too (checked live: a missing cell shows, with the
        # running value carried into it).
        out["pad"] = {"rows": list(rows), "cols": list(cols)}
    return out


# The compute-usings Tableau densifies a crosstab for (checked live; Table and Cell are not).
_PADDED = {"Rows", "Columns", "Pane", "RowInPane", "ColumnInPane"}


def _dimension_sorts(ws: ET.Element, label_of, formula_of) -> dict[str, dict]:
    """The sheet's sorts of its dimensions, by column: {direction, members (a manual order),
    by (the measure's formula a computed sort orders by), nested (sorted within each outer
    member of its shelf)}. An ascending natural sort is the view's default and not listed."""
    out: dict[str, dict] = {}
    for el in ws.iter():
        if not _is_sort_tag(el.tag):
            continue
        ref = el.get("column") or el.get("dimension-to-sort") or ""
        lab = label_of(ref) if ref else None
        if not lab:
            continue
        d = (el.get("direction") or "ASC").lower()
        if el.tag == "natural-sort" and d == "asc":
            continue
        rec: dict = {"direction": "desc" if d == "desc" else "asc"}
        if el.tag == "manual-sort":
            rec["members"] = [None if (b.text or "").strip() == "%null%" else
                              (b.text or "").strip().strip('"') for b in el.iter("bucket")]
        elif el.tag == "computed-sort" or el.tag.startswith("shelf-sort"):
            by = el.get("using") or el.get("measure-to-sort-by") or ""
            f = formula_of(by) if by else None
            if not f:
                rec["unread"] = True
            else:
                rec["by"] = f
            rec["nested"] = el.tag != "computed-sort"
        out[lab] = rec
    return out


def _date_label(kind: str, part: str, field: str) -> str:
    """The name Tableau shows on the pill: `YEAR(Order Date)`, `MONTH(Order Date)` for a
    truncated month too — its own label, kept distinct from the date part's."""
    return f"{part.upper()}({field})" if kind == "date_part" or part == "year" \
        else f"{part.upper()}-TRUNC({field})"


def _date_formula(kind: str, part: str, field: str) -> str:
    """The pill as a Tableau calc. A year part and a truncation group exactly as DATETRUNC
    does; any other part (the month ACROSS years) is DATEPART, never a truncation."""
    if kind == "date_trunc" or part == "year":
        return f"DATETRUNC('{part}', [{field}])"
    return f"DATEPART('{part}', [{field}])"


def _text_of(node: ET.Element | None) -> str | None:
    """Concatenate a Tableau formatted-text block. Titles, tooltips and captions all use it."""
    if node is None:
        return None
    runs = [r.text.strip() for r in node.findall(".//run") if r.text and r.text.strip()]
    if not runs and node.text and node.text.strip():
        runs = [node.text.strip()]
    return " ".join(runs) or None


def _number_format(ws: ET.Element, col_index: dict[str, dict]) -> str | None:
    """The author's number format: percent, currency, basis points.

    This is the single most valuable thing on a widget for question authoring, because it is
    the UNIT — and the SQL never carries it. `0.71` reads completely differently as a ratio,
    a percentage and a currency amount.
    """
    for fmt in ws.findall(".//format"):
        attr = (fmt.get("attr") or "").lower()
        if "number-format" in attr or attr in ("text-format", "numeric-format"):
            val = fmt.get("value")
            if val:
                return val
    for col in col_index.values():
        if col.get("default_format"):
            return col["default_format"]
    return None


def _axis_titles(ws: ET.Element) -> dict[str, str]:
    """Axis captions — the author's business vocabulary for the columns."""
    out: dict[str, str] = {}
    for ax in ws.findall(".//axis"):
        name = (ax.get("name") or ax.get("class") or "axis").lower()
        title = ax.get("caption") or _text_of(ax.find(".//title"))
        if title:
            out[name] = title
    for fmt in ws.findall(".//format"):
        if (fmt.get("attr") or "").lower().endswith("title") and fmt.get("value"):
            out.setdefault((fmt.get("field") or "title").lower(), fmt.get("value"))
    return out


def _reference_lines(ws: ET.Element) -> list[dict]:
    """A reference line is the business TARGET, written down. `0.8` on a sell-through chart
    means the threshold everyone is managing against — and it appears nowhere in the SQL."""
    out: list[dict] = []
    for rl in ws.findall(".//reference-line"):
        out.append({
            "value": rl.get("value") or rl.get("constant-value"),
            "label": rl.get("label") or rl.get("caption"),
            "kind": rl.get("type") or "line",
        })
    for rl in ws.findall(".//refline"):
        out.append({"value": rl.get("value"), "label": rl.get("label"), "kind": "line"})
    return [r for r in out if r.get("value") or r.get("label")]


def _conditional_formatting(ws: ET.Element) -> list[dict]:
    """A red/green rule carries the same signal as a reference line: where good becomes bad."""
    out: list[dict] = []
    for enc in ws.findall(".//encodings/color"):
        pal = enc.get("palette") or enc.get("type")
        if pal:
            out.append({"on": enc.get("column"), "palette": pal,
                        "center": enc.get("center")})
    return out


def _dashboards_of(root: ET.Element) -> dict[str, list[dict]]:
    """Which dashboards each worksheet appears on. Zones reference sheets by name.

    This is the container envelope's backbone — and `n_dashboards` is the BI lane's ranking
    signal, which on a BI-only run is the only one there is.
    """
    out: dict[str, list[dict]] = {}
    for i, dash in enumerate(root.findall("./dashboards/dashboard")):
        dname = dash.get("name") or f"dashboard-{i}"
        title_el = dash.find(".//title//run")
        dtitle = (title_el.text if title_el is not None else "") or dname
        for pos, zone in enumerate(dash.findall(".//zone")):
            sheet = zone.get("name")
            if sheet:
                out.setdefault(sheet, []).append(
                    {"name": dname, "title": dtitle, "position": pos})
    return out


def _asset(role: str, name: str | None, el: ET.Element | None = None) -> dict[str, Any]:
    """One BI asset's IDENTITY, as the vendor states it: the repository id, path and site
    Tableau Server records on a published workbook, datasource or view.

    Names alone are not an identity — two sites can each have a "Sales" workbook — and the
    repository id is what a catalog crawler keys the same object by. Atlan's GUID and
    qualified name are joined on at A2, from the run's resolved asset scope.
    """
    rl = el.find("repository-location") if el is not None else None
    return {"role": role, "name": name or None,
            "vendor_id": (rl.get("id") or None) if rl is not None else None,
            "path": (rl.get("path") or None) if rl is not None else None,
            "site": (rl.get("site") or None) if rl is not None else None,
            "catalog_id": None, "catalog_qualified_name": None}


def _dashboard_prose(root: ET.Element) -> dict[str, list[str]]:
    """Author-written text on each dashboard — text zones, titles, captions.

    This is where an author most often writes down, in plain English, what the dashboard is
    for. It is PROSE: it may ground a naming claim at C3 and can never authorise a filter.
    """
    out: dict[str, list[str]] = {}
    for i, dash in enumerate(root.findall("./dashboards/dashboard")):
        dname = dash.get("name") or f"dashboard-{i}"
        texts: list[str] = []
        for run in dash.findall(".//run"):
            if run.text and run.text.strip():
                texts.append(run.text.strip())
        out[dname] = texts
    return out


def _mark_class(ws: ET.Element) -> str:
    mark = ws.find(".//mark")
    return (mark.get("class") if mark is not None and mark.get("class") else "worksheet")


def _filter_field(raw: str) -> tuple[str | None, str | None]:
    """A filter's field: `[dsid].[pill]` on a worksheet, a bare `[field]` on a data source
    (a data source filter names its own field, never a data source)."""
    dsid, field = _pill_field(raw)
    if field:
        return dsid, field
    m = re.fullmatch(r"\s*\[([^\[\]]+)\]\s*", raw or "")
    return (None, m.group(1)) if m else (None, None)


# `<filter class='quantitative' included-values=…>`: which rows a range keeps beside the
# range itself (as Desktop saves it: in-range, in-range-or-null, non-null, all).
_INCLUDED = {"in-range": "range", "in-range-or-null": "range", "non-null": "not_null",
             "null": "is_null", "all": "all"}


_PARAM_REF = re.compile(r"\[Parameters\]\.\[([^\[\]]+)\]")


def _named_expression(expr: str, display, params: dict | None = None) -> str:
    """An expression a filter carries, with each field named as its formulas name it and each
    parameter by its caption (`[Parameters].[Threshold]`, as a calc's formula names it)."""
    expr = _PARAM_REF.sub(lambda m: f"[Parameters].[{(params or {}).get(m.group(1), (m.group(1),))[0]}]",
                          expr)
    if not display:
        return expr
    return re.sub(r"(?<!\[Parameters\]\.)\[([^\[\]]+)\]",
                  lambda m: f"[{display(m.group(1))}]", expr)


def _param_count(raw: str | None, params: dict | None) -> tuple[int | None, str | None]:
    """A Top N's count: a number, or a parameter (`[Parameters].[Parameter 1]`) at its current
    value. -> (count, the parameter's caption or None)."""
    raw = (raw or "").strip()
    if raw.isdigit():
        return int(raw), None
    m = _PARAM_REF.fullmatch(raw)
    if m and params and m.group(1) in params:
        caption, value = params[m.group(1)]
        try:
            n = float(str(value).strip().strip('"'))
        except ValueError:
            return None, caption
        return (int(n) if n == int(n) and n > 0 else None), caption
    return None, None


def _date_member(value: str, part: str | None) -> str:
    """A member of a truncated date pill as the date it names, which the truncation equals.

    Tableau saves a Month / Year member (`my:`) as `YYYYMM` and a Month / Day / Year member
    (`mdy:`) as `YYYYMMDD` (seen live: `member='202611'`). Compared as written, the number
    meets a date and Snowflake refuses the statement at compile time. A continuous truncation
    (`tmn:`) saves `#2026-11-01 00:00:00#`, a date literal whose marks are not SQL. Anything
    else is returned unchanged."""
    text = (value or "").strip()
    lit = re.fullmatch(r"#(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}:\d{2}))?#", text)
    if lit:
        day, clock = lit.groups()
        return day if clock in (None, "00:00:00") else f"{day} {clock}"
    ym = re.fullmatch(r"(\d{4})(\d{2})", text)
    if part == "month" and ym and 1 <= int(ym.group(2)) <= 12:
        return f"{ym.group(1)}-{ym.group(2)}-01"
    ymd = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if part == "day" and ymd and 1 <= int(ymd.group(2)) <= 12 \
            and 1 <= int(ymd.group(3)) <= 31:
        return f"{ymd.group(1)}-{ymd.group(2)}-{ymd.group(3)}"
    return value


def _filters(ws: ET.Element, col_index: dict[str, dict], *,
             scope: str | None = None, date_cols: list | None = None,
             display=None, stage: str | None = None,
             elements: list[ET.Element] | None = None,
             params: dict | None = None) -> list[dict]:
    """Every filter with its EVALUATION STAGE preserved (trap: order of operations).

    `scope` overrides the recorded scope — "datasource" for a workbook SHARED filter
    (`<shared-views>`, "apply to all worksheets using this data source"). `stage` overrides
    the stage — "datasource" for a DATA SOURCE filter (a `<filter>` of the data source
    itself, or of its extract), which applies to every row before any context filter.
    `elements` are the filters to read (default: every `<filter>` under `ws`). A filter on a
    DATE PILL (`YEAR(Order Date)` = 2025) filters that part or truncation, never the raw date:
    it names the derived field, declared like the pill's own (`date_cols`).

    The stage of a quantitative filter is the PILL's: a range on a dimension or a raw date is
    a row filter, and only a range on an aggregated pill (`SUM(Sales)`) is applied after
    aggregation."""
    out: list[dict] = []
    for f in (ws.findall(".//filter") if elements is None else elements):
        dsid, field = _filter_field(f.get("column") or "")
        if not field:
            continue
        kind, part, dbare = _derivation(field, _instances(ws))
        aggregated = kind == "agg"
        if kind in ("date_part", "date_trunc") and dbare and date_cols is not None:
            disp = display(dbare) if display else dbare
            if kind == "date_part":
                # The member is the part's NUMBER (`2025`, `3`): compared with DATEPART, never
                # with the truncation a year pill groups by.
                label = f"{part.upper()}-PART({disp})"
                date_cols.append((dsid, label, f"DATEPART('{part}', [{disp}])"))
            else:
                label = _date_label(kind, part, disp)
                date_cols.append((dsid, label, _date_formula(kind, part, disp)))
            bare = label
        else:
            _, bare = _aggregation_of(field)
        if not bare:
            continue        # a filter on Tableau's synthetic `[:Measure Names]` pill
        rec = col_index.get(f"[{bare}]") or {}
        if rec.get("is_calculated") and rec.get("name"):
            bare = rec["name"]      # a calc filter is resolved by caption, like its measure
        is_context = (f.get("context") or "").lower() == "true"
        fclass = (f.get("class") or "").lower()
        this_stage = stage or ("context" if is_context else "dimension")
        ranks = any("top" in (b.get("value") or "").lower() for b in f.findall(".//bin"))
        if stage is None and (fclass == "bin" or ranks or
                              (fclass == "quantitative" and aggregated)):
            this_stage = "measure"
        if stage is None and (f.get("kind") or "").lower() == "hide":
            # HIDDEN members (`kind='hide'`): seen live, a hidden member still counts in a
            # running sum over the view; only its row is left out of what the view shows.
            this_stage = "display"
        vals = _filter_values(f)
        if kind == "date_trunc":
            vals = [_date_member(v, part) for v in vals]
        rng = f.find(".//min")
        # An EXCLUDE filter: `<groupfilter function="except">` over (all members, the excluded
        # ones). Reading its members as an inclusion list inverts it — "hide NULL" became
        # "only NULL", and a KPI returned nothing.
        top = next((g for g in f if g.tag == "groupfilter"), None)
        top_fn = (top.get("function") or "").lower() if top is not None else ""
        exclude = top_fn == "except"
        # Every member selected: the filter is a control a reader sees, and narrows nothing.
        everything = (not vals and rng is None and top is not None and top_fn == "level-members")
        included = _INCLUDED.get((f.get("included-values") or "").lower())
        extra: dict[str, Any] = {}
        value: Any = vals[0] if len(vals) == 1 else (vals or None)
        op = ("not_in" if exclude else "in") if vals else (
            "range" if (rng is not None or f.find(".//max") is not None) else "filter")
        if not vals and (rng is not None or f.find(".//max") is not None):
            # A RANGE keeps both bounds; either may be open. They were read as nothing,
            # which left a bounded range with no value at all.
            bounds = {k: ((f.find(f".//{k}").text or "").strip().strip("#\"'") or None
                          if f.find(f".//{k}") is not None else None)
                      for k in ("min", "max")}
            value = bounds if (bounds["min"] or bounds["max"]) else None
            if value is not None and (f.get("included-values") or "").lower() \
                    == "in-range-or-null":
                value["or_null"] = True     # the range, and the rows with no value
        elif top_fn == "range" and not vals and (top.get("from") or top.get("to")):
            # A RANGE OF MEMBERS (`<groupfilter function='range' from=… to=…>`): the members
            # from one to the other, both kept, in the field's order (seen live: ONLINE to
            # PARTNER keeps both and drops STORE and the NULL member).
            op = "range"
            value = {k: (top.get(a) or "").strip().strip("#\"'") or None
                     for k, a in (("min", "from"), ("max", "to"))}
            lo, hi = value["min"], value["max"]
            if lo is not None and hi is not None:
                # Either end may come first: the range is from the lesser to the greater
                # (seen live: 'apple' to 'ONLINE' keeps ONLINE, PARTNER and apple, the
                # warehouse's order, upper case before lower case, as 'ONLINE' to 'apple'
                # does).
                try:
                    swap = float(lo) > float(hi)
                except ValueError:
                    swap = lo > hi
                if swap:
                    value = {"min": hi, "max": lo}
            extra = {"value_kind": "literal"}
        elif fclass == "quantitative" and included in ("not_null", "is_null"):
            op, value, extra = included, True, {"value_kind": "literal"}
        elif fclass == "quantitative" and included == "all":
            everything = True
        # A CONDITION (`function='filter' expression=…`: "by formula", "by field", a wildcard
        # match), or a TOP N (`function='end'`): the field's members that pass, compiled from
        # the expression the file holds — never pasted.
        cond = next((g for g in f.iter("groupfilter")
                     if (g.get("function") or "").lower() == "filter" and g.get("expression")),
                    None)
        end = next((g for g in f.iter("groupfilter")
                    if (g.get("function") or "").lower() in ("end", "top")), None)
        if not vals and end is not None:
            order = next((g for g in end.iter("groupfilter")
                          if (g.get("function") or "").lower() == "order"
                          and g.get("expression")), None)
            # The count is a number, or a parameter's current value (seen live: a Top N
            # whose count names a parameter keeps that many).
            n, n_param = _param_count(end.get("count") or end.get("value"), params)
            desc = ((order.get("direction") if order is not None else None)
                    or "DESC").upper() != "ASC"
            if (end.get("end") or "top").lower() == "bottom":
                desc = not desc
            op = "top_n"
            value = ({"n": n, "by": _named_expression(order.get("expression").strip(),
                                                      display, params),
                      "direction": "desc" if desc else "asc",
                      **({"n_parameter": n_param} if n_param else {})}
                     if (n and order is not None) else None)
            extra = {"value_kind": "expression"}
        elif not vals and cond is not None:
            op = "condition"
            value = {"expression": _named_expression(cond.get("expression").strip(), display,
                                                     params),
                     "exclude": exclude}
            extra = {"value_kind": "expression"}
        if fclass == "relative-date":
            # A RELATIVE DATE filter ("last 3 months", "this year"): whole periods counted
            # from today, or from the anchor the author fixed.
            first, last = f.get("first-period"), f.get("last-period")
            # Tableau 2025.1 writes the period as `period-type-v2` (seen in a customer's
            # workbook, version 18.1); earlier files write `period-type`.
            period = (f.get("period-type") or f.get("period-type-v2") or "").lower()
            ok = all(re.fullmatch(r"-?\d+", x or "") for x in (first, last)) and period
            # Attributes this reader does not know may move the window (an anchor spelled
            # another way): the filter is then refused by name, never read from today.
            odd = sorted(k for k in f.attrib if k.split("}")[-1] not in _RELATIVE_ATTRS)
            anchor = (f.get("period-anchor") or "").strip().strip("#").strip()
            if anchor and not re.fullmatch(r"\d{4}-\d{2}-\d{2}( 00:00:00)?", anchor):
                odd.append("period-anchor")
            op, value = "relative_date", ({
                "unknown": odd,
                "period": period, "first": int(first), "last": int(last),
                "include_null": (f.get("include-null") or "").lower() == "true",
                "include_future": (f.get("include-future") or "true").lower() != "false",
                **({"anchor": anchor[:10]} if anchor and "period-anchor" not in odd else {}),
            } if ok else None)
            extra = {"value_kind": "expression", "_ds": dsid}
        if ranks:
            op = "top_rank"         # the rank filter of a Top N: recovered into `top_n`
        if aggregated and fclass == "quantitative" and stage is None and not ranks:
            # A filter on an AGGREGATED pill (SUM(Sales) on Filters): a test of that
            # aggregate on each row of the view, after aggregation. The measure it tests is
            # named by the caller (`_measure_agg`), who declares it.
            extra = {**extra, "_measure_agg": (dsid, _aggregation_of(field)[1] or "",
                                               _derivation(field, _instances(ws))[1])}
        out.append({
            "scope": scope or ("context" if is_context else "worksheet"),
            "stage": FILTER_STAGE.get(this_stage, "dimension"),
            "column": bare if bare.endswith(")") and "(" in bare else bare.lower(),
            "op": op,
            "value": value,
            **({"value_kind": "absent"} if everything else extra),
            "evidence": f"tableau filter class={fclass or 'categorical'} "
                        f"context={is_context}"
                        + (f" stage={stage}" if stage else ""),
            **({"_dsid": dsid} if dsid else {}),
        })
    return out


# The attributes of a relative date filter this reader reads (checked live: Tableau Cloud
# applies these). The ANCHOR is `period-anchor='#YYYY-MM-DD#'` (seen live: an `anchor` or
# `anchor-date` attribute is ignored); any attribute outside this set refuses the filter by
# name, never read from today.
_RELATIVE_ATTRS = {"class", "column", "context", "filter-group", "first-period", "last-period",
                   "period-type", "period-type-v2", "include-future", "include-null",
                   "period-anchor"}
# The first day of a week when a data source states none (no `<date-options start-of-week>`):
# seen live, a relative "this week" on such a file ran Sunday to Saturday.
_DEFAULT_WEEK_START = "sunday"

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def _fiscal_start(elements: list[ET.Element], field: str | None = None) -> int | None:
    """The month (1-12) a date field's fiscal year starts in, from the first element that says.

    The field's own setting comes first: the data source's `<column fiscal-year-start='4'>`
    (a field's Default Properties > Fiscal Year Start). Seen live on Tableau Cloud: that
    attribute makes the field's year and quarter pills fiscal (April and July starts), while a
    copy on a worksheet's dependency list alone does nothing. Then the data source's Date
    Properties (`<date-options fiscal-year-start='april'>`), as documented; on Cloud a
    hand-built file with only that rendered calendar years, so it stays the fallback."""
    if field:
        for el in elements:
            for col in el.findall("column"):
                raw = (col.get("fiscal-year-start") or "").strip()
                if col.get("name") == field and raw.isdigit() and 1 <= int(raw) <= 12:
                    return int(raw)
    for el in elements:
        opts = _date_options(el)
        raw = (opts.get("fiscal-year-start") or "").strip().lower() if opts is not None else ""
        if raw in _MONTHS:
            return _MONTHS.index(raw) + 1
        if raw.isdigit() and 1 <= int(raw) <= 12:
            return int(raw)
    return None


def _week_start(elements: list[ET.Element]) -> str:
    """The day a data source's weeks start on (`<date-options start-of-week>`), from the
    first element that says; Tableau's default when none does."""
    for el in elements:
        opts = _date_options(el)
        raw = (opts.get("start-of-week") or "").strip().lower() if opts is not None else ""
        if raw:
            return raw
    return _DEFAULT_WEEK_START


def _date_options(ds: ET.Element) -> ET.Element | None:
    """A data source's Date Properties: `<overridable-settings><date-options …>` as Desktop
    saves it, or a bare `<date-options>` child."""
    found = ds.find("overridable-settings/date-options")
    return found if found is not None else ds.find("date-options")


_DATE_PILL = re.compile(r"^(DATETRUNC|DATEPART)\('(\w+)', (\[[^\]]+\])\)$")


def _fiscal_formula(formula: str, start: int) -> str:
    """A date pill's formula on a fiscal year starting in month `start` (> 1).

    Tableau names a fiscal year by the calendar year it ENDS in (an April start: April 2025
    to March 2026 is FY 2026), so the year number is the year of the date moved forward by
    13 - start months, and the fiscal quarter the quarter of the date moved back by
    start - 1. A year or quarter truncation is the fiscal period's first day. A week NUMBER
    of a fiscal year is named, not compiled (`FISCAL_WEEK`, refused by its rule row). A
    month, a day and a week truncation are the calendar's."""
    m = _DATE_PILL.match(formula or "")
    if not m:
        return formula
    fn, part, field = m.group(1), m.group(2).lower(), m.group(3)
    back = f"DATEADD('month', {1 - start}, {field})"
    if fn == "DATEPART" and part == "week":
        return f"FISCAL_WEEK({field})"
    if fn == "DATEPART" and part == "year":
        return f"DATEPART('year', DATEADD('month', {13 - start}, {field}))"
    if fn == "DATEPART" and part == "quarter":
        return f"DATEPART('quarter', {back})"
    if fn == "DATETRUNC" and part in ("year", "quarter"):
        return f"DATEADD('month', {start - 1}, DATETRUNC('{part}', {back}))"
    return formula


def _datasource_filters(ds: ET.Element, col_index: dict[str, dict], display=None,
                        params: dict | None = None) -> list[dict]:
    """A data source's OWN filters: the `<filter>` children of the data source (Data Source
    Filters, embedded or published) and those of its extract when the extract is what the
    workbook reads. Every worksheet on the data source applies them, to every row, before
    any context filter; unread, the statement ran over rows the workbook never sees."""
    els = [f for f in ds if f.tag == "filter"]
    ex = ds.find("extract")
    if ex is not None and (ex.get("enabled") or "true").lower() != "false":
        els += [f for f in ex if f.tag == "filter"]
    if not els:
        return []
    return _filters(ds, col_index, scope="datasource", stage="datasource", elements=els,
                    display=display, params=params)


def _with_shared(own: list[dict], ws: ET.Element, shared: dict[str, list[dict]]) -> list[dict]:
    """A worksheet's own filters plus the shared filters of every datasource it uses; the
    worksheet's own filter on a column wins over the shared one."""
    used = [d.get("datasource") for d in ws.findall(".//datasource-dependencies")]
    have = {f["column"] for f in own}
    out = list(own)
    for ds in dict.fromkeys(u for u in used if u):
        for f in shared.get(ds, []):
            if f["column"] not in have:
                have.add(f["column"])
                out.append(f)
    return out


def _filter_values(f: ET.Element) -> list[str]:
    """The literal values a Tableau filter selects.

    Tableau writes these as an ATTRIBUTE on `groupfilter` (`member='"RETAIL"'`), not as
    element text, and a union filter nests one groupfilter per member. Reading only element
    text finds nothing — which leaves every BI filter with `value: None`, so the predicate is
    dropped from the authored SQL and the value never reaches retrieval.

    Quantitative filters carry `<min>`/`<max>` instead; those are ranges, not vocabulary, and
    are handled by the caller.
    """
    out: list[str] = []
    for gf in f.findall(".//groupfilter"):
        member = gf.get("member")
        if member:
            out.append(member.strip().strip('"\''))
    for m in f.findall(".//member"):
        raw = m.get("value") or m.get("member") or (m.text or "")
        raw = raw.strip().strip('"\'')
        if raw:
            out.append(raw)
    # Order-preserving dedup: a union filter repeats the member inside and outside the group.
    return list(dict.fromkeys(out))


def _top_n(ws: ET.Element, col_index: dict[str, dict]) -> int | None:
    """Trap 3: Top-N hides behind a rank calc plus a 'Top 1-N' bin filter.

    Recovering it matters because the SQL without a LIMIT runs, witnesses clean, and answers
    a different question.
    """
    for f in ws.findall(".//filter"):
        for b in f.findall(".//bin"):
            val = b.get("value") or ""
            if "top" not in val.lower():
                continue
            # "Top 1-10" is a RANGE: the first number is the rank floor, the last is N.
            # Taking the first match returns 1 and silently caps the result at one row.
            nums = re.findall(r"(\d+)", val)
            if nums:
                return int(nums[-1])
    for gf in ws.findall(".//groupfilter"):
        if (gf.get("function") or "").lower() in ("end", "top"):
            n = gf.get("count") or gf.get("value")
            if n and str(n).isdigit():
                return int(n)
    # A rank calc with no bin filter is still a signal worth recording upstream.
    for col in col_index.values():
        if col.get("formula") and RANK_FN.search(col["formula"]):
            return None
    return None


_RANK_ARG = re.compile(r"^\s*(?:RANK|RANK_DENSE|RANK_UNIQUE|RANK_MODIFIED)\s*\(\s*(.+?)\s*"
                       r"(?:,\s*['\"](asc|desc)['\"]\s*)?\)\s*$", re.I | re.S)


def _top_n_sort(ws: ET.Element, col_index: dict[str, dict], display) -> str | None:
    """WHAT a Top-N ranks by, as `<expression> ASC|DESC` in Tableau's own calc language.

    A Top-N with no sort field is ordered by the widget's first measure downstream, and a
    "top 10 departments by units RECEIVED" on a sheet that shows units SOLD then returns a
    different ten departments — a statement that runs, witnesses clean and answers another
    question. Two places state it:

      * a Top-N groupfilter (`function='end'`) wraps an ORDER groupfilter whose `expression`
        and `direction` are the ranking; `end='bottom'` keeps the other end of that order;
      * a rank calc filtered by a "Top 1-N" bin: the calc's argument is the ranking, and
        RANK orders descending unless told `'asc'`.

    None when neither says, and the compiler keeps its documented fallback.
    """
    def _named(expr: str) -> str:
        return re.sub(r"\[([^\[\]]+)\]", lambda m: f"[{display(m.group(1))}]", expr)

    for gf in ws.findall(".//groupfilter"):
        if (gf.get("function") or "").lower() not in ("end", "top"):
            continue
        order = next((g for g in gf.iter("groupfilter")
                      if (g.get("function") or "").lower() == "order" and g.get("expression")),
                     None)
        if order is None:
            continue
        desc = (order.get("direction") or "DESC").upper() != "ASC"
        if (gf.get("end") or "top").lower() == "bottom":
            desc = not desc
        return f"{_named(order.get('expression').strip())} {'DESC' if desc else 'ASC'}"
    for f in ws.findall(".//filter"):
        if not any("top" in (b.get("value") or "").lower() for b in f.findall(".//bin")):
            continue
        _, field = _pill_field(f.get("column") or "")
        _, bare = _aggregation_of(field or "")
        rec = col_index.get(f"[{bare}]") or {}
        m = _RANK_ARG.match(rec.get("formula") or "")
        if m:
            desc = (m.group(2) or "desc").lower() == "desc"
            return f"{_named(m.group(1).strip())} {'DESC' if desc else 'ASC'}"
    return None


def _parameters(root: ET.Element, col_index: dict[str, dict]) -> list[dict]:
    """Trap 4: parameter-driven CASE branches. Recorded with the declared default; a branch
    is NEVER picked silently."""
    params: list[dict] = []
    for ds in root.findall(".//datasource"):
        if (ds.get("name") or "") != "Parameters":
            continue
        for c in ds.findall(".//column"):
            rec = {
                "name": (c.get("caption") or c.get("name") or "").strip("[]"),
                "default": c.get("value"),
                "resolved_branch": None,
            }
            if (c.get("param-domain-type") or "") == "list":
                dom = [{"value": m.get("value"), "alias": m.get("alias")}
                       for m in c.iter("member") if m.get("value") is not None]
                if dom:
                    rec["domain"] = dom
            params.append(rec)
    used = {m.group(1) for col in col_index.values() if col.get("formula")
            for m in [PARAM_CASE.search(col["formula"])] if m}
    for p in params:
        if p["name"] in used:
            p["resolved_branch"] = (
                f"declared default {p['default']!r}; the branch is recorded, never chosen")
    return params


# ------------------------------------------------------------------- published chains
def _identity_keys(ds: ET.Element, file_name: str = "") -> set[str]:
    """The names a datasource IS known by — never the names of what it points at."""
    rl = ds.find("repository-location")
    names = [rl.get("id") if rl is not None else None,
             ds.get("formatted-name"), ds.get("caption")]
    names += _file_stems(file_name)
    return {k for k in (_ds_name_key(n) for n in names) if k}


def _target_keys(ds: ET.Element, file_name: str, is_file: bool) -> set[str]:
    """The names a PUBLISHED reference points AT. A workbook's reference is spelled with the
    published datasource's own names (repository id, caption) as well as its sqlproxy
    connection's; a published datasource's own file (`is_file`) that is itself built on
    another published datasource points only through its sqlproxy connection — its caption
    and repository id are its own."""
    if not is_file:
        return _published_keys(ds, file_name)
    conn = ds.find(".//connection")
    names = [conn.get("dbname"), conn.get("server-ds-friendly-name")] if conn is not None else []
    return {k for k in (_ds_name_key(n) for n in names) if k}


def _is_proxy(ds: ET.Element) -> bool:
    conn = ds.find(".//connection")
    return conn is not None and (conn.get("class") or "").lower() == "sqlproxy"


def _ds_label(ds: ET.Element, file_name: str = "") -> str:
    name = ds.get("caption") or ds.get("formatted-name") or ds.get("name") or ""
    return f"{name!r} ({file_name})" if file_name else repr(name)


def _proxy_target(ds: ET.Element) -> str:
    conn = ds.find(".//connection")
    if conn is None:
        return "?"
    return conn.get("server-ds-friendly-name") or conn.get("dbname") or "?"


# ------------------------------------------------------------------------- pulse
# The Pulse metric-definitions REST API (`GET /api/-/pulse/definitions`) names the
# aggregation as `AGGREGATION_<NAME>`.
_PULSE_AGG = {"sum": "sum", "average": "avg", "avg": "avg", "count": "count",
              "count_distinct": "count_distinct", "countd": "count_distinct",
              "distinct_count": "count_distinct", "min": "min", "minimum": "min",
              "max": "max", "maximum": "max", "median": "median"}
_CALC_AGG = re.compile(r"\b(SUM|AVG|MIN|MAX|COUNT|COUNTD|MEDIAN|ATTR)\s*\(", re.I)


def _pulse_definitions(f: pathlib.Path) -> list[dict]:
    """The definitions in a saved Pulse response, as the API returns them: a
    `{"definitions": [...]}` list response, one `{"definition": {...}}`, or a bare list."""
    with f.open("rb") as src:
        data = src.read(MAX_XML_MEMBER_BYTES + 1)
    if len(data) > MAX_XML_MEMBER_BYTES:
        raise TableauError(f"{f.name} is over the {MAX_XML_MEMBER_BYTES} cap. Refused.")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TableauError(f"{f.name} is not a JSON document ({type(exc).__name__}). Save the "
                           f"response of GET /api/-/pulse/definitions as it is.") from None
    if isinstance(doc, dict) and isinstance(doc.get("definitions"), list):
        defs = doc["definitions"]
    elif isinstance(doc, dict) and isinstance(doc.get("definition"), dict):
        defs = [doc["definition"]]
    elif isinstance(doc, list):
        defs = doc
    else:
        raise TableauError(
            f"{f.name} is not a Pulse definitions response: expected "
            f"{{\"definitions\": [...]}} (GET /api/-/pulse/definitions) or "
            f"{{\"definition\": {{...}}}} (GET /api/-/pulse/definitions/<id>).")
    return [d for d in defs if isinstance(d, dict)]


def _pulse_value(v: dict) -> str | None:
    for key in ("string_value", "number_value", "int_value", "double_value", "bool_value"):
        if v.get(key) is not None:
            return str(v[key]).lower() if key == "bool_value" else str(v[key])
    return None


def _pulse_format(spec: dict) -> str | None:
    kind = str(((spec.get("representation_options") or {}).get("type")) or "").upper()
    for word, fmt in (("CURRENCY", "currency"), ("PERCENT", "percent"), ("NUMBER", "number")):
        if word in kind:
            return fmt
    return None


# ------------------------------------------------------------------------------ parse
def parse(path: pathlib.Path, *, dialect: str = "") -> dict[str, Any]:
    roots = _roots(path)
    notes: list[str] = []
    vcs: list[tuple[str, VirtualConnection]] = []
    for f in _vc_files(path):
        for name, root in _read_vc(f):
            vcs.append((name, VirtualConnection(name, root)))
    vc_columns = {c.get("luid").lower(): c.get("physical")
                  for _n, vc in vcs for c in vc.root.findall("column")}
    siblings = _sibling_datasources(path)
    for _n, r in (*roots, *siblings):
        _name_vc_columns(r, vc_columns)
    tables: list[dict] = []
    measures: list[dict] = []
    relationships: list[dict] = []
    usage: list[dict] = []
    unresolved: list[str] = []
    resolved_pub: list[str] = []
    model_name = (path.name[:-len(PULSE_SUFFIX)] if _is_pulse(path) else path.stem)

    wb_root = next((r for name, r in roots if name.lower().endswith(".twb")), roots[0][1])
    dash_of = _dashboards_of(wb_root)
    prose_of = _dashboard_prose(wb_root)
    dash_el = {d.get("name"): d for d in wb_root.findall("./dashboards/dashboard")}
    assets: list[dict] = []
    if wb_root.tag == "workbook":
        assets.append(_asset("workbook", model_name, wb_root))

    col_index: dict[str, dict] = {}
    ds_lineage: dict[str, dict] = {}

    def _datasources(root: ET.Element) -> list[ET.Element]:
        # `iter`, not `findall(".//")`: a .tds's ROOT is the datasource, and a descendant
        # search never sees it. Bare references (a worksheet's `<datasource name=…/>` with no
        # children) are skipped — they carry no connection and would overwrite the real one.
        return [d for d in root.iter("datasource") if len(d)]

    # Every datasource the input holds (`own`: emitted) and every one beside a single file
    # (read only to RESOLVE a reference — never emitted, so a stray .tds adds nothing):
    # (file, element, is the file's own root, own).
    entries = [(n, ds, ds is r, True) for n, r in roots for ds in _datasources(r)
               if (ds.get("name") or "") != "Parameters"]
    entries += [(n, ds, ds is r, False) for n, r in siblings for ds in _datasources(r)]

    lineage_of: dict[int, dict] = {}
    error_of: dict[int, TableauError] = {}

    def _lineage(n: str, ds: ET.Element) -> dict:
        """The datasource's physical lineage, computed once. A model that lives outside
        Tableau is refused here, by name, wherever the datasource is met."""
        if id(ds) in error_of:
            raise error_of[id(ds)]
        if id(ds) not in lineage_of:
            _refuse_external_model(ds, n)
            lineage_of[id(ds)] = _physical(ds, vcs=vcs, dialect=dialect)
        return lineage_of[id(ds)]

    # A PUBLISHED datasource's connection lives in its .tds. Index every datasource that can
    # END a reference — a physical one — and every published datasource's own FILE that is
    # itself built on another published datasource (a link of a chain), by EVERY NAME IT
    # ANSWERS TO: repository id, formatted name, caption, file stem. Matching on the
    # repository id alone missed the common case of a .tds downloaded on its own, whose ids
    # are spelled differently from the workbook's reference; the name is what both sides
    # share. A workbook's own sqlproxy reference is never an end: it is a pointer.
    by_key: dict[str, list[tuple]] = {}
    for e in entries:
        n, ds, is_file, _own = e
        if _is_proxy(ds):
            if not is_file:
                continue
            keys = _identity_keys(ds, n)
        else:
            try:
                lin = _lineage(n, ds)
            except TableauError as exc:
                if _own:
                    raise
                # A sibling the run may never reference: refused only when a chain reaches it.
                error_of[id(ds)] = exc
                lin = None
            if lin is not None and not (lin["object"] or lin["raw_expression"]):
                continue
            keys = _published_keys(ds, n)
        for k in keys:
            by_key.setdefault(k, []).append(e)

    def _resolve(start: tuple) -> tuple[list[tuple], tuple | None]:
        """Walk a published reference to the datasource that holds the physical connection,
        through every published datasource it is built on, at any depth. -> (the links after
        `start`, the physical end), or ([], None) when not even the first link was supplied
        (the named acquisition gap). A link missing further down, or a cycle, is refused by
        name: the chain is known to exist and is broken."""
        n0, ds0, file0, _ = start
        chain: list[tuple] = []
        seen = {id(ds0)}
        keys = _target_keys(ds0, n0, file0)
        while True:
            cands = [e for k in sorted(keys) for e in by_key.get(k, [])]
            fresh = [e for e in cands if id(e[1]) not in seen]
            trail = " -> ".join([_ds_label(ds0, n0)] + [_ds_label(e[1], e[0]) for e in chain])
            if not fresh:
                if cands:
                    raise TableauError(
                        f"published datasources form a cycle: {trail} -> back to "
                        f"{_ds_label(cands[0][1], cands[0][0])}. A chain of published "
                        f"datasources must end at one with a warehouse connection.")
                if not chain:
                    return [], None
                last = chain[-1]
                raise TableauError(
                    f"published datasource chain {trail} is broken: "
                    f"{_ds_label(last[1], last[0])} is itself built on the published "
                    f"datasource {_proxy_target(last[1])!r} (a sqlproxy connection), and no "
                    f".tds/.tdsx for it is beside the input. Download "
                    f"{_proxy_target(last[1])!r} from Tableau and put it beside the others; "
                    f"the chain is followed to the datasource that holds the warehouse "
                    f"connection.")
            e = fresh[0]
            chain.append(e)
            seen.add(id(e[1]))
            if not _lineage(e[0], e[1])["published_datasource"]:
                return chain, e
            keys = _target_keys(e[1], e[0], e[2])

    # Resolve each reference first, so a .tds it adopted is not ALSO emitted as a table of its
    # own: it is the same object, and two copies would give every measure two homes.
    adopted: dict[int, tuple[list[tuple], tuple]] = {}
    for e in entries:
        if e[3] and _is_proxy(e[1]):
            chain, end = _resolve(e)
            if end is not None:
                adopted[id(e[1])] = (chain, end)
    adopter_of: dict[int, int] = {}
    for owner, (chain, _end) in adopted.items():
        for link in chain:
            adopter_of.setdefault(id(link[1]), owner)
    # A link is the same object as the reference that reached it, even when it is itself a
    # file of the input whose own chain was walked: one table, the reference's.
    linked = set(adopter_of)

    table_by_ds: dict[int, str] = {}          # id(datasource) -> its table's name
    # (datasource id, internal field) -> the logical table it lives on, where a data source
    # has several (the relationships model); otherwise the data source's one table.
    field_home: dict[tuple[str, str], str] = {}
    table_of_dsid: dict[str, str] = {}        # a worksheet's datasource id -> table name
    ds_fields: dict[str, dict[str, tuple[str, str, str]]] = {}
    index_of_table: dict[str, dict] = {}      # table name -> internal column name -> column
    table_rec: dict[str, dict] = {}
    filter_src: dict[str, list[ET.Element]] = {}   # datasource id -> elements holding its filters
    for n, ds, _is_file, own in entries:
        if not own:
            continue
        dsid = ds.get("name") or ds.get("formatted-name") or ""
        caption = ds.get("caption") or ds.get("formatted-name") or dsid
        if id(ds) in linked:
            # Its columns still index (the workbook's reference may name a calc only the
            # .tds defines), but the table is the workbook's.
            col_index.update({k: v for k, v in _columns(ds)[1].items()
                              if k not in col_index})
            continue
        lineage = _lineage(n, ds)
        src_index: dict[str, dict] = {}
        if lineage["published_datasource"] and id(ds) in adopted:
            chain, end = adopted[id(ds)]
            lineage = {**_lineage(end[0], end[1]), "published_datasource": True}
            resolved_pub.append(caption)
            # The calcs of every link, nearest first, for the ones the reference does not
            # repeat.
            for link in chain:
                for k, v in _columns(link[1])[1].items():
                    src_index.setdefault(k, v)
        cols, index = _columns(ds)
        have = set(index)
        cols += [c for k, c in src_index.items() if k not in have]
        index = {**src_index, **index}
        for name, role, kind in lineage.pop("_extract_columns", None) or []:
            # An aggregated extract's column no `<column>` declares: the field its metadata
            # record names, with the role its type gives it.
            if f"[{name}]" not in index:
                rec = {"name": name, "caption": None, "default_format": None,
                       "data_type": _EXTRACT_TYPES.get(kind or ""), "description": "",
                       "is_calculated": False, "formula": None, "formula_language": None,
                       "source_column": name.lower(), "role": role}
                cols.append(rec)
                index[f"[{name}]"] = rec
        col_index.update(index)
        ds_lineage[dsid] = lineage
        # Its data source filters: its own, and those of every published data source it is
        # built on (they live in that data source's file, not in the workbook's reference).
        filter_src[dsid] = [ds] + [link[1] for link in
                                   (adopted[id(ds)][0] if id(ds) in adopted else ())]
        if lineage.get("_outputs"):
            # A union read as one statement: the reader's own name for it (its database and
            # schema, and a name no warehouse table has), its columns read off the
            # statement's output.
            lineage = dict(lineage)
            outputs = lineage.pop("_outputs")
            uname = lineage.pop("_union")
            lineage["name_hint"] = ".".join([(lineage.get("database") or "tableau").lower(),
                                             (lineage.get("schema") or "tableau").lower(),
                                             _union_output(uname) + "__union"])
            ds_lineage[dsid] = lineage
            for c in cols:
                if c.get("source_column") and c["source_column"] in outputs:
                    c["source_column"] = outputs[c["source_column"]]
        qualified = _qualified(lineage)
        custom_sql = lineage.get("object_kind") == "custom-sql"
        if lineage["published_datasource"] and not (qualified or custom_sql):
            unresolved.append(caption)
        table = {
            "name": lineage.pop("name_hint", None) or qualified or caption,
            "description": "",
            "is_hidden": False,
            "source_lineage": lineage,
            "columns": cols,
        }
        if custom_sql:
            # A custom-SQL datasource has no physical name: the statement IS the source.
            # The caption is its logical name, which A2 keys the query by.
            table["logical_name"] = caption
        # The RELATIONSHIPS model: one table per logical table, each with its own columns,
        # related by the column pairs Tableau's object graph names. Collapsed onto the first
        # table, a field of another one compiled against the wrong table (seen live).
        logical = None
        resolved = adopted.get(id(ds))
        for el in [ds, *([resolved[1][1]] if resolved else []),
                   *(link[1] for link in (resolved[0] if resolved else ()))]:
            logical = _logical_tables(el)
            if logical:
                break
        if logical and not custom_sql:
            home_of: dict[str, str] = {}
            split: dict[str, dict] = {}
            for leaf_name, leaf in logical["leaves"].items():
                lc = _leaf_connection(ds, leaf)
                q = _qualify(_bracket_join(leaf.get("table") or leaf_name),
                             lineage.get("database") or (lc.get("dbname") if lc is not None
                                                         else None),
                             lineage.get("schema") or (lc.get("schema") if lc is not None
                                                       else None))
                home_of[leaf_name.lower()] = q
            primary = table["name"]
            kept = []
            for c in cols:
                internal = (c.get("source_column") or "").lower()
                owner = home_of.get(logical["parent"].get(internal, "").lower())
                if c.get("source_column"):
                    c["source_column"] = logical["remote"].get(internal, internal).lower()
                if owner and owner != primary and not c.get("is_calculated"):
                    split.setdefault(owner, []).append(c)
                    field_home[(dsid, internal)] = owner
                else:
                    kept.append(c)
                    if owner:
                        field_home[(dsid, internal)] = owner
            cols[:] = kept
            for owner, ocols in split.items():
                extra = {"name": owner, "description": "", "is_hidden": False,
                         "source_lineage": {**lineage, "object": owner, "object_kind": "table",
                                            "reads": [owner]},
                         "columns": ocols}
                tables.append(extra)
                table_rec.setdefault(owner, extra)
                index_of_table.setdefault(owner, {}).update(
                    {k: v for k, v in index.items()
                     if (v.get("source_column") or "") in {c.get("source_column") for c in ocols}})
            for a, b in logical["pairs"]:
                ta = home_of.get(logical["parent"].get(a.lower(), "").lower())
                tb = home_of.get(logical["parent"].get(b.lower(), "").lower())
                if ta and tb and ta != tb:
                    # A relationship (the logical layer) is not a join: each table is
                    # aggregated at its own grain and the results are joined, keeping
                    # what does not match on either side, and either table's dimensions
                    # cut the other's measures. `full` and `both` say so in the common
                    # fields; the multiplicity is not stated, so the other table is read as
                    # its distinct rows and never multiplies this one.
                    relationships.append({
                        "from_table": ta, "from_column": logical["remote"].get(a.lower(), a),
                        "to_table": tb, "to_column": logical["remote"].get(b.lower(), b),
                        "cardinality": "unknown", "cross_filter": "both", "is_active": True,
                        "kind": "relationship", "join_type": "full"})
        tables.append(table)
        table_by_ds[id(ds)] = table["name"]
        table_of_dsid.setdefault(dsid, table["name"])
        # Each plain field of the data source by its name: (its table, its column). A blend
        # links two data sources on the fields of the same name.
        for internal, rec in index.items():
            if rec.get("is_calculated") or not rec.get("source_column"):
                continue
            home = field_home.get((dsid, internal.strip("[]").lower())) or table["name"]
            ds_fields.setdefault(dsid, {}).setdefault(
                str(rec["name"]).lower(), (home, rec["source_column"], internal.strip("[]")))
        index_of_table.setdefault(table["name"], {}).update(
            {k: v for k, v in index.items() if k not in index_of_table.get(table["name"], {})})
        table_rec.setdefault(table["name"], table)
        assets.append(_asset("datasource", _PROJECT_SUFFIX.sub("", caption) or caption, ds))
        for c in cols:
            if c["is_calculated"] and (c["role"] == "measure" or
                                       RANK_FN.search(c["formula"] or "")):
                measures.append({
                    "name": c["name"],
                    "formula": c["formula"],
                    "formula_language": "tableau-calc",
                    "home_table": qualified or caption,
                    "folder": c.get("folder"),
                    "description": c.get("description") or "",
                    # Trap 1, stated on every Tableau measure.
                    "aggregation_at_use_site": True,
                })
    for link, owner in adopter_of.items():
        if owner in table_by_ds:
            table_by_ds.setdefault(link, table_by_ds[owner])

    # A calc's formula names OTHER calcs by their internal name (`[Calculation_1234…]`,
    # `[Sales YTD (copy)_5678…]`), while measures are keyed by caption. Rewrite those
    # references to the caption so the compiler can inline them; a plain column keeps its
    # internal name, which is the warehouse column the SQL reads.
    # A caption shared by two DIFFERENT calcs (case-insensitive) cannot be the key: the
    # compiler would resolve one to the other. Those keep their internal name.
    # So does a calc whose caption is a PLAIN column's name: a calc captioned "Month"
    # (`FLOAT([month])`) would capture every `[month]` in every other formula, and
    # `MAKEDATE([year],[month],1)` would build its date from '7.0'.
    _by_cap: dict[str, set] = {}
    for k, r in col_index.items():
        if r.get("is_calculated") and r.get("name"):
            _by_cap.setdefault(r["name"].lower(), set()).add(r.get("formula"))
    _plain = {k.strip("[]").lower() for k, r in col_index.items() if not r.get("is_calculated")}

    def _shadow(name: str | None) -> bool:
        return (name or "").lower() in _plain

    for k, r in col_index.items():
        if not r.get("is_calculated") or k.strip("[]").lower() in _plain:
            continue
        if len(_by_cap.get((r.get("name") or "").lower(), ())) > 1:
            r["name"] = k.strip("[]")
        elif _shadow(r.get("name")):
            r["name"] = f"{r['name']} (calc)"     # still the author's label, no longer the column's
    for m in measures:
        if len(_by_cap.get((m.get("name") or "").lower(), ())) > 1:
            m["name"] = next((k.strip("[]") for k, r in col_index.items()
                              if r.get("formula") == m.get("formula")), m["name"])
        elif _shadow(m.get("name")):
            m["name"] = f"{m['name']} (calc)"
    calc_caption = {k.strip("[]"): r["name"] for k, r in col_index.items()
                    if r.get("is_calculated") and r.get("name") and r["name"] != k.strip("[]")}

    # Parameters likewise: a formula names `[Parameters].[Parameter 1]` (internal) while the
    # recorded parameter carries its caption. One spelling, so A3 can find the default.
    param_caption = {}
    # Each parameter by its internal name: (caption, current value), for a filter that names
    # one (a Top N's count, a condition).
    param_now: dict[str, tuple[str, str | None]] = {}
    for ds in wb_root.iter("datasource"):
        if (ds.get("name") or "") == "Parameters":
            for c in ds.findall(".//column"):
                internal = (c.get("name") or "").strip("[]")
                if internal and c.get("caption"):
                    param_caption[internal] = c.get("caption")
                if internal:
                    param_now[internal] = (c.get("caption") or internal, c.get("value"))

    def _captioned(formula: str | None) -> str | None:
        if not formula:
            return formula
        formula = re.sub(r"\[Parameters\]\.\[([^\[\]]+)\]",
                         lambda m: f"[Parameters].[{param_caption.get(m.group(1), m.group(1))}]",
                         formula)
        if not calc_caption:
            return formula
        return re.sub(r"(?<!\[Parameters\]\.)\[([^\[\]]+)\]",
                      lambda m: f"[{calc_caption.get(m.group(1), m.group(1))}]", formula)

    for rec in col_index.values():
        rec["formula"] = _captioned(rec.get("formula"))
    for m in measures:
        m["formula"] = _captioned(m.get("formula"))

    params = _parameters(wb_root, col_index)

    def _display(bare: str) -> str:
        """A pill names a field by its INTERNAL name (`Calculation_1234…`); measures and
        columns are keyed by their caption. Resolve, or every calc's formula is lost."""
        if not bare:
            return bare
        rec = col_index.get(f"[{bare}]") or {}
        # Only a CALC is renamed: a plain column's internal name IS its warehouse column.
        return (rec.get("name") or bare) if rec.get("is_calculated") else bare

    # SHARED filters ("apply to all worksheets using this data source") live outside every
    # worksheet, in <shared-views><shared-view name=dsid>. Unread, every sheet lost them — the
    # dashboard's currency selection among them.
    shared: dict[str, list[dict]] = {}
    for sv in wb_root.findall("./shared-views/shared-view"):
        shared.setdefault(sv.get("name") or "", []).extend(
            _filters(sv, col_index, scope="datasource", params=param_now))
    ds_filters: dict[str, list[dict]] = {}
    for dsid, els in filter_src.items():
        seen_f: set[str] = set()
        for el in els:
            for f in _datasource_filters(el, col_index, _display, param_now):
                if "_ds" in f:
                    f["_ds"] = dsid
                key = json.dumps([f["column"], f["op"], f["value"]], sort_keys=True, default=str)
                if key not in seen_f:
                    seen_f.add(key)
                    ds_filters.setdefault(dsid, []).append(f)

    # Fields a sheet AGGREGATES on a shelf or the Marks card, and date pills it groups by.
    # Trap 1 cuts both ways: a calc is not the only measure. A plain field dropped on a shelf
    # as SUM(Quantity) is a measure of that sheet, and a workbook with no calculated field at
    # all is still a workbook of questions.
    shelf_fields: list[tuple[str | None, str, str, str | None]] = []
    date_cols: list[tuple[str | None, str, str]] = []
    # A field shown with a SECOND aggregation on one sheet (`SUM(Quantity)` and
    # `AVG(Quantity)`): the first keeps the plain field, each other one is a calc measure.
    agg_calcs: list[tuple[str | None, str, str, str | None]] = []
    quick_names: dict[str, str] = {}

    # The workbook's custom blend links: each map pairs a field of one data source with a
    # field of another, ((data source, field), (data source, field)) by internal names.
    # Tableau applies a relationship only as the editor saves it (probed live): `source` and
    # `target` the two data sources' bare names (in brackets it links nothing), and each map
    # end qualified by its data source (`[ds].[field]`; a bare `[field]` links nothing),
    # either way round.
    blend_maps: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for rel in wb_root.findall("./datasource-relationships/datasource-relationship"):
        ends_ds = {rel.get("source") or "", rel.get("target") or ""}
        for mp in rel.findall("./column-mapping/map"):
            ends = [_pill_field(mp.get(k) or "") for k in ("key", "value")]
            if all(d and f for d, f in ends) and {d for d, _f in ends} == ends_ds:
                blend_maps.append(((ends[0][0], ends[0][1].strip("[]")),
                                   (ends[1][0], ends[1][1].strip("[]"))))
    for ws in wb_root.findall("./worksheets/worksheet"):
        wname = ws.get("name") or "worksheet"
        title_el = ws.find(".//title//run")
        title = (title_el.text if title_el is not None else "") or wname

        ws_measures: list[str] = []
        ws_cols: list[str] = []
        aggs: dict[str, str] = {}
        inst = _instances(ws)
        tcs = _table_calc_instances(ws)
        # Each pill with the place it sits: a shelf (rows / cols, outer first), the Marks
        # card, or a member of Measure Values. A table calculation runs along the shelves.
        placed: list[tuple[str, str]] = []
        for where in ("rows", "cols"):
            for shelf in ws.findall(f".//{where}"):
                placed += [(p, where) for p in
                           re.findall(r"\[[^\]]+\]\.\[[^\]]+\]", shelf.text or "")]
        # The Marks card: text/label, colour, size, detail, tooltip, shape, path, angle.
        placed += [(enc.get("column") or "", "marks") for enc in ws.findall(".//encodings/*")]
        if any((_pill_field(p)[1] or "").lower() in MEASURE_VALUES for p, _w in placed):
            # `Measure Values` stands for the measures the `Measure Names` filter keeps,
            # each a full pill in the filter's members.
            for f in ws.findall(".//filter"):
                if (_pill_field(f.get("column") or "")[1] or "").lower() == ":measure names":
                    placed += [(v, "values") for v in _filter_values(f)]
        shelf_dims: dict[str, list[str]] = {"rows": [], "cols": [], "marks": []}
        pending_tc: list[tuple[str, dict | None, str | None]] = []
        base_of: dict[str, str] = {}      # a dimension on the sheet -> the field it shows

        def _member_of(ref: str, literal: str) -> str | None:
            # An addressed pill's dimension as the calc language writes it, for a member of
            # it named by a literal (a year pill's member is the year's number).
            k, v, b = _derivation(_pill_field(ref)[1] or "", inst)
            if not b:
                return None
            if k == "date_part" and v == "year" and re.fullmatch(r"-?\d+", literal):
                return f"YEAR([{_display(b)}])"
            if k in ("date_part", "date_trunc"):
                return _date_formula(k, v, _display(b))
            rec = col_index.get(f"[{b}]") or {}
            return (f"({rec['formula']})" if rec.get("is_calculated") and rec.get("formula")
                    else f"[{_display(b)}]")
        for raw, where in placed:
            dsid, field = _pill_field(raw)
            if not field or field.lower() in MEASURE_VALUES:
                continue
            kind, value, bare = _derivation(field, inst)
            if not bare:
                # Tableau's synthetic `[:Measure Names]` / `[:Measure Values]` pills carry no
                # field name.
                continue
            disp = _display(bare)
            tc = tcs.get(field.strip("[]"))
            quick = _QUICK_PREFIX.match(field.strip("[]"))
            if kind == "agg" and ((value != "custom" and tc is not None) or quick):
                if quick and value == "custom":
                    value = _AGG_HEADS.get(field.strip("[]").split(":")[1].lower(), "custom")
                # A QUICK table calculation on an aggregate (`pcto:sum:Sales:qk`): its own
                # measure, the calc language's formula for it, never the bare aggregate.
                rec = col_index.get(f"[{bare}]") or {}
                inner = (f"({rec['formula']})" if rec.get("is_calculated")
                         and rec.get("formula") else f"[{bare}]")
                label, formula, refusal = _quick_calc(tc or {"type": field.split(":")[0]},
                                                      value or "", disp, inner, _member_of)
                formula = formula or f"{_AGG_FN.get(value or '', 'SUM')}({inner})"

                def _named(label: str, formula: str) -> str:
                    # One name per formula: the same label over another formula (a rank the
                    # other way, a window of another width) is a measure of its own.
                    n, base_label = 1, label
                    while quick_names.setdefault(label, formula) != formula:
                        n += 1
                        label = f"{base_label} ({n})"
                    agg_calcs.append((dsid, label, _captioned(formula), None))
                    return label
                label = _named(label, formula)
                own_tc, nested_calcs = tc, {}
                for sec in (tc or {}).get("secondary") or []:
                    # A SECONDARY calculation runs over the one before it, by its own
                    # compute-using (checked live: a Percent Difference along the year of a
                    # running total down the table): the one before is a calc of its own,
                    # nested in it.
                    if refusal:
                        break
                    prev = label
                    label2, formula2, refusal = _quick_calc(sec, value or "", disp, inner,
                                                            _member_of, (prev, f"[{prev}]"))
                    if formula2 is None:
                        break
                    nested_calcs[prev] = own_tc
                    label, own_tc = _named(label2, formula2), sec
                ws_measures.append(label)
                aggs[label] = "custom"
                pending_tc.append((label, {**(own_tc or {}), "nested_calcs": nested_calcs}
                                   if nested_calcs else own_tc, refusal))
                continue
            if kind == "agg" and value == "custom":
                # An aggregate calc: when it calls a table-calculation function it runs by the
                # pill's compute-using (Table (across) when the pill states none).
                pending_tc.append((disp, tc, None))
            if kind == "agg":
                if disp in aggs and aggs[disp] != value:
                    fn = _AGG_FN.get(value or "")
                    if fn is None:
                        notes.append(f"worksheet {wname!r} shows {disp!r} aggregated as "
                                     f"{aggs[disp]!r} and as {value!r}; the second has no "
                                     f"formula form and is not emitted.")
                        continue
                    label = f"{fn}({disp})"
                    rec = col_index.get(f"[{bare}]") or {}
                    inner = (f"({rec['formula']})" if rec.get("is_calculated")
                             and rec.get("formula") else f"[{bare}]")
                    agg_calcs.append((dsid, label, f"{fn}({inner})", None))
                    ws_measures.append(label)
                    aggs[label] = "custom"
                    continue
                ws_measures.append(disp)
                aggs[disp] = value
                shelf_fields.append((dsid, bare, disp, None))
            elif kind in ("date_part", "date_trunc"):
                label = _date_label(kind, value, disp)
                date_cols.append((dsid, label, _date_formula(kind, value, disp)))
                ws_cols.append(label)
                base_of[label] = bare
                if where in shelf_dims:
                    shelf_dims[where].append(label)
            else:
                ws_cols.append(disp)
                base_of[disp] = bare
                if where in shelf_dims:
                    shelf_dims[where].append(disp)

        def _label_of(ref: str) -> str | None:
            k, v, b = _derivation(_pill_field(ref)[1] or "", inst)
            if not b:
                return None
            return _date_label(k, v, _display(b)) if k in ("date_part", "date_trunc") \
                else _display(b)
        table_calcs: dict[str, dict] = {}

        def _expand(ref: str) -> list[str]:
            # Every pill of the field `ref` names, in shelf order (rows, columns, marks).
            b = _derivation(_pill_field(ref)[1] or "", inst)[2]
            return [d for d in shelf_dims["rows"] + shelf_dims["cols"] + shelf_dims["marks"]
                    if base_of.get(d) == b]

        def _sort_formula(ref: str) -> str | None:
            # The measure a computed sort orders by, as the calc language writes it.
            k, v, b = _derivation(_pill_field(ref)[1] or "", inst)
            if not b:
                return None
            rec = col_index.get(f"[{b}]") or {}
            inner = (f"({rec['formula']})" if rec.get("is_calculated") and rec.get("formula")
                     else f"[{b}]")
            if k == "agg" and v in _AGG_FN:
                return f"{_AGG_FN[v]}({inner})"
            if k == "agg" and v == "custom" and rec.get("formula"):
                return f"({rec['formula']})"
            return None
        dim_sorts = _dimension_sorts(ws, _label_of, _sort_formula)
        formula_of = {m["name"].lower(): m.get("formula") or "" for m in measures}
        formula_of.update({lab.lower(): f for _d, lab, f, _h in agg_calcs})

        def _nested(name: str, tc: dict | None) -> dict:
            # The compute-using of each table calculation the measure's formula reaches
            # through another calc (checked live, as Desktop saves it: a <table-calc
            # field='[that calc]'> on the pill; without one, that calc runs Table (across),
            # whatever the pill's own compute-using is).
            given = {(_label_of(ref) or "").lower(): rec
                     for ref, rec in ((tc or {}).get("nested") or {}).items()}
            given.update({k.lower(): v for k, v in ((tc or {}).get("nested_calcs") or {}).items()})
            out: dict[str, dict] = {}
            seen, todo = {name.lower()}, [formula_of.get(name.lower(), "")]
            while todo:
                for ref in re.findall(r"\[([^\[\]]+)\]", todo.pop()):
                    key = ref.lower()
                    if key in seen or key not in formula_of:
                        continue
                    seen.add(key)
                    todo.append(formula_of[key])
                    if not TABLE_CALC_FN.search(formula_of[key]):
                        continue
                    how_n = _addressing(given.get(key) or {"ordering-type": "Rows"},
                                        shelf_dims["rows"], shelf_dims["cols"],
                                        shelf_dims["marks"], _label_of, _expand)
                    how_n.pop("pad", None)
                    out[ref] = how_n
            return out
        for name, tc, refusal in pending_tc:
            how = ({"refusal": refusal} if refusal else
                   _addressing(tc, shelf_dims["rows"], shelf_dims["cols"],
                               shelf_dims["marks"], _label_of, _expand))
            nested = {} if "refusal" in how else _nested(name, tc)
            bad = next((v["refusal"] for v in nested.values() if v.get("refusal")), None)
            if bad:
                how = {"refusal": f"a calculation it nests: {bad}"}
            elif nested:
                how["nested"] = {k: {x: v[x] for x in ("addressing", "partitioning")}
                                 for k, v in nested.items()}
            moved = {d: dim_sorts[d] for d in how.get("addressing") or [] if d in dim_sorts}
            if any(v.get("unread") for v in moved.values()):
                how = {"refusal": "the sheet sorts a dimension the calculation runs along by a "
                                  "field whose formula is not read"}
            elif moved:
                # A sorted dimension is run along in the order the view shows it (checked
                # live: a computed sort by the sum descending, a manual order, a descending
                # alphabetic sort).
                how["sort"] = {}
                for d, v in moved.items():
                    rec = {k: v[k] for k in ("direction", "members", "by") if k in v}
                    if v.get("nested"):
                        shelf = next((sh for sh in (shelf_dims["rows"], shelf_dims["cols"])
                                      if d in sh), [])
                        rec["within"] = shelf[:shelf.index(d)] if d in shelf else []
                    how["sort"][d] = rec
            table_calcs[name] = how

        # BLENDS: a SECONDARY data source beside the primary (the first the sheet uses). NOT a
        # SQL join: the secondary is aggregated over the links the view holds and joined to
        # the primary's rows after aggregation. Its links are the fields both data sources
        # hold under one name (Tableau links on them); each is an edge of kind `blend`, and
        # the compiler activates the ones the view groups by (seen live: a link that is not
        # in the view does not narrow the secondary).
        used = [r.get("datasource") for r in ws.findall(".//datasource-dependencies")
                if r.get("datasource") in ds_lineage]
        secondaries: set[str] = set()
        if len(used) > 1:
            primary = used[0]
            for secondary in used[1:]:
                pt, st = table_of_dsid.get(primary), table_of_dsid.get(secondary)
                if not pt or not st or pt == st:
                    continue
                secondaries.add(secondary)
                pf, sf = ds_fields.get(primary, {}), ds_fields.get(secondary, {})
                pairs = [(pf[name], sf[name]) for name in sorted(set(pf) & set(sf))]
                # A CUSTOM link (Data > Edit Blend Relationships): two fields of different
                # names the workbook maps (`<datasource-relationships>`), either way round.
                by_internal = {d: {v[2]: v for v in ds_fields.get(d, {}).values()}
                               for d in (primary, secondary)}
                for (da, fa), (db, fb) in blend_maps:
                    if {da, db} != {primary, secondary}:
                        continue
                    if da == secondary:
                        (da, fa), (db, fb) = (db, fb), (da, fa)
                    a, b = by_internal[primary].get(fa), by_internal[secondary].get(fb)
                    if a and b and (a, b) not in pairs:
                        pairs.append((a, b))
                for a, b in pairs:
                    relationships.append({
                        "from_table": a[0], "from_column": a[1],
                        "to_table": b[0], "to_column": b[1],
                        "cardinality": "many-to-one (post-aggregation)",
                        "cross_filter": None, "is_active": True,
                        "kind": "blend",
                    })

        dashboards = dash_of.get(wname, [])
        usage.append({
            "unit_id": f"tableau::{wname}",
            "container": (dashboards[0]["name"] if dashboards else wname),
            "container_kind": "dashboard" if dashboards else "worksheet",
            "dashboards": dashboards,
            "visual_id": wname,
            # ElementTree has no attribute XPath, so the mark class is read off the
            # element rather than queried for.
            "visual_type": _mark_class(ws),
            "title": title,
            "measures": sorted(set(ws_measures)),
            "columns": sorted(set(ws_cols)),
            "filters": _measure_filters(
                _with_shared(_filters(ws, col_index, date_cols=date_cols,
                                      display=_display, params=param_now), ws, shared),
                col_index, _display,
                agg_calcs)
                       + [f for d in dict.fromkeys(
                           r.get("datasource") for r in ws.findall(".//datasource-dependencies"))
                          for f in ds_filters.get(d or "", [])],
            "sort": (_top_n_sort(ws, col_index, _display)
                     if _top_n(ws, col_index) else None),
            "top_n": _top_n(ws, col_index),
            "parameters": params,
            "subtitle": _text_of(ws.find(".//caption")),
            "description": _text_of(ws.find(".//description")),
            "number_format": _number_format(ws, col_index),
            "axis_titles": _axis_titles(ws),
            "tooltip": _text_of(ws.find(".//tooltip")),
            "reference_lines": _reference_lines(ws),
            "conditional_formatting": _conditional_formatting(ws),
            "source_fragments": {
                "widget": None,      # filled by a2 (written to the run dir)
                "dashboard": None,
                "prose": [t for d in dashboards for t in prose_of.get(d["name"], [])],
            },
            # A sheet placed twice on one dashboard is still on one dashboard.
            "assets": ([_asset("view", wname, ws)]
                       + [_asset("dashboard", n, dash_el.get(n))
                          for n in dict.fromkeys(d["name"] for d in dashboards)]),
            # A Tableau calculated field named on a shelf, a filter or a sort is an
            # EXPRESSION, compiled from its formula, never a stored column.
            "expression_language": "tableau-calc",
            **({"table_calcs": table_calcs} if table_calcs else {}),
            "_aggregations": aggs,
        })
        for f in usage[-1]["filters"]:
            # A filter on a field of a blend's SECONDARY data source narrows the secondary's
            # rows only: it names that data source's table.
            fd = f.pop("_dsid", None)
            if fd in secondaries:
                home = (ds_fields.get(fd, {}).get(str(f.get("column") or "").lower())
                        or (table_of_dsid.get(fd),))[0]
                if home:
                    f["table"] = home

    # PULSE metric definitions, on the published datasources this input holds.
    for f in _pulse_files(path):
        usage += _pulse_usage(f, entries, table_by_ds, index_of_table, _display,
                              shelf_fields, params, notes)

    def _add_measure(dsid: str | None, bare: str, disp: str, home: str | None = None) -> None:
        rec = col_index.get(f"[{bare}]") or {}
        calc = bool(rec.get("is_calculated"))
        measures.append({
            "name": disp,
            # A plain field has no formula: it is a column, aggregated as the shelf says
            # (the unit's `aggregation`). A calc keeps its own and is aggregated the same way.
            "formula": rec.get("formula") if calc else None,
            "formula_language": "tableau-calc" if calc else None,
            "home_table": home or field_home.get((dsid or "", bare.lower()))
            or table_of_dsid.get(dsid or ""),
            "folder": rec.get("folder"),
            "description": rec.get("description") or "",
            "aggregation_at_use_site": True,
            # A plain field aggregated on the shelf is defined by the view (an implicit
            # measure); a calculated field is the data source's or the workbook's own.
            **({} if calc else {"origin": "visual"}),
        })

    have_measure = {m["name"].lower() for m in measures}
    for dsid, label, formula, home in agg_calcs:
        if label.lower() not in have_measure:
            have_measure.add(label.lower())
            measures.append({"name": label, "formula": formula,
                             "formula_language": "tableau-calc",
                             "home_table": home or table_of_dsid.get(dsid or ""),
                             "folder": None, "description": "",
                             "aggregation_at_use_site": True,
                             # Defined on the shelf (a quick table calculation, an
                             # aggregation chosen there), not by the data source.
                             "origin": "visual"})
    for dsid, bare, disp, home in shelf_fields:
        if disp.lower() not in have_measure:
            have_measure.add(disp.lower())
            _add_measure(dsid, bare, disp, home)
    # A date pill is a derived dimension: declared on its table as the calc it is, so the
    # compiler groups by the part or truncation and never by the raw date. On a data source
    # whose Date Properties start the year in another month, a year or quarter pill is the
    # FISCAL one.
    def fiscal_of(dsid: str | None, formula: str) -> int | None:
        ref = re.search(r"\[[^\]]+\]", formula or "")
        m = _fiscal_start(filter_src.get(dsid or "") or [], ref.group(0) if ref else None)
        return m if m and m > 1 else None
    # A relative date filter counts whole weeks from the first day its data source states
    # (`week_start` on the value), Tableau's default when it states none.
    for u in usage:
        for f in u.get("filters") or []:
            dsid = f.pop("_ds", None)
            v = f.get("value")
            if f.get("op") == "relative_date" and isinstance(v, dict) \
                    and v.get("period") == "week":
                v["week_start"] = _week_start(filter_src.get(dsid or "") or [])
    for dsid, label, formula in date_cols:
        start = fiscal_of(dsid, formula)
        if start:
            formula = _fiscal_formula(formula, start)
        ref = re.search(r"\[([^\]]+)\]", formula)
        home = field_home.get((dsid or "", ref.group(1).lower())) if ref else None
        t = table_rec.get(home or table_of_dsid.get(dsid or "", ""))
        if t is None or any(c["name"].lower() == label.lower() for c in t["columns"]):
            continue
        t["columns"].append({
            "name": label, "caption": label, "default_format": None, "data_type": None,
            "description": "", "is_calculated": True, "formula": formula,
            "formula_language": "tableau-calc", "source_column": None, "role": None})

    seen_rel: set[tuple] = set()
    deduped = []
    for r in relationships:
        key = (r["from_table"], r["to_table"], r["kind"], r.get("from_column"),
               r.get("to_column"))
        if key not in seen_rel:
            seen_rel.add(key)
            deduped.append(r)
    relationships = deduped
    # The start of week each data source states (`<date-options start-of-week>`), written into
    # its weekday parts: `DATEPART('weekday', …)` counts from it, and the compiler refuses a
    # weekday whose start it is not told rather than guess the locale's.
    # A data source that states none starts its weeks on Tableau's default day (checked
    # live: the week numbers of 1,070 dates and a relative week both ran from Sunday).
    week_of: dict[str, str] = {}
    for _n, ds, _is_file, own in entries:
        opts = _date_options(ds)
        tname = table_of_dsid.get(ds.get("name") or ds.get("formatted-name") or "")
        if own and tname and opts is not None and opts.get("start-of-week"):
            week_of[tname] = opts.get("start-of-week").lower()
        elif own and tname:
            week_of.setdefault(tname, _DEFAULT_WEEK_START)
    for tname, start in week_of.items():
        def _week(formula: str | None) -> str | None:
            if not formula:
                return formula
            formula = _WEEK_FN.sub(lambda m: f"DATEPART('week', {m.group(1)})", formula)
            return _WEEKDAY_NO_START.sub(lambda m: f"{m.group(1)}, '{start}')", formula)
        for c in (table_rec.get(tname) or {}).get("columns", []):
            c["formula"] = _week(c.get("formula"))
        for m in measures:
            if m.get("home_table") == tname:
                m["formula"] = _week(m.get("formula"))
    sql_params: dict[str, ET.Element] = {}
    for pds in wb_root.iter("datasource"):
        if (pds.get("name") or "") == "Parameters":
            for c in pds.findall("column"):
                for key in ((c.get("name") or "").strip("[]"), c.get("caption") or ""):
                    if key:
                        sql_params.setdefault(key, c)
    _custom_sql_statements(tables, sql_params, notes)

    viewer = _viewer(path)
    if (viewer or {}).get("time_zone"):
        # A relative date filter counts its periods from today, and "to date" ends at the
        # current moment, both in the time zone the viewer's tool reads them in.
        for u in usage:
            for f in u.get("filters") or []:
                v = f.get("value")
                if f.get("op") == "relative_date" and isinstance(v, dict):
                    v["time_zone"] = viewer["time_zone"]
    # The tool's own metadata the files do not hold (a certified data source, a description),
    # from the fetch's `bi_tags.json`; and each calc's references to the others (a chain).
    try:
        _bi_tags.apply_sidecar(assets, _bi_tags.load_sidecar(path))
    except _bi_tags.SidecarError as exc:
        raise TableauError(str(exc)) from exc
    _measure_uses(measures)
    interactions = _interactions(wb_root, param_caption)
    out = {
        "contract_version": "1.0",
        "vendor": "tableau",
        "source_file": str(path),
        "model_name": model_name,
        **({"viewer": viewer} if viewer else {}),
        "tables": tables,
        "measures": measures,
        "relationships": relationships,
        "usage": usage,
        "assets": assets,
        **({"interactions": interactions} if interactions else {}),
        "_unresolved_published": sorted(set(unresolved)),
        "_resolved_published": sorted(set(resolved_pub)),
    }
    if notes:
        out["_reader_notes"] = notes
    return out


# A workbook's actions that change a set or a parameter (the twb schema's
# <edit-group-action> and <edit-parameter-action>, under <actions>), by the common word.
_ACTION_KIND = {"edit-group-action": "set_action", "edit-parameter-action": "parameter_action"}


def _interactions(wb_root: ET.Element, param_caption: dict[str, str]) -> list[dict]:
    """The set and parameter actions the workbook saves (`bi_facts.interactions`): what each
    changes (`target`, the set's or parameter's caption, from the action's <param> naming
    it), how it is run, from which sheets, and what a run and a clear do. The set's members
    and the parameter's value the file saves are the state the statements are compiled for."""
    sets = {}
    for g in wb_root.iter("group"):
        name = (g.get("name") or "").strip("[]")
        if name:
            sets[name.lower()] = g.get("caption") or name
    out = []
    for actions in wb_root.findall("actions"):
        for el in actions:
            kind = _ACTION_KIND.get(el.tag)
            if kind is None:
                continue
            target = None
            for prm in el.iter("param"):
                v = prm.get("value") or ""
                m = re.fullmatch(r"\[Parameters\]\.\[([^\[\]]+)\]", v.strip())
                if kind == "parameter_action" and m:
                    target = param_caption.get(m.group(1), m.group(1))
                    break
                last = re.findall(r"\[([^\[\]]+)\]", v)
                if kind == "set_action" and last and last[-1].lower() in sets:
                    target = sets[last[-1].lower()]
                    break
            if target is None:
                continue
            act, src = el.find("activation"), el.find("source")
            change = el.find("add-or-remove-marks")
            clear = el.find("clear-option")
            out.append({
                "kind": kind, "name": el.get("caption") or (el.get("name") or "").strip("[]")
                or None,
                "target": target,
                "activation": act.get("type") if act is not None else None,
                "sources": [src.get("worksheet")] if src is not None and src.get("worksheet")
                else [],
                "change": change.get("value") if change is not None else None,
                "on_clear": clear.get("type") if clear is not None else None})
    return out


def _measure_uses(measures: list[dict]) -> None:
    """`uses`: the other calculated measures each calc names (`[Caption]`)."""
    calcs = {m["name"].lower(): m["name"] for m in measures if m.get("formula")}
    for m in measures:
        used = []
        for ref in re.findall(r"\[([^\]]+)\]", m.get("formula") or ""):
            n = calcs.get(ref.lower())
            if n and n != m["name"] and n not in used:
                used.append(n)
        if used:
            m["uses"] = used


MEASURE_VALUES = {"multiple values", ":measure values"}
VIEWER_FILE = "viewer.json"


def _viewer(path: pathlib.Path) -> dict | None:
    """The identity this input's user functions are evaluated for: `viewer.json` beside the
    workbook (or in the input folder), schema `bi_viewer` (its domain and groups and whether
    its name can be bound at verification, never a name, and the time zone it reads "now"
    in), written by the fetch for the identity it signed in as or by the operator.
    Untrusted data: validated, values kept as text and used only as literals."""
    folder = path if path.is_dir() else path.parent
    f = folder / VIEWER_FILE
    if not f.is_file():
        return None
    import jsonschema  # noqa: PLC0415 - only when a viewer file is present

    from metrics_agent.lib import artifacts as _artifacts  # noqa: PLC0415
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
        jsonschema.validate(doc, _artifacts.schema("bi_viewer.schema.json"))
    except (ValueError, jsonschema.ValidationError) as exc:
        raise TableauError(f"{f.name}: not a viewer file ({str(exc)[:200]})") from exc
    return {k: doc[k] for k in ("domain", "groups", "source", "user_name_bindable",
                                "time_zone") if k in doc}


def _measure_filters(filters: list[dict], col_index: dict[str, dict], display,
                     agg_calcs: list) -> list[dict]:
    """Each filter on an aggregated pill names the MEASURE it tests (`measure`): a calc's own
    name, or the aggregate of a plain field (`SUM(AMOUNT)`), declared as a measure of its
    own so the test is compiled from it (a HAVING at the view's grain)."""
    for f in filters:
        agg = f.pop("_measure_agg", None)
        if not agg:
            continue
        dsid, bare, value = agg
        disp = display(bare)
        rec = col_index.get(f"[{bare}]") or {}
        if value == "custom" or (rec.get("is_calculated") and value is None):
            f["measure"] = disp
            continue
        fn = _AGG_FN.get(value or "")
        if fn is None:
            continue        # an aggregation with no formula form: refused by its stage
        inner = (f"({rec['formula']})" if rec.get("is_calculated") and rec.get("formula")
                 else f"[{bare}]")
        label = f"{fn}({disp})"
        agg_calcs.append((dsid, label, f"{fn}({inner})", None))
        f["measure"] = label
    return filters


def _pulse_usage(f: pathlib.Path, entries: list[tuple], table_by_ds: dict[int, str],
                 index_of_table: dict[str, dict], display, shelf_fields: list,
                 params: list[dict], notes: list[str]) -> list[dict]:
    """One question unit per Pulse metric definition in `f`: its measure (a field and an
    aggregation), its filters, its time dimension, over the published datasource whose
    .tds/.tdsx is beside it — matched by the file's name (`<datasource>.pulse.json`), or the
    only one there is.

    A definition's measure is aggregated as the definition says, exactly like a field on a
    shelf, so it is a unit like a worksheet's. `is_running_total` is Pulse's ordinary
    CUMULATIVE metric (the value accumulated over the measurement period, as the Pulse page
    saves a sum), not a table calculation: the measure is the same aggregate, and the period
    is said in the subtitle. The default metric's own filters apply to the number Pulse
    shows, so they are the unit's too. A definition written only as a viz state is said in a
    reader note and not emitted. Fields are named as the API names them: the datasource's
    remote names (`EXTENDED_PRICE`), or its captions."""
    defs = _pulse_definitions(f)
    stem = urllib.parse.unquote_plus(f.name[:-len(PULSE_SUFFIX)])
    files = [e for e in entries if e[2] and e[3]]
    want = _ds_name_key(stem)
    ds_entry = next((e for e in files if want in _identity_keys(e[1], e[0])), None)
    if ds_entry is None and len(files) == 1:
        ds_entry = files[0]
    if ds_entry is None:
        names = ", ".join(sorted({_ds_label(e[1], e[0]) for e in files})) or "none"
        raise TableauError(
            f"{f.name}: no published datasource beside it is named {stem!r} (found: {names}). "
            f"Name the definitions file after the datasource it is built on "
            f"(<datasource>{PULSE_SUFFIX}).")
    ids = {str(((d.get("specification") or {}).get("datasource") or {}).get("id") or "")
           for d in defs} - {""}
    if len(ids) > 1:
        raise TableauError(
            f"{f.name} holds definitions on {len(ids)} different datasources "
            f"({', '.join(sorted(ids)[:4])}). Save one file per published datasource, named "
            f"after it (<datasource>{PULSE_SUFFIX}).")
    tname = table_by_ds.get(id(ds_entry[1]))
    index = index_of_table.get(tname or "", {})

    def _field(name: str) -> tuple[str, dict] | None:
        key = (name or "").strip().strip("[]").lower()
        for internal, rec in index.items():
            if key in {internal.strip("[]").lower(), str(rec.get("name") or "").lower(),
                       str(rec.get("caption") or "").lower()}:
                return internal.strip("[]"), rec
        return None

    out: list[dict] = []
    seen: set[str] = set()
    for d in defs:
        meta = d.get("metadata") or {}
        name = str(meta.get("name") or meta.get("id") or "").strip() or "pulse metric"
        spec = d.get("specification") or {}
        basic = spec.get("basic_specification") or {}
        measure = basic.get("measure") or {}
        if not measure.get("field"):
            notes.append(f"Pulse definition {name!r} ({f.name}) has no basic measure (an "
                         f"advanced, viz-state definition); not emitted.")
            continue
        hit = _field(measure["field"])
        if hit is None:
            notes.append(f"Pulse definition {name!r} ({f.name}) measures "
                         f"{measure['field']!r}, which is not a field of {tname!r}; not "
                         f"emitted. Is the .tds beside it the datasource it is built on?")
            continue
        bare, rec = hit
        disp = display(bare)
        raw_agg = re.sub(r"^aggregation_", "", str(measure.get("aggregation") or "").lower())
        agg = _PULSE_AGG.get(raw_agg)
        if rec.get("is_calculated") and _CALC_AGG.search(rec.get("formula") or ""):
            agg = "custom"      # an aggregate calc aggregates itself
        if agg is None:
            notes.append(f"Pulse definition {name!r} ({f.name}) aggregates with "
                         f"{measure.get('aggregation')!r}, which the reader does not know; "
                         f"recorded as custom.")
            agg = "custom"
        shelf_fields.append((None, bare, disp, tname))
        filters = []
        default = next((m for m in d.get("metrics") or []
                        if isinstance(m, dict) and m.get("is_default")), None) or {}
        mspec = default.get("specification") or {}
        for flt in [*(basic.get("filters") or []), *(mspec.get("filters") or [])]:
            fh = _field(str(flt.get("field") or ""))
            col = ((fh[1].get("name") if fh[1].get("is_calculated") else fh[0])
                   if fh else str(flt.get("field") or "")).lower()
            vals = [v for v in (_pulse_value(x) for x in flt.get("categorical_values") or []
                                if isinstance(x, dict)) if v is not None]
            op = re.sub(r"^operator_", "", str(flt.get("operator") or "").lower())
            op = {"equal": "in", "equals": "in", "in": "in", "not_equal": "not_in",
                  "not_in": "not_in"}.get(op, op or "filter")
            filters.append({
                "scope": "worksheet", "stage": "dimension", "column": col, "op": op,
                "value": vals[0] if len(vals) == 1 else (vals or None),
                "evidence": "tableau pulse definition filter"})
        time_field = str((basic.get("time_dimension") or {}).get("field") or "") or None
        period = mspec.get("measurement_period") or {}
        grain = re.sub(r"^granularity_by_", "", str(period.get("granularity") or "").lower()) \
            or None
        window = str(period.get("range") or "").upper()
        if time_field:
            th = _field(time_field)
            # The caption a person reads ("Order Date"), whatever name the API used.
            time_field = (str(th[1].get("caption") or "") or display(th[0])) if th else time_field
            how = ("accumulated over each " + grain if spec.get("is_running_total") and grain
                   else "accumulated over the period" if spec.get("is_running_total")
                   else f"the latest {grain}'s value" if grain else None)
            subtitle = f"Pulse metric over {time_field}" + (f", {how}" if how else "")
            # The window Pulse shows: the current period so far. Applied as the unit's filter
            # where the calendar part means the same in every warehouse; any other window
            # (a previous complete period, a week) is said, never approximated.
            if th and window == "RANGE_CURRENT_PARTIAL" and grain in ("day", "month",
                                                                       "quarter", "year"):
                filters.append({
                    "scope": "worksheet", "stage": "dimension", "column": th[0].lower(),
                    "op": "period_to_date", "value": grain,
                    "evidence": "tableau pulse measurement period"})
                subtitle += f"; shown for the current {grain} to date"
            elif window:
                notes.append(f"Pulse definition {name!r} ({f.name}) shows the window "
                             f"{window.lower()} by {grain or '?'}; the question is the measure "
                             f"over all time, with that window said, not applied.")
        else:
            subtitle = None
        uid = f"tableau::pulse::{name}"
        while uid in seen:
            uid += "'"
        seen.add(uid)
        out.append({
            "unit_id": uid, "container": name, "container_kind": "worksheet",
            "dashboards": [], "visual_id": f"pulse::{name}", "visual_type": "pulse-metric",
            "title": name, "measures": [disp], "columns": [], "filters": filters,
            "sort": None, "top_n": None, "parameters": params,
            "subtitle": subtitle,
            "description": str(meta.get("description") or "") or None,
            "number_format": _pulse_format(d),
            "axis_titles": {}, "tooltip": None, "reference_lines": [],
            "conditional_formatting": [],
            "source_fragments": {"widget": None, "dashboard": None, "prose": []},
            "assets": [{"role": "view", "name": name,
                        "vendor_id": str(meta.get("id")) if meta.get("id") else None,
                        "path": None, "site": None, "catalog_id": None,
                        "catalog_qualified_name": None}],
            "expression_language": "tableau-calc",
            "_aggregations": {disp: agg},
        })
        # The breakdowns a viewer may cut the metric by (`allowed_dimensions`): one question
        # each, grouped by it, so the metric carries those dimensions like a worksheet's.
        base = out[-1]
        for dim in (d.get("extension_options") or {}).get("allowed_dimensions") or []:
            dh = _field(str(dim))
            if dh is None:
                notes.append(f"Pulse definition {name!r} ({f.name}) may be broken down by "
                             f"{dim!r}, which is not a field of {tname!r}; that breakdown "
                             f"is not emitted.")
                continue
            dcol = display(dh[0])
            shelf_fields.append((None, dh[0], dcol, tname))
            label = str(dh[1].get("caption") or "") or dcol
            out.append({**base, "unit_id": f"{base['unit_id']}::by::{dcol}",
                        "visual_id": f"{base['visual_id']}::by::{dcol}",
                        "title": f"{name} by {label}", "columns": [dcol],
                        "filters": list(base["filters"])})
    return out


_SQL_PARAM = re.compile(r"<\[Parameters\]\.\[([^\[\]]+)\]>")


def _param_literal(name: str, params: dict[str, ET.Element]) -> str:
    """A parameter's declared default as a SQL literal of its declared type, checked against
    that type: never the raw text pasted into the statement."""
    col = params.get(name)
    if col is None:
        raise TableauError(f"custom SQL names the parameter {name!r}, which the workbook does "
                           f"not declare")
    dtype = (col.get("datatype") or "").lower()
    raw = (col.get("value") or "").strip()
    if dtype == "string" and len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return "'" + raw[1:-1].replace("'", "''") + "'"
    if dtype in ("integer", "real") and re.fullmatch(r"-?\d+(\.\d+)?", raw):
        return raw
    if dtype in ("date", "datetime"):
        m = re.fullmatch(r"#(\d{4}-\d{2}-\d{2})( \d{2}:\d{2}:\d{2})?#", raw)
        if m:
            kind = "TIMESTAMP" if m.group(2) else "DATE"
            return f"CAST('{m.group(1)}{m.group(2) or ''}' AS {kind})"
    if dtype == "boolean" and raw.lower() in ("true", "false"):
        return raw.upper()
    raise TableauError(f"custom SQL names the parameter {name!r}, whose declared default "
                       f"{raw[:40]!r} is not a {dtype or 'typed'} value; the statement cannot "
                       f"be written without guessing it")


def _custom_sql_statements(tables: list[dict[str, Any]],
                           params: dict[str, ET.Element] | None = None,
                           notes: list[str] | None = None) -> None:
    """Say, in the common format, which tables are a custom-SQL statement.

    A custom-SQL datasource names no physical object: the statement IS its source
    (`statement_is_source`), so it is a warehouse target exactly when the statement was
    recovered, and the calcs over it compile against the statement's output columns. A
    Tableau pill names a plain field by its INTERNAL name (the query's output column) while
    the model column carries the caption, so both spellings map to the output column in
    `native_columns`. A calc is not a column of the query — it is compiled from its formula —
    and mapping it would stop the compiler inlining it.

    A Tableau PARAMETER in the statement (`<[Parameters].[Region]>`) is the parameter's
    declared default, written as a literal of its declared type (`params`: internal name and
    caption -> its `<column>`); a parameter with no usable default refuses the table by name,
    as the statement does not parse with the token in it.

    Run after the calc captions are settled: a column's `name` is rewritten above."""
    for t in tables:
        lin = t.get("source_lineage") or {}
        sql = lin.get("raw_expression") or ""
        if lin.get("object_kind") != "custom-sql" or not sql or not t.get("logical_name"):
            continue
        if _SQL_PARAM.search(sql):
            try:
                sql = _SQL_PARAM.sub(lambda m: _param_literal(m.group(1), params or {}), sql)
            except TableauError as exc:
                t["source_lineage"] = {**lin, "refusal": str(exc)}
                continue
            if notes is not None:
                notes.append(f"custom SQL of {t['logical_name']!r}: each Tableau parameter "
                             f"in it is fixed at its declared default")
        plain = [c for c in t["columns"] if not c.get("is_calculated")]
        pairs = [(c["source_column"], c["source_column"]) for c in plain
                 if c.get("source_column")]
        pairs += [(c["name"], c.get("source_column") or c["name"]) for c in plain]
        # A copy: two tables may share one lineage dict (a published datasource adopted twice).
        t["source_lineage"] = {**lin, "native_sql": sql, "statement_is_source": True,
                               "native_columns": [{"name": n, "column": col}
                                                  for n, col in pairs]}


# --------------------------------------------------------------------------- the reader
class TableauReader(FormulaCompiler):
    """The `tableau` BI reader (`sockets.BIReader`), registered under `metrics_agent.readers`.

    One form: a workbook or datasource file, a packaged one, a Pulse definitions file beside
    its datasource, or a directory holding them (with any virtual-connection files the
    datasources connect through)."""

    name = "tableau"
    # The formula languages it writes; its formulas compile through `FormulaCompiler`.
    languages = ("tableau-calc",)
    # A virtual connection's file is read beside a datasource, never as a model of its own.
    suffixes = SUFFIXES + VC_SUFFIXES

    def detect(self, path: pathlib.Path) -> bool:
        if path.is_dir():
            return bool(_tableau_files(path))
        return path.suffix.lower() in SUFFIXES or _is_pulse(path)

    def parse(self, path: pathlib.Path, **opts: Any) -> dict[str, Any]:
        # The run's dialect is what a custom-SQL statement is read in (the tables it reads).
        return parse(path, dialect=str(opts.get("dialect") or ""))

    @staticmethod
    def models(path: pathlib.Path) -> list[pathlib.Path]:
        """The workbooks a folder holds, one model each: a fetch's `workbooks/<name>/`
        folders (each a workbook with its data sources beside it). Any other path is one
        model, read as it is."""
        root = pathlib.Path(path) / "workbooks"
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir() and _tableau_files(p))
