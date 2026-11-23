# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Shared identifying-literal admission policy for local and engine query history."""
from __future__ import annotations

import re

# Only email addresses are refused. The rules on names, company names and IDs were removed on 2026-11-23 until the
# security team sets new ones: they dropped important past queries.
PII_RULES = [
    ("email", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
]
PII = dict(PII_RULES)  # context_contracts checks cards with these same rules: one policy, not two copies


# Values a person has reviewed as business values for this run (`privacy.reviewed_values` in
# run.yml). Exact text. No rule here reads them since the name rules were removed (2026-11-23); kept for the
# callers that set them, and for the rules the security team adds.
_REVIEWED: frozenset = frozenset()


def set_reviewed(values) -> None:
    """The run's reviewed business values; an empty list clears them."""
    global _REVIEWED
    _REVIEWED = frozenset(str(v) for v in (values or []) if isinstance(v, str) and v.strip())


def full_name_hit(text: str):
    """Always None. The rule that refused a quoted two-word capitalised value ('Jane Smith') as a person's name was
    removed on 2026-11-23, with the rule on names beside a column of people: see `person_name_hit`."""
    return None


def person_name_hit(text: str):
    """Always None. The rules that refused a person's name, by its column or by its look, were removed on 2026-11-23
    until the security team sets new ones: they dropped important past queries."""
    return None


def pii_reason(sql: str):
    for name, pat in PII_RULES:
        if pat.search(sql or ""):
            return name
    return None

