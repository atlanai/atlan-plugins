"""Power BI tiles as visual elements, from report files. No Power BI Desktop.

    mine_powerbi(path, dialect=None) -> MinedElements

`path` is a file or a folder holding any of:

    .pbip      a Power BI project: `<name>.Report/` (PBIR `definition/` or a
               legacy `report.json`) and `<name>.SemanticModel/` (TMDL
               `definition/` or `model.bim`), paired through definition.pbir
    .pbix      a report with its model: the report from `Report/definition/`
               (PBIR) or `Report/Layout`; the model from `DataModelSchema`, or
               the compressed `DataModel` through pbixray (optional)
    .pbit      a template: `DataModelSchema` and `Report/Layout`
    folders    a bare `*.Report` / `*.SemanticModel` pair, or a TMDL
               `definition/` beside a PBIR `definition/`

Each data visual on a visible page becomes a `Candidate` whose `sql` is the
tile SQL (see `tile`), in the dialect of the fact table's Power Query
connector (`dialect` overrides). Visuals that cannot be translated without
guessing are returned in `refused` with a reason; layout visuals (slicers,
text, images, buttons) are not tiles and are only counted.

The work is split by what can go wrong: `model`/`tmdl` read the semantic
model, `mquery` turns a partition's M into a relation, `dax` translates
measures and calculated columns, `report` reads pages and visuals, `tile`
assembles and checks the SQL.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import sqlglot

from eval_builder.elements import ElementKind, MinedElements, VisualElement, _slug
from eval_builder.select import Candidate
from eval_builder.sources.powerbi import model as M
from eval_builder.sources.powerbi.report import Report, load_legacy, load_pbir
from eval_builder.sources.powerbi.tile import NON_DATA, Builder

EXTRACTOR = "eval_builder.sources.powerbi/1"
SUFFIXES = (".pbix", ".pbit", ".pbip")
CATEGORIES = ("DAX", "display measure", "measures span tables", "decorative tile", "relationships",
              "unsupported filter", "unsupported field", "unresolved field", "calculated column",
              "calculated table", "Power Query", "non-warehouse source", "semantic model unavailable",
              "custom visual", "AI visual", "invalid tile SQL", "table", "other")


def refusal_category(reason: str) -> str:
    return next((c for c in CATEGORIES if (reason or "").startswith(c)), "other")


@dataclass
class Project:
    name: str
    path: Path
    load_report: Callable[[], Report]
    load_model: Callable[[], M.Model]


# ---------------------------------------------------------------- discovery

def discover(root: Path) -> tuple[list[Project], list[str]]:
    root = Path(root).expanduser()
    if root.is_file():
        files, skipped = [root], []
    elif root.is_dir():
        files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES)
        skipped = []
    else:
        raise FileNotFoundError(f"no such file or folder: {root}")
    projects: list[Project] = []
    claimed: set[Path] = set()
    for f in files:
        s = f.suffix.lower()
        if s == ".pbix":
            projects.append(_pbix(f))
        elif s == ".pbit":
            projects.append(_pbit(f))
        elif s == ".pbip":
            p = _pbip(f, skipped)
            if p:
                projects.append(p)
                claimed |= {f.parent / f"{f.stem}.Report", f.parent / f"{f.stem}.SemanticModel"}
        else:
            skipped.append(f"{f.name}: not a Power BI file")
    if root.is_dir():                           # *.Report folders without a .pbip beside them
        for rep in sorted(root.rglob("*.Report")):
            if rep.is_dir() and rep not in claimed:
                p = _report_folder(rep, skipped)
                if p:
                    projects.append(p)
    return projects, skipped


def _zip_reader(path: Path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    return (lambda p: z.read(p) if p in names else None), (lambda pre: [n for n in names if n.startswith(pre)]), names


def _pbix(path: Path) -> Project:
    read, ls, names = _zip_reader(path)

    def report():
        if "Report/definition/report.json" in names:
            return load_pbir(read, ls, "Report/definition")
        if "Report/Layout" in names:
            return load_legacy(M.read_json_part(read("Report/Layout")))
        raise M.ModelUnavailable("the .pbix has no report layout")
    def model():
        try:
            return M.from_pbix(path)
        except M.ModelUnavailable as e:
            # A thin report on a published model: use the model exported beside
            # it (`model.bim` from Tabular Editor or the XMLA endpoint), when
            # there is exactly one.
            bims = sorted(path.parent.glob("*.bim"))
            if not str(e).startswith("live connection") or len(bims) != 1:
                raise
            m = M.from_tmsl(M.read_json_part(bims[0].read_bytes()))
            m.notes.append(f"live report paired with the model file beside it: {bims[0].name}")
            return m
    return Project(path.stem, path, report, model)


def _pbit(path: Path) -> Project:
    read, ls, names = _zip_reader(path)

    def model():
        if "DataModelSchema" not in names:
            raise M.ModelUnavailable("the .pbit has no DataModelSchema")
        return M.from_tmsl(M.read_json_part(read("DataModelSchema")))

    def report():
        if "Report/definition/report.json" in names:
            return load_pbir(read, ls, "Report/definition")
        return load_legacy(M.read_json_part(read("Report/Layout")))
    return Project(path.stem, path, report, model)


def _folder_reader(base: Path):
    def read(p):
        f = base / p
        return f.read_bytes() if f.is_file() else None

    def ls(prefix):
        d = base / prefix
        start = d if d.is_dir() else d.parent
        return [str(x.relative_to(base)).replace("\\", "/") for x in start.rglob("*") if x.is_file()]
    return read, ls


def _report_from(rep_dir: Path) -> Callable[[], Report]:
    def load():
        read, ls = _folder_reader(rep_dir)
        if (rep_dir / "definition" / "report.json").is_file():
            return load_pbir(read, ls, "definition")
        if (rep_dir / "report.json").is_file():
            return load_legacy(M.read_json_part((rep_dir / "report.json").read_bytes()))
        raise M.ModelUnavailable(f"{rep_dir.name} has neither definition/ (PBIR) nor report.json")
    return load


def _model_from(sm_dir: Path | None, why: str = "") -> Callable[[], M.Model]:
    def load():
        if sm_dir is None or not sm_dir.is_dir():
            raise M.ModelUnavailable(why or "the semantic model folder was not provided")
        if (sm_dir / "definition").is_dir():
            return M.from_tmdl(sm_dir / "definition")
        if (sm_dir / "model.bim").is_file():
            return M.from_tmsl(M.read_json_part((sm_dir / "model.bim").read_bytes()))
        raise M.ModelUnavailable(f"{sm_dir.name} has neither definition/ (TMDL) nor model.bim")
    return load


def _semantic_model_of(rep_dir: Path) -> tuple[Path | None, str]:
    pbir = rep_dir / "definition.pbir"
    if pbir.is_file():
        doc = M.read_json_part(pbir.read_bytes())
        ref = (doc.get("datasetReference") or {})
        if ref.get("byPath", {}).get("path"):
            return (rep_dir / ref["byPath"]["path"]).resolve(), ""
        if ref.get("byConnection"):
            return None, "live connection: the report points at a published semantic model"
    guess = rep_dir.parent / rep_dir.name.replace(".Report", ".SemanticModel")
    return (guess if guess.is_dir() else None), ""


def _pbip(path: Path, skipped: list[str]) -> Project | None:
    rep = path.parent / f"{path.stem}.Report"
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for a in doc.get("artifacts", []):
            if (a.get("report") or {}).get("path"):
                rep = (path.parent / a["report"]["path"]).resolve()
    except (OSError, json.JSONDecodeError):
        pass
    if not rep.is_dir():
        skipped.append(f"{path.name}: report folder {rep.name} not found")
        return None
    sm, why = _semantic_model_of(rep)
    return Project(path.stem, path, _report_from(rep), _model_from(sm, why))


def _report_folder(rep: Path, skipped: list[str]) -> Project | None:
    sm, why = _semantic_model_of(rep)
    return Project(rep.name[:-len(".Report")], rep, _report_from(rep), _model_from(sm, why))


# ---------------------------------------------------------------- mining

def mine_powerbi(root: Path, *, dialect: str | None = None, include_hidden: bool = False) -> MinedElements:
    projects, skipped = discover(Path(root))
    candidates: list[Candidate] = []
    refused: list[VisualElement] = []
    layout = 0
    ids: set[str] = set()
    schema: dict[str, dict[str, dict]] = {}
    for proj in projects:
        wb_id = f"{_slug(proj.name)}-{hashlib.sha256(str(proj.path.name).encode()).hexdigest()[:6]}"
        try:
            report = proj.load_report()
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{proj.path.name}: report unreadable ({type(e).__name__}: {str(e)[:80]})")
            continue
        try:
            model = proj.load_model()
            model_error = None
        except Exception as e:  # noqa: BLE001 - every tile of this report is refused with the reason
            model, model_error = None, f"semantic model unavailable: {str(e)[:160]}"
        builder = Builder(model, report, proj.name, wb_id, dialect) if model else None
        for page in report.pages:
            if page.hidden and not include_hidden:
                continue
            for v in page.visuals:
                if v.type in NON_DATA or not v.roles:
                    layout += 1
                    continue
                if builder is None:
                    el = VisualElement(tool="powerbi", workbook=proj.name, page=page.name,
                                       element_id=f"{wb_id}:{page.id}:{v.id}", title=v.title or v.type,
                                       kind=ElementKind.UNKNOWN, raw_kind=v.type, workbook_id=wb_id,
                                       refusal=model_error)
                else:
                    try:
                        el = builder.build(page, v)
                    except Exception as e:  # noqa: BLE001 - an unexpected shape is refused, not fatal
                        el = VisualElement(tool="powerbi", workbook=proj.name, page=page.name,
                                           element_id=f"{wb_id}:{page.id}:{v.id}", title=v.title or v.type,
                                           kind=ElementKind.UNKNOWN, raw_kind=v.type, workbook_id=wb_id,
                                           refusal=f"other: {type(e).__name__}: {str(e)[:120]}")
                if el is None:
                    layout += 1
                    continue
                el.provenance.update({"files": [proj.path.name], "extractor": EXTRACTOR,
                                      "sqlglot": sqlglot.__version__, "dialect": el.dialect})
                if model is not None and model.notes:
                    el.provenance["model_notes"] = list(model.notes)
                if not el.usable:
                    if refusal_category(el.refusal or "") == "other" and not (el.refusal or "").startswith("other"):
                        el.refusal = f"other: {el.refusal}"
                    refused.append(el)
                    continue
                qid = f"powerbi-{wb_id}-{_slug(page.name)}-{_slug(el.title)}"
                if qid in ids:
                    qid += "-" + hashlib.sha256(el.element_id.encode()).hexdigest()[:6]
                ids.add(qid)
                candidates.append(Candidate(query_id=qid, sql=el.sql, original_sql=el.original_sql,
                                            page=page.name, element_type=el.kind.value, run_count=1,
                                            distinct_users=1, element=el))
                if builder is not None:
                    _add_schema(schema, builder, el)
    kinds = Counter(c.element.kind.value for c in candidates)
    reasons = Counter(refusal_category(e.refusal or "") for e in refused)
    note = (f"{len(candidates)} tile(s) from {len(projects)} report(s) "
            f"({', '.join(f'{k} {n}' for k, n in kinds.most_common()) or 'none'}); "
            f"{len(refused)} refused ({', '.join(f'{k} {n}' for k, n in reasons.most_common()) or 'none'}); "
            f"{layout} layout visual(s) (slicers, text, images) not tiles")
    if skipped:
        note += f"; {len(skipped)} skipped: {skipped[0]}"
    return MinedElements(candidates, refused, {t: list(c.values()) for t, c in schema.items()},
                         source="bi:powerbi", note=note)


def _add_schema(schema: dict, b: Builder, el: VisualElement) -> None:
    """Model columns of the fact, keyed by its physical table, so the drafter
    sees the column descriptions the model carries."""
    fact = el.provenance.get("fact_table")
    t = b.m.table(fact) if fact else None
    if t is None or len(el.source_relation) != 1:
        return
    cols = schema.setdefault(el.source_relation[0], {})
    for c in t.columns.values():
        if c.source_column:
            cols.setdefault(c.source_column.lower(), {"column": c.source_column, "type": c.data_type or "",
                                                      "comment": c.description or (c.name if c.name != c.source_column else None)})


__all__ = ["CATEGORIES", "discover", "mine_powerbi", "refusal_category"]
