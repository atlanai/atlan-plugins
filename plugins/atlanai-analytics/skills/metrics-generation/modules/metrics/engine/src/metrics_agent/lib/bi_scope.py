# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The warehouse tables a BI model reads, from its own lineage.

ONE DEFINITION, TWO CALLERS. S0 needs the set before lane H runs (H0 admits a query only when
it reads a bounded table, and a BI + history run names no tables by hand), and the lakehouse
fetch needs the same set to decide which of a tenant's queries are about this model at all.
Two derivations would drift; so both call this.

The rule is A2's: a table counts when its partition resolved to a warehouse object
(`source_lineage.object`) or its name is already a three-part `db.schema.table`. A table the
model defines in DAX or M with no warehouse target is not a source table and never enters the
set — it has no queries to find. Every object a table's source READS (`source_lineage.reads`,
filled by the reader: the tables of a join, the tables a SQL statement source names) counts
too: a statement source names no object of its own, and without them a model built on one
bounded nothing.
"""
from __future__ import annotations

import pathlib


def parse(path: pathlib.Path, vendor: str, model: str | None = None, *,
          dialect: str = "") -> dict:
    """The `bi_facts` document for one BI model, by the same registered reader A1 would use.
    A path the reader says holds several models (its `models` hook) gives the tables of every
    one of them: joins do not change which tables a model reads. `model` is the BI input's
    `bi_model` setting and `dialect` the run's, each handed to the reader as A1 hands it."""
    from metrics_agent import readers as _readers  # noqa: PLC0415 — reader deps are heavy
    from metrics_agent import sockets as S  # noqa: PLC0415

    try:
        paths = _readers.models(_readers.load(vendor), pathlib.Path(path)) or [pathlib.Path(path)]
        opts: dict = {"dialect": dialect} if dialect else {}
        if model:
            opts["model"] = model
        docs = [_readers.parse(p, vendor, **opts) for p in paths]
    except (_readers.ReaderError, S.ReaderRefused) as exc:
        raise SystemExit(f"bi_scope: {exc}") from None
    if len(docs) == 1:
        return docs[0]
    return {"tables": [t for d in docs for t in d.get("tables") or []]}


def warehouse_tables(facts: dict, qualify=None) -> list[str]:
    """Sorted, de-duplicated `DB.SCHEMA.TABLE` names the model's tables resolve to. `qualify`
    is the run's SQL-check plug saying how its estate names a table in full (a name the plug
    leaves short is not a table it can check)."""
    out = set()

    def full(x: str) -> str:
        return str(qualify(x) if qualify else x).upper()
    for t in facts.get("tables") or []:
        # By the time a parser returns, a resolved partition has rewritten `name` to the
        # physical target; `source_lineage.object` may be that same name or only its leaf.
        lin = t.get("source_lineage") or {}
        obj = lin.get("object") or ""
        # A table that is a STATEMENT is known by the reader's own name, which no warehouse
        # table has: the tables it reads are in `reads`.
        name = "" if lin.get("statement") else (t.get("name") or "")
        for cand in (name, obj):
            if full(cand).count(".") >= 2:
                out.add(full(cand))
                break
        # Every object the source READS: a join's other tables, a statement's tables.
        out.update(full(str(r)) for r in lin.get("reads") or [] if full(str(r)).count(".") >= 2)
    return sorted(out)


def tables_of(path: pathlib.Path, vendor: str, model: str | None = None, *,
              dialect: str = "", qualify=None) -> list[str]:
    return warehouse_tables(parse(path, vendor, model, dialect=dialect), qualify)
