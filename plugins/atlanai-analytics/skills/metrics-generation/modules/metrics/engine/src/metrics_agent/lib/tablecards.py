# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a TABLE is, said in the language of the questions it answered.

Retrieval could reach a table only when a metric card happened to name it in its SQL.
Measured on this estate, the table an answer needs was absent from the retrieved layer for
9 of 19 lost questions, and no ranker built from the question text, the table's name or its
columns put the right table in a top-5 shortlist more than 40% of the time: a column is
called `LAST_CL_PEAK_STAGE_NUMBER` and the question says "peak", a table is called
`FCT_ONBOARDING_ACTIVITY` and the question says "booked or completed".

A table card closes that by making the TABLE a retrievable object described the way people
ask for it — the questions the estate already answered with it, the columns it actually
filters and groups on, and the grains it was read at. Every field is lifted from the verified
query corpus; nothing here reads a sealed answer, a canonical statement or any eval artifact,
and the builder must never be given one.
"""
from __future__ import annotations

import collections
import re
from typing import Any, Iterable

_TBL = re.compile(r"(?:FROM|JOIN)\s+([A-Za-z_][\w.]*\.[\w]+)", re.I)


def tables_of(sql: str) -> set[str]:
    """The fully-qualified tables a statement reads."""
    return {t.upper() for t in _TBL.findall(str(sql or ""))}


def canonicalise(tables: set[str]) -> dict[str, str]:
    """Fold an under-qualified reference into the table it names.

    A statement may write `MARTS.DIM_ACCOUNTS` where the same table appears elsewhere as
    `BENCH_ACME_PROD.MARTS.DIM_ACCOUNTS`. They are one table, and keeping them
    apart splits a card's evidence in two. A short name folds into a longer one ONLY when
    exactly one longer name ends with it — two candidates mean the reference is ambiguous and
    it keeps its own identity rather than being assigned to a guess.
    """
    full = [t for t in tables if t.count(".") >= 2]
    out: dict[str, str] = {}
    for t in tables:
        if t.count(".") >= 2:
            out[t] = t
            continue
        cands = [f for f in full if f.endswith("." + t)]
        out[t] = cands[0] if len(cands) == 1 else t
    return out


def build(objects: Iterable[dict], *, min_queries: int = 1) -> list[dict]:
    """One card per table the corpus actually reads.

    A table nobody has queried gets no card: the card's value IS the usage, and inventing
    one from a bare schema would put a table in the shortlist with nothing to rank it by.
    """
    seen_tables: set[str] = set()
    for o in objects:
        seen_tables |= tables_of(o.get("sql"))
    canon = canonicalise(seen_tables)
    by_table: dict[str, list[dict]] = {}
    for o in objects:
        for t in {canon.get(x, x) for x in tables_of(o.get("sql"))}:
            by_table.setdefault(t, []).append(o)

    cards = []
    for table, users in sorted(by_table.items()):
        if len(users) < min_queries:
            continue
        questions = list(dict.fromkeys(
            str(o.get("question") or "").strip() for o in users if o.get("question")))
        descriptions = list(dict.fromkeys(
            str(o.get("description") or "").strip() for o in users if o.get("description")))
        cols = collections.Counter()
        for o in users:
            for f in (o.get("filters") or []):
                c = str(f.get("column") or "").split(".")[-1].lower()
                if c:
                    cols[c] += 1
            for g in (o.get("group_by") or []):
                c = str(g).split(".")[-1].lower()
                if c and not c.isdigit():
                    cols[c] += 1
        grains = collections.Counter(str(o.get("grain") or "").strip()
                                     for o in users if o.get("grain"))
        partners = collections.Counter()
        for o in users:
            for t in tables_of(o.get("sql")):
                if t != table:
                    partners[t] += 1
        cards.append({
            "table": table,
            "n_queries": len(users),
            # THE SEARCHABLE TEXT. The estate's own words for what it asked of this table —
            # which is the vocabulary a question arrives in, and the one thing a table name
            # and a column list do not carry.
            "questions": questions[:12],
            "descriptions": descriptions[:6],
            "columns_used": [{"column": c, "n": n} for c, n in cols.most_common(20)],
            "grains": [g for g, _ in grains.most_common(4)],
            "joined_with": [t for t, _ in partners.most_common(6)],
            "derived_from": sorted({str(o.get("vq_id")) for o in users if o.get("vq_id")}),
        })
    return cards


_STOP = set("how many what which the a an of is are do we our us by at with as not no now "
            "and or to in on for it its their they got total show me list all per each "
            "have has had was were been be this that there here who whom whose".split())


def _toks(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text).lower())
            if len(w) > 2 and w not in _STOP}


def rank(question: str, cards: list[dict], k: int = 5) -> list[tuple[str, float]]:
    """Rank tables for a question against their cards.

    Scored on the card's QUESTION text first, because that is the register a question
    arrives in; the columns and the table's own name contribute less, since the measured
    failure was exactly that they do not match business language. Usage breaks ties: where
    two tables answer equally well on text, the estate's own preference decides.
    """
    q = _toks(question)
    if not q:
        return []
    out = []
    for c in cards:
        qt = _toks(" ".join(c.get("questions", []) + c.get("descriptions", [])))
        ct = _toks(" ".join(x["column"].replace("_", " ") for x in c.get("columns_used", [])))
        nt = _toks(c["table"].split(".")[-1].replace("_", " "))
        score = (3.0 * len(q & qt) / (len(q) or 1)
                 + 1.0 * len(q & ct) / (len(q) or 1)
                 + 1.5 * len(q & nt) / (len(q) or 1))
        if score:
            out.append((c["table"], score + 0.001 * min(c.get("n_queries", 0), 20)))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out[:k]
