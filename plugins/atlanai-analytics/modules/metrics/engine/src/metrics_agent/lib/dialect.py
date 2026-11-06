# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The declared SQL dialect — blocking at S0, consumed at four separate points.

  A3            compiling: date arithmetic, string functions, window syntax, identifier quoting
  H0 / H1       the sqlglot READ dialect. Parsing one engine's SQL as generic ANSI silently
                mis-reads its own syntax (QUALIFY, semi-structured access, `::` casts) — and a
                mis-parse drops a query as out-of-scope, which surfaces as a scope finding
                rather than a bug.
  A4 / H5 / D5  the witness executes against a real engine
  outputs       every verified query and metric card records the dialect it is written in

A destination will rarely catch a wrong one: a dialect is usually a producer-declared string,
required non-empty and validated no further, so a metric under the wrong dialect is
structurally valid, permanently wrong, and discovered only by the first person who runs it.

THE CORE NAMES NO DIALECT (AGENT_GUARDRAILS rule 22). A dialect is any one SQL parser knows
(sqlglot's registry): the run declares it, a SQL-check plug speaks it, and nothing here lists
which engines exist.
"""
from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def _known() -> dict[str, str]:
    """{name: sqlglot name} for every dialect the SQL parser reads."""
    from sqlglot.dialects.dialect import Dialect  # noqa: PLC0415

    return {name: name for name in Dialect.classes if name}


def __getattr__(name: str):
    if name == "SUPPORTED":
        return _known()
    raise AttributeError(name)


class DialectError(ValueError):
    """Raised on an absent dialect, an unknown one, or a contradiction with the log."""


def normalise(declared: str | None) -> str:
    if not declared or not declared.strip():
        raise DialectError(
            "no SQL dialect declared. It is REQUIRED and blocking: A3 authors in it, B3/B5 "
            "parse with it, every witness executes against it, and definition.dialect must be "
            "non-empty. Set `dialect` in run.yml."
        )
    key = declared.strip().lower()
    if key not in _known():
        raise DialectError(
            f"unknown dialect {declared!r}. Known (the SQL parser's): "
            f"{', '.join(sorted(_known()))}."
        )
    return key


def of(doc: dict, what: str) -> str:
    """The dialect `doc` (an artifact) was written in. Missing is a refusal, never a default:
    a default would silently parse a Databricks or BigQuery estate as Snowflake."""
    declared = (doc or {}).get("dialect")
    if not declared:
        raise DialectError(
            f"{what} records no SQL dialect. Every artifact downstream of S0 carries the run's "
            "dialect; one without it was written by an older version or edited by hand. "
            "Re-run from S0.")
    return normalise(declared)


def sqlglot_read(declared: str) -> str:
    """The name to hand sqlglot's `read=`. Never omit it: generic parsing is a silent
    mis-read, not a parse failure."""
    return _known()[normalise(declared)]


def check_against_log(declared: str, log_engine: str | None) -> None:
    """For a query log the dialect is not a choice — the log came out of a specific engine, and the
    log usually records which. A declared dialect contradicting it is a contradiction, not a
    preference."""
    if not log_engine or not log_engine.strip():
        return
    want = normalise(declared)
    got = log_engine.strip().lower()
    if got in _known() and _known()[got] != _known()[want]:
        raise DialectError(
            f"declared dialect {want!r} contradicts the query log's recorded engine {got!r}. "
            "Parsing one estate's corpus with another's grammar mis-reads it silently — fix "
            "the declaration or the log; never proceed."
        )


def check_single(dialects: list[str]) -> str:
    """One run emits one dialect.

    A mixed-dialect input set is refused rather than published with per-object dialects: a
    consumer retrieving a mixed set has no way to tell which statements it can execute, and
    the dataset is the unit a consumer retrieves.
    """
    found = sorted({normalise(d) for d in dialects if d})
    if not found:
        raise DialectError("no dialect could be resolved for this run")
    if len(found) > 1:
        raise DialectError(
            f"mixed-dialect estate: {', '.join(found)}. One run emits one dialect — split this "
            "into one run per dialect, each with its own dataset and metric set."
        )
    return found[0]
