# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Deterministic identity: artifact names and source_ids.

`source_id` is the registry's reconciliation key. A create naming a live line in
(workspace, kind, source_type, source_id) becomes an APPEND — on the bulk path too, since
create_batch and create_one share prepare_one and identity is reconciled before dispatch.

So the whole re-run story rests on one property: **source_id must be SEMANTIC and stable
across runs.** It is never derived from SQL text, from a content hash or from the run id —
those change for cosmetic reasons, and each change forks a second artifact while the original
stays put. Five uploads, five artifacts.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_NAME = 512  # the registry's artifact-name length limit
_KEBAB_STRIP = re.compile(r"[^a-z0-9]+")
# The registry refuses a name containing control characters. Built with chr() rather than
# written as escapes so the source file itself stays free of control bytes.
_CONTROL_CHARS = frozenset(chr(i) for i in range(0x20)) | {chr(0x7F)}
_SEP = chr(0x1F)  # unit separator: cannot occur in a table or column name


def kebab(text: str, *, max_len: int = 80) -> str:
    """A stable, readable handle. Convention is kebab-case; the enforced rule is only
    non-empty / <=512 / no control characters."""
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _KEBAB_STRIP.sub("-", norm.lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or "unnamed"


# A symbol a name carries that a column alias would otherwise drop: two measures whose names
# differ only by one (`Margin` and `Margin %`, `Sales` and `Sales ($)`, a format-string
# variant) would get one alias, and a statement showing both would have two columns of one name
# (seen live: the metric layer then read one for the other).
_ALIAS_WORDS = (("%", " pct "), ("$", " usd "), ("\u20ac", " eur "), ("\u00a3", " gbp "),
                ("#", " num "))


def column_alias(name: str) -> str:
    """The SQL column alias a compiler gives a named measure: lower snake case, a symbol kept
    as a word (`Margin %` is `margin_pct`)."""
    for sym, word in _ALIAS_WORDS:
        name = str(name).replace(sym, word)
    return kebab(name).replace("-", "_")


def artifact_name(*parts: str, max_len: int = MAX_NAME) -> str:
    """Join parts into one handle and enforce the registry's actual rule.

    `kebab` IS CALLED WITH THIS BUDGET, NOT ITS OWN DEFAULT. It caps each part at 80
    characters unless told otherwise, so this function advertised 512 and delivered 80 per
    part — and `MAX_NAME` never bound anything. Measured on a real layer: 4,100 of 5,197
    record names (79%) were cut mid-word, losing 195,478 characters in total.

    That text is not cosmetic. `name` is the registry's HIGHEST-weighted search field, so
    every question's tail — where the qualifying words usually live — was demoted out of it.
    The full question survives at weight B in `display_name`, which is why this degrades
    ranking rather than breaking it, and why it went unnoticed.
    """
    name = "-".join(kebab(p, max_len=max_len) for p in parts if p)
    name = "".join(ch for ch in name if ch not in _CONTROL_CHARS).strip()
    if not name:
        raise ValueError("artifact name is empty after normalisation")
    return name[:max_len]


def _digest(*parts: str, n: int = 12) -> str:
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()[:n]


def semantic_shape_key(
    tables: list[str],
    aggregations: list[tuple[str, str]],
    group_by: list[str],
    predicates: list[str],
) -> str:
    """The identity of a QUESTION, independent of how its SQL was spelled.

    sorted(tables) + sorted((fn, expr)) + sorted(group_by) + the normalised, parameterized
    predicate set. Two candidates whose keys are equal are the same question; C2 merges them.
    A differing predicate or grain makes them variants, which are kept separate and never
    merged — polarity opposites and period variants share a formula but are different
    questions.
    """
    t = ",".join(sorted(x.strip().lower() for x in tables if x))
    a = ",".join(sorted(f"{fn.strip().lower()}({expr.strip().lower()})"
                        for fn, expr in aggregations))
    g = ",".join(sorted(x.strip().lower() for x in group_by if x))
    p = ",".join(sorted(x.strip().lower() for x in predicates if x))
    return _digest(t, a, g, p)


def family_key(name: str, aggregation_type: str, base_tables: list[str]) -> str:
    """Identity of a METRIC family. Uses the family's own name plus its aggregation type and
    base tables — semantic, so a reworded formula or a reformatted statement keeps the key."""
    t = ",".join(sorted(x.strip().lower() for x in base_tables if x))
    return _digest(kebab(name), aggregation_type.strip().lower(), t)


def shape_signature(filters: list[str], group_by: list[str], grain: str) -> str:
    """Filter/grain signature for a metric slice. D1b uses it to prefer DISTINCT shapes over
    repeat spellings when retaining examples, so the examples show the metric's range rather
    than the same slice forty times."""
    f = ",".join(sorted(x.strip().lower() for x in filters if x))
    g = ",".join(sorted(x.strip().lower() for x in group_by if x))
    return _digest(f, g, grain.strip().lower(), n=10)


# ---- source_id constructors ------------------------------------------------
# The `vq:` prefix predates the move off eval dataset records and is kept deliberately: it is
# the reconciliation key the live layer already carries, and changing it would fork every
# saved query onto a second line.
def sql_query_source_id(scope: str, shape_key: str) -> str:
    return f"vq:{kebab(scope)}:{shape_key}"


def metric_source_id(scope: str, fam_key: str) -> str:
    return f"metric:{kebab(scope)}:{fam_key}"
