# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Snowflake `CREATE SEMANTIC VIEW` DDL -> the native semantic model shape `semantic_view` reads.

PROVENANCE. Written for this repo; not ported. A third spelling of the same object, so it is a
front end and nothing more: it rewrites the DDL into the dict `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW`
would have returned (`tables`, `relationships`, `verified_queries`) and hands that to the
existing reader. No fact, relationship or measure is interpreted here that the YAML path does not
interpret the same way.

WHAT IS READ, by clause:

  * `TABLES` — `alias AS DB.SCHEMA.OBJECT [PRIMARY KEY (..)] [UNIQUE (..)] [WITH SYNONYMS ..]
    [COMMENT = '..']`. The alias is the logical name; without one, the object name is.
  * `RELATIONSHIPS` — `[name AS] left (cols) REFERENCES right [(cols)]`. A reference with no
    columns means the right table's primary key, as Snowflake defines it. The declared type is
    one-to-one when the left columns ARE the left table's primary key, many-to-one otherwise:
    a reference always points at a key, so those are the only two it can be. Every column pair
    is kept, in order. A column list holding `BETWEEN .. AND ..` or `ASOF` is a RANGE or AS-OF
    relationship: it joins on a condition, so it is kept as one (`relationship_kind`,
    `condition` verbatim) and never read as column names.
  * `FACTS`, `DIMENSIONS`, `METRICS` — `[PRIVATE|PUBLIC] table.name AS expr [WITH SYNONYMS ..]
    [COMMENT = '..']`. The `table.` prefix places the entry on its logical table; a metric with
    no prefix is a view-level (derived) metric. A prefix naming no table refuses. `PRIVATE`
    is kept as the native `access_modifier: private_access`.
  * `AI_VERIFIED_QUERIES` — `name AS (QUESTION '..' SQL '..' [VERIFIED_BY ..] ..)`, emitted as
    native `verified_queries`. The reader reports them; it does not publish them.
  * `COMMENT`, `AI_SQL_GENERATION`, `AI_QUESTION_CATEGORIZATION` — kept under `instructions`, so
    a caller can see them and the reader can say they were not carried.

Comments (`--`, `/* */`) are stripped outside string literals; `''` inside a literal is an
escaped quote. A clause this module does not know is refused by name rather than skipped, so a
newer DDL feature is never silently lost.

