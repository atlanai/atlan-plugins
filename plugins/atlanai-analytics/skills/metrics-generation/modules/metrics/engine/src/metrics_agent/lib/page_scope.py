# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The pages of a report a person chose, and the units that are on them.

WHY A RUN-LEVEL RECORD AND NOT run.yml. A report is fetched (and translated) before the person
says which of its pages matter, and run.yml cannot change once a run has started. So the pick
is its own small record in the run directory, `page_scope.json`, written by whoever asked
(the host's `pages` command). A2 reads it: A2 is where a report's visuals become question
units, so dropping the other pages there is the one place every later stage (the compiler,
its review, the questions, the descriptions, the metric proposals and the cards) sees only
the chosen pages. The runner re-runs A2, and so everything after it, when the record changes.

WHAT IS KEPT. Each chosen page's units, plus the pages those units name themselves, as their
tooltip or a drillthrough button's target (`links`, from the reader), followed to closure. A
drillthrough page the report merely offers because a tile shows its bound column is NOT kept:
the build follows what the person picked (their words, 2026-11-23), and that rule alone took a
one-page pick of 11 tiles to 23.

WHAT IS SAID. Every unit left out is listed in A2's `dropped[]` with its page and why, and
`question_units.json` carries the scope (`page_scope`): what was asked for, what matched,
each supporting page and why, each page left out, and any name that matched no page. A pick
that names no page of the report filters nothing, and says so: a misspelt page never turns a
50-tile report into an empty build.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import unicodedata

RECORD = "page_scope.json"
SCHEMA = "page_scope.schema.json"
# The digest of "no filter": no record, a record for every page, or a pick naming no page.
NONE = "none"


def norm(name) -> str:
    """A page name as a person may type it: case, surrounding quotes and runs of spaces,
    underscores or hyphens do not matter ("job  costs", "Job_Costs", "JOB COSTS")."""
    text = unicodedata.normalize("NFKC", str(name or "")).strip().strip("\"'`").strip()
    return re.sub(r"[\s_\-]+", " ", text).casefold()


def read(run: pathlib.Path) -> dict | None:
    """The run's page record, schema-checked, or None when the person chose none."""
    from metrics_agent.lib import artifacts  # noqa: PLC0415

    if not (pathlib.Path(run) / RECORD).is_file():
        return None
    return artifacts.read(pathlib.Path(run), RECORD, SCHEMA)


def requested(record: dict | None) -> list[str]:
    """The page names a record asks for; empty for every page."""
    if not record or record.get("all"):
        return []
    return [p for p in dict.fromkeys(str(x).strip() for x in record.get("pages") or []) if p]


def digest(record: dict | None) -> str:
    """What A2 records it was scoped by, and the runner compares: `none` for every page."""
    want = sorted({norm(p) for p in requested(record)} - {""})
    if not want:
        return NONE
    return hashlib.sha256(json.dumps(want).encode()).hexdigest()[:16]


def _pages_of(u: dict) -> list[str]:
    """The pages (containers) a unit is on: its container, and any dashboard it is placed on."""
    out = [str(u.get("container") or "")]
    out += [str(d.get("name") or "") for d in u.get("dashboards") or [] if isinstance(d, dict)]
    return [p for p in dict.fromkeys(out) if p]


def apply(usage: list[dict], record: dict | None) -> tuple[list[dict], list[dict], dict | None]:
    """(the units kept, a `dropped[]` entry per unit left out, the scope summary or None).

    No record, or one for every page: every unit, nothing dropped, no summary."""
    want = requested(record)
    if not want:
        if record is None:
            return list(usage), [], None
        return list(usage), [], {"requested": [], "all": True, "applied": False,
                                 "chosen": [], "supporting": [], "left_out": [],
                                 "unmatched": [], "note": None}
    pages: list[str] = []
    for u in usage:
        for p in _pages_of(u):
            if p not in pages:
                pages.append(p)
    by_norm: dict[str, list[str]] = {}
    for p in pages:
        by_norm.setdefault(norm(p), []).append(p)
    chosen: list[str] = []
    unmatched: list[str] = []
    for name in want:
        hits = by_norm.get(norm(name)) or []
        if not hits:
            unmatched.append(name)
        chosen += [h for h in hits if h not in chosen]
    if not chosen:
        note = (f"No page of this report is called {', '.join(repr(n) for n in want)}, so "
                f"every page is built. Its pages: {', '.join(pages) or 'none'}.")
        return list(usage), [], {"requested": want, "all": False, "applied": False,
                                 "chosen": [], "supporting": [], "left_out": [],
                                 "unmatched": unmatched, "note": note}

    # The supporting pages, to closure: what a kept page's units name as their tooltip or as a
    # drillthrough button's target. Never a page offered only by a shared column.
    linked_names = {norm(p): p for p in pages}
    kept = list(chosen)
    supporting: list[dict] = []
    frontier = list(chosen)
    while frontier:
        page = frontier.pop(0)
        units = [u for u in usage if page in _pages_of(u)]
        found: dict[str, str] = {}
        for u in units:
            for link in u.get("links") or []:
                target = linked_names.get(norm(link))
                if target:
                    found.setdefault(target, "linked")
        for target, why in found.items():
            if target in kept:
                continue
            kept.append(target)
            frontier.append(target)
            supporting.append({"page": target, "for": page, "why": why})

    keep_set = set(kept)
    out, dropped, left = [], [], {}
    picked = ", ".join(chosen)
    for u in usage:
        if keep_set & set(_pages_of(u)):
            out.append(u)
            continue
        page = str(u.get("container") or "")
        left[page] = left.get(page, 0) + 1
        dropped.append({"unit_id": str(u["unit_id"]), "reason": "not_on_chosen_pages",
                        "detail": f"on the page {page!r}, which was not picked (picked: "
                                  f"{picked}), and no picked tile drills through to it or "
                                  f"shows it as a tooltip"})
    note = None
    if unmatched:
        note = (f"No page is called {', '.join(repr(n) for n in unmatched)}; the build covers "
                f"the pages that matched: {picked}.")
    return out, dropped, {"requested": want, "all": False, "applied": True, "chosen": chosen,
                          "supporting": supporting,
                          "left_out": [{"page": p, "units": n} for p, n in left.items()],
                          "unmatched": unmatched, "note": note}
