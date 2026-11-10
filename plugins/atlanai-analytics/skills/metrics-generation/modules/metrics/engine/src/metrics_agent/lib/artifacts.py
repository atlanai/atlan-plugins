# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Artifact IO with schema validation on every write.

Rule 1: schemas before scripts. A stage validates its artifact BEFORE exit, so a shape error
is caught where it was produced rather than three stages later with a worse message.
"""
from __future__ import annotations

import functools
import json
import pathlib
from typing import Any

import jsonschema

from metrics_agent._paths import SCHEMAS


class ArtifactError(RuntimeError):
    """Raised when an artifact is missing or fails its contract."""


# EVERY FILE A STAGE LEAVES IN THE RUN DIRECTORY, and the schema it is held to. A key is an
# `fnmatch` pattern on the path relative to the run directory; a `.jsonl` file is checked line
# by line. `tests/test_artifact_schemas.py` runs the golden estate and fails on any file that
# matches neither this table nor UNSCHEMAD, so a new artifact cannot arrive without a contract.
RUN_ARTIFACTS: dict[str, str] = {
    "run_inputs.json": "run_inputs.schema.json",
    "manifest.json": "manifest.schema.json",
    "access_report.json": "access_report.schema.json",
    # lane A
    "bi_facts.json": "bi_facts.schema.json",
    "a1_sidecar.json": "a1_sidecar.schema.json",
    "question_units.json": "question_units.schema.json",
    "fragments/*.widget.json": "widget_fragment.schema.json",
    "fragments/*.dashboard.json": "dashboard_fragment.schema.json",
    "bi_candidates.json": "candidates.schema.json",
    "a3_compile_report.json": "a3_compile_report.schema.json",
    "a3b_prompt.json": "a3b_prompt.schema.json",
    "a3b_review.json": "a3b_review.schema.json",
    "a3_loop.json": "a3_loop.schema.json",
    "a4_quarantine.json": "a4_quarantine.schema.json",
    # lane C
    "candidates.json": "candidates.schema.json",
    "merged_candidates.json": "candidates.schema.json",
    "c3_prompt.json": "c3_prompt.schema.json",
    "questioned_candidates.json": "candidates.schema.json",
    "c3_authored.json": "c3_authored.schema.json",
    "verify_report.json": "verify_report.schema.json",
    "c4_flagged.json": "c4_flagged.schema.json",
    "c4_candidates.json": "candidates.schema.json",
    "verified_queries.bi.json": "verified_queries.schema.json",
    # lane H
    "query_corpus.jsonl": "query_corpus.schema.json",
    "query_shapes.json": "query_shapes.schema.json",
    "history_measures.json": "history_measures.schema.json",
    "verified_queries.history.json": "verified_queries.schema.json",
    # lane M
    "verified_queries.json": "verified_queries.schema.json",
    "m2_prompt.json": "m2_prompt.schema.json",
    "sql_descriptions.json": "sql_descriptions.schema.json",
    # lanes D and X
    "metric_candidates.json": "metric_candidates.schema.json",
    "x1_prompt.json": "x1_prompt.schema.json",
    "metric_proposals.json": "metric_proposals.schema.json",
    "x2_prompt.json": "x2_prompt.schema.json",
    "merged_metrics.json": "merged_metrics.schema.json",
    "x3_prompt.json": "x3_prompt.schema.json",
    "x3_repair.json": "x3_repair.schema.json",
    "x3_loop.json": "x3_loop.schema.json",
    "metric_cards.json": "metric_spec.schema.json",
    "x4_prompt.json": "x4_prompt.schema.json",
    "metric_relationships.json": "metric_relationships.schema.json",
    "consistency_report.json": "consistency_report.schema.json",
}

# The files deliberately held to no JSON schema, each with the reason. Nothing else may be.
UNSCHEMAD: dict[str, str] = {
    ".trace_handoffs.jsonl": "an append-only log of answers handed to the tracer; replayed "
                             "into the trace, never read as a contract",
    ".trace_context.json": "tracing plug state (session and span ids), private to that plug",
    "compiler_rules_learned.yml": "a rules table in YAML, not JSON; every row is checked by "
                                  "lib/rules.py (validate_proposal) before it is written",
    "x3_repaired_r*.json": "an author's answer, copied for the next round; x3_emit validates "
                           "it on ingest like any answer",
    "x3_answers_r*_*.json": "an author's answer with superseded cards removed; x3_emit "
                            "validates it on ingest like any answer",
}

# A PLUG PACKAGE'S OWN ARTIFACTS ARE ITS OWN. The tables above list only what the core writes;
# a package whose stages write into the run directory registers a module under this group with
# `SCHEMAS` (its schema directory), `RUN_ARTIFACTS` and `UNSCHEMAD` in the same shape. The
# core never names a destination's files, and `schema()` finds a package's schema by name.
ARTIFACT_GROUP = "metrics_agent.artifacts"


@functools.lru_cache(maxsize=1)
def _plug_contracts() -> tuple:
    """(schema directory, RUN_ARTIFACTS, UNSCHEMAD) of every installed package that declares
    them. A package that is registered but cannot be imported is an error that names it, not a
    silent gap: its files would otherwise surface later as artifacts with no contract."""
    from importlib import metadata  # noqa: PLC0415

    out = []
    for ep in sorted(metadata.entry_points(group=ARTIFACT_GROUP), key=lambda e: e.name):
        try:
            mod = ep.load()
        except Exception as exc:  # noqa: BLE001 - any import failure is the package's
            raise ArtifactError(f"artifact contracts {ep.name!r} ({ep.value}) could not be "
                                f"loaded: {type(exc).__name__}: {exc}") from exc
        out.append((pathlib.Path(mod.SCHEMAS), dict(mod.RUN_ARTIFACTS),
                    dict(getattr(mod, "UNSCHEMAD", {}))))
    return tuple(out)


def contract_of(rel: str) -> str | None:
    """The schema a run-directory file is held to, or None for a listed exception.

    Raises for a file that is in neither table: an artifact with no contract is the defect.
    """
    import fnmatch
    tables = [(RUN_ARTIFACTS, UNSCHEMAD)] + [(r, u) for _d, r, u in _plug_contracts()]
    for schemad, unschemad in tables:
        for table in (schemad, unschemad):
            for pattern, value in table.items():
                if fnmatch.fnmatchcase(rel, pattern):
                    return value if table is schemad else None
    raise ArtifactError(f"{rel}: no schema in RUN_ARTIFACTS and no reason in UNSCHEMAD, in the "
                        f"core or in any installed package's `{ARTIFACT_GROUP}`")


@functools.lru_cache(maxsize=1)
def _vocabularies() -> dict[str, list]:
    """What an `x-vocabulary` property may hold. The common formats name no tool's language
    (AGENT_GUARDRAILS rule 22): the installed readers declare theirs (`sockets.BIReader`)."""
    from metrics_agent import readers  # noqa: PLC0415
    from metrics_agent.lib import evidence  # noqa: PLC0415

    return {"evidence": list(evidence.sources()),
            "formula-language": [*readers.languages(), readers.CORE_LANGUAGE, None]}


def _fill_vocabularies(node: Any) -> None:
    if isinstance(node, dict):
        vocab = node.get("x-vocabulary")
        if vocab is not None:
            node["enum"] = list(_vocabularies()[vocab])
        for value in node.values():
            _fill_vocabularies(value)
    elif isinstance(node, list):
        for value in node:
            _fill_vocabularies(value)


def schema(name: str) -> dict[str, Any]:
    """A schema by file name: the core's, else the one an installed package ships, with every
    `x-vocabulary` filled from the installed readers."""
    path = SCHEMAS / name
    if not path.is_file():
        found = [d / name for d, _r, _u in _plug_contracts() if (d / name).is_file()]
        if not found:
            raise ArtifactError(f"no schema {name} at {path} or in any installed package")
        path = found[0]
    doc = json.loads(path.read_text())
    _fill_vocabularies(doc)
    return doc


# At most this many further failures are listed after the first; the count says how many more.
_MORE_ERRORS = 20


def validate(data: Any, schema_name: str, *, what: str = "artifact") -> None:
    """Raise with the failing path spelled out. jsonschema's own message names the rule; the
    path is what tells you which object in a 400-element array broke it.

    EVERY FAILURE, NOT THE FIRST. An author fixing a refused answer file used to see one
    error, fix it, and be refused again on the next: several rounds for one file. The first
    line is the one jsonschema ranks most relevant, worded as before; the rest follow, in
    document order."""
    s = schema(schema_name)
    v = jsonschema.validators.validator_for(s)(s)
    errors = list(v.iter_errors(data))
    if not errors:
        return

    def at(err) -> str:
        return "/".join(str(p) for p in err.absolute_path) or "<root>"
    first = jsonschema.exceptions.best_match(errors)
    rest = sorted((e for e in errors if e is not first),
                  key=lambda e: [str(p) for p in e.absolute_path])
    lines = [f"{what} failed {schema_name} at {at(first)}: {first.message}"]
    lines += [f"  also at {at(e)}: {e.message}" for e in rest[:_MORE_ERRORS]]
    if len(rest) > _MORE_ERRORS:
        lines.append(f"  … and {len(rest) - _MORE_ERRORS} more")
    raise ArtifactError("\n".join(lines)) from None


def line_checker(schema_name: str, *, what: str):
    """`check(row, line_no)` for a JSONL artifact written row by row.

    The validator is built once, not per row: a corpus runs to hundreds of thousands of lines.
    """
    s = schema(schema_name)
    v = jsonschema.validators.validator_for(s)(s)

    def check(row: Any, line_no: int) -> None:
        err = jsonschema.exceptions.best_match(v.iter_errors(row))
        if err is not None:
            where = "/".join(str(p) for p in err.absolute_path) or "<root>"
            raise ArtifactError(
                f"{what} line {line_no} failed {schema_name} at {where}: {err.message}")
    return check


def dump(path: pathlib.Path, data: Any, schema_name: str, *, indent: int = 2,
         ensure_ascii: bool = True, newline: bool = True) -> pathlib.Path:
    """Validate, then write `path` — for a file a stage writes BESIDE its artifact (a prompt
    bundle, a sidecar, a report) rather than through `Ctx.write`, which also traces it. The
    caller still decides what the manifest records; the contract is not optional."""
    validate(data, schema_name, what=path.name)
    path.write_text(json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
                    + ("\n" if newline else ""))
    return path


# A run directory holds the customer's statements, BI definitions and what was published:
# only its owner may list or read it, from the moment it exists.
RUN_DIR_MODE = 0o700


def ensure_run(run: pathlib.Path) -> pathlib.Path:
    """Create the run directory owner-only (0700) if it does not exist. An existing directory
    keeps the mode its owner gave it."""
    run.mkdir(parents=True, exist_ok=True, mode=RUN_DIR_MODE)
    return run


def write(run: pathlib.Path, name: str, data: Any, schema_name: str) -> pathlib.Path:
    validate(data, schema_name, what=name)
    ensure_run(run)
    path = run / name
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    return path


def read(run: pathlib.Path, name: str, schema_name: str | None = None) -> Any:
    path = run / name
    if not path.is_file():
        raise ArtifactError(
            f"missing artifact {name} in {run} — run the stage that produces it. "
            f"Missing artifacts raise; there is no silent fallback."
        )
    data = json.loads(path.read_text())
    if schema_name:
        validate(data, schema_name, what=name)
    return data


def sha256_of(data: Any) -> str:
    """Canonical hash of an artifact body, used for layer_sha. Sorted keys and compact
    separators so the value depends on content, never on formatting."""
    import hashlib

    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def times_blanked(value: Any) -> Any:
    """`value` with every `*_at` timestamp set to None, for hashing content only. A timestamp
    (a witness's `executed_at`) is when, not what: left in a layer's hash, every re-run that
    executes its statements would be a new version of the same layer. Blanked rather than
    dropped, so a layer whose timestamps were already empty (parse mode) keeps its hash."""
    if isinstance(value, dict):
        return {k: (None if k.endswith("_at") else times_blanked(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [times_blanked(v) for v in value]
    return value
