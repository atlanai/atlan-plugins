#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Runs `metrics_agent.stages.x3_loop`. Kept so existing commands keep working; prefer
`metrics-agent stage x3_loop`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from metrics_agent.stages.x3_loop import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
