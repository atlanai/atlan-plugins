# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Every field of a metric definition, and the rule that fills it.

The contract (the Atlan Agent Registry's metric validation): `spec_version` `1.0-draft.4`,
controlled vocabularies on `aggregation.type`, `reaggregation`, `null_handling` and
`output.datatype`, exactly one of `source.table`/`source.sql`, unique names within `dimensions`
and `joins`, and every `dimensions[].required_join` naming a declared join. The layer this
replaces broke two of those on a majority of its cards — 313 of 514 declared `output.datatype:
custom`, which is an AGGREGATION value and not an output one, and 24 declared `avg` or `median`,
which the aggregation vocabulary does not contain either.

Everything here is derived from the statement or from a fixed table. The one field a model
writes is the artifact-level `description`, because it is the searchable text and the
sentence a reader judges the card by; it is authored elsewhere and validated, never invented
here.
"""
from __future__ import annotations

import re
from typing import Any

# THE METRIC KIND'S SPEC, AND ONLY IT. The registry's draft 2 retired the definition's `status`,
# `approved_by` and `approved_at` — lifecycle and releases belong to the registry; draft 4
# retired `owner` (the envelope's `owner_id` is the owner) and `domain`. An earlier
# spec is refused outright, and a stored draft-1 metric upgrades by an append that raises
# `definition.version` — which r3b does, because the spec is part of the content it compares.
# The `sql_query` kind has its own spec (r1_envelopes.SQL_QUERY_SPEC_VERSION), unchanged.
SPEC_VERSION = "1.0-draft.4"

# The vocabularies, copied from the contract rather than guessed. A value outside them is an
# advisory warning at the gateway and a silent quality loss in the layer.
AGGREGATION_TYPES = ("sum", "count", "count_distinct", "min", "max", "ratio", "custom")
REAGGREGATION = ("", "sum", "min", "max", "none")
NULL_HANDLING = ("", "ignore", "zero_fill", "propagate")
OUTPUT_DATATYPES = ("integer", "decimal", "string", "boolean", "date", "timestamp")

# How a SQL function maps onto the declared aggregation. Anything absent is `custom`, which
# the vocabulary allows and which says honestly that the shape is not one of the named ones —
# `avg` and `median` are real SQL and are NOT in the contract, so they land here.
_AGG_FROM_FN = {
    "sum": "sum", "count": "count", "min": "min", "max": "max",
    "count_distinct": "count_distinct", "countdistinct": "count_distinct",
    "ratio": "ratio", "div": "ratio", "divide": "ratio",
}
_REAGG_FOR = {"sum": "sum", "count": "sum", "count_distinct": "none", "min": "min",
              "max": "max", "ratio": "none", "custom": "none"}

_INT_FN = re.compile(r"(?i)^\s*(count|count_distinct)\s*\(")
_BOOL_FN = re.compile(r"(?i)^\s*(bool|boolor_agg|booland_agg)")
_DATE_FN = re.compile(r"(?i)^\s*(max|min)\s*\(\s*[\w.]*(date|_dt|_at|_ts|time)[\w.]*\s*\)")
_RATIO = re.compile(r"(?i)(/|\bdiv\b|\bdivide\s*\(|\bratio\b)")
_DECIMAL_HINT = re.compile(r"(?i)(amount|arr|acv|mrr|revenue|price|cost|value|rate|pct|"
                           r"percent|ratio|avg|average|median|percentile|score)")
_NUMERIC_TYPE = ("int", "num", "dec", "float", "double", "real", "bigint", "smallint", "money")
_DATE_TYPE = ("date", "timestamp", "datetime")


def aggregation_type(fn: str, formula: str) -> str:
    """The declared aggregation, mapped onto the contract's vocabulary.

    `avg` and `median` are legitimate SQL and are not in the list; publishing them anyway is
    what put 24 cards outside the contract. They are `custom`, which is the value the
    contract provides for exactly this.
    """
    f = str(fn or "").strip().lower()
    if f in _AGG_FROM_FN:
        return _AGG_FROM_FN[f]
    if _RATIO.search(str(formula or "")) and f not in ("count", "sum", "min", "max"):
        return "ratio"
    return "custom"


def reaggregation(agg_type: str) -> str:
    return _REAGG_FOR.get(agg_type, "none")


def output_datatype(formula: str, fn: str, datatype_of: Any = None) -> str:
    """What the metric RETURNS, in the contract's vocabulary.

    `custom` is not one of the six the contract accepts for an output — it is an aggregation
    value, and 313 of 514 cards used it here. The shape of the expression answers this
    deterministically in almost every case: a count is an integer, a ratio is a decimal, a
    boolean aggregate is a boolean, `MAX` of a date column is a date, and anything else takes
    the wrapped column's warehouse datatype. `decimal` is the fallback because a measure is
    a number unless something says otherwise.
    """
    f = str(formula or "")
    if _INT_FN.match(f) or str(fn or "").lower() in ("count", "count_distinct"):
        return "integer"
    if _BOOL_FN.match(f):
        return "boolean"
    if _RATIO.search(f):
        return "decimal"
    if _DATE_FN.match(f):
        return "date"
    col = ""
    m = re.match(r"(?i)^\s*\w+\(\s*([\w.]+)\s*\)\s*$", f)
    if m:
        col = m.group(1).rsplit(".", 1)[-1].lower()
    declared = ""
    if col:
        if callable(datatype_of):
            declared = str(datatype_of(col) or "").lower()
        elif isinstance(datatype_of, dict):
            declared = str(datatype_of.get(col) or "").lower()
    if declared.startswith(_DATE_TYPE):
        return "date" if declared.startswith("date") else "timestamp"
    if declared.startswith(("varchar", "text", "char", "string")):
        return "string"
    if declared.startswith("bool"):
        return "boolean"
    if declared.startswith(("int", "bigint", "smallint")):
        return "integer"
    if declared.startswith(_NUMERIC_TYPE):
        return "decimal"
    if col and _DECIMAL_HINT.search(col):
        return "decimal"
    return "decimal"


def output_unit(formula: str, alias: str) -> str | None:
    """Currency, percent or count where the words say so, and nothing where they do not."""
    t = f"{formula} {alias}".lower()
    if re.search(r"(?i)\b(arr|acv|mrr|amount|revenue|price|cost|pipeline)\b", t):
        return "currency"
    if re.search(r"(?i)(pct|percent|rate|ratio)", t):
        return "percent"
    if re.search(r"(?i)^\s*count", str(formula or "")):
        return "count"
    return None


def zero_denominator(agg_type: str, formula: str) -> Any:
    """A ratio with no zero-denominator policy draws an advisory warning. `NULLIF` in the
    expression IS the policy, so it is declared rather than left for the gateway to notice."""
    if agg_type != "ratio":
        return None
    return "null" if "nullif" in str(formula or "").lower() else None


# A metric named `close_date` or `pages` identifies nothing. The alias is what ONE query
# called its output column, and once families are keyed on the measure rather than the alias
# — which is the regrouping that collapsed 859 candidates to 332 measures — the surviving
# modal alias is frequently the shortest, least distinctive of the set. 194 of 449 cards came
# out named by a bare column alias, among them `pages`, `synced_at`, `triggers_4` and
# `close_date`.
#
# That is not only unreadable, it is the retrieval defect: the registry's FTS document weights
# `name` highest (A), and a name carrying no business vocabulary cannot be matched by a
# question asked in business words. The words are available and they are FACTS FROM THE QUERY
# — the table the measure is computed over — so the alias is qualified by them rather than by
# anything invented. `p95_requests` over `ATLAN_MCP_REQUEST` becomes
# `atlan_mcp_request_p95_requests`, which a question about MCP requests can actually reach.
#
# Only the words the alias does not already carry are added, so a measure the estate already
# named well keeps its name: `engaged_customers` over `MCP_ACCOUNT_SUMMARY` does not become
# `mcp_account_summary_engaged_customers` in full, only what it lacks.
_ENTITY_NOISE = frozenset(
    ("fct", "dim", "stg", "agg", "int", "rpt", "mart", "marts", "tbl", "tmp", "vw",
     "v", "base", "final", "all", "data", "tab", "table"))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if t]


def qualified_name(alias: str, entity: str) -> tuple[str, str]:
    """`(name, basis)` — the alias, qualified by the entity words it does not already carry.

    Returns the basis alongside the name because a renamed metric is the kind of change a
    reader has to be able to audit: the field says which table supplied the qualifier, so the
    name can be checked against the statement rather than taken on trust.
    """
    alias = str(alias or "").strip()
    if not alias:
        return "", ""
    have = set(_tokens(alias))
    table = str(entity or "").split(".")[-1]
    add = [t for t in _tokens(table)
           if t not in have and t not in _ENTITY_NOISE and not t.isdigit()]
    if not add:
        return alias, "alias (already distinctive)"
    # The alias keeps its own spelling — spaces and all, because `slugs.artifact_name`
    # normalises on the way to the wire and the card should still read as the estate wrote it.
    return " ".join(add) + " " + alias, f"entity:{table} + alias"
