"""The Power BI semantic model, one shape whatever file it came from.

    TMDL       a PBIP `*.SemanticModel/definition/` folder (Power BI Desktop's
               current save format, and what Fabric git integration writes)
    TMSL JSON  `model.bim` in an older PBIP, or `DataModelSchema` inside a
               .pbit template (UTF-16)
    .pbix      the compressed `DataModel` part, read with pbixray (an optional
               dependency); a .pbix without one is a live connection to a
               remote model and has nothing to read

No Power BI Desktop is needed for any of them. What each loader returns is the
same `Model`: tables with their columns (source column or DAX expression),
measures, their partition source (M or DAX), and the relationships.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from eval_builder.sources.powerbi import tmdl


@dataclass
class Column:
    name: str
    source_column: str | None = None            # the relation's column; None for a calculated one
    expression: str | None = None               # DAX, for a calculated column
    data_type: str | None = None
    summarize_by: str | None = None             # the default aggregation of a dropped-in column
    description: str | None = None

    @property
    def calculated(self) -> bool:
        return self.expression is not None


@dataclass
class Table:
    name: str
    columns: dict[str, Column] = field(default_factory=dict)
    measures: dict[str, str] = field(default_factory=dict)          # name -> DAX
    m: str | None = None                        # the import/DirectQuery partition's M
    dax: str | None = None                      # a calculated table's DAX
    mode: str | None = None
    hidden: bool = False


@dataclass
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    active: bool = True


@dataclass
class Model:
    tables: dict[str, Table] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    expressions: dict[str, str] = field(default_factory=dict)       # shared M (name -> M)
    origin: str = ""                            # "tmdl" | "tmsl" | "pbix"
    notes: list[str] = field(default_factory=list)

    def measure(self, name: str) -> str | None:
        """DAX names are case-insensitive, and references carry stray spaces."""
        key = " ".join(name.split()).lower()
        for t in self.tables.values():
            if name in t.measures:
                return t.measures[name]
            for n, dax_ in t.measures.items():
                if " ".join(n.split()).lower() == key:
                    return dax_
        return None

    def measure_home(self, name: str) -> str | None:
        """The table a measure is defined on: an unqualified column in it means
        a column of that table."""
        key = " ".join(name.split()).lower()
        for t in self.tables.values():
            if any(" ".join(n.split()).lower() == key for n in t.measures):
                return t.name
        return None

    def column(self, table: str, name: str) -> Column | None:
        """DAX column names are case-insensitive."""
        t = self.table(table)
        if t is None:
            return None
        if name in t.columns:
            return t.columns[name]
        low = name.lower()
        return next((c for n, c in t.columns.items() if n.lower() == low), None)

    def table(self, name: str) -> Table | None:
        if name in self.tables:
            return self.tables[name]
        low = name.lower()                      # DAX table names are case-insensitive
        return next((t for n, t in self.tables.items() if n.lower() == low), None)


class ModelUnavailable(Exception):
    """The file carries no semantic model this adapter can read."""


# ---------------------------------------------------------------- TMDL

def from_tmdl(definition: Path) -> Model:
    nodes = tmdl.load_folder(definition)
    model = Model(origin="tmdl")
    for n in nodes:
        if n.keyword == "table":
            model.tables[n.name] = _tmdl_table(n)
        elif n.keyword == "relationship":
            try:
                ft, fc = tmdl.column_ref(n.props["fromColumn"])
                tt, tc = tmdl.column_ref(n.props["toColumn"])
            except (KeyError, ValueError):
                model.notes.append(f"relationship {n.name} unreadable")
                continue
            model.relationships.append(Relationship(ft, fc, tt, tc,
                                                    n.props.get("isActive", "true").lower() != "false"))
        elif n.keyword == "expression" and n.expression:
            model.expressions[n.name] = n.expression
        elif n.keyword == "model":
            for c in n.children:
                if c.keyword == "table":                # a table declared inline in model.tmdl
                    model.tables[c.name] = _tmdl_table(c)
    return model


def _tmdl_table(n: tmdl.Node) -> Table:
    t = Table(n.name, hidden="isHidden" in n.props)
    for c in n.all("column"):
        t.columns[c.name] = Column(c.name, source_column=c.props.get("sourceColumn"),
                                   expression=c.expression, data_type=c.props.get("dataType"),
                                   summarize_by=c.props.get("summarizeBy"),
                                   description=c.props.get("description"))
    for mz in n.all("measure"):
        if mz.expression:
            t.measures[mz.name] = mz.expression
    for p in n.all("partition"):
        kind = (p.expression or "").strip().lower()
        src = p.props.get("source")
        if kind == "calculated":
            t.dax = src
        elif kind in ("m", "") and src:
            t.m, t.mode = src, p.props.get("mode")
    return t


# ---------------------------------------------------------------- TMSL (model.bim, DataModelSchema)

def _text(v) -> str | None:
    if v is None:
        return None
    return "\n".join(v) if isinstance(v, list) else str(v)


def from_tmsl(doc: dict) -> Model:
    m = doc.get("model", doc)
    model = Model(origin="tmsl")
    for td in m.get("tables", []):
        t = Table(td["name"], hidden=bool(td.get("isHidden")))
        for c in td.get("columns", []):
            if c.get("type") == "rowNumber":
                continue
            t.columns[c["name"]] = Column(
                c["name"], source_column=c.get("sourceColumn"),
                expression=_text(c.get("expression")) if c.get("type") in ("calculated", "calculatedTableColumn") and c.get("expression") else None,
                data_type=c.get("dataType"), summarize_by=c.get("summarizeBy"),
                description=_text(c.get("description")))
        for mz in td.get("measures", []):
            t.measures[mz["name"]] = _text(mz.get("expression")) or ""
        for p in td.get("partitions", []):
            src = p.get("source") or {}
            if src.get("type") == "calculated":
                t.dax = _text(src.get("expression"))
            elif src.get("type") in ("m", None) and src.get("expression"):
                t.m, t.mode = _text(src.get("expression")), p.get("mode")
        model.tables[t.name] = t
    for r in m.get("relationships", []):
        model.relationships.append(Relationship(r["fromTable"], r["fromColumn"], r["toTable"],
                                                r["toColumn"], r.get("isActive", True) is not False))
    for e in m.get("expressions", []):
        if e.get("kind", "m") == "m":
            model.expressions[e["name"]] = _text(e.get("expression")) or ""
    return model


def read_json_part(raw: bytes) -> dict:
    """A JSON part of a Power BI zip: UTF-16 LE in .pbix/.pbit, UTF-8 elsewhere."""
    for enc in ("utf-16-le", "utf-8-sig", "utf-16"):
        try:
            text = raw.decode(enc)
            if text.lstrip("﻿").lstrip().startswith(("{", "[")):
                return json.loads(text.lstrip("﻿"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("not a JSON part")


# ---------------------------------------------------------------- .pbix

def from_pbix(path: Path) -> Model:
    """The model inside a .pbix: `DataModelSchema` when present (rare), else
    the compressed `DataModel` through pbixray."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "DataModelSchema" in names:
            return from_tmsl(read_json_part(z.read("DataModelSchema")))
        if "DataModel" not in names:
            raise ModelUnavailable("live connection: the .pbix has no embedded semantic model")
    try:
        from pbixray import PBIXRay
    except ImportError:
        raise ModelUnavailable(
            "the .pbix model is compressed (VertiPaq); install pbixray to read it, "
            "or save the report as PBIP (TMDL) and pass that folder") from None
    x = PBIXRay(str(path))
    model = Model(origin="pbix")
    try:
        _pbix_store(x, model)
    except Exception as e:  # noqa: BLE001 - a pbixray internal moved: fall back to its frames
        model = Model(origin="pbix")
        model.notes.append(f"metadata store unreadable ({type(e).__name__}); calculated tables and "
                           "their relationships may be missing")
        _pbix_frames(x, model)
    for _, r in x.dax_measures.iterrows():
        if r["TableName"] in model.tables:
            model.tables[r["TableName"]].measures[r["Name"]] = r["Expression"]
    for _, r in x.dax_tables.iterrows():
        if r["TableName"] in model.tables:
            model.tables[r["TableName"]].dax = r["Expression"]
    for _, r in x.power_query.iterrows():
        if r["TableName"] in model.tables:
            model.tables[r["TableName"]].m = r["Expression"]
    model.notes.append("model read from the .pbix DataModel with pbixray")
    return model


