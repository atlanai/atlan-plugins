"""Tableau export files -> the model a tile is resolved against.

A tile is a worksheet, a worksheet names fields of a datasource, and a
datasource names its fields in terms of relations that are warehouse tables or
custom SQL. Every hop of that chain is an exact attribute in the XML, but three
of them are commonly guessed at, and each guess is wrong somewhere in real
files:

  * A field's relation is `metadata-record/parent-name`, never the
    `(Relation Name)` suffix Tableau leaves on a local name. In a real workbook
    `[orders (Custom SQL Query1)]` can live in relation `[Orders Joined]`;
    `parent-name` named a relation of its own datasource every time.
  * A workbook on a published datasource only proxies it (`sqlproxy`). Its
    metadata names the published field (`remote-name`), which has to be looked
    up again inside the published `.tds`, paired on `repository-location/@id`.
    The `.tds` can be older than the workbook, so a missing field blocks the
    tiles that use it, not the workbook.
  * Fields are recorded with `class="measure"` as well as `class="column"`;
    reading only the latter loses half of a proxied workbook's fields.

Files are untrusted input. XML declaring a DOCTYPE or ENTITY is refused (entity
expansion is the classic XML bomb), archive members are size-capped before
they are read, and byte-identical copies (an archive and the XML extracted
from it) are read once, remembering every path so the workbook's LUID can be
taken from whichever file name carries it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sqlglot_lineage

MAX_MEMBER_BYTES = 256 * 1024 * 1024
_DTD = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_BRACKETED = re.compile(r"\[((?:[^\]]|\]\])*)\]")

# Tableau connection class -> sqlglot dialect. Anything else (Excel, Google
# Sheets, a bare extract) is not a warehouse this package can write SQL for.
DIALECTS = {
    "redshift": "redshift", "snowflake": "snowflake", "postgres": "postgres",
    "bigquery": "bigquery", "sqlserver": "tsql", "databricks": "databricks",
    "spark": "spark", "oracle": "oracle", "mysql": "mysql", "teradata": "teradata",
    "sqlite": "sqlite",
}


def _conn_class(conn) -> str | None:
    """A connection's class; a generic ODBC connection names its engine in `odbc-dbms-name`."""
    cls = conn.get("class")
    if cls == "genericodbc" and (conn.get("odbc-dbms-name") or "").strip().lower() in DIALECTS:
        return conn.get("odbc-dbms-name").strip().lower()
    return cls


def bracket_parts(text: str | None) -> list[str]:
    """`[bi_assets].[events]` -> ["bi_assets", "events"]."""
    return [p.replace("]]", "]") for p in _BRACKETED.findall(text or "")]


@dataclass
class Doc:
    labels: list[str]               # every path (or archive::member) with these bytes
    root: ET.Element
    sha12: str

    @property
    def label(self) -> str:
        return self.labels[0]

    @property
    def kind(self) -> str:
        return "workbook" if self.root.tag == "workbook" else "datasource"

    @property
    def luid(self) -> str | None:
        """Exports are named by Tableau LUID; the XML itself never carries it."""
        for label in self.labels:
            for part in reversed(label.replace("::", "/").split("/")):
                m = _UUID.search(part)
                if m:
                    return m.group(0).lower()
        return None


def load(root: Path) -> tuple[list[Doc], list[str]]:
    """Every .twb/.tds under `root`, including inside .twbx/.tdsx, read once."""
    blobs: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) or not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix in (".twb", ".tds"):
            if p.stat().st_size > MAX_MEMBER_BYTES:
                skipped.append(f"{rel}: larger than {MAX_MEMBER_BYTES} bytes")
                continue
            blobs.append((str(rel), p.read_bytes()))
        elif suffix in (".twbx", ".tdsx"):
            try:
                with zipfile.ZipFile(p) as z:
                    for info in z.infolist():
                        if not info.filename.lower().endswith((".twb", ".tds")):
                            continue
                        if info.file_size > MAX_MEMBER_BYTES:
                            skipped.append(f"{rel}::{info.filename}: expands past the size cap")
                            continue
                        blobs.append((f"{rel}::{info.filename}", z.read(info)))
            except zipfile.BadZipFile:
                skipped.append(f"{rel}: not a readable archive")
    docs: list[Doc] = []
    by_hash: dict[str, Doc] = {}
    for label, blob in blobs:
        h = hashlib.sha256(blob).hexdigest()[:12]
        if h in by_hash:
            by_hash[h].labels.append(label)
            continue
        if _DTD.search(blob):
            skipped.append(f"{label}: declares a DOCTYPE/ENTITY; refused")
            continue
        try:
            el = ET.fromstring(blob)
        except ET.ParseError as e:
            skipped.append(f"{label}: not well-formed XML ({e})")
            continue
        if el.tag not in ("workbook", "datasource"):
            skipped.append(f"{label}: root <{el.tag}> is not a workbook or datasource")
            continue
        by_hash[h] = Doc([label], el, h)
        docs.append(by_hash[h])
    return docs, skipped


