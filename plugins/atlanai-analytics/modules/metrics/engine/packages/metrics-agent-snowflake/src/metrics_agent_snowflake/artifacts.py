# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What this package registers under the core's `metrics_agent.artifacts` entry points.

Its stages write no run artifact of their own; it ships its policy defaults (`POLICY`, the
`semantic_view.*` section its reader reads), merged under the run's policy.
"""
from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent
# No schema of its own: the package directory, where a schema would be looked up and none is.
SCHEMAS = HERE
RUN_ARTIFACTS: dict[str, str] = {}
UNSCHEMAD: dict[str, str] = {}
POLICY = HERE / "policy.yml"
