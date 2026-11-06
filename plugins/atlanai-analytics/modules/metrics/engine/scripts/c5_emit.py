#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Runs `metrics_agent.stages.c5_emit`. Kept so existing commands keep working; prefer
`metrics-agent stage c5_emit`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from metrics_agent.stages.c5_emit import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
