# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Literals withheld from every artifact: a person's address or name in a BI file.

A1 scrubs every `pii.redact_patterns` match out of what a BI reader read (guardrail 8). A
formula or a filter that COMPARES with such a literal (`[Owner] = 'someone@example.com'`) is
still a real question about real rows, so the literal is not dropped: it is NUMBERED. Each
distinct value becomes the marker `<redacted:N>` (N by first occurrence, in the order A1 reads
the models), the same value the same N everywhere. No artifact holds the value or a digest of
it; the numbering exists only while a stage holds the BI files in memory.

The compiled statement reads the literal as a bind placeholder (`:withheld_N`, spelled by the
`op.withheld_bind` rule row per dialect). Whoever runs the statement binds it to the value the
BI file holds. Verification (A4) does exactly that, in memory: it reads the run's BI inputs
again through the same readers and the same scrub (`recover`), binds, executes, masks every
recovered value out of the verdict, and drops them.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

from . import pii as _pii

# Text a PERSON wrote about the model (and the literals inside its formulas): every
# `pii.redact_patterns` pattern applies. Everywhere else a string is a NAME or an IDENTITY — a
# visual's GUID, an item's directory, a table — and a UUID there is the identity itself, so
# only the patterns that cannot match a UUID (an email) apply.
_UUID_SAMPLE = "00000000-0000-4000-8000-000000000000"
PROSE_KEYS = frozenset({
    "title", "subtitle", "description", "tooltip", "caption", "label", "prose", "rows",
    "cols", "annotations", "text", "formula", "raw_expression", "value", "default",
    "evidence", "resolved_branch", "number_format", "default_format", "native_sql",
    # A declared model's example questions, and the SQL beside them and its dimensions': a
    # person wrote both, literals included.
    "question", "sql"})

MARKER = "<redacted:{n}>"
MARKER_RE = re.compile(r"<redacted:(\d+)>")
# What a bind placeholder is called for withheld literal N (`of` on a candidate), and the
# rule row that spells it per dialect.
OF = "withheld_{n}"
RULE = "op.withheld_bind"


class Numbering:
    """Distinct withheld values, numbered by first occurrence. Held in memory only."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def marker(self, value: str) -> str:
        n = self.values.setdefault(value, len(self.values) + 1)
        return MARKER.format(n=n)

    def by_of(self) -> dict[str, str]:
        """{`withheld_N`: value}: what a statement's placeholders bind to."""
        return {OF.format(n=n): v for v, n in self.values.items()}


def scrub_prose(node, scrubber, key: str = "", numbering: Numbering | None = None):
    """`node` with PII redacted in place of every string it holds (guardrail 8: scrub always).

    The BI lane's prose is carried VERBATIM on purpose — it is the context C3 reads and the
    containment of an instruction in it is structural — but verbatim never meant a person's
    email or a ticket's UUID reaches an artifact. The words stay; the literal does not. Each
    distinct value becomes a numbered marker (`numbering`), so a name used in two places is
    still the same name afterwards and a statement can bind it again; without a numbering
    every match is the plain `<redacted>`."""
    if isinstance(node, dict):
        return {k: scrub_prose(v, scrubber, k, numbering) for k, v in node.items()}
    if isinstance(node, list):
        return [scrub_prose(v, scrubber, key, numbering) for v in node]
    if not isinstance(node, str):
        return node
    out = node
    for pat in scrubber.patterns:
        if key not in PROSE_KEYS and pat.search(_UUID_SAMPLE):
            continue
        if numbering is None:
            out = pat.sub(_pii.PLACEHOLDER, out)
        else:
            out = pat.sub(lambda m: numbering.marker(m.group(0)), out)
    return out


