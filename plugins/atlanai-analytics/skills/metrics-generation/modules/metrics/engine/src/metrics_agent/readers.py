# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Finding the installed BI readers: the one way the pipeline reaches one.

A reader turns one BI tool's definition files into the common `bi_facts` format (the
contract is `sockets.BIReader`). A package registers each reader under the entry-point group
`metrics_agent.readers`, with the vendor name as the entry-point name:

    [project.entry-points."metrics_agent.readers"]
    tableau = "metrics_agent.plugs.readers.tableau:TableauReader"

The core's own readers are registered exactly like that, in the core's pyproject.toml, so
nothing here knows which readers exist: the `bi_vendor` choices in run.yml, the files the local-files
plug collects, A1's parse and the lineage S0 bounds a run by all ask this module. Adding a BI
tool is installing a package; no core file changes.

The same goes for compiling. A reader that compiles its formulas into SQL (`compile_unit`)
brings its own vocabulary table and regression corpus, and A3 and the A3b gate reach them here:
the core never knows which formula languages exist. The languages the readers declare are also
the evidence sources a claim may cite for a formula (`lib/evidence.py`).
"""
from __future__ import annotations

import pathlib
from importlib import metadata
from typing import Any

from metrics_agent import sockets as S

GROUP = "metrics_agent.readers"


class ReaderError(RuntimeError):
    """No reader by that name is installed, two packages register one name, or a registered
    reader does not meet the contract."""


def _entry_points() -> dict[str, Any]:
    found: dict[str, Any] = {}
    for ep in metadata.entry_points(group=GROUP):
        other = found.get(ep.name)
        if other is not None and other.value != ep.value:
            raise ReaderError(f"BI reader {ep.name!r} is registered twice ({other.value} and "
                              f"{ep.value}). Uninstall one of them.")
        found[ep.name] = ep
    return found


def names() -> tuple[str, ...]:
    """Every installed reader's name, sorted. Reads package metadata only."""
    return tuple(sorted(_entry_points()))


def load(name: str) -> S.BIReader:
    """The reader registered as `name`, checked against the contract."""
    eps = _entry_points()
    if name not in eps:
        raise ReaderError(f"no BI reader named {name!r} is installed (installed: "
                          f"{', '.join(sorted(eps)) or 'none'})")
    try:
        obj = eps[name].load()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - any import failure is the reader's
        raise ReaderError(f"BI reader {name!r} ({eps[name].value}) could not be loaded: "
                          f"{type(exc).__name__}: {exc}") from exc
    reader = obj() if isinstance(obj, type) else obj
    if getattr(reader, "name", None) != name:
        raise ReaderError(f"BI reader {eps[name].value} calls itself "
                          f"{getattr(reader, 'name', None)!r}; the entry-point name and the "
                          "reader's `name` must match")
    for attr in ("parse", "detect"):
        if not callable(getattr(reader, attr, None)):
            raise ReaderError(f"BI reader {name!r} defines no `{attr}` (see sockets.BIReader)")
    return reader


def all_readers() -> list[S.BIReader]:
    return [load(n) for n in names()]


def suffixes() -> tuple[str, ...]:
    """Every file suffix some installed reader reads: what a folder of BI files is filtered
    by."""
    return tuple(sorted({s.lower() for r in all_readers() for s in (r.suffixes or ())}))


def detect(path: pathlib.Path) -> str | None:
    """The one reader that recognises `path`, or None when none does or several do (a guess
    between two readers is still a guess)."""
    hits = [r.name for r in all_readers() if r.detect(pathlib.Path(path))]
    return hits[0] if len(hits) == 1 else None


def refusal(path: pathlib.Path) -> str | None:
    """Why `path` is not ONE model any installed reader would read (a reader that knows the
    path is, say, a whole tenant says so), or None."""
    for r in all_readers():
        hook = getattr(r, "refuse", None)
        why = hook(pathlib.Path(path)) if callable(hook) else None
        if why:
            return why
    return None


def model_gap(path: pathlib.Path) -> str | None:
    """Why `path` holds no model any installed reader could read a measure from (a reader
    that knows the path is, say, a report without its semantic model says so), or None.
    Not a refusal: the input is left out and the run reads its other inputs."""
    for r in all_readers():
        hook = getattr(r, "model_gap", None)
        why = hook(pathlib.Path(path)) if callable(hook) else None
        if why:
            return why
    return None


def one_model(path: pathlib.Path) -> pathlib.Path | None:
    """The single model `path` holds, when an installed reader knows `path` as a container of
    models (say, a whole tenant's extract) that holds exactly one a run could read; else None.
    With one there is no choice to make; with several, `refusal` says why."""
    for r in all_readers():
        hook = getattr(r, "one_model", None)
        got = hook(pathlib.Path(path)) if callable(hook) else None
        if got:
            return pathlib.Path(got)
    return None


def form(reader: S.BIReader, path: pathlib.Path) -> str:
    """Which of `reader`'s forms reads `path` (its name when it has only one)."""
    hook = getattr(reader, "form", None)
    return hook(pathlib.Path(path)) if callable(hook) else reader.name


