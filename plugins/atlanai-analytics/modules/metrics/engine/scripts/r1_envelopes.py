# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Runs stage `r1_envelopes`, which the Atlan plug package provides. Kept so existing commands keep
working; prefer `metrics-agent stage r1_envelopes`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from metrics_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["stage", "r1_envelopes", *sys.argv[1:]]))
