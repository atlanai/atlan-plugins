"""Check golden SQL against the schema Atlan holds, when no warehouse can run it.

Executing the answer key is the strongest check there is, and `review` still
does it whenever Snowflake is reachable. A BI or file build often has no
warehouse at all, and a Redshift or Databricks source never has one here. Left
there, a record whose SQL names a column the table no longer has reaches the
reviewer looking perfectly well formed.

So the fallback asks the catalog: does every table the SQL reads exist in Atlan,
and does every column it uses exist on that table? The comparison is sqlglot's
column qualification against the catalog's columns — deterministic, no model.

Two routes to the same catalog, because the CLI is a subprocess and cannot call
an MCP tool itself:

    REST   ATLAN_BASE_URL + ATLAN_API_KEY set -> the CLI searches the tenant
    file   otherwise the agent fetches the columns with its Atlan MCP and saves
           them to <work>/atlan_schema.json, in the shape `load_file` reads

Three outcomes, and only one of them drops a record:

    ok          every table found, every column on it
    missing     a column the table does not have — the answer key is broken
    unverified  could not tell: a table Atlan does not hold, a lookup that
                failed, an incomplete file. Kept, and marked so a reviewer sees it.

Atlan is only as fresh as its last crawl, so `ok` means "in the catalog", not
"in the warehouse". That is weaker than execution and is reported as such.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SCHEMA_FILE = "atlan_schema.json"
REQUEST_FILE = "atlan_schema_request.json"

OK, MISSING, UNVERIFIED = "ok", "missing", "unverified"

_PAGE = 1000


@dataclass
class Outcome:
    status: str
    detail: str


@dataclass
class Match:
    """One catalog object a table reference resolved to."""
    qualified_name: str
    columns: set[str]
    complete: bool = True


# A reference maps to its matches, or to None when the lookup itself failed or
# the reference was never fetched — "could not tell", not "does not exist".
Catalog = dict[str, list[Match] | None]


def _key(ref: str) -> str:
    return ref.lower()


def rest_configured() -> bool:
    return bool(os.environ.get("ATLAN_BASE_URL") and os.environ.get("ATLAN_API_KEY"))


def route(work: Path) -> str | None:
    """Which catalog this run can check against: "REST", "MCP file", or None."""
    if rest_configured():
        return "REST"
    if (work / SCHEMA_FILE).exists():
        return "MCP file"
    return None


# --------------------------------------------------------------------------
# Tables the SQL reads
# --------------------------------------------------------------------------

def table_refs(sql: str, dialect: str | None = None) -> list[str]:
    from eval_builder.select import _parse, _tables

    tree = _parse(sql, dialect)
    return _tables(tree) if tree is not None else []


# --------------------------------------------------------------------------
# REST
# --------------------------------------------------------------------------

def _ci(field: str, value: str) -> dict:
    return {"term": {field: {"value": value, "case_insensitive": True}}}


def _dsl(ref: str, offset: int) -> dict:
    """Active columns of every table or view the reference could name.

    Matched on the parts the SQL wrote, case-insensitively: `S.T` matches T in
    schema S in any database and any connection. More than one parent comes
    back when the same name is catalogued twice, and `check` handles that.
    """
    *qual, name = ref.split(".")
    filters = [
        {"term": {"__typeName.keyword": "Column"}},
        {"term": {"__state": "ACTIVE"}},
        {"bool": {"should": [_ci("tableName.keyword", name), _ci("viewName.keyword", name)],
                  "minimum_should_match": 1}},
    ]
    if len(qual) >= 1:
        filters.append(_ci("schemaName.keyword", qual[-1]))
    if len(qual) >= 2:
        filters.append(_ci("databaseName.keyword", qual[-2]))
    return {
        "dsl": {"from": offset, "size": _PAGE, "sort": [{"__guid": {"order": "asc"}}],
                "query": {"bool": {"filter": filters}}},
        "attributes": ["name", "tableQualifiedName", "viewQualifiedName"],
    }


def _search(body: dict) -> dict:
    base = os.environ["ATLAN_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        base + "/api/meta/search/indexsearch",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['ATLAN_API_KEY']}",
            "Content-Type": "application/json",
            # Cloudflare in front of the tenant rejects urllib's default agent
            # (error 1010) before the request reaches Atlan.
            "User-Agent": "atlan-eval-agents",
        },
        method="POST",
    )
    # A proxy in the path cuts some responses short (IncompleteRead, measured
    # at about 1 call in 6 through a sandbox proxy); a retry gets the whole
    # body. An HTTP status is the tenant's answer and is not retried.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError:
            raise
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise


def fetch_rest(refs: list[str]) -> tuple[Catalog, list[str]]:
    """(catalog, errors). A failed lookup leaves that reference unverified."""
    catalog: Catalog = {}
    errors: list[str] = []
    for ref in dict.fromkeys(refs):
        cols: dict[str, set[str]] = {}
        offset, total = 0, None
        try:
            while total is None or offset < total:
                res = _search(_dsl(ref, offset))
                total = res.get("approximateCount") or 0
                entities = res.get("entities") or []
                for e in entities:
                    a = e.get("attributes") or {}
                    parent = a.get("tableQualifiedName") or a.get("viewQualifiedName")
                    if parent and a.get("name"):
                        cols.setdefault(parent, set()).add(a["name"])
                if not entities:
                    break
                offset += len(entities)
        except urllib.error.HTTPError as e:
            catalog[_key(ref)] = None
            errors.append(f"{ref}: Atlan returned HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001 - one failed lookup is not a crash
            catalog[_key(ref)] = None
            errors.append(f"{ref}: {type(e).__name__}")
            continue
        catalog[_key(ref)] = [Match(qn, c) for qn, c in sorted(cols.items())]
    return catalog, errors


# --------------------------------------------------------------------------
# The file the agent writes from its Atlan MCP
# --------------------------------------------------------------------------

def write_request(work: Path, refs: list[str]) -> Path:
    """Name the tables the agent must look up, and the shape to save them in."""
    path = work / REQUEST_FILE
    path.write_text(json.dumps({
        "save_to": str(work / SCHEMA_FILE),
        "tables": list(dict.fromkeys(refs)),
        "how": [
            "for each table: search_assets_tool with asset_type "
            "[Table, View, MaterialisedView] and conditions {name, schemaName, "
            "databaseName} — only the parts the reference has",
            "for EVERY result (not only the first): get_assets_tool with "
            "relationships [columns]",
            "save each result as {qualified_name, column_total: columns.total, "
            "columns: columns.items} under the reference exactly as listed here; "
            "a table with no results gets []",
        ],
        "shape": {"tables": {"SCHEMA.TABLE": [
            {"qualified_name": "default/snowflake/123/DB/SCHEMA/TABLE",
             "column_total": 2, "columns": [{"name": "COL_A"}, {"name": "COL_B"}]}]}},
    }, indent=1))
    return path


def load_file(path: Path) -> Catalog:
    """Read the agent's file. A copy whose columns fall short of its total was
    cut off by paging; it is kept but marked incomplete, so nothing is dropped
    on the strength of a column list that is known to be partial."""
    raw = json.loads(path.read_text()).get("tables") or {}
    catalog: Catalog = {}
    for ref, matches in raw.items():
        if matches is None:
            catalog[_key(ref)] = None
            continue
        out = []
        for m in matches:
            names = {c["name"] if isinstance(c, dict) else c
                     for c in m.get("columns") or [] if c}
            total = m.get("column_total")
            out.append(Match(m.get("qualified_name") or "?", names,
                             complete=total is None or len(names) >= int(total)))
        catalog[_key(ref)] = out
    return catalog


def load(work: Path, refs: list[str]) -> tuple[Catalog | None, str, list[str]]:
    """(catalog, route, errors) for whichever route this run has."""
    r = route(work)
    if r == "REST":
        catalog, errors = fetch_rest(refs)
        return catalog, r, errors
    if r == "MCP file":
        try:
            return load_file(work / SCHEMA_FILE), r, []
        except Exception as e:  # noqa: BLE001 - a broken file verifies nothing
            return {}, r, [f"{SCHEMA_FILE} unreadable: {type(e).__name__}"]
    return None, "", []


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

_UNRESOLVED = re.compile(r"Column '([^']+)' could not be resolved|Unknown column: (\S+)")


def _qualify(sql: str, schema: dict, dialect: str | None = None) -> str | None:
    """None when every column resolves; otherwise the error text."""
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify

    try:
        from eval_builder import dialect as D

        dialect = dialect or D.run()
        tree = sqlglot.parse_one(sql, read=dialect)
        # Quoting keeps case under Snowflake rules, but the catalog's names are
        # compared case-insensitively here: a quoted "amount" from a
        # Redshift dashboard must still find AMOUNT.
        if dialect == "snowflake":
            # Quoting keeps case under Snowflake rules, but the catalog's names
            # are compared case-insensitively here. A name with a space or a
            # symbol is one identifier only while quoted, so it stays quoted.
            for ident in tree.find_all(exp.Identifier):
                if ident.name.replace("_", "").isalnum():
                    ident.set("quoted", False)
        qualify(tree, schema=schema, dialect=dialect,
                validate_qualify_columns=True, infer_schema=False)
        return None
    except Exception as e:  # noqa: BLE001 - classified by the caller
        return str(e).splitlines()[0] if str(e) else type(e).__name__


def _schema(columns_by_ref: dict[str, set[str]]) -> dict:
    """Keyed by the reference as the SQL wrote it, padded to db.schema.table so
    1-, 2- and 3-part names can share one schema."""
    schema: dict = {}
    for ref, cols in columns_by_ref.items():
        parts = ref.split(".")
        db, sch, name = (["__db", "__schema"][: 3 - len(parts)] + parts)[-3:]
        schema.setdefault(db, {}).setdefault(sch, {})[name] = {c: "TEXT" for c in cols}
    return schema


def check(sql: str, catalog: Catalog, dialect: str | None = None) -> Outcome:
    from eval_builder import dialect as D

    dialect = dialect or D.run()
    refs = table_refs(sql, dialect)
    if not refs:
        return Outcome(UNVERIFIED, "no table found in the SQL to check")

    # Every table has to resolve before any column is judged: with one table
    # unknown, an unqualified column that belongs to it reads as missing, and a
    # good record would be dropped.
    unresolved = []
    for ref in refs:
        matches = catalog.get(_key(ref))
        if matches is None:
            unresolved.append(f"{ref} not looked up")
        elif not matches:
            unresolved.append(f"{ref} not found in Atlan")
        elif not all(m.complete for m in matches):
            unresolved.append(f"{ref} column list incomplete")
    if unresolved:
        return Outcome(UNVERIFIED, "; ".join(unresolved))

    # The same name catalogued more than once (a dev and a prod connection)
    # is checked against every copy. Passing on the columns all copies share
    # means it passes on each; failing on the columns any copy has means it
    # fails on each. In between, the copies disagree and it cannot be told.
    matches = {ref: catalog[_key(ref)] for ref in refs}
    shared = {r: set.intersection(*(m.columns for m in ms)) for r, ms in matches.items()}
    any_copy = {r: set.union(*(m.columns for m in ms)) for r, ms in matches.items()}
    where = "; ".join(
        ms[0].qualified_name if len(ms) == 1 else f"{r} in {len(ms)} copies"
        for r, ms in matches.items())

    # The connector Atlan catalogues the tables under is the warehouse the SQL
    # runs on; a record in another dialect cannot be checked against it.
    connectors = {D.from_qualified_name(m.qualified_name) for ms in matches.values() for m in ms} - {None}
    if connectors and dialect not in connectors:
        return Outcome(UNVERIFIED, f"dialect mismatch: the SQL is {dialect}, Atlan catalogues "
                                   f"{where} under {', '.join(sorted(connectors))}")
    if _qualify(sql, _schema(shared), dialect) is None:
        return Outcome(OK, where)
    err = _qualify(sql, _schema(any_copy), dialect)
    if err is None:
        return Outcome(UNVERIFIED, f"columns differ between the copies of {where}")
    m = _UNRESOLVED.search(err)
    if m:
        col = m.group(1) or m.group(2)
        return Outcome(MISSING, f"column {col} not in Atlan's schema for {where}")
    return Outcome(UNVERIFIED, f"could not check the SQL: {err[:100]}")
