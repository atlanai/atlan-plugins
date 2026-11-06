# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The closed-world rule, as code.

Every authored filter and literal cites evidence. No citation, no filter. This is
simultaneously the quality control and the prompt-injection control: a model that "followed"
an instruction planted in a dashboard text box still has to cite evidence for the filter it
was told to add, and a text box is not an evidence source.

The vocabulary is CLOSED. A new source is a deliberate addition here, never a string a caller
invents at the point of use. It has two parts: the core's own sources below, and the formula
languages the installed BI readers declare (`sockets.BIReader.languages`): a formula the reader
read is evidence for what it says, in whichever tool's language it is written. The core names
no such language itself (AGENT_GUARDRAILS rule 22).
"""
from __future__ import annotations

import functools
from typing import Any, Iterable

CORE_SOURCES = (
    "column-profile",   # observed values of a real column
    "schema",           # the warehouse's own declaration
    "query-log",        # analysts independently choosing a clause
    "visual-usage",     # a filter placed on a visual/worksheet/dashboard
    "verified-query",   # an already-certified statement
    "declared-model",   # a declared model's own metric, dimension, fact or relationship expression
)

# Sources an author may quote but which can NEVER authorise a filter. Author-written prose is
# attacker-influenceable: it may ground a NAMING claim and nothing more.
PROSE_ONLY = ("dashboard-text", "visual-title", "sql-comment", "asset-description")


@functools.lru_cache(maxsize=1)
def sources() -> tuple[str, ...]:
    """The whole vocabulary: the readers' formula languages, then the core's sources."""
    from metrics_agent import readers  # noqa: PLC0415 - the registry reads package metadata

    return tuple(dict.fromkeys([*readers.languages(), *CORE_SOURCES]))


def __getattr__(name: str) -> Any:
    if name == "SOURCES":
        return sources()
    raise AttributeError(name)


class EvidenceError(ValueError):
    """Raised when a citation names a source outside the closed vocabulary."""


def cite(claim: str, source: str, detail: str = "") -> dict[str, str]:
    if source not in sources():
        if source in PROSE_ONLY:
            raise EvidenceError(
                f"{source!r} is prose. It may ground a naming claim in a description, but it "
                f"can never authorise a filter — that is the injection boundary."
            )
        raise EvidenceError(
            f"unknown evidence source {source!r}. The vocabulary is closed: "
            f"{', '.join(sources())}."
        )
    out = {"claim": claim, "source": source}
    if detail:
        out["detail"] = detail
    return out


def validate(citations: Iterable[dict[str, Any]]) -> None:
    for c in citations:
        if "claim" not in c or "source" not in c:
            raise EvidenceError(f"citation missing claim/source: {c!r}")
        if c["source"] not in sources():
            raise EvidenceError(f"citation names unknown source {c['source']!r}")


def cited_claims(citations: Iterable[dict[str, Any]]) -> set[str]:
    return {c["claim"].strip().lower() for c in citations if c.get("claim")}


def is_grounded(claim: str, citations: Iterable[dict[str, Any]]) -> bool:
    """A claim is grounded when something in the closed vocabulary vouches for it.

    Matching is substring-tolerant in one direction only: a citation may be more specific
    than the claim it supports, never vaguer.
    """
    needle = claim.strip().lower()
    if not needle:
        return False
    for c in citations:
        got = str(c.get("claim", "")).strip().lower()
        if got and (needle == got or needle in got):
            return True
    return False
