# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A semantic view's verified queries, compiled to plain SQL over its base tables.

PROVENANCE. Written for this repo; not ported. The reader's (`semantic_view.py`)
vendor front end for one clause: a Snowflake semantic view's `AI_VERIFIED_QUERIES` are written
against the view itself (`SELECT ... FROM SEMANTIC_VIEW(<view> METRICS .. DIMENSIONS ..
WHERE ..)`), so they run only where the view is deployed. Each becomes a declared EXAMPLE of
the metrics it reads (`measures[].declared.examples`), never a saved query.

HOW A CALL COMPILES, on the model `lib/declared` reads (the same one A3 places metrics in):

  * every table is aliased by its ROLE, so a metric's or dimension's own SQL runs as written,
    and two roles over one table are two joins;
  * a metric is named `table.metric`, or by its bare name when that names exactly one metric
    (a view-level metric has no table prefix); every metric of one call is computed over one
    table, which is the FROM;
  * a dimension or fact named in DIMENSIONS or WHERE is replaced by what it stands for, and so
    is every name inside it;
  * every other table is joined along declared relationships that never fan out (many-to-one
    forwards, one-to-one either way), on every key column, as LEFT JOINs;
  * dimensions are grouped by position; output columns take the view's names, as the view
    returns them, so SQL around the call (ORDER BY, CROSS JOIN) still reads.

Nothing is guessed: a call naming an unknown metric or dimension, metrics of two tables, FACTS,
or a table no such path reaches is not compiled, and the reason is returned.