def model_sources(bi: dict) -> list[dict]:
    """The run's BI inputs (run_inputs `bi`), a path the reader says holds SEVERAL models
    read as one model each: a model's names (tables, metrics) are its own, so two models
    under one path are two models, prefixed by `<plug>/<model file>`. A reader that declares
    no `models` reads its path as one model. Raises `readers.ReaderError`."""
    from metrics_agent import readers as _readers  # noqa: PLC0415 - the registry loads plugs

    sources = bi.get("sources") or [{"plug": None, "path": bi["path"], "vendor": bi["vendor"]}]
    out = []
    for src in sources:
        files = _readers.models(_readers.load(src["vendor"]), pathlib.Path(src["path"]))
        if len(files) <= 1:
            out.append(src if not files else {**src, "path": str(files[0])})
            continue
        plug = src.get("plug") or "bi"
        out += [{**src, "path": str(f), "plug": f"{plug}/{f.stem}"} for f in files]
    return out


def recover(ri: dict, policy: Any) -> dict[str, str]:
    """{`withheld_N`: value} for the run's BI inputs, read again exactly as A1 read them
    (the same readers, the same order, the same scrub). IN MEMORY ONLY: the caller binds the
    values into a statement it executes and drops them; never written, logged or recorded."""
    from metrics_agent import readers as _readers  # noqa: PLC0415

    bi = ri.get("bi")
    if not bi:
        return {}
    numbering = Numbering()
    scrubber = _pii.Scrubber(policy)
    for src in model_sources(bi):
        raw = _readers.parse(pathlib.Path(src["path"]), src["vendor"],
                             dialect=ri.get("dialect") or "", policy=policy,
                             model=src.get("model"))
        raw.pop("_reader_notes", None)
        scrub_prose(raw, scrubber, "", numbering)
    return numbering.by_of()


def _literals(sql: str):
    """(start, end, body) of each single-quoted string literal in `sql` (a doubled quote is
    an escaped one), comments and double-quoted identifiers skipped."""
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif c == "/" and sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == '"':
            j = sql.find('"', i + 1)
            i = n if j < 0 else j + 1
        elif c == "'":
            j, body = i + 1, []
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    body.append("'")
                    j += 2
                elif sql[j] == "'":
                    break
                else:
                    body.append(sql[j])
                    j += 1
            yield i, j + 1, "".join(body)
            i = j + 1
        else:
            i += 1


def bind_markers(sql: str, placeholder) -> tuple[str, list[str]]:
    """`sql` with each string literal that holds a withheld marker read as its bind
    placeholder(s) instead: a literal that IS a marker becomes the placeholder; one that holds
    a marker among other text becomes the CONCAT of its parts. `placeholder(n)` spells the
    placeholder for marker N (None: no form for the dialect, raises ValueError). Returns (the
    statement, the `of` of each placeholder it reads, in order)."""
    out, last, ofs = [], 0, []
    for start, end, body in list(_literals(sql)):
        if not MARKER_RE.search(body):
            continue
        parts, pos = [], 0
        for m in MARKER_RE.finditer(body):
            if m.start() > pos:
                parts.append("'" + body[pos:m.start()].replace("'", "''") + "'")
            ph = placeholder(int(m.group(1)))
            if not ph:
                raise ValueError("no bind placeholder for a withheld literal in this dialect")
            parts.append(ph)
            of = OF.format(n=int(m.group(1)))
            if of not in ofs:
                ofs.append(of)
            pos = m.end()
        if pos < len(body):
            parts.append("'" + body[pos:].replace("'", "''") + "'")
        out.append(sql[last:start])
        out.append(parts[0] if len(parts) == 1 else "CONCAT(" + ", ".join(parts) + ")")
        last = end
    out.append(sql[last:])
    return "".join(out), ofs


def mask(text: Any, values: dict[str, str] | None) -> Any:
    """`text` with every recovered value (any case) replaced by its label `<withheld_N>`."""
    if not isinstance(text, str) or not values:
        return text
    for of, val in sorted(values.items(), key=lambda kv: -len(str(kv[1] or ""))):
        if val:
            text = re.sub(re.escape(str(val)), f"<{of}>", text, flags=re.IGNORECASE)
    return text