_AUTO = ("LocalDateTable_", "DateTableTemplate_")


def _pbix_store(x, model: Model) -> None:
    """Tables, columns and relationships from the model's own metadata store.
    pbixray's `tmschema_tables` leaves out calculated tables, and its
    `relationships` frame drops any to-column without an ExplicitName (every
    column of a calculated table), so the store is read directly."""
    conn = x._metadata.source._db.conn           # noqa: SLF001 - no public accessor
    ids = {}
    for tid, name, hidden in conn.execute("SELECT ID, Name, IsHidden FROM [Table]"):
        if name and not name.startswith(_AUTO):
            ids[tid] = name
            model.tables[name] = Table(name, hidden=bool(hidden))
    for tid, name, src, expr, typ, summ, desc in conn.execute(
            "SELECT TableID, COALESCE(ExplicitName, InferredName), SourceColumn, Expression, Type, "
            "SummarizeBy, Description FROM [Column]"):
        t = model.tables.get(ids.get(tid, ""))
        if t is None or not name or typ == 3:        # 3: the RowNumber column
            continue
        t.columns[name] = Column(name, source_column=src or None,
                                 expression=expr if typ == 2 and expr else None,
                                 summarize_by=_summarize(summ), description=desc or None)
    for ft, fc, tt, tc, active in conn.execute(
            "SELECT ft.Name, COALESCE(fc.ExplicitName, fc.InferredName), tt.Name, "
            "COALESCE(tc.ExplicitName, tc.InferredName), r.IsActive FROM Relationship r "
            "LEFT JOIN [Table] ft ON r.FromTableID = ft.ID LEFT JOIN [Column] fc ON r.FromColumnID = fc.ID "
            "LEFT JOIN [Table] tt ON r.ToTableID = tt.ID LEFT JOIN [Column] tc ON r.ToColumnID = tc.ID"):
        if None not in (ft, fc, tt, tc) and ft in model.tables and tt in model.tables:
            model.relationships.append(Relationship(ft, fc, tt, tc, bool(active)))


def _pbix_frames(x, model: Model) -> None:
    for _, r in x.tmschema_tables.iterrows():
        if not str(r["Name"]).startswith(_AUTO):
            model.tables[r["Name"]] = Table(r["Name"], hidden=bool(r.get("IsHidden")))
    for _, c in x.tmschema_columns.iterrows():
        t = model.tables.get(c["TableName"])
        if t is None or not isinstance(c["Name"], str) or c["Name"].startswith("RowNumber-"):
            continue
        expr = c["Expression"] if isinstance(c["Expression"], str) and c["Expression"].strip() else None
        t.columns[c["Name"]] = Column(c["Name"],
                                      source_column=c["SourceColumn"] if isinstance(c["SourceColumn"], str) else None,
                                      expression=expr, summarize_by=_summarize(c.get("SummarizeBy")))


def _summarize(v) -> str | None:
    # TMSchema SummarizeBy: 1 default, 2 none, 3 sum, 4 min, 5 max, 6 count, 7 average, 8 distinctCount
    return {2: "none", 3: "sum", 4: "min", 5: "max", 6: "count", 7: "average", 8: "distinctCount"}.get(
        int(v)) if isinstance(v, (int, float)) and v == v else None
