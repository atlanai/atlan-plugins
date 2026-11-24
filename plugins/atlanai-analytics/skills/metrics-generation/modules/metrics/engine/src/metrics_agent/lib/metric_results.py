# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Checks shared by the engine and the standalone project helper."""
from decimal import Decimal, InvalidOperation


def valid_reference(expected, tolerance) -> bool:
    def numeric(value):
        return type(value) in (int, float) and Decimal(str(value)).is_finite()

    return (expected is None or numeric(expected)) and numeric(tolerance) and tolerance >= 0


def result_problem(rows: list, *, scalar: bool, expected=None, tolerance=0, row_count=None) -> str | None:
    observed = len(rows) if row_count is None else row_count
    if scalar and observed > 1:
        return "returns more than one row with no grouping"
    if expected is None:
        return None
    if observed != 1 or len(rows) != 1 or len(rows[0]) != 1:
        return "the expected value requires exactly one numeric result"
    try:
        actual = Decimal(str(rows[0][0]))
        if actual.is_finite() and abs(actual - Decimal(str(expected))) <= Decimal(str(tolerance)):
            return None
    except InvalidOperation:
        return "the expected value requires exactly one numeric result"
    return "the result differs from the expected value beyond its tolerance"