def manifest_names(root: Path) -> dict[str, str]:
    """LUID -> asset name from the export's `manifest.json`.

    The XML carries only the content URL, which keeps the name a workbook was
    first published under; the manifest has the current one."""
    try:
        data = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = [e for key in ("artifacts", "assets") for e in data.get(key) or []
               if isinstance(e, dict)]
    return {str(e["luid"]).lower(): str(e["name"]).strip()
            for e in entries if e.get("luid") and e.get("name")}


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

@dataclass
class Relation:
    name: str | None
    kind: str                       # table | text | join | collection | other
    table: str | None = None        # [schema].[table]
    sql: str | None = None          # custom SQL, angle brackets un-doubled
    join: str | None = None
    on: ET.Element | None = None
    children: list["Relation"] = field(default_factory=list)

    def leaves(self) -> list["Relation"]:
        if self.kind in ("join", "collection"):
            return [leaf for c in self.children for leaf in c.leaves()]
        return [self]

    @property
    def table_name(self) -> str | None:
        """`[bi_assets].[events]` -> `bi_assets.events`, as the relation writes it."""
        if self.kind != "table":
            return None
        return ".".join(bracket_parts(self.table)) or None


    def tables(self, dialect: str | None) -> list[str]:
        """The warehouse tables this relation reads, as its text writes them."""
        if self.kind == "table":
            return [self.table_name] if self.table_name else []
        if self.kind != "text":
            return sorted({t for c in self.children for t in c.tables(dialect)})
        try:
            tree = sqlglot.parse_one(self.sql or "", read=dialect)
        except Exception:  # noqa: BLE001 - unparseable custom SQL is refused when assembled
            return []
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        return sorted({_dotted(t) for t in tree.find_all(exp.Table)
                       if t.db or t.name.lower() not in ctes})

    def lineage(self, column: str, dialect: str | None) -> tuple[str | None, list[str], bool]:
        """Custom SQL's last hop: the SELECT expression behind an output column,
        the warehouse columns it reads, and whether it is one of them unchanged."""
        return _sql_lineage(self.sql or "", column, dialect)


def _dotted(t: exp.Table) -> str:
    return ".".join(p for p in (t.catalog, t.db, t.name) if p)


@functools.lru_cache(maxsize=4096)
def _sql_lineage(sql: str, column: str, dialect: str | None) -> tuple[str | None, list[str], bool]:
    try:
        node = sqlglot_lineage(column, sql, dialect=dialect)
    except Exception:  # noqa: BLE001 - an untraceable column is reported by the caller
        return None, [], False
    leaves = [n for n in node.walk() if not n.downstream and isinstance(n.source, exp.Table)]
    cols = sorted({f"{_dotted(n.source)}.{n.name.split('.')[-1]}" for n in leaves})
    plain = isinstance(node.expression.unalias(), exp.Column) and len(cols) == 1
    return node.expression.sql(dialect=dialect), cols, plain


def _is_relation(tag: str) -> bool:
    # Tableau 2020.2+ also writes `_.fcp.ObjectModelEncapsulateLegacy.true...relation`.
    return tag == "relation" or tag.endswith("...relation")


