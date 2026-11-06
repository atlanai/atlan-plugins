# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Observed filter values — the literals that survive a shape collapse.

Two queries differing only in a literal collapse to one shape, and only the representative's
literal stays in the SQL. Retrieval is keyword-based, so the others become unfindable:
`customer = 'aa'` and `customer = 'bb'` are one shape but two things a person searches for.

Captured at H1 (the only stage where the collapsed group still exists) and at A3 for the BI
lane; the policy below is applied once, here, so both lanes shape the result identically.

WHERE THEY LAND, and why it is two places:

  input.observed_values   a flat list of strings. This is what the registry's own `q` filter
                          scans — `record_text_matches` reads `input`, `expected`, `label` and
                          `categories`, and its `in_values` handles a string OR an array of
                          strings. It does NOT read `extra`.
  extra.observed_values   the structured per-column detail with counts, for a consumer's
                          BM25F index and for human review.
"""
from __future__ import annotations

import re
from typing import Any

MAX_TOKENS = 2000  # a hard ceiling on the flat list, independent of the per-column cap


class Shaper:
    def __init__(self, policy: Any) -> None:
        self.min_users: int = policy.get("observed_values.min_distinct_users")
        self.max_per_column = policy.get("observed_values.max_per_column")
        self.min_chars: int = policy.get("observed_values.min_value_chars")
        self._skip = [re.compile(p)
                      for p in policy.get("observed_values.skip_column_patterns")]
        self._redact = [re.compile(p) for p in policy.get("pii.redact_patterns")]

    def _skip_column(self, column: str) -> bool:
        bare = column.split(".")[-1]
        return any(p.search(bare) for p in self._skip)

    def _redacted(self, value: str) -> str:
        out = value
        for pat in self._redact:
            out = pat.sub("<redacted>", out)
        return out

    def apply(self, raw: dict[str, list[dict]]) -> dict[str, Any]:
        """{column: [{value, n_exec, n_users}]} -> the shaped block carried downstream.

        Returns both the structured detail and the flat token list, plus what was withheld —
        a cap that bites is REPORTED, never a silent truncation.
        """
        by_column: dict[str, list[dict]] = {}
        withheld: dict[str, int] = {}

        for column, values in raw.items():
            if self._skip_column(column):
                withheld[column] = withheld.get(column, 0) + len(values)
                continue
            kept = []
            for v in values:
                text = self._redacted(str(v["value"]))
                if len(text) < self.min_chars:
                    continue
                if v.get("n_users", 0) < self.min_users:
                    continue
                kept.append({"value": text, "n_exec": v.get("n_exec", 0),
                             "n_users": v.get("n_users", 0)})
            # Deduplicate AFTER redaction: two emails both become "<redacted>".
            merged: dict[str, dict] = {}
            for k in kept:
                m = merged.setdefault(k["value"], {"value": k["value"], "n_exec": 0,
                                                   "n_users": 0})
                m["n_exec"] += k["n_exec"]
                m["n_users"] = max(m["n_users"], k["n_users"])
            ordered = sorted(merged.values(), key=lambda x: (-x["n_exec"], x["value"]))
            if self.max_per_column is not None and len(ordered) > self.max_per_column:
                withheld[column] = withheld.get(column, 0) + (len(ordered) - self.max_per_column)
                ordered = ordered[: self.max_per_column]
            if ordered:
                by_column[column] = ordered

        tokens = sorted({v["value"] for vals in by_column.values() for v in vals})
        truncated = False
        if len(tokens) > MAX_TOKENS:
            withheld["_tokens"] = len(tokens) - MAX_TOKENS
            tokens = tokens[:MAX_TOKENS]
            truncated = True

        return {
            "by_column": by_column,
            "tokens": tokens,
            "withheld": withheld,
            "truncated": truncated or bool(withheld),
        }


def merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    """Union two shaped blocks — C2 needs this so a cross-lane merge keeps BOTH lanes' values.

    Whichever side is missing, the other survives whole. That is the case people forget: a BI
    candidate merging with a history candidate must not drop the history literals.
    """
    if not a:
        return b
    if not b:
        return a
    by_column: dict[str, list[dict]] = {}
    for src in (a, b):
        for col, vals in (src.get("by_column") or {}).items():
            bucket = {v["value"]: dict(v) for v in by_column.get(col, [])}
            for v in vals:
                cur = bucket.setdefault(v["value"], {"value": v["value"], "n_exec": 0,
                                                     "n_users": 0})
                cur["n_exec"] += v.get("n_exec", 0)
                cur["n_users"] = max(cur["n_users"], v.get("n_users", 0))
            by_column[col] = sorted(bucket.values(),
                                    key=lambda x: (-x["n_exec"], x["value"]))
    withheld: dict[str, int] = {}
    for src in (a, b):
        for k, n in (src.get("withheld") or {}).items():
            withheld[k] = withheld.get(k, 0) + n
    return {
        "by_column": by_column,
        "tokens": sorted({v["value"] for vals in by_column.values() for v in vals}),
        "withheld": withheld,
        "truncated": bool(a.get("truncated")) or bool(b.get("truncated")),
    }


def from_filters(filters: list[dict], shaper: Shaper) -> dict[str, Any]:
    """The BI lane's entry point: a unit's own filter values, with no execution counts.

    A visual filter was placed once by an author, so `n_exec`/`n_users` are 1 — the value is
    evidenced by visual usage rather than by adoption, and the floor is met by construction.
    """
    raw: dict[str, list[dict]] = {}
    for f in filters:
        val = f.get("value")
        if val is None or isinstance(val, dict):
            continue        # a range's bounds are limits, not vocabulary anyone searches for
        values = val if isinstance(val, list) else [val]
        for v in values:
            if v is None:
                continue
            raw.setdefault(f["column"], []).append(
                {"value": str(v), "n_exec": 1, "n_users": 1})
    return shaper.apply(raw)
