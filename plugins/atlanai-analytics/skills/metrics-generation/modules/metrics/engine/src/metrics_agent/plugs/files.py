# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The `files` output plug: publish nowhere; the bundle stays on disk.

What a run with `publish: [files]` (nothing leaves the run directory) uses. `plan` lists the
bundle's files; `publish` sends nothing and says so in a ledger, so every run, published or
not, ends with the same kind of record.
"""
from __future__ import annotations

from typing import Any

from metrics_agent import sockets as S


class Files:
    name = "files"
    kind = "output"
    lane = "files"
    requires_env: tuple[str, ...] = ()

    def __init__(self, settings: dict[str, Any] | None = None, *, dialect: str = "") -> None:
        self.settings = dict(settings or {})

    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        return S.CheckResult(True, "nothing to connect to")

    def resolve(self, settings: dict[str, Any]) -> dict[str, Any]:
        return {}

    def plan(self, bundle: S.Bundle) -> S.PublishPlan:
        paths = [p for p in (bundle.verified_queries, bundle.metrics, bundle.relationships)
                 if p is not None]
        return S.PublishPlan("files", tuple({"kind": "file", "path": str(p),
                                             "exists": p.exists()}
                                            for p in paths),
                             "the bundle stays in the run directory; nothing is sent")

    def publish(self, bundle: S.Bundle) -> S.Ledger:
        return {"lane": "files", "sent": 0, "rows": [],
                "note": f"published nowhere; the bundle is in {bundle.run_dir}"}
