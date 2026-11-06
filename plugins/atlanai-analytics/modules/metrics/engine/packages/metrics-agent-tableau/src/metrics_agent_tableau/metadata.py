# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A published data source rebuilt from the Tableau Metadata API, for when its download is
refused (HTTP 403: the token's user has no Download Data Source permission).

The Metadata API (GraphQL at `<server>/api/metadata/graphql`, the same session token) reads
what the catalogue knows of the data source: its fields, each calculated field's formula, the
columns each field reads, and the upstream tables (database, schema, table) or custom SQL. That
is converted into a .tds the core's Tableau reader reads like a downloaded one: the same
`repository-location` id and name, so the workbook's reference finds it; the warehouse
connection class, database and schema; the table as the relation; one column per field.

REDUCED FIDELITY, said in the manifest (`LOST`). The Metadata API does not give the join logic
between several upstream tables (they are written as a join with no clause, and the reader
reads the first as the primary table), the relationships of the logical layer, data-source
filters, or folders and hierarchies.

When the Metadata API is not available to this token (turned off on Tableau Server, not
licensed on Tableau Cloud, or this data source not in its index), `rebuild_tds` raises, and
the fetch refuses by name.

UNSURE, and to confirm on a live site: the field names of the query below follow the public
Metadata API schema (`PublishedDatasource`, `ColumnField`, `CalculatedField`,
`DatabaseTable`, `CustomSQLTable`); a server whose schema differs answers with `errors`, which
is reported as the Metadata API being unavailable, never guessed around.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from metrics_agent_tableau import rest as R

LOST = ("join logic between upstream tables", "logical-layer relationships",
        "data source filters", "folders and hierarchies")

QUERY = """
query metricsAgentDatasource($luid: String!) {
  publishedDatasources(filter: {luid: $luid}) {
    luid
    name
    fields {
      __typename
      name
      isHidden
      ... on ColumnField {
        dataType
        role
        upstreamColumns { name table { __typename name ... on CustomSQLTable { query } } }
      }
      ... on CalculatedField { dataType role formula }
    }
    upstreamTables {
      name
      ... on DatabaseTable { fullName schema database { name connectionType } }
    }
  }
}
""".strip()

DATATYPES = {"INTEGER": "integer", "REAL": "real", "STRING": "string", "DATETIME": "datetime",
             "DATE": "date", "BOOLEAN": "boolean"}
ROLES = {"MEASURE": "measure", "DIMENSION": "dimension"}


def fetch(session: R.Session, luid: str) -> dict[str, Any]:
    """The data source as the Metadata API knows it, or `TableauError` saying why not."""
    try:
        doc = session.graphql(QUERY, {"luid": R.segment(luid, "data source")})
    except R.TableauError as exc:
        raise R.TableauError(f"the Metadata API is not available to this token ({exc})",
                             status=exc.status) from None
    if doc.get("errors"):
        first = doc["errors"][0] if isinstance(doc["errors"], list) and doc["errors"] else {}
        said = session.redact(str((first or {}).get("message") or ""))[:160]
        raise R.TableauError(f"the Metadata API refused the query ({said or 'an error'})")
    found = ((doc.get("data") or {}).get("publishedDatasources")) or []
    if len(found) != 1:
        raise R.TableauError("the Metadata API does not know this data source (not indexed "
                             "yet, or not visible to this token)")
    return found[0]


def _bracket(*parts: str) -> str:
    return ".".join(f"[{p}]" for p in parts if p)


def _custom_sql(meta: dict) -> str | None:
    for f in meta.get("fields") or []:
        for col in f.get("upstreamColumns") or []:
            table = col.get("table") or {}
            if table.get("__typename") == "CustomSQLTable" and (table.get("query") or "").strip():
                return table["query"].strip()
    return None


def to_tds(meta: dict, rec: dict, site: str) -> bytes:
    """The .tds the reader reads. `rec` is the REST record of the data source (its name and
    content URL, the identity the workbook's reference matches on)."""
    name = str(rec.get("name") or meta.get("name") or "")
    content_url = str(rec.get("contentUrl") or "")
    tables = [t for t in meta.get("upstreamTables") or [] if t.get("name")]
    custom = _custom_sql(meta)
    if not tables and not custom:
        if meta.get("upstreamTables"):
            # Tables are there, with every name blanked: Tableau hides Catalog assets a user
            # has no permission on (seen live for an Explorer), so nothing names the table.
            raise R.TableauError(
                "the Metadata API hides its upstream tables from this user (their names come "
                "back empty)", fix="ask the site admin to grant this user View on the database "
                "or tables in Tableau Catalog (External Assets), or the Download Data Source "
                "permission on the data source")
        raise R.TableauError("the Metadata API lists no upstream table or custom SQL for it")

    root = ET.Element("datasource", {"formatted-name": name, "caption": name,
                                     "inline": "true", "version": "18.1"})
    root.append(ET.Comment(" rebuilt by metrics-agent tableau-cloud from the Tableau Metadata "
                           "API: no join logic, relationships or data source filters "))
    ET.SubElement(root, "repository-location", {
        "id": content_url, "path": f"/t/{site}/datasources" if site else "/datasources",
        "revision": "1.0", **({"site": site} if site else {})})
    first = tables[0] if tables else {}
    db = (first.get("database") or {})
    conn = ET.SubElement(root, "connection", {
        "class": str(db.get("connectionType") or "unknown").lower(),
        "dbname": str(db.get("name") or ""), "schema": str(first.get("schema") or "")})
    if tables:
        def relation(parent: ET.Element, t: dict) -> None:
            tdb = (t.get("database") or {}).get("name") or ""
            ET.SubElement(parent, "relation", {
                "name": t["name"], "type": "table",
                "table": _bracket(tdb, t.get("schema") or "", t["name"])})
        if len(tables) == 1:
            relation(conn, tables[0])
        else:
            join = ET.SubElement(conn, "relation", {"join": "inner", "type": "join"})
            for t in tables:
                relation(join, t)
    else:
        # Tableau doubles < and > inside custom SQL; the reader undoes it.
        rel = ET.SubElement(conn, "relation", {"name": "Custom SQL Query", "type": "text"})
        rel.text = custom.replace("<", "<<").replace(">", ">>")

    # Internal names: a column field by the column it reads, a calc by a generated id.
    # Formulas name fields by their caption; they are rewritten to the internal names, as
    # Tableau's own .tds writes them.
    fields = [f for f in meta.get("fields") or [] if f.get("name")]
    internal: dict[str, str] = {}
    n = 0
    for f in fields:
        if f.get("__typename") == "CalculatedField":
            n += 1
            internal[f["name"]] = f"[Calculation_{n}]"
        else:
            cols = [c.get("name") for c in f.get("upstreamColumns") or [] if c.get("name")]
            internal[f["name"]] = f"[{cols[0] if len(cols) == 1 else f['name']}]"

    def rewrite(formula: str) -> str:
        return re.sub(r"(?<!\]\.)\[([^\[\]]+)\]",
                      lambda m: internal.get(m.group(1), m.group(0)), formula)

    for f in fields:
        attrs = {"name": internal[f["name"]], "caption": f["name"],
                 "datatype": DATATYPES.get(str(f.get("dataType") or "").upper(), "string")}
        role = ROLES.get(str(f.get("role") or "").upper())
        if role:
            attrs["role"] = role
            attrs["type"] = "quantitative" if role == "measure" else "nominal"
        if f.get("isHidden"):
            attrs["hidden"] = "true"
        col = ET.SubElement(root, "column", attrs)
        if f.get("__typename") == "CalculatedField" and f.get("formula"):
            ET.SubElement(col, "calculation", {"class": "tableau",
                                               "formula": rewrite(str(f["formula"]))})
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def rebuild_tds(session: R.Session, rec: dict) -> bytes:
    """Fetch and convert: the .tds bytes for a data source whose download was refused."""
    meta = fetch(session, str(rec.get("id") or ""))
    return to_tds(meta, rec, session.site_content_url)
