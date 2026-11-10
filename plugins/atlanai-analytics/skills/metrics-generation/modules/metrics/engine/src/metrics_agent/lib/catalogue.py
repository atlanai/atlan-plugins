# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Column metadata for the tables a run touches, with the lookup rules the D lane needs.

Filled by whichever plugs supply table schemas (`inputs.schema` in run.yml). Empty is
normal: the catalogue is always optional, and a dimension whose datatype it cannot resolve is
still emitted with the gap admitted.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class Columns:
    """Resolved column metadata with the lookup rules the D lane needs.

    Two lookup shapes, because a verified query names tables at whatever depth the author
    wrote: fully qualified (`retail.fct_sales`) or bare (`fct_sales`). A bare name that is
    AMBIGUOUS across schemas resolves to nothing rather than to whichever row came first —
    publishing the wrong table's datatype is worse than admitting the gap.
    """

    def __init__(self, by_key: dict[str, Any], *, connection_qn: str = "",
                 fetched_at: str | None = None, source: str = ""):
        self.by_key = by_key
        self.source = source
        self.connection_qn = connection_qn
        self.fetched_at = fetched_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        self._by_suffix: dict[str, list[str]] = {}
        for key in by_key:
            parts = key.split(".")
            if len(parts) >= 2:
                self._by_suffix.setdefault(".".join(parts[-2:]), []).append(key)

    @classmethod
    def empty(cls) -> "Columns":
        return cls({}, connection_qn="", fetched_at="")

    def __bool__(self) -> bool:
        return bool(self.by_key)

    def __len__(self) -> int:
        return len(self.by_key)

    def get(self, table: str, column: str) -> dict[str, Any] | None:
        if not self.by_key:
            return None
        table, column = str(table or "").lower(), str(column or "").lower()
        exact = self.by_key.get(f"{table}.{column}")
        if exact:
            return exact
        # `table` may be qualified more deeply or more shallowly than the catalogue records it.
        bare = table.split(".")[-1]
        hits = [k for k in self._by_suffix.get(f"{bare}.{column}", [])]
        if len(hits) == 1:
            return self.by_key[hits[0]]
        if len(hits) > 1:
            # Ambiguous: several schemas hold a table of this name. Admit the gap.
            return None
        return None

    def content_sha(self) -> str:
        """A stable digest of what this run actually read.

        A catalogue is a LIVE source: two runs a week apart can legitimately differ. Recording the
        digest on the manifest makes such a difference attributable instead of mysterious —
        the same reason the witness records `executed_at` only when something ran.
        """
        blob = json.dumps(self.by_key, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()

    def manifest_record(self, note: str = "") -> dict[str, Any]:
        return {
            "source": self.source,
            "connection": self.connection_qn,
            "fetched_at": self.fetched_at,
            "columns": len(self.by_key),
            "content_sha": self.content_sha() if self.by_key else None,
            "note": note,
        }
