# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""ANSWERS CARRIED FROM AN EARLIER RUN OVER THE SAME SOURCES.

A dashboard is built first; its past queries may be added afterwards, in a second run that
reads the dashboard and the history together so the merge can fold duplicates. The dashboard's
statements are the same statements, with the same ids, so what was already said about them is
carried (`prepare --carry-from`) into `answers/<gate>/carried-*.json` and only the statements no
answer covers are asked about.

A carried answer for a statement this run no longer holds is dropped, never ingested: the gate
would refuse an unknown id, and a statement that changed is asked about again.
"""
from __future__ import annotations

import glob
import json
import pathlib


def apply(ctx, gate: str, key: str, pattern: str | None, layer_ids: set, objs: list) -> list:
    """The objects still to ask about. Prunes each carried file to `layer_ids` in place."""
    files = [pathlib.Path(p) for p in sorted(glob.glob(pattern))] if pattern else []
    carried, dropped = set(), 0
    for path in files:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        rows = doc.get(key) if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            continue
        keep = [r for r in rows if isinstance(r, dict) and r.get("vq_id") in layer_ids]
        dropped += len(rows) - len(keep)
        if len(keep) != len(rows):
            path.write_text(json.dumps({**doc, key: keep}, indent=1))
        carried |= {r["vq_id"] for r in keep}
    if not files:
        return objs
    rest = [o for o in objs if o["vq_id"] not in carried]
    ctx.say(f"{gate}: {len(carried)} statement(s) carried from the earlier run"
            + (f", {dropped} it held that this run does not" if dropped else "")
            + f"; {len(rest)} to answer.")
    return rest
