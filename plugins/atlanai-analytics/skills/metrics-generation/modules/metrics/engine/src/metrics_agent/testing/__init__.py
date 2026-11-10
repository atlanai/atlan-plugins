# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Test helpers for people who write plugs.

`metrics_agent.testing.conformance` is the socket contract as a pytest suite: subclass one of
its classes in your plug's tests and run pytest. It needs pytest, which the `testing` extra
installs (from a clone: `pip install -e ".[testing]"`); importing this package alone needs nothing.
"""
