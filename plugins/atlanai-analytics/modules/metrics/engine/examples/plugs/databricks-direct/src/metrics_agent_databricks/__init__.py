# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Example plug: `databricks-direct`, a read-only databricks connection for the SQL check.

A community example, not maintained with the core: it shows how small a SQL-check plug is.
Install it with `pip install -e examples/plugs/databricks-direct`.
"""
from __future__ import annotations

import os
from typing import Any

from metrics_agent import sockets as S

__version__ = "0.1.0.dev0"


class Direct:
    name = "databricks-direct"
    default_dialect = "databricks"
    kind = "input"
    custody = "customer"
    requires_env: tuple[str, ...] = ("METRICS_AGENT_WAREHOUSE_DSN",)

    def __init__(self, settings: dict[str, Any] | None = None, *, dialect: str = "databricks"):
        self.settings = dict(settings or {})
        self.dialect = dialect or "databricks"

    def check(self, settings: dict[str, Any]) -> S.CheckResult:
        if not os.environ.get("METRICS_AGENT_WAREHOUSE_DSN"):
            return S.env_refusal(self.name, ("METRICS_AGENT_WAREHOUSE_DSN",))
        try:
            from metrics_agent.socket_gates import ReadOnlyRunner

            with ReadOnlyRunner(type(self)(settings, dialect=self.dialect).sql_runner(),
                                self.dialect) as ro:
                ro.fetch("SELECT 1", 1)
        except Exception as exc:  # the check reports, it never raises
            return S.CheckResult(False, f"could not connect: {type(exc).__name__}",
                                 fix="METRICS_AGENT_WAREHOUSE_DSN")
        return S.CheckResult(True, "connected")

    def sql_runner(self):
        from metrics_agent.lib import engine as _engine

        dsn = os.environ.get("METRICS_AGENT_WAREHOUSE_DSN") or ""
        if not dsn:
            raise _engine.EngineError("METRICS_AGENT_WAREHOUSE_DSN is unset — there is deliberately no default.")

        def connect():
            from databricks import sql as vendor
            return vendor.connect(**_engine.parse_kv(dsn))  # server_hostname=…;http_path=…;…

        return _engine.Engine("databricks", dsn, self.dialect, connector=connect)