def models(reader: S.BIReader, path: pathlib.Path) -> list[pathlib.Path]:
    """The models `path` holds, one path each, as `reader` sees them (its optional `models`
    hook: a folder of several model files is several models); [] when the reader declares no
    such hook or finds no more than the path itself, which is then read as ONE model."""
    hook = getattr(reader, "models", None)
    if not callable(hook):
        return []
    return [pathlib.Path(p) for p in hook(pathlib.Path(path)) or []]


def parse(path: pathlib.Path, vendor: str, **opts: Any) -> dict[str, Any]:
    """One model in the common `bi_facts` format, read by the reader named `vendor`. `opts`
    are the same for every reader (A1 passes the run's `dialect` and `policy`, and `model`, the
    BI input's `bi_model` setting); a reader takes what it needs and ignores the rest."""
    reader = load(vendor)
    form(reader, path)              # refuses a path the reader cannot read, by name
    facts = reader.parse(pathlib.Path(path), **opts)
    _check_languages(reader, facts)
    return facts


# ── languages ────────────────────────────────────────────────────────────────────────────
CORE_LANGUAGE = "sql"               # the core's own: never declared by a reader


def languages_of(reader: S.BIReader) -> tuple[str, ...]:
    return tuple(getattr(reader, "languages", None) or ())


def languages() -> tuple[str, ...]:
    """Every formula language an installed reader declares, readers in name order."""
    return tuple(dict.fromkeys(lang for r in all_readers() for lang in languages_of(r)))


def _check_languages(reader: S.BIReader, facts: dict[str, Any]) -> None:
    """A formula in a language the reader did not declare is one no compiler and no evidence
    rule knows: refused at the boundary rather than met later as a mystery."""
    declared = set(languages_of(reader)) | {CORE_LANGUAGE}
    seen = {m.get("formula_language") for m in facts.get("measures") or []}
    seen |= {c.get("formula_language") for t in facts.get("tables") or []
             for c in t.get("columns") or []}
    seen |= {u.get("expression_language") for u in facts.get("usage") or []}
    stray = sorted(lang for lang in seen - {None} if lang not in declared)
    if stray:
        raise ReaderError(f"BI reader {reader.name!r} wrote formulas in {stray}, which it does "
                          f"not declare in `languages` ({sorted(declared - {CORE_LANGUAGE})})")


# ── compilers ────────────────────────────────────────────────────────────────────────────
def compiles(reader: S.BIReader) -> bool:
    return callable(getattr(reader, "compile_unit", None))


def compiler(vendor: str) -> S.BIReader | None:
    """The reader that wrote a model of `vendor`, when it compiles its formulas; else None."""
    reader = load(vendor)
    return reader if compiles(reader) else None


def _compilers() -> list[S.BIReader]:
    return [r for r in all_readers() if compiles(r)]


def rule_table_paths(policy_path: Any = None, explicit: Any = None) -> list[pathlib.Path]:
    """The rule table(s) A3 compiles with: `explicit` (A3's `--rules`, the A3b loop's candidate
    table, a run's learned table); else the operator's `bi_compiler_rules.yml` beside the run's
    policy; else every table the installed compilers declare."""
    from metrics_agent._paths import RULES_NAME, beside_policy  # noqa: PLC0415

    if explicit:
        return [pathlib.Path(explicit)]
    own = beside_policy(policy_path, RULES_NAME) if policy_path else None
    if own is not None:
        return [own]
    return list(dict.fromkeys(pathlib.Path(r.compiler_rules) for r in _compilers()
                              if getattr(r, "compiler_rules", None)))


def rule_table(policy_path: Any = None, explicit: Any = None):
    """The loaded rule table (`lib/rules.Rules`): one table, or the union of the compilers'."""
    from metrics_agent.lib import rules as _rules  # noqa: PLC0415

    return _rules.load_many(rule_table_paths(policy_path, explicit))


def corpus_dirs(policy_path: Any = None) -> list[pathlib.Path]:
    """The regression corpus: the operator's `compiler_corpus/` beside the run's policy, else
    every corpus the installed compilers declare."""
    from metrics_agent._paths import CORPUS_NAME, beside_policy  # noqa: PLC0415

    own = beside_policy(policy_path, CORPUS_NAME) if policy_path else None
    if own is not None:
        return [own]
    return list(dict.fromkeys(pathlib.Path(r.compiler_corpus) for r in _compilers()
                              if getattr(r, "compiler_corpus", None)))


def _case_languages(case: dict[str, Any]) -> set[str]:
    unit = case.get("unit") or {}
    found = {m.get("formula_language") for m in unit.get("measures") or []}
    found.add(unit.get("expression_language"))
    return {lang for lang in found if lang and lang != CORE_LANGUAGE}


def corpus(policy_path: Any = None) -> list[tuple[dict[str, Any], list[S.BIReader]]]:
    """Every regression case with the compilers it gates: the one(s) declaring its formula
    language, or EVERY compiler for a case written in no tool's language (its shape — joins,
    filters, aggregation — holds for all of them)."""
    from metrics_agent.lib import rules as _rules  # noqa: PLC0415

    comps = _compilers()
    out = []
    for d in corpus_dirs(policy_path):
        for case in _rules.load_corpus(d):
            langs = _case_languages(case)
            who = [r for r in comps if langs & set(languages_of(r))] if langs else comps
            out.append((case, who))
    return out
