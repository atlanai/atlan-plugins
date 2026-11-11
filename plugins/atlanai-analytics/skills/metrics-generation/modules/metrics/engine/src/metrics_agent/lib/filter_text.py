"""One filter, written the way a reader and a matcher must both see it: with its polarity.

The parser records a predicate under an enclosing NOT as the positive predicate with
`negated: true` (`NOT x IS NULL` is `{column: x, op: is, value: NULL, negated: true}`).
Every place that turns a filter back into text, or into a key, has to carry that flag:
without it an exclusion reads as an inclusion (`x IS NOT NULL` shown as `x is null`) and
two opposite questions share one key.
"""
from __future__ import annotations

_WORD_OPS = ("in", "between", "like", "ilike", "rlike", "regexp")


def restriction(f: dict, *, with_value: bool = True, mark_stage: bool = True) -> str:
    """The filter as text an author reads and can quote back, polarity included.

    `with_value=False` drops the literal (for shape keys, where two statements differing
    only in a value are the same question) but never the negation."""
    col, op = f.get("column"), str(f.get("op") or "")
    value = f.get("value") if with_value else None
    is_null_test = op.lower() in ("is", "is not", "is_not")
    if is_null_test and not with_value:
        value = f.get("value")          # IS NULL carries no literal worth dropping
    tail = f" {value}" if value is not None else ""
    if f.get("negated"):
        if op.lower() == "is":
            text = f"{col} is not{tail}"
        elif op.lower() in _WORD_OPS:
            text = f"{col} not {op}{tail}"
        else:
            text = f"not ({col} {op}{tail})"
    else:
        text = f"{col} {op}{tail}"
    if mark_stage and (f.get("stage") or "") not in ("where", ""):
        text += " [in a CTE]"
    return text


def key(f: dict) -> str:
    """A matching key for the predicate without its literal: same column, same operator,
    same polarity. `x IS NULL` and `x IS NOT NULL` never share one."""
    return restriction(f, with_value=False, mark_stage=False)