Deterministic, and it never executes or connects to anything.
"""
from __future__ import annotations

import re

from metrics_agent.lib import declared as _declared

_CALL = re.compile(r"SEMANTIC_VIEW\s*\(", re.I)
_PARTS = re.compile(r"\b(METRICS|DIMENSIONS|FACTS)\b(.*?)(?=\b(?:METRICS|DIMENSIONS|FACTS)\b|$)",
                    re.I | re.S)


class NotCompiled(ValueError):
    pass


def _call_body(sql: str, start: int) -> tuple[str, int]:
    depth, j = 0, start
    for is_code, text in _declared.outside_literals(sql[start:]):
        if not is_code:
            j += len(text)
            continue
        for ch in text:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return sql[start + 1:j], j + 1
            j += 1
    raise NotCompiled("unbalanced SEMANTIC_VIEW( call")


def _split(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _ref(ref: str) -> tuple[str | None, str]:
    parts = [p.strip().strip('"') for p in ref.split(".")]
    if len(parts) == 1:
        return None, parts[0].lower()
    if len(parts) == 2:
        return parts[0].lower(), parts[1].lower()
    raise NotCompiled(f"{ref!r} is not `table.name` or `name`")


class _Call:
    """One SEMANTIC_VIEW(...) call, resolved against the model."""

    def __init__(self, model: _declared.Model, body: str):
        self.model = model
        m = re.match(r"\s*([\w$.\"]+)\s+(.*)$", body, re.S)
        if not m:
            raise NotCompiled("SEMANTIC_VIEW call names no view")
        rest = m.group(2)
        w = re.search(r"\bWHERE\b", rest, re.I)
        self.where = rest[w.end():].strip() if w else ""
        rest = rest[:w.start()] if w else rest
        parts = {"METRICS": "", "DIMENSIONS": "", "FACTS": ""}
        for kw, val in _PARTS.findall(rest):
            parts[kw.upper()] = val
        if _split(parts["FACTS"]):
            raise NotCompiled("FACTS in a query call are not compiled")
        self.metrics = [self._metric(r) for r in _split(parts["METRICS"])]
        self.dimensions = [self._dimension(r) for r in _split(parts["DIMENSIONS"])]
        homes = {self.model.items[k].home for k in self.metrics}
        if len(homes) != 1 or None in homes:
            raise NotCompiled(f"metrics from {len(homes)} tables in one call")
        self.home = homes.pop()

    def _metric(self, ref: str) -> tuple:
        role, name = _ref(ref)
        if role is not None:
            key = (role, name)
            if self.model.items.get(key) is None or self.model.items[key].kind != "measure":
                raise NotCompiled(f"unknown metric {ref}")
            return key
        # A bare name: a view-level metric, or a table's metric no other table also names.
        hits = [k for k, it in self.model.items.items()
                if it.kind == "measure" and k[1] == name]
        if len(hits) != 1:
            raise NotCompiled(f"metric {ref} names {'no' if not hits else len(hits)} metric"
                              f"{'' if len(hits) == 1 else 's'} of the view")
        return hits[0]

    def _dimension(self, ref: str) -> tuple:
        role, name = _ref(ref)
        if role is None:
            hits = [(k, c) for k, r in self.model.roles.items() for c in r.columns
                    if str(c["name"]).lower() == name]
            if len(hits) != 1:
                raise NotCompiled(f"dimension {ref} names {len(hits) or 'no'} column(s) of "
                                  f"the view")
            role = hits[0][0]
        r = self.model.roles.get(role)
        if r is None or not any(str(c["name"]).lower() == name for c in r.columns):
            raise NotCompiled(f"unknown dimension {ref}")
        return (role, name)

    def sql_of(self, key: tuple) -> str:
        item = self.model.items.get(key)
        if item is not None:
            return self.model.expand(item.sql, self.home)
        r = self.model.roles[key[0]]
        c = next(c for c in r.columns if str(c["name"]).lower() == key[1])
        return f"{key[0]}.{c.get('source_column') or c['name']}"

    def where_refs(self) -> list[tuple]:
        """The dimensions WHERE names, by (role, name)."""
        out = []
        for is_code, text in _declared.outside_literals(self.where):
            if not is_code:
                continue
            for m in re.finditer(r'(?<![\w.$"])"?([A-Za-z_][\w$]*)"?\s*\.\s*"?([A-Za-z_][\w$]*)"?'
                                 r'(?![\w$"(])', text):
                role, name = m.group(1).lower(), m.group(2).lower()
                r = self.model.roles.get(role)
                if r is not None and any(str(c["name"]).lower() == name and
                                         c.get("role") == "dimension" for c in r.columns):
                    out.append((role, name))
        return out

    def compile(self) -> str:
        model = self.model
        dims = [(k, self.sql_of(k)) for k in self.dimensions]
        mets = [(k, self.sql_of(k)) for k in self.metrics]
        where = model.expand(self.where, self.home) if self.where else ""
        roles = [q for _, s in dims + mets for q in _declared.qualifiers(s)]
        roles += _declared.qualifiers(where)
        unknown = sorted({q for q in roles if q not in model.roles})
        if unknown:
            raise NotCompiled(f"reads {unknown}, which are not tables of the view")
        try:
            hops = model.place(self.home, roles)
        except _declared.NotPlaced as exc:
            raise NotCompiled(str(exc)) from None
        names = {r: r for r in model.roles}
        lines = ["SELECT " + ",\n       ".join(
            [f"{_declared.requalify(s, names)} AS {k[1]}" for k, s in dims]
            + [f"{_declared.requalify(s, names)} AS {k[1]}" for k, s in mets]),
            f"FROM {model.roles[self.home].physical} AS {self.home}"]
        for e in hops:
            on = " AND ".join(f"{e.src}.{sc} = {e.dst}.{dc}" for sc, dc in e.pairs)
            lines.append(f"LEFT JOIN {model.roles[e.dst].physical} AS {e.dst} ON {on}")
        if where:
            lines.append(f"WHERE {_declared.requalify(where, names)}")
        if dims:
            lines.append("GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims))))
        return "\n".join(lines)


def compile_query(model: _declared.Model, sql: str) -> tuple[str, list[_Call]]:
    """A verified query with every SEMANTIC_VIEW(...) call compiled in place -> (sql, calls)."""
    out, calls, i = [], [], 0
    for m in _CALL.finditer(sql):
        if m.start() < i:
            continue
        body, end = _call_body(sql, m.end() - 1)
        call = _Call(model, body)
        out.append(sql[i:m.start()])
        out.append("(\n" + call.compile() + "\n)")
        calls.append(call)
        i = end
    if not calls:
        raise NotCompiled("no SEMANTIC_VIEW call in the query")
    out.append(sql[i:])
    return "".join(out).strip(), calls


def compile_all(model: _declared.Model, queries: list[dict]) -> tuple[list[dict], list[tuple]]:
    """(examples for lib/declared, [(name, why it did not compile)]). Every query is kept: one
    that did not compile has `sql: None`, so the count is always the view's."""
    examples, failed = [], []
    for q in queries:
        name = str(q.get("name") or "")
        rec = {"name": name, "question": q.get("question"), "sql": None,
               "metrics": [], "dimensions": [], "refs": []}
        try:
            rec["sql"], calls = compile_query(model, str(q.get("sql") or ""))
        except (NotCompiled, _declared.NotPlaced) as exc:
            failed.append((name, str(exc)))
        else:
            for c in calls:
                rec["metrics"] += [k for k in c.metrics if k not in rec["metrics"]]
                rec["dimensions"] += [k for k in c.dimensions if k not in rec["dimensions"]]
                rec["refs"] += [k for k in c.dimensions + c.where_refs()
                                if k not in rec["refs"]]
        examples.append(rec)
    return examples, failed
