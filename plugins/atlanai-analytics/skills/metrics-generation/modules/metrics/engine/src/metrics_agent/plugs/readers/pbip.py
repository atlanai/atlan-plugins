# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A Power BI project (PBIP) as its author hands it over.

Power BI Desktop saves a project as `<Name>.pbip` beside `<Name>.Report/` and
`<Name>.SemanticModel/`; that is what sits in a git repository. A person points at any of the
four: the `.pbip` they open, the report folder, the model folder, or the folder holding them.
Each names the same thing, so each resolves here to one model and the reports over it:

* a `.pbip` lists its report (`artifacts[].report.path`);
* a report names its model by path (`definition.pbir`, `datasetReference.byPath`);
* a model is the reports beside it that name it.

A report that names its model by connection (a thin report over a published model) has no
model here; that stays the reader's `model_gap`. A folder holding several projects is not one
model: it is refused with the projects named, never merged and never guessed.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib


@dataclasses.dataclass(frozen=True)
class Project:
    model: pathlib.Path                 # the <Name>.SemanticModel folder
    kind: str                           # "tmdl" (definition/*.tmdl) or "bim" (model.bim)
    reports: tuple[pathlib.Path, ...]   # the <Name>.Report folders over it

    @property
    def model_file(self) -> pathlib.Path:
        """What the form's reader is handed: the folder (TMDL) or its model.bim (TMSL)."""
        return self.model if self.kind == "tmdl" else self.model / "model.bim"


class Ambiguous(ValueError):
    """A folder that holds several projects; the message names them."""


def _json(path: pathlib.Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def is_report(path: pathlib.Path) -> bool:
    return path.is_dir() and (path / "definition.pbir").is_file()


def is_model(path: pathlib.Path) -> bool:
    return path.is_dir() and ((path / "definition.pbism").is_file()
                              or (path.name.lower().endswith((".semanticmodel", ".dataset"))
                                  and _kind(path) is not None))


def _kind(model: pathlib.Path) -> str | None:
    if any((model / "definition").glob("**/*.tmdl")) if (model / "definition").is_dir() else False:
        return "tmdl"
    if (model / "model.bim").is_file():
        return "bim"
    return None


def model_of(report: pathlib.Path) -> pathlib.Path | None:
    """The model folder a report names by path, when it is on disk."""
    ref = (_json(report / "definition.pbir").get("datasetReference") or {})
    rel = (ref.get("byPath") or {}).get("path")
    if not rel:
        return None
    cand = (report / str(rel)).resolve()
    return cand if is_model(cand) else None


def _reports_over(model: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """The report folders beside `model` that name it."""
    try:
        siblings = sorted(p for p in model.parent.iterdir() if is_report(p))
    except OSError:
        return ()
    target = model.resolve()
    return tuple(r for r in siblings if (m := model_of(r)) is not None and m == target)


def _project(model: pathlib.Path, first: pathlib.Path | None = None) -> Project | None:
    kind = _kind(model)
    if kind is None:
        return None
    # A handover that names ONE report (its .pbip or its folder) is that report: the others
    # over the same model were not asked for. A model, or the folder holding the project, is
    # every report beside it that names it.
    reports = [first] if first is not None else list(_reports_over(model))
    return Project(model.resolve(), kind, tuple(reports))


def resolve(path: pathlib.Path) -> Project | None:
    """The project `path` names, or None when it is not a project handover (a fetched
    extract, a bare TMDL folder, a .pbix: each is read as before).

    Raises `Ambiguous` for a folder of several projects."""
    path = pathlib.Path(path)
    if path.is_file() and path.suffix.lower() == ".pbip":
        for art in _json(path).get("artifacts") or []:
            rel = ((art or {}).get("report") or {}).get("path")
            if rel and is_report(path.parent / rel):
                report = path.parent / rel
                model = model_of(report)
                return _project(model, report) if model else None
            rel = ((art or {}).get("semanticModel") or (art or {}).get("dataset") or {}).get("path")
            if rel and is_model(path.parent / rel):
                return _project(path.parent / rel)
        return None
    if not path.is_dir():
        return None
    if is_report(path):
        model = model_of(path)
        return _project(model, path) if model else None
    if is_model(path):
        return _project(path)
    if path.name == "definition" and is_model(path.parent):
        return _project(path.parent)       # the model's own TMDL folder
    # The folder that holds a project: its direct children only, so a fetched extract (whose
    # models sit under semanticModels/<workspace>/<id>/) is never taken for one.
    try:
        kids = sorted(path.iterdir())
    except OSError:
        return None
    models = [k for k in kids if is_model(k) and _kind(k)]
    reports = [k for k in kids if is_report(k)]
    if not models:
        if len(reports) == 1 and (m := model_of(reports[0])) is not None:
            return _project(m, reports[0])
        return None
    if len(models) == 1:
        return _project(models[0])
    if len(reports) == 1 and (m := model_of(reports[0])) is not None:
        return _project(m, reports[0])
    pbips = sorted(k.name for k in kids if k.is_file() and k.suffix.lower() == ".pbip")
    names = pbips or sorted(m.name for m in models)
    raise Ambiguous(
        f"{path.name} holds {len(models)} Power BI projects, and a run reads one model. "
        f"Set `bi` to one of: {', '.join(names[:12])}"
        + (f" (and {len(names) - 12} more)" if len(names) > 12 else "") + ".")


def thin_report(path: pathlib.Path) -> bool:
    """A handover whose report names its model by connection and brings no model: a `.pbip`
    or report folder over a published model."""
    path = pathlib.Path(path)
    report = None
    if path.is_file() and path.suffix.lower() == ".pbip":
        for art in _json(path).get("artifacts") or []:
            rel = ((art or {}).get("report") or {}).get("path")
            if rel and is_report(path.parent / rel):
                report = path.parent / rel
    elif is_report(path):
        report = path
    return report is not None and model_of(report) is None
