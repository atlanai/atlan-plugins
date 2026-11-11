"""Tableau tiles as visual elements, from .twb/.tds export files.

    mine_tableau(root, dialect=None) -> MinedElements

`root` holds workbooks (.twb/.twbx) and the published datasources they use
(.tds/.tdsx), paired on `repository-location/@id`. Each dashboard tile (and
each standalone worksheet) becomes a `Candidate` whose `sql` is the tile SQL
in the datasource's own dialect (overridable with `dialect`) and whose
`element` is the `VisualElement`. A FIXED level-of-detail expression becomes
a window column on a row stage of the tile SQL, in Tableau's order of
operations. Tiles that cannot be translated without guessing (INCLUDE and
EXCLUDE expressions, a FIXED value aggregated at another grain, table
calculations, blends, sets, a published datasource file that lacks a field the
tile uses, non-warehouse sources, decorative tiles) are returned in `refused`
with a reason.

The work is split by what can go wrong: `model` reads the files and pairs
datasources, `calc` translates Tableau's formula language, `tile` reads what a
sheet shows and how it is filtered, `sql` assembles and prunes the query. No
step guesses; each refuses with a reason a reviewer can read.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import sqlglot

from eval_builder.elements import ElementKind, MinedElements, VisualElement, _slug
from eval_builder.select import Candidate
from eval_builder.sources.tableau.model import DS, PublishedIndex, Workbook, load, manifest_names
from eval_builder.sources.tableau.tile import placements, resolve

EXTRACTOR = "eval_builder.sources.tableau/1"
# Every refusal reason starts with one of these, so a run can be summarised and
# a reviewer can filter by kind. `other` only marks a reason that slipped the list.
CATEGORIES = ("LOD FIXED", "LOD INCLUDE", "LOD EXCLUDE", "table calculation", "blend", "set", "bin",
              "published datasource lacks field", "published datasource was not provided",
              "decorative tile", "non-warehouse source", "relationships", "user function",
              "unsupported function", "unsupported expression", "unparseable formula",
              "unsupported filter", "unsupported relation", "unresolved field", "custom SQL",
              "invalid tile SQL", "no warehouse relation", "unexpected worksheet shape", "other")


def refusal_category(reason: str) -> str:
    """The category a refusal reason starts with. The published datasource's id
    sits inside its two reasons (`published datasource <id> lacks field <name>`)."""
    reason = re.sub(r"^published datasource \S+", "published datasource", reason or "")
    return next((c for c in CATEGORIES if reason.startswith(c)), "other")


def mine_tableau(root: Path, *, dialect: str | None = None) -> MinedElements:
    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"no such directory: {root}")
    docs, skipped = load(root)
    names = manifest_names(root)
    pidx = PublishedIndex([d for d in docs if d.kind == "datasource"])
    workbooks = [Workbook(d, names) for d in docs if d.kind == "workbook"]
    candidates: list[Candidate] = []
    refused: list[VisualElement] = []
    ids: set[str] = set()
    for wb in workbooks:
        places = placements(wb)
        on_many = {n for n, k in Counter(n for n, db, _ in places if db).items() if k > 1}
        for ws_name, dashboard, zone in places:
            try:
                el = resolve(wb, pidx, ws_name, dashboard, zone, dialect)
            except Exception as e:  # noqa: BLE001 - an unexpected shape is refused, not fatal
                el = VisualElement(tool="tableau", workbook=wb.name, page=dashboard,
                                   element_id=f"{wb.id}:{ws_name}", title=ws_name,
                                   kind=ElementKind.UNKNOWN, raw_kind="", workbook_id=wb.id,
                                   refusal=f"unexpected worksheet shape: {type(e).__name__}: {str(e)[:120]}")
            files = list(wb.doc.labels) + [x["published_file"] for x in el.provenance.get("datasources", [])
                                           if x.get("published_file")]
            el.provenance.update({"files": files, "extractor": EXTRACTOR, "sqlglot": sqlglot.__version__,
                                  "dialect": el.dialect,
                                  "repository_ids": sorted({x["published"] for x in el.provenance.get(
                                      "datasources", []) if x.get("published")})})
            if el.usable and not el.displayed_measures:
                # A sheet that shows labels only (a program name, a title
                # built from data) is a listing, not a question: the same rule
                # as Sigma's listing tables.
                el.refusal = "decorative tile (no measure)"
                el.sql = None
            if not el.usable:
                if refusal_category(el.refusal) == "other":
                    el.refusal = f"other: {el.refusal}"
                refused.append(el)
                continue
            qid = f"tableau-{wb.id}-{_slug(ws_name)}"
            if ws_name in on_many and dashboard:
                qid += f"-{_slug(dashboard)}"
            if qid in ids:                  # slugs collide when names differ only in punctuation
                qid += "-" + hashlib.sha256(f"{ws_name}\0{dashboard}".encode()).hexdigest()[:6]
            ids.add(qid)
            candidates.append(Candidate(query_id=qid, sql=el.sql, original_sql=el.original_sql,
                                        page=dashboard, element_type=el.kind.value, run_count=1,
                                        distinct_users=1, element=el))
    read = {t for c in candidates for t in c.element.source_relation}
    sources = [ds for copies in pidx.by_repo.values() for ds in copies] + [
        ds for wb in workbooks for ds in wb.datasources.values() if ds.kind not in ("sqlproxy", "parameters")]
    schema = {t: cols for t, cols in _schema(sources).items() if t in read}
    kinds = Counter(c.element.kind.value for c in candidates)
    reasons = Counter(refusal_category(e.refusal or "") for e in refused)
    note = (f"{len(candidates)} tile(s) from {len(workbooks)} workbook(s) "
            f"({', '.join(f'{k} {n}' for k, n in kinds.most_common()) or 'none'}); "
            f"{len(refused)} refused ({', '.join(f'{k} {n}' for k, n in reasons.most_common()) or 'none'})")
    if skipped:
        note += f"; {len(skipped)} file(s) skipped: {skipped[0]}"
    return MinedElements(candidates, refused, schema, source="bi:tableau", note=note)


def _schema(sources: list[DS]) -> dict[str, list[dict]]:
    """Warehouse table -> columns, typed from the datasource metadata. A custom
    SQL output types its base column only when it projects that column unchanged."""
    out: dict[str, dict[str, dict]] = {}
    for ds in sources:
        for local, m in ds.meta.items():
            rel = ds.relations.get(m["parent"])
            if rel is None or not m["remote"]:
                continue
            col = ds.columns.get(local)
            caption = col.get("caption") if col is not None else None
            if rel.kind == "table" and rel.table_name:
                found = [(f"{rel.table_name}.{m['remote']}", m["local_type"])]
            elif rel.kind == "text":
                _, base, plain = rel.lineage(m["remote"], ds.dialect())
                found = [(c, m["local_type"] if plain else "") for c in base]
            else:
                continue
            for qualified, typ in found:
                table, column = qualified.rsplit(".", 1)
                entry = out.setdefault(table, {}).setdefault(column.lower(), {
                    "column": column, "type": typ or "",
                    "comment": caption if caption and caption != column else None})
                entry["type"] = entry["type"] or typ or ""
    return {t: list(cols.values()) for t, cols in sorted(out.items())}


__all__ = ["CATEGORIES", "mine_tableau", "MinedElements", "refusal_category"]
