# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""One copy of each BI native query per prompt bundle.

A statement compiled over a BI table that is a native query carries that query whole as its
derived table, so every widget on the table repeats it: measured on one report, 13 copies of
a 3 KB query in each of four bundles, nearly half of what the model read. The bundle keeps one
copy under `shared_sources` and each statement names it where the query stood. Nothing the
model is shown is lost, and nothing it answers is read against the bundle: every gate checks
its answer against the statements in the run, never against this text.
"""
from __future__ import annotations

import json
import pathlib

# A query shorter than this costs less to repeat than to look up.
MIN_CHARS = 400
SKIP_KEYS = ("derived_source",)     # text the author copies as written stays whole


def native_bodies(run: pathlib.Path) -> list[str]:
    """Every native query the run's BI units read, as the compiler embeds it."""
    path = pathlib.Path(run) / "question_units.json"
    if not path.is_file():
        return []
    seen: dict[str, None] = {}
    for u in json.loads(path.read_text()).get("units") or []:
        sql = str(((u.get("native_source") or {}).get("sql")) or "").strip().rstrip(";")
        if len(sql) >= MIN_CHARS:
            seen.setdefault(sql, None)
    # The longest first, so a query that contains another is replaced whole.
    return sorted(seen, key=len, reverse=True)


def _strings(node, skip=False):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, skip or k in SKIP_KEYS)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, skip)
    elif isinstance(node, str) and not skip:
        yield node


def _replace(node, table: dict[str, str]):
    if isinstance(node, dict):
        return {k: (v if k in SKIP_KEYS else _replace(v, table)) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace(v, table) for v in node]
    if isinstance(node, str):
        for body, ref in table.items():
            if body in node:
                node = node.replace(body, ref)
        return node
    return node


def factor(doc: dict, run: pathlib.Path) -> dict:
    """`doc` with each native query that appears in it more than once replaced by
    `<shared_sources.Sn>`, and `shared_sources` holding each once. Unchanged when none
    repeats."""
    bodies = native_bodies(run)
    if not bodies:
        return doc
    text = list(_strings(doc))
    shared = [b for b in bodies if sum(t.count(b) for t in text) > 1]
    if not shared:
        return doc
    table = {b: f"<shared_sources.S{i + 1}>" for i, b in enumerate(shared)}
    out = _replace(doc, table)
    out["shared_sources"] = {ref[len("<shared_sources."):-1]: body for body, ref in table.items()}
    if isinstance(out.get("instruction"), str):
        out["instruction"] += (" A statement's SQL that shows <shared_sources.Sn> reads the "
                               "query held under that key in `shared_sources`, in that place.")
    return out


def rewrite(path: pathlib.Path, schema_name: str, run: pathlib.Path, **dump_kwargs) -> None:
    """Factor a prompt bundle already written at `path`, validated against its schema again."""
    from metrics_agent.lib import artifacts  # noqa: PLC0415

    path = pathlib.Path(path)
    doc = json.loads(path.read_text())
    out = factor(doc, run)
    if out is not doc:
        artifacts.dump(path, out, schema_name, **dump_kwargs)


def expand(text, shared: dict | None):
    """`text` with each `<shared_sources.Sn>` put back: for an ingest that reads its own
    bundle and compares with the statements in the run."""
    if not isinstance(text, str) or not shared:
        return text
    for key, body in shared.items():
        text = text.replace(f"<shared_sources.{key}>", body)
    return text