Deterministic, and it never executes or connects to anything.
"""
from __future__ import annotations

import re
from typing import Any


class SemanticViewDDLError(ValueError):
    pass


_HEAD = re.compile(r"^\s*CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?SEMANTIC\s+VIEW\s+"
                   r"(?:IF\s+NOT\s+EXISTS\s+)?([\w$.\"]+)", re.I)
_LISTS = ("TABLES", "RELATIONSHIPS", "FACTS", "DIMENSIONS", "METRICS", "AI_VERIFIED_QUERIES")
_TEXTS = ("COMMENT", "AI_SQL_GENERATION", "AI_QUESTION_CATEGORIZATION")


def is_ddl(text: str) -> bool:
    """Detection only. A malformed DDL is still a DDL: `to_native` says what is wrong."""
    try:
        text = _strip_comments(text)
    except SemanticViewDDLError:
        text = re.sub(r"(?m)^\s*--.*$", "", text)
    return bool(_HEAD.match(text))


def _strip_comments(text: str) -> str:
    out, i, n, q = [], 0, len(text), False
    while i < n:
        c = text[i]
        if q:
            out.append(c)
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    out.append("'")
                    i += 1
                else:
                    q = False
        elif c == "'":
            q = True
            out.append(c)
        elif text.startswith("--", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
            continue
        else:
            out.append(c)
        i += 1
    if q:
        raise SemanticViewDDLError("unterminated string literal")
    return "".join(out)


def _literal(s: str, i: int) -> tuple[str, int]:
    """The string literal starting at s[i] == "'" -> (value, index after it)."""
    j, buf = i + 1, []
    while j < len(s):
        if s[j] == "'":
            if j + 1 < len(s) and s[j + 1] == "'":
                buf.append("'")
                j += 2
                continue
            return "".join(buf), j + 1
        buf.append(s[j])
        j += 1
    raise SemanticViewDDLError("unterminated string literal")


def _group(s: str, i: int) -> tuple[str, int]:
    """The parenthesised group starting at s[i] == '(' -> (inner text, index after it)."""
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "'":
            _, j = _literal(s, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise SemanticViewDDLError("unbalanced parentheses")


def _split(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` at depth 0, outside literals."""
    parts, buf, depth, j = [], [], 0, 0
    while j < len(s):
        c = s[j]
        if c == "'":
            _, k = _literal(s, j)
            buf.append(s[j:k])
            j = k
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        j += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _find_kw(s: str, pattern: str) -> re.Match | None:
    """First match of `pattern` at depth 0 and outside literals."""
    rx = re.compile(pattern, re.I)
    depth, j = 0, 0
    while j < len(s):
        c = s[j]
        if c == "'":
            _, j = _literal(s, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and (j == 0 or not (s[j - 1].isalnum() or s[j - 1] == "_")):
            m = rx.match(s, j)
            if m:
                return m
        j += 1
    return None


def _trailing(entry: str) -> tuple[str, list[str], str | None]:
    """Split `<head> [WITH SYNONYMS [=] (..)] [COMMENT = '..']` -> (head, synonyms, comment)."""
    synonyms: list[str] = []
    comment: str | None = None
    cut = len(entry)
    m = _find_kw(entry, r"WITH\s+SYNONYMS\s*=?\s*\(")
    if m:
        inner, _ = _group(entry, m.end() - 1)
        synonyms = [_literal(x, 0)[0] for x in _split(inner) if x.startswith("'")]
        cut = min(cut, m.start())
    m = _find_kw(entry, r"COMMENT\s*=\s*'")
    if m:
        comment, _ = _literal(entry, m.end() - 1)
        cut = min(cut, m.start())
    return entry[:cut].strip(), synonyms, comment


def _ident(s: str) -> str:
    return s.strip().strip('"')


def _cols(s: str) -> list[str]:
    return [_ident(c) for c in _split(s)]


def _clauses(body: str) -> dict[str, Any]:
    """Top-level clauses after the view name, in any order. Unknown ones refuse by name."""
    out: dict[str, Any] = {}
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in " \t\r\n;":
            i += 1
        if i >= n:
            break
        m = re.compile(r"COPY\s+GRANTS", re.I).match(body, i)
        if m:
            i = m.end()
            continue
        m = re.compile(r"([A-Z_]+)", re.I).match(body, i)
        if not m:
            raise SemanticViewDDLError(f"unexpected text in the DDL: {body[i:i + 30]!r}")
        kw, i = m.group(1).upper(), m.end()
        while i < n and body[i] in " \t\r\n=":
            i += 1
        if kw in _LISTS and i < n and body[i] == "(":
            inner, i = _group(body, i)
            out[kw] = _split(inner)
        elif kw in _TEXTS and i < n and body[i] == "'":
            out[kw], i = _literal(body, i)
        else:
            raise SemanticViewDDLError(f"unsupported semantic view clause {kw!r}")
    return out


def _table(entry: str) -> dict[str, Any]:
    head, synonyms, comment = _trailing(entry)
    m = re.match(r"^(?:([\w$\"]+)\s+AS\s+)?([\w$.\"]+)\s*(.*)$", head, re.I | re.S)
    if not m:
        raise SemanticViewDDLError(f"unreadable TABLES entry: {head[:60]!r}")
    alias, target, rest = m.group(1), m.group(2), m.group(3)
    parts = [_ident(p) for p in target.split(".")]
    if len(parts) != 3:
        raise SemanticViewDDLError(f"table {target!r} is not DB.SCHEMA.OBJECT")
    t: dict[str, Any] = {"name": _ident(alias) if alias else parts[2],
                         "base_table": {"database": parts[0], "schema": parts[1],
                                        "table": parts[2]}}
    pk = _find_kw(rest, r"PRIMARY\s+KEY\s*\(")
    if pk:
        t["primary_key"] = {"columns": _cols(_group(rest, pk.end() - 1)[0])}
    if synonyms:
        t["synonyms"] = synonyms
    if comment:
        t["description"] = comment
    return t


_CONDITION = re.compile(r"\b(BETWEEN|ASOF)\b", re.I)


def _relationship(entry: str, pks: dict[str, list[str]]) -> dict[str, Any]:
    m = re.match(r"^(?:([\w$\"]+)\s+AS\s+)?([\w$\"]+)\s*\(([^)]*)\)\s*REFERENCES\s+([\w$\"]+)"
                 r"\s*(?:\(([^)]*)\))?\s*$", entry.strip(), re.I | re.S)
    if not m:
        raise SemanticViewDDLError(f"unreadable RELATIONSHIPS entry: {entry[:60]!r}")
    left, right = _ident(m.group(2)), _ident(m.group(4))
    for t in (left, right):
        if t.lower() not in pks:
            raise SemanticViewDDLError(f"relationship names table {t!r}, which TABLES does "
                                       f"not declare")
    name = _ident(m.group(1)) if m.group(1) else f"{left}_to_{right}"
    # A RANGE OR AS-OF relationship joins on a condition. Its column lists are not pairs of
    # equal columns, and splitting `BETWEEN a AND b EXCLUSIVE` on commas reads keywords as
    # column names. It is kept whole, by kind, for a reader to refuse by name.
    cond = _CONDITION.search(f"{m.group(3)} {m.group(5) or ''}")
    if cond:
        lcols = [c for c in _cols(m.group(3)) if not _CONDITION.search(c)]
        return {"name": name, "left_table": left, "right_table": right,
                "relationship_kind": "range" if cond.group(1).upper() == "BETWEEN" else "asof",
                "condition": " ".join(f"({m.group(3).strip()}) REFERENCES {right} "
                                      f"({(m.group(5) or '').strip()})".split()),
                "relationship_columns": [{"left_column": lcols[0] if lcols else
                                          _cols(m.group(3))[0],
                                          "right_column": re.findall(
                                              r"[A-Za-z_][\w$]*",
                                              _CONDITION.sub(" ", m.group(5) or right))[0]}],
                "relationship_type": "many_to_one"}
    lcols = _cols(m.group(3))
    rcols = _cols(m.group(5)) if m.group(5) else pks[right.lower()]
    if len(lcols) != len(rcols) or not rcols:
        raise SemanticViewDDLError(f"relationship {left} -> {right}: column lists do not pair")
    one = {c.lower() for c in lcols} == {c.lower() for c in pks[left.lower()]}
    return {"name": name, "left_table": left, "right_table": right,
            "relationship_columns": [{"left_column": a, "right_column": b}
                                     for a, b in zip(lcols, rcols)],
            "relationship_type": "one_to_one" if one else "many_to_one"}


def _item(entry: str) -> tuple[str | None, dict[str, Any]]:
    head, synonyms, comment = _trailing(entry)
    m = re.match(r"^(?:(PRIVATE|PUBLIC)\s+)?(?:([\w$\"]+)\.)?([\w$\"]+)\s+AS\s+(.+)$",
                 head, re.I | re.S)
    if not m:
        raise SemanticViewDDLError(f"unreadable entry: {head[:60]!r}")
    item: dict[str, Any] = {"name": _ident(m.group(3)), "expr": m.group(4).strip()}
    if (m.group(1) or "").upper() == "PRIVATE":
        item["access_modifier"] = "private_access"
    if synonyms:
        item["synonyms"] = synonyms
    if comment:
        item["description"] = comment
    return (_ident(m.group(2)) if m.group(2) else None), item


def _verified(entry: str) -> dict[str, Any]:
    m = re.match(r"^([\w$\"]+)\s+AS\s*\(", entry.strip(), re.I)
    if not m:
        raise SemanticViewDDLError(f"unreadable AI_VERIFIED_QUERIES entry: {entry[:60]!r}")
    inner, _ = _group(entry.strip(), m.end() - 1)
    q: dict[str, Any] = {"name": _ident(m.group(1))}
    i = 0
    while i < len(inner):
        k = re.compile(r"\s*([A-Z_]+)\s*", re.I).match(inner, i)
        if not k or k.end() >= len(inner) + 1:
            break
        key, i = k.group(1).lower(), k.end()
        if i < len(inner) and inner[i] == "'":
            q[key], i = _literal(inner, i)
        else:
            v = re.compile(r"([^\s']+)").match(inner, i)
            if not v:
                break
            q[key], i = v.group(1), v.end()
    if not (q.get("question") and q.get("sql")):
        raise SemanticViewDDLError(f"verified query {q['name']!r} has no QUESTION or SQL")
    return q


def to_native(text: str) -> dict[str, Any]:
    """DDL text -> the native semantic model dict, plus `instructions` the reader reports."""
    clean = _strip_comments(text)
    head = _HEAD.match(clean)
    if not head:
        raise SemanticViewDDLError("not a CREATE SEMANTIC VIEW statement")
    view = _ident(head.group(1).split(".")[-1])
    clauses = _clauses(clean[head.end():])
    if not clauses.get("TABLES"):
        raise SemanticViewDDLError("the semantic view declares no TABLES")

    tables = [_table(e) for e in clauses["TABLES"]]
    by_name = {t["name"].lower(): t for t in tables}
    pks = {k: (t.get("primary_key") or {}).get("columns", []) for k, t in by_name.items()}
    view_metrics: list[dict] = []
    for clause, key in (("FACTS", "facts"), ("DIMENSIONS", "dimensions"), ("METRICS", "metrics")):
        for e in clauses.get(clause) or []:
            owner, item = _item(e)
            if owner is None and clause == "METRICS":       # a view-level (derived) metric
                view_metrics.append(item)
                continue
            t = by_name.get((owner or "").lower())
            if t is None:
                raise SemanticViewDDLError(f"{clause.lower()} entry {owner}.{item['name']} "
                                           f"names a table TABLES does not declare")
            t.setdefault(key, []).append(item)

    doc: dict[str, Any] = {
        "name": view,
        "tables": tables,
        "relationships": [_relationship(e, pks) for e in clauses.get("RELATIONSHIPS") or []],
        "instructions": {k.lower(): clauses[k] for k in _TEXTS if k in clauses},
    }
    if clauses.get("COMMENT"):
        doc["description"] = clauses["COMMENT"]
    if view_metrics:
        doc["metrics"] = view_metrics
    if clauses.get("AI_VERIFIED_QUERIES"):
        doc["verified_queries"] = [_verified(e) for e in clauses["AI_VERIFIED_QUERIES"]]
    return doc
