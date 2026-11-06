#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Runs stage `t1_lineage`, which the Atlan plug package provides. Kept so existing commands keep
working; prefer `metrics-agent stage t1_lineage`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from metrics_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["stage", "t1_lineage", *sys.argv[1:]]))
