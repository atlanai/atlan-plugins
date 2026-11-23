# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Shared identifying-literal admission policy for local and engine query history."""
from __future__ import annotations

import re

_PERSON = (r"(?:assignee|assigned_agent|agent|requester|submitter|reviewer|author|owner|user|updater|actor|"
           r"creator|employee|csm|manager)")
_CUSTOMER = r"(?:customer|account|organization|organisation|org|company|tenant|domain|client)"
_RECORD = (r"(?:member|visitor|session|device|patient|contact|lead|subscriber|ticket|record|case|order|invoice|"
           r"incident|issue|transaction|payment|subscription|cart|shipment)")
_OPS = r"(?:=|<>|!=|\bi?like\b|\bnot\s+in\s*\(|\bin\s*\()"
_COL = r"[\w.\"]*?"
PII_RULES = [
    ("email", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("person_id", re.compile(rf"\b{_COL}{_PERSON}_?id\"?\s*{_OPS}\s*[\d,\s']{{7,}}", re.I)),
    ("customer_name", re.compile(rf"\b{_COL}{_CUSTOMER}(?:_account)?(?:_name)?\"?\s*{_OPS}\s*'", re.I)),
    ("record_id", re.compile(r"\b[\w.\"]*?(?:ticket|record|case|order|invoice|incident|issue)_?id\"?\s*"
                             r"(?:=|in\s*\()\s*'?\d{4,}", re.I)),
    ("history_value", re.compile(r"(?:\bvalue\"?|prev_assignee)\s*(?:=|<>|!=|in\s*\()\s*'?\d{10,}", re.I)),
    # Any literal, of any length, compared with the ID of a person, customer or record: a short
    # numeric `user_id = 42` identifies as well as a long one.
    ("entity_id", re.compile(rf"\b{_COL}(?:{_PERSON}|{_CUSTOMER}|{_RECORD})_?(?:id|uuid|key|number|no)\"?\s*{_OPS}\s*(?:'|[-+]?\d)", re.I)),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    # A prefixed ID such as 'cus_8Hk2Lm9' or 'ord_ab12cd': a short prefix, then letters and digits.
    ("prefixed_id", re.compile(r"'[a-z]{2,5}_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}'")),
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

