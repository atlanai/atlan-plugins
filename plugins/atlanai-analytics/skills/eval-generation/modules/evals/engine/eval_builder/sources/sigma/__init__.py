"""Sigma elements as visual elements, from Atlan.

    mine_sigma(workbook, work, export_dir=None, base_url=None, api_key=None)
        -> MinedElements

`workbook` is an Atlan guid or qualifiedName of a SigmaWorkbook. A live run
reads it from Atlan (`atlan.fetch`, credentials from the arguments or
ATLAN_BASE_URL / ATLAN_API_KEY) and saves workbook, pages, elements, fields,
lineage and columns as JSON under `<work>/sigma/`; with `export_dir` the same
files are read back and nothing touches the network.

Each element is classified (`shapes`), its labels bound to measures and
dimensions and its tile SQL derived (`tile`). A usable element becomes a
`Candidate` whose `sql` is the tile SQL and whose `element` is the
`VisualElement`; the others are returned in `refused`, each with its reason.
The column metadata is fetched for the tables the tile SQLs read only, after
they are known; a table element with several measures then keeps the most
used one and its SQL is derived again.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sqlglot

from eval_builder import dialect as D
from eval_builder.prune import dialect_scope
from eval_builder.elements import MinedElements
from eval_builder.sources.sigma import atlan, tile

ADAPTER = "sigma-tile/1"


def mine_sigma(workbook: str, work: Path, *, export_dir: Path | None = None,
               base_url: str | None = None, api_key: str | None = None) -> MinedElements:
    from eval_builder.select import Candidate

    client = None
    if export_dir is not None:
        folder = Path(export_dir)
        data = atlan.load(folder)
        wb = data["workbook"]
        if workbook not in (wb.get("guid"), wb.get("qualifiedName")):
            raise ValueError(f"{folder} holds workbook {wb.get('qualifiedName')}, not {workbook}")
    else:
        client = atlan.Atlan(base_url or os.environ["ATLAN_BASE_URL"],
                             api_key or os.environ["ATLAN_API_KEY"])
        data = atlan.fetch(client, workbook)
        folder = Path(work) / "sigma"
        atlan.save(data, folder)
    wb = data["workbook"]
    provenance = {"tenant": wb.get("tenant"), "workbook_qualified_name": wb.get("qualifiedName"),
                  "adapter": ADAPTER, "sqlglot": sqlglot.__version__, "export": str(folder)}

    fields: dict[str, list[dict]] = defaultdict(list)
    for f in data["fields"]:
        fields[f.get("sigmaDataElementQualifiedName")].append(f)
    inputs: dict[str, set[str]] = defaultdict(set)
    for p in data["lineage"]:
        for out in p.get("outputs") or []:
            inputs[out].update(i for i in p.get("inputs") or [] if i)

    known = {D.from_qualified_name(c.get("qualifiedName")) for c in data.get("columns") or []} - {None}
    workbook_dialect = known.pop() if len(known) == 1 else D.run()
    tiles = []
    for rec in sorted(data["elements"], key=lambda e: (e.get("sigmaPageName") or "",
                                                       (e.get("name") or "").strip(),
                                                       e.get("qualifiedName") or "")):
        # The element's own crawl time: a workbook entity can lag its elements by months.
        synced = rec.get("lastSyncRunAt") or wb.get("lastSyncRunAt")
        when = datetime.fromtimestamp(synced / 1000, timezone.utc).isoformat() if synced else None
        own = fields.get(rec.get("qualifiedName"), [])
        dialect = _element_dialect(own, inputs) or workbook_dialect
        with dialect_scope(dialect):
            t = tile.analyse(rec, wb, own, inputs, {**provenance, "last_sync_run_at": when})
            t.element.dialect = dialect
            t.element.provenance["dialect_from"] = "atlan connector" if _element_dialect(own, inputs) \
                else ("workbook columns" if workbook_dialect != D.run() else "--dialect / fallback")
            tile.render(t)
        tiles.append(t)

    tables = sorted({tb for t in tiles if t.element.usable for tb in t.element.source_relation})
    if client is not None:
        data["columns"] = atlan.fetch_columns(client, tables)
        atlan.save({"columns": data["columns"]}, folder)
    usage = _usage(data["columns"])
    for t in tiles:
        with dialect_scope(t.element.dialect):
            if tile.choose_measure(t, usage):
                tile.render(t)

    used = {tb for t in tiles if t.element.usable for tb in t.element.source_relation}
    candidates = [Candidate(query_id=f"sigma-{t.element.element_id}", sql=t.element.sql,
                            original_sql=t.element.original_sql, page=t.element.page,
                            element_type=t.element.raw_kind, run_count=1, distinct_users=1,
                            element=t.element)
                  for t in tiles if t.element.usable]
    refused = [t.element for t in tiles if not t.element.usable]
    return MinedElements(candidates=candidates, refused=refused,
                         schema=_schema(data["columns"], used), source="bi:sigma",
                         note=_note(wb, candidates, refused))


def _element_dialect(fields: list[dict], inputs: dict[str, set[str]]) -> str | None:
    """The warehouse a tile reads, from the Atlan qualified names its fields'
    lineage reaches (`default/<connector>/…`). None when they name no
    connector or more than one."""
    seen, todo, found = set(), [f.get("qualifiedName") for f in fields if f.get("qualifiedName")], set()
    while todo:
        qn = todo.pop()
        if qn in seen:
            continue
        seen.add(qn)
        d = D.from_qualified_name(qn)
        if d:
            found.add(d)
        todo.extend(inputs.get(qn, ()))
    return found.pop() if len(found) == 1 else None


def _usage(columns: list[dict]) -> dict[str, tuple[float, float, float]]:
    """DB.SCHEMA.TABLE.COLUMN (upper case) -> (sourceReadUserCount,
    sourceReadCount, popularityScore), the highest over the connections that
    catalogue the column."""
    out: dict[str, tuple[float, float, float]] = {}
    for c in columns:
        key = f"{c.get('table')}.{c.get('name')}".upper()
        mine = tuple(float(c.get(k) or 0) for k in
                     ("sourceReadUserCount", "sourceReadCount", "popularityScore"))
        out[key] = tuple(max(a, b) for a, b in zip(out.get(key, (0, 0, 0)), mine))
    return out


def _schema(columns: list[dict], tables: set[str]) -> dict[str, list[dict]]:
    """{DB.SCHEMA.TABLE: [{column, type, comment}]} for the tables the tiles
    read; a table catalogued twice is merged by column name."""
    by_table: dict[str, dict[str, dict]] = defaultdict(dict)
    for c in sorted(columns, key=lambda c: c.get("qualifiedName") or ""):
        if c.get("table") not in tables or not c.get("name"):
            continue
        col = by_table[c["table"]].setdefault(c["name"], {"column": c["name"], "type": None,
                                                          "comment": None})
        col["type"] = col["type"] or c.get("dataType")
        col["comment"] = col["comment"] or c.get("description") or c.get("userDescription")
    return {t: sorted(cols.values(), key=lambda c: c["column"])
            for t, cols in sorted(by_table.items())}


def _note(wb: dict, candidates: list, refused: list) -> str:
    kinds = Counter(c.element.kind.value for c in candidates)
    reasons = Counter(el.refusal for el in refused)
    return (f'Sigma workbook "{wb.get("name")}": {len(candidates) + len(refused)} elements -> '
            f"{len(candidates)} tiles (" + ", ".join(f"{k} {n}" for k, n in kinds.most_common())
            + f"); {len(refused)} refused ("
            + "; ".join(f"{n} {r}" for r, n in reasons.most_common()) + ")")


__all__ = ["mine_sigma", "MinedElements"]
