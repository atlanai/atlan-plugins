# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The warehouse gate — ONE permission for ALL warehouse access, not just execution.

PROVENANCE. Ported from an earlier project's warehouse gate, including the operator
ruling that produced it. The old scoping — "reading metadata is not executing SQL" — let a run
record `witness: false` beside a report saying it had probed, and one did. Metadata reads,
column profiles and execution are the same permission.

Deliberately left behind: that module's four named stage hooks, which were specific to its
pipeline. Here the gate is a single predicate every warehouse-touching stage asks.
"""
from __future__ import annotations

from typing import Any

# Modes that are allowed to open a connection at all.
LIVE_MODES = {"explain", "execute"}


class WarehouseRefused(RuntimeError):
    """Raised when a stage asks for warehouse access the run was not granted."""


def allows_warehouse(witness_mode: str) -> bool:
    """True only when the run explicitly asked for live warehouse access.

    `parse` is NOT live: it confirms the statement parses and needs no substrate. A stage that
    treats `parse` as permission to connect is the exact defect this module exists to prevent.
    """
    return witness_mode in LIVE_MODES


def require(witness_mode: str, stage: str) -> None:
    """Refuse by name rather than degrading quietly."""
    if not allows_warehouse(witness_mode):
        raise WarehouseRefused(
            f"{stage} needs warehouse access but this run's witness mode is "
            f"{witness_mode!r}. Set `witness_mode: explain` or `execute` in run.yml, or accept the "
            f"stated cost: nothing is execution-verified and every record ships "
            f"label: parse_only."
        )


def degrade(witness_mode: str, stage: str, what: str) -> str | None:
    """For stages that may proceed WITHOUT the warehouse but must say so on stdout.

    Returns the disclaimer to record, or None when access is granted. This is the only
    legitimate softening in the pipeline — MISSING INFRASTRUCTURE, never a failed check —
    and it is safe because the degraded verdict is not a passing one.
    """
    if allows_warehouse(witness_mode):
        return None
    return (
        f"{stage}: {what} unavailable (witness mode {witness_mode!r}). Recorded as absent "
        f"evidence, which is NOT the same as a passing check."
    )
