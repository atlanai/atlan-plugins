# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Shared identifying-literal admission policy for local and engine query history."""
import re

_PERSON = (r"(?:assignee|assigned_agent|agent|requester|submitter|reviewer|author|owner|user|updater|actor|"
           r"creator|employee|csm|manager)")
_CUSTOMER = r"(?:customer|account|organization|organisation|org|company|tenant|domain|client)"
_RECORD = (r"(?:member|visitor|session|device|patient|contact|lead|subscriber|ticket|record|case|order|invoice|"
           r"incident|issue|transaction|payment|subscription|cart|shipment)")
_OPS = r"(?:=|<>|!=|\bi?like\b|\bin\s*\()"
_COL = r"[\w.\"]*?"
PII_RULES = [
    ("email", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("person_name", re.compile(rf"\b{_COL}{_PERSON}(?:_name|_email|_full_name)?\"?\s*{_OPS}\s*'", re.I)),
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
    ("full_name", re.compile(r"'%?[A-Z][a-z]+ [A-Z][a-z]+%?'")),
]
_FULL_NAME_OK = re.compile(r"'%?(?:Pending Ruling|Not Applicable|Closed Won|Closed Lost|United States|"
                           r"New York|Human Assisted|First Response)%?'")


PII = dict(PII_RULES)  # context_contracts checks cards with these same rules: one policy, not two copies


# A two-word capitalised literal is a business label, not a person, in two cases a rule can see.
# 1. One of its words is a function word no "First Last" name holds: 'No Coverage', 'On Pace'.
_LABEL_WORDS = frozenset(("no", "not", "non", "very", "on", "off", "in", "out", "per", "all", "with", "without",
                          "and", "or", "the", "of", "to", "at", "by", "for", "from"))
# 2. It is compared with a column that holds categories, such as a status, tier or stage:
#    "Coverage Tier" = 'Actively Worked'. The column's own name is the evidence, so a column
#    of people (owner, rep, manager) keeps its literals refused.
_CATEGORY = (r"(?:status|state|stage|tier|type|category|class|classification|bucket|band|level|segment|"
             r"priority|severity|reason|outcome|result|phase|grade|rating|channel|motion|plan|label|flag|"
             # Organisational groupings: LINE_OF_BUSINESS = 'Variable Spend', LOB, BUSINESS_UNIT.
             r"business|lob|division|department|unit|vertical|sector|industry|region|territory|"
             r"portfolio|program|programme)")
# The category word may be followed by what the column holds of it: CATEGORY_NAME, REGION_CODE.
_CATEGORY_TAIL = _CATEGORY + r"(?:_?(?:name|desc|description|code|label))?"
_CATEGORY_COLUMN = re.compile(
    _CATEGORY_TAIL + r"[\"`\])]*\s*(?:=|<>|!=|\bnot\s+in\s*\(|\bin\s*\(|\bi?like\b)\s*(?:'[^']*'\s*,\s*)*$", re.I)
# 3. It is the label a CASE writes into a column named as a category:
#    CASE WHEN score >= 2.5 THEN 'Actively Worked' ... END AS coverage_tier.
_CASE_BRANCH = re.compile(r"\b(?:then|else)\s*$", re.I)
_CASE_ALIAS = re.compile(r"\bend\b\s*(?:as\s+)?[\"`\[]?([\w ]+?)[\"`\]]?\s*(?:,|\bfrom\b|\)|$)", re.I)
# 4. It is a label a CASE writes from tests on category columns only, or a value compared with
#    that CASE's result. A BI calculated field compiles this way:
#    CASE WHEN t.CHANNEL = 'ONLINE' THEN 'Variable Spend' ELSE 'Fixed Spend' END IN ('Variable Spend').
#    A test on anything else (an ID, a person, a score) leaves its labels to the other rules.
_LITERAL = re.compile(r"'[^']*'")
_KEYWORD = re.compile(r"\b(case|end|when|then|else)\b", re.I)
_TESTED = re.compile(r"([A-Za-z_][\w$]*)[\"`\])]*\s*(?:=|<>|!=|>=|<=|>|<|\bnot\s+in\b|\bin\b|\bi?like\b|\bis\b)",
                     re.I)
_ALIAS = re.compile(r"\s*(?:as\s+)?[\"`\[]?([A-Za-z_][\w ]*?)[\"`\]]?\s*(?:,|\bfrom\b|\)|$)", re.I)
_COMPARED = re.compile(r"\s*\)*\s*(?:=|<>|!=|\bnot\s+in\s*\(|\bin\s*\()\s*((?:'[^']*'\s*,\s*)*'[^']*')", re.I)

# Values a person has reviewed as business values for this run (`privacy.reviewed_values` in
# run.yml). Exact text only: a reviewed 'Variable Spend' clears that literal and nothing else.
_REVIEWED: frozenset = frozenset()


def set_reviewed(values) -> None:
    """The run's reviewed business values; an empty list clears them."""
    global _REVIEWED
    _REVIEWED = frozenset(str(v) for v in (values or []) if isinstance(v, str) and v.strip())


def _case_labels(text: str) -> set:
    """Start offsets of the literals rule 4 reads as labels."""
    masked = _LITERAL.sub(lambda m: "'" + "x" * (len(m.group(0)) - 2) + "'", text)
    words = [(m.start(), m.end(), m.group(1).lower()) for m in _KEYWORD.finditer(masked)]
    out: set = set()
    for i, (start, _end, word) in enumerate(words):
        if word != "case":
            continue
        depth, close, tests, branches, keyword, mark = 0, None, [], [], None, None
        for s, e, w in words[i + 1:]:
            if w == "case":
                depth += 1
            elif w == "end" and depth:
                depth -= 1
            elif depth == 0:
                if keyword == "when" and w == "then":
                    tests.append(masked[mark:s])
                elif keyword in ("then", "else") and w in ("when", "else", "end"):
                    branches.append((mark, s))
                if w == "end":
                    close = e
                    break
                keyword, mark = w, e
        if close is None or not tests:
            continue
        alias = _ALIAS.match(text, close)
        if alias and (re.search(_PERSON, alias.group(1), re.I)
                      or (re.search(r"name$", alias.group(1), re.I)
                          and not re.search(_CATEGORY_TAIL + r"$", alias.group(1), re.I))):
            continue        # the CASE writes a person's column: its labels are names
        columns = [c for t in tests for c in _TESTED.findall(t)]
        if not columns or not all(re.search(_CATEGORY_TAIL + r"$", c, re.I) for c in columns):
            continue
        values = set()
        for lo, hi in branches:
            for lit in _LITERAL.finditer(text, lo, hi):
                out.add(lit.start())
                values.add(lit.group(0))
        compared = _COMPARED.match(text, close)
        if compared:
            for lit in _LITERAL.finditer(text, compared.start(1), compared.end(1)):
                if lit.group(0) in values:
                    out.add(lit.start())
    return out


def _is_label(text: str, m, case_labels: set | None = None) -> bool:
    words = m.group(0).strip("'%").split()
    if any(w.lower() in _LABEL_WORDS for w in words):
        return True
    before = text[max(0, m.start() - 240):m.start()]
    if _CATEGORY_COLUMN.search(before):
        return True
    if _CASE_BRANCH.search(before):
        alias = _CASE_ALIAS.search(text, m.end())
        # The nearest END closes this CASE unless another CASE opens first.
        if alias and "case" not in text[m.end():alias.start()].lower() \
                and re.search(_CATEGORY_TAIL + r"$", alias.group(1).strip(), re.I):
            return True
    return m.start() in (_case_labels(text) if case_labels is None else case_labels)


def full_name_hit(text: str):
    """The first quoted two-word capitalised literal that is not a known business value, or None.
    Every match is tried: an allowed value earlier in the text does not hide a name after it."""
    text = text or ""
    labels = None
    for m in PII["full_name"].finditer(text):
        if _FULL_NAME_OK.search(m.group(0)) or m.group(0).strip("'%") in _REVIEWED:
            continue
        if labels is None:
            labels = _case_labels(text)
        if not _is_label(text, m, labels):
            return m.group(0)
    return None


def pii_reason(sql: str):
    for name, pat in PII_RULES:
        if name == "full_name":
            if full_name_hit(sql):
                return name
        elif pat.search(sql or ""):
            return name
    return None