def parse_relation(el: ET.Element) -> Relation:
    t = el.get("type")
    kids = [parse_relation(c) for c in el if _is_relation(c.tag)]
    if t == "join":
        clause = el.find("clause")
        return Relation(None, "join", join=el.get("join"), children=kids,
                        on=clause.find("expression") if clause is not None else None)
    if t == "collection":
        return Relation(None, "collection", children=kids)
    if t == "text":
        # Tableau doubles angle brackets inside relation text: `<<>>` is `<>`.
        text = (el.text or "").replace("<<", "<").replace(">>", ">").strip()
        return Relation(el.get("name"), "text", sql=text)
    if t == "table":
        return Relation(el.get("name"), "table", table=el.get("table"))
    return Relation(el.get("name"), t or "other")


# ---------------------------------------------------------------------------
# Fields and datasources
# ---------------------------------------------------------------------------

@dataclass
class FieldDef:
    name: str                       # bracketed local name, e.g. "[event_date]"
    kind: str                       # physical | calc | group | bin | set | unknown
    caption: str | None = None
    datatype: str | None = None     # Tableau's type (after any user change)
    source_type: str | None = None  # metadata <local-type>, before a user change
    role: str | None = None
    formula: str | None = None
    remote: str | None = None       # metadata remote-name
    parent: str | None = None       # metadata parent-name, unbracketed
    customized: bool = False        # datatype-customized: Tableau casts the value
    element: ET.Element | None = None

    @property
    def label(self) -> str:
        return self.caption or self.name.strip("[]")


def param_def(c: ET.Element) -> dict:
    calc = c.find("calculation")
    value = c.get("value") if c.get("value") is not None else (
        calc.get("formula") if calc is not None else None)
    return {"name": c.get("name"), "caption": c.get("caption"),
            "datatype": c.get("datatype"), "value": value}


class DS:
    """One <datasource>, from a workbook or a published .tds."""

    def __init__(self, el: ET.Element, doc: Doc):
        self.el, self.doc = el, doc
        self.name = el.get("name") or el.get("formatted-name") or "?"
        self.caption = el.get("caption") or el.get("formatted-name") or self.name
        conn = el.find("connection")     # the live connection, not the extract's
        self.conn_class = conn.get("class") if conn is not None else None
        self.kind = "parameters" if self.name == "Parameters" else (self.conn_class or "none")
        rl = el.find("repository-location")
        self.repo_id = rl.get("id") if rl is not None else None
        if self.repo_id is None and conn is not None and self.kind == "sqlproxy":
            self.repo_id = conn.get("dbname")
        self.warehouses = {nc.get("name"): _conn_class(nc.find("connection"))
                           for nc in (conn.iter("named-connection") if conn is not None else [])
                           if nc.find("connection") is not None}
        self.top: Relation | None = None
        rels = [c for c in (conn if conn is not None else []) if _is_relation(c.tag)]
        # Prefer the object-model form when both are written.
        rels.sort(key=lambda c: 0 if c.tag.endswith("true...relation") else 1 if c.tag == "relation" else 2)
        if rels:
            self.top = parse_relation(rels[0])
        self.relations = {r.name: r for r in (self.top.leaves() if self.top else []) if r.name}
        self.meta: dict[str, dict] = {}
        for mr in (conn.iter("metadata-record") if conn is not None else []):
            local = mr.findtext("local-name")
            if mr.get("class") in ("column", "measure") and local:
                self.meta[local] = {"remote": mr.findtext("remote-name"),
                                    "parent": (mr.findtext("parent-name") or "").strip("[]"),
                                    "local_type": mr.findtext("local-type"), "family": mr.findtext("family")}
        # Tableau's own column map says the same as parent-name/remote-name (every
        # time in real files); it stands in when a field has no metadata record.
        for cm in (conn.findall("cols/map") if conn is not None else []):
            parts = bracket_parts(cm.get("value"))
            if cm.get("key") not in self.meta and len(parts) == 2:
                self.meta[cm.get("key")] = {"remote": parts[1], "parent": parts[0],
                                            "local_type": None, "family": None}
        self.columns = {c.get("name"): c for c in el.findall("column") if c.get("name")}
        self.groups = {g.get("name"): g for g in el.findall("group") if g.get("name")}
        self.filters = el.findall("filter")
        self.extract_filters = el.findall("extract/filter")
        self.params: dict[str, dict] = {}
        for dd in el.findall("datasource-dependencies"):
            if dd.get("datasource") == "Parameters":
                self.params.update({c.get("name"): param_def(c) for c in dd.findall("column")})
        if self.kind == "parameters":
            self.params.update({c.get("name"): param_def(c) for c in el.findall("column")})
        # The datasource's own date options override the inherited ones.
        do = el.find("date-options")
        if do is None:
            do = el.find("overridable-settings/date-options")
        self.week_start = do.get("start-of-week") if do is not None else None
        # object-graph object id -> relation, for row counts of a logical table.
        self.objects: dict[str, str | None] = {}
        for o in el.findall("object-graph/objects/object"):
            rel = next((p.find("relation").get("name") for p in o.findall("properties")
                        if p.get("context") == "" and p.find("relation") is not None), None)
            self.objects[o.get("id")] = rel

    def dialect(self) -> str | None:
        """The warehouse this datasource's SQL runs on, when there is exactly one."""
        classes = {c for c in self.warehouses.values() if c}
        if not classes and self.conn_class in DIALECTS:
            classes = {self.conn_class}
        return DIALECTS.get(next(iter(classes))) if len(classes) == 1 else None

    def field(self, name: str) -> FieldDef | None:
        col, meta = self.columns.get(name), self.meta.get(name)
        if col is None and meta is None and name not in self.groups:
            return None
        fd = FieldDef(name=name, kind="unknown", element=col)
        if col is not None:
            fd.caption, fd.datatype, fd.role = col.get("caption"), col.get("datatype"), col.get("role")
            fd.customized = col.get("datatype-customized") == "true"
            calc = col.find("calculation")
            if calc is not None:
                cls = calc.get("class")
                if cls == "tableau" and calc.get("formula") is not None:
                    fd.kind, fd.formula = "calc", calc.get("formula")
                else:
                    fd.kind = {"categorical-bin": "group", "bin": "bin"}.get(cls, f"calculation class {cls}")
        if fd.kind == "unknown" and name in self.groups:
            fd.kind = "set"
        if meta is not None:
            fd.datatype = fd.datatype or meta["local_type"]
            fd.source_type = meta["local_type"]
            if fd.kind == "unknown":
                fd.kind, fd.remote, fd.parent = "physical", meta["remote"], meta["parent"]
        return fd


