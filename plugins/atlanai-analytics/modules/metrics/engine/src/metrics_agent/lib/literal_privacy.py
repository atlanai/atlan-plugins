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


PII = dict(PII_RULES)  # metrics_mine checks cards with these same rules: one policy, not two copies


# Classification uses the compared column, never a growing list of company/place names.
_PLACE_COLUMN = re.compile(r'(?:^|_)(?:city|town|country|region|state|province|county)(?:_name)?$', re.I)
_LABEL_COLUMN = re.compile(r'^(?:status|stage|category)(?:_name)?$', re.I)
_ORG_COLUMN = re.compile(r'^(?:supplier|vendor|manufacturer)(?:_company|_organization|_organisation)?(?:_name)?$', re.I)
_ORG_END = re.compile(r'\b(?:ltd|limited|inc|incorporated|llc|llp|plc|corp|corporation|gmbh|builders|construction)\.?$', re.I)
_PERSON_VALUE_COLUMN = re.compile(r'^(?:name|full_name|first_name|last_name|person_name|email|phone|phone_number)$|'
                                  r'(?:^|_)(?:owner|user|employee|contact|assignee|requester)(?:_full)?_name$', re.I)
_IDENT = re.compile(r'[A-Za-z_][A-Za-z_0-9$]*')


def _sql_tokens(text):
    """Only enough lexical structure to prove simple literal/column comparisons.

    Comments (including nested blocks) and string contents cannot supply column evidence.
    Unsupported expression shapes keep the conservative identifying-literal decision.
    """
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith('--', i):
            end = text.find('\n', i + 2)
            i = len(text) if end < 0 else end + 1
            continue
        if text.startswith('/*', i):
            depth, i = 1, i + 2
            while i < len(text) and depth:
                if text.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif text.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            continue
        start = i
        if text[i] in "'\"`[":
            quote = text[i]
            close = ']' if quote == '[' else quote
            i += 1
            while i < len(text):
                if quote == "'" and text[i] == '\\':
                    i += 2
                elif text[i] == close:
                    if i + 1 < len(text) and text[i + 1] == close:
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            result.append(('str' if quote == "'" else 'ident', text[start:i], start, i))
        elif text[i] == '$' and (tag := re.match(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$', text[i:])):
            end = text.find(tag[0], i + len(tag[0]))
            i = len(text) if end < 0 else end + len(tag[0])
            result.append(('opaque', text[start:i], start, i))
        elif match := _IDENT.match(text, i):
            i = match.end()
            result.append(('ident', match[0], start, i))
        else:
            i += 2 if text[i:i + 2] in ('<>', '!=', '<=', '>=') else 1
            result.append(('symbol', text[start:i], start, i))
    return result


def _column(token):
    if token[0] != 'ident':
        return None
    value = token[1]
    if value.startswith(('"', '`', '[')):
        value = value[1:-1]
    return value.lower()


def _in_columns(tokens):
    """Index complete literal-only IN lists once, including long history predicates."""
    result = {}
    for at, token in enumerate(tokens):
        if at < 2 or token[1] != '(' or tokens[at - 1][1].lower() != 'in':
            continue
        col = at - 2
        if tokens[col][1].lower() == 'not':
            col -= 1
        column = _column(tokens[col]) if col >= 0 else None
        if not column:
            continue
        members, pos, want_value = [], at + 1, True
        while pos < len(tokens):
            current = tokens[pos]
            if want_value and current[0] == 'str':
                members.append(pos)
                want_value = False
            elif not want_value and current[1] == ',':
                want_value = True
            elif not want_value and current[1] == ')':
                result.update((index, column) for index in members)
                break
            else:
                break
            pos += 1
    return result


def _compared_column(tokens, index, in_columns):
    operators = {'=', '<>', '!=', 'like', 'ilike'}
    if index >= 2 and tokens[index - 1][1].lower() in operators:
        return _column(tokens[index - 2])
    if index in in_columns:
        return in_columns[index]
    if index + 2 < len(tokens) and tokens[index + 1][1].lower() in operators:
        at = index + 2
        while at + 2 < len(tokens) and tokens[at + 1][1] == '.' and _column(tokens[at + 2]):
            at += 2
        return _column(tokens[at])
    return None


def _literal_column(tokens, match, positions, in_columns):
    index = positions.get((match.start(), match.end()))
    return _compared_column(tokens, index, in_columns) if index is not None else None


def _business_value(column, literal):
    if not column:
        return False
    if _PLACE_COLUMN.search(column) or _LABEL_COLUMN.fullmatch(column):
        return True
    return bool(_ORG_COLUMN.fullmatch(column) and _ORG_END.search(literal.strip("'%")))


def full_name_hit(text: str, *, sql=False):
    """Keep unknown names private; prove business values only from simple SQL context."""
    tokens = _sql_tokens(text or '') if sql else []
    positions = {(token[2], token[3]): i for i, token in enumerate(tokens) if token[0] == 'str'}
    in_columns = _in_columns(tokens) if sql else {}
    for match in PII["full_name"].finditer(text or ""):
        column = _literal_column(tokens, match, positions, in_columns) if sql else None
        if column and _PERSON_VALUE_COLUMN.search(column):
            return match.group(0)
        if _FULL_NAME_OK.fullmatch(match.group(0)) or sql and _business_value(column, match.group(0)):
            continue
        return match.group(0)
    return None


def pii_reason(sql: str):
    for name, pat in PII_RULES:
        if name == "full_name":
            if full_name_hit(sql, sql=True):
                return name
        elif pat.search(sql or ""):
            return name
    return None

