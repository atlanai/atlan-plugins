#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Runs the Power BI definition extractor, which now lives in the Power BI plug package at
`packages/metrics-agent-powerbi/src/metrics_agent_powerbi/pbi_definition_extract.py`
(standard library only: copy that one file to run it anywhere). A `.env` beside this script is still read, as before."""
import os
import pathlib
import runpy

HERE = pathlib.Path(__file__).resolve().parent
os.environ.setdefault("PBI_ENV_FILE", str(HERE / ".env"))
runpy.run_path(str(HERE.parents[1] / "packages" / "metrics-agent-powerbi" / "src"
                   / "metrics_agent_powerbi" / "pbi_definition_extract.py"),
               run_name="__main__")