class Workbook:
    def __init__(self, doc: Doc, names: dict[str, str]):
        self.doc = doc
        root = doc.root
        self.luid = doc.luid
        rl = root.find("repository-location")
        content_url = rl.get("id") if rl is not None else None
        member = next((l.split("::", 1)[1] for l in doc.labels if "::" in l), None)
        self.name = (names.get(self.luid or "") or (Path(member).stem if member else None)
                     or content_url or Path(doc.label).stem)
        self.id = self.luid or content_url or doc.sha12
        self.datasources = {d.get("name"): DS(d, doc) for d in root.findall("datasources/datasource")}
        pds = self.datasources.get("Parameters")
        self.params = pds.params if pds else {}
        self.worksheets = {w.get("name"): w for w in root.findall("worksheets/worksheet")}
        self.dashboards = root.findall("dashboards/dashboard")
        # Filters "applied to all worksheets using this datasource".
        self.shared_filters: dict[str, list[tuple[ET.Element, dict]]] = {}
        for sv in root.findall("shared-views/shared-view"):
            deps = {c.get("name"): c for dd in sv.findall("datasource-dependencies")
                    for c in dd.findall("column")}
            for f in sv.findall("filter"):
                self.shared_filters.setdefault(sv.get("name"), []).append((f, deps))


class PublishedIndex:
    """Published datasources (.tds) by `repository-location/@id`."""

    def __init__(self, docs: list[Doc]):
        self.by_repo: dict[str, list[DS]] = {}
        for d in docs:
            ds = DS(d.root, d)
            self.by_repo.setdefault(ds.repo_id or Path(d.label).stem, []).append(ds)

    def pair(self, wds: DS) -> DS | None:
        """The .tds a `sqlproxy` datasource stands for. The workbook's revision is
        its own, not the datasource's, so among copies the one holding most of
        the fields the workbook names wins; drift is then checked per field."""
        cands = self.by_repo.get(wds.repo_id or "", [])
        wanted = [m["remote"] for m in wds.meta.values() if m.get("remote")]
        return min(cands, default=None, key=lambda c: (
            -sum(c.field(f"[{r}]") is not None for r in wanted), c.doc.label))
