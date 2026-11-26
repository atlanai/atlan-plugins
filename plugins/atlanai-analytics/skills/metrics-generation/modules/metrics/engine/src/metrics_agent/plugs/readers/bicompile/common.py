# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What every part of the compiler shares: the rule and corpus paths, the token pattern, CompileError. Part of bicompile; see __init__.py."""
from __future__ import annotations

import pathlib
import re
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
RULES = DATA / "bi_compiler_rules.yml"
CORPUS = DATA / "compiler_corpus"

# An expression the compiler understands is built from these and nothing else. Anything the
# tokeniser cannot classify is reported by name rather than guessed at — the whole value of a
# compiler over a model is that it knows what it does not know.
_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<col>\[[^\]]+\])                    # [Units Sold]  — Tableau and DAX both use these
    # Table[Column] — DAX. A keyword followed by a field (Tableau's `THEN [x]`, `AND [y]`;
    # DAX's `RETURN [x]`, an LOD's `{FIXED [d] : …}`) is NOT a qualified reference, so the
    # keywords are excluded.
    | (?P<qualified>(?!(?i:then|else|elseif|when|and|or|not|if|case|end|return|fixed|include|exclude)\b)[A-Za-z_][\w]*\s*\[[^\]]+\])
    # 'Date'[Date] — DAX quotes a table name containing a space or a reserved word. It MUST
    # be tried before the string literal, or `'Date'` tokenises as a string and `[Date]`
    # arrives where an operator was expected. That is not a hypothetical: it is how the
    # Power BI fixture writes time intelligence.
    | (?P<quoted_qualified>'(?:[^']|'')*'\s*\[[^\]]+\])
    | (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
    # #2025-01-01# — a Tableau date (or date and time) literal; a date parameter's value.
    | (?P<datelit>\#\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?\#)
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<func>[A-Za-z_][\w.]*)
    | (?P<op><=|>=|<>|!=|==|&&|\|\||[-+*/%<>=&^])   # `&&` / `||`: DAX's logical AND / OR
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<comma>,)
    | (?P<colon>:)                           # `{FIXED [d] : SUM([x])}`
    | (?P<brace>[{}])                        # an LOD opens with one
""", re.X)


# The table-calculation functions of the calc language, by name.
TABLE_CALC_NAMES = re.compile(r"^(RUNNING_\w+|WINDOW_\w+|TOTAL|INDEX|FIRST|LAST|SIZE|LOOKUP|"
                              r"PREVIOUS_VALUE|RANK\w*)$", re.I)


def _constant_count(toks: list) -> int | None:
    """A Top N set's count: one whole number (a parameter stands at its value by now), in
    parentheses or not; None for anything else. Tableau itself rejects any other count
    (checked live: count='2 + 3' fails with "The filter limit expression is invalid")."""
    while len(toks) >= 2 and toks[0][0] == "lparen" and toks[-1][0] == "rparen":
        toks = toks[1:-1]
    if len(toks) == 1 and toks[0][0] == "num" and re.fullmatch(r"\d+", str(toks[0][1])):
        return int(toks[0][1])
    return None


def _calls_table_calc(formula: str) -> bool:
    """Whether a calc-language formula calls a table-calculation function."""
    return any(TABLE_CALC_NAMES.match(name)
               for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", formula))


class _SortNeedsWindow(Exception):
    """A table calculation's order is a window of its own (a member's aggregate over finer
    rows): it is computed over the grouped rows as a statement of their own."""


class CompileError(ValueError):
    """The compiler cannot produce a statement it believes in. Always carries the reason."""


Result = Compiled          # the socket's result type; this name is the engine's own
