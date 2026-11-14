"""Read-only Atlan REST for one Sigma workbook, saved so any run can be replayed.

Everything comes from `POST /api/meta/search/indexsearch`, the one endpoint an
ordinary API key can read Sigma assets with: entity GET answers 403 on these
tenants. Column lineage is read in bulk as ColumnProcess entities whose
qualifiedName starts with the workbook's (one indexsearch page holds a
thousand; walking `lineage/list` field by field would take ten thousand calls).

A live run saves what it read under `<work>/sigma/`:

    workbook.json   the workbook, plus the tenant host it came from
    pages.json      its pages
    elements.json   its data elements, each with its compiled SQL
    fields.json     every element's fields (name, Sigma column id, formula)
    lineage.json    ColumnProcess: warehouse column inputs -> field outputs
    columns.json    the warehouse columns of every table a tile reads, from
                    every connection that catalogues the table

and `load` reads the same files back, so tests and offline reruns go through
the same code as a live run. Only qualified names, names, SQL, types,
descriptions and usage counts are kept: nothing a viewer typed.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

INDEXSEARCH = "/api/meta/search/indexsearch"
# Cloudflare in front of the tenants rejects urllib's default agent (error 1010).
USER_AGENT = "atlan-eval-agents"
FILES = ("workbook", "pages", "elements", "fields", "lineage", "columns")

WORKBOOK = ["name", "qualifiedName", "sourceURL", "lastSyncRunAt", "sigmaPageCount",
            "connectionQualifiedName"]
PAGE = ["name", "qualifiedName", "sigmaDataElementCount"]
ELEMENT = ["name", "qualifiedName", "sigmaDataElementType", "sigmaDataElementQuery",
           "sigmaDataElementFieldCount", "sigmaPageName", "sigmaPageQualifiedName", "sourceURL",
           "lastSyncRunAt"]
FIELD = ["name", "qualifiedName", "sigmaDataElementQualifiedName", "sigmaDataElementFieldFormula"]
COLUMN = ["name", "qualifiedName", "dataType", "description", "userDescription",
          "sourceReadCount", "sourceReadUserCount", "popularityScore"]


class AtlanError(RuntimeError):
    """Atlan answered with an HTTP error: its answer, not a transport failure."""


class Atlan:
    def __init__(self, base_url: str, api_key: str, tries: int = 6):
        self.base = base_url.rstrip("/")
        self.host = urllib.parse.urlparse(self.base).hostname
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                         "Accept": "application/json", "User-Agent": USER_AGENT}
        self.tries = tries

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        for attempt in range(self.tries):
            req = urllib.request.Request(self.base + path, data=data, method="POST",
                                         headers=self._headers)
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                # A 4xx is the tenant's answer; a 5xx from the proxy in front
                # of it is worth another try.
                if e.code in (502, 503, 504) and attempt < self.tries - 1:
                    time.sleep(1 + attempt)
                    continue
                raise AtlanError(f"HTTP {e.code} on POST {path}") from None
            except (http.client.IncompleteRead, urllib.error.URLError, ConnectionError,
                    TimeoutError, json.JSONDecodeError):
                # A proxy in the path cuts some bodies short; a retry gets them whole.
                if attempt == self.tries - 1:
                    raise
                time.sleep(1 + attempt)
        raise AssertionError("unreachable")

    def search(self, filters: list[dict], attributes: list[str],
               relations: list[str] = (), page: int = 500) -> list[dict]:
        """Every ACTIVE entity matching `filters`, paged by from/size."""
        out: list[dict] = []
        while True:
            body = {"dsl": {"from": len(out), "size": page, "sort": [{"__guid": {"order": "asc"}}],
                            "query": {"bool": {"filter": [*filters, {"term": {"__state": "ACTIVE"}}]}}},
                    "attributes": list(attributes), "relationAttributes": list(relations),
                    "suppressLogs": True}
            res = self._post(INDEXSEARCH, body)
            entities = res.get("entities") or []
            out += entities
            if len(entities) < page:
                # A short page ends the read; a count beyond it means pages were lost.
                if (res.get("approximateCount") or 0) > len(out):
                    raise AtlanError(f"read {len(out)} of {res['approximateCount']} entities")
                return out


def _type(name: str) -> dict:
    return {"term": {"__typeName.keyword": name}}


def _ci(field: str, value: str) -> dict:
    return {"term": {field: {"value": value, "case_insensitive": True}}}


def _flat(entity: dict, attributes: list[str]) -> dict:
    a = entity.get("attributes") or {}
    return {"guid": entity.get("guid"), **{k: a.get(k) for k in attributes}}


def _ref(r: dict) -> str | None:
    return (r.get("uniqueAttributes") or {}).get("qualifiedName") \
        or (r.get("attributes") or {}).get("qualifiedName")


def fetch(client: Atlan, workbook: str) -> dict:
    """The workbook (an Atlan guid or qualifiedName), its pages, elements,
    fields and column lineage."""
    key = {"term": {"qualifiedName": workbook}} if "/" in workbook else {"term": {"__guid": workbook}}
    found = client.search([_type("SigmaWorkbook"), key], WORKBOOK, page=5)
    if len(found) != 1:
        raise LookupError(f"{len(found)} ACTIVE SigmaWorkbook found for {workbook}")
    wb = {**_flat(found[0], WORKBOOK), "tenant": client.host}
    inside = {"term": {"sigmaWorkbookQualifiedName": wb["qualifiedName"]}}
    processes = client.search(
        [_type("ColumnProcess"), {"prefix": {"qualifiedName": wb["qualifiedName"] + "/"}}],
        ["qualifiedName", "inputs", "outputs"], ["qualifiedName"], page=1000)
    return {
        "workbook": wb,
        "pages": [_flat(e, PAGE) for e in client.search([_type("SigmaPage"), inside], PAGE)],
        "elements": [_flat(e, ELEMENT) for e in
                     client.search([_type("SigmaDataElement"), inside], ELEMENT, page=50)],
        "fields": [_flat(e, FIELD) for e in
                   client.search([_type("SigmaDataElementField"), inside], FIELD, page=1000)],
        "lineage": [{
            "qualifiedName": (p.get("attributes") or {}).get("qualifiedName"),
            "inputs": [_ref(r) for r in (p.get("attributes") or {}).get("inputs") or []
                       if r.get("typeName") == "Column"],
            "outputs": [_ref(r) for r in (p.get("attributes") or {}).get("outputs") or []
                        if r.get("typeName") == "SigmaDataElementField"],
        } for p in processes],
    }


def fetch_columns(client: Atlan, tables: list[str]) -> list[dict]:
    """Columns of each DB.SCHEMA.TABLE, from every connection that has it."""
    out = []
    for ref in tables:
        *qual, name = ref.split(".")
        filters = [_type("Column"), {"bool": {"should": [_ci("tableName.keyword", name),
                                                         _ci("viewName.keyword", name)],
                                              "minimum_should_match": 1}}]
        if qual:
            filters.append(_ci("schemaName.keyword", qual[-1]))
        if len(qual) > 1:
            filters.append(_ci("databaseName.keyword", qual[-2]))
        out += [{**_flat(e, COLUMN), "table": ref}
                for e in client.search(filters, COLUMN, page=1000)]
    return out


def save(data: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        if name in data:
            (folder / f"{name}.json").write_text(json.dumps(data[name], indent=1, ensure_ascii=False))


def load(folder: Path) -> dict:
    """A saved export; columns.json is optional (a run before the column fetch)."""
    data = {name: json.loads((folder / f"{name}.json").read_text())
            for name in FILES if (folder / f"{name}.json").exists()}
    missing = [n for n in FILES[:5] if n not in data]
    if missing:
        raise FileNotFoundError(f"{folder}: no {', '.join(m + '.json' for m in missing)}")
    data.setdefault("columns", [])
    return data
