# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Policy defaults owned by the Databricks plug."""
from pathlib import Path
POLICY = Path(__file__).with_name("policy.yml")
SCHEMAS = Path(__file__).parent
RUN_ARTIFACTS = {}
UNSCHEMAD = {}
