"""Prove an eval is fair before it is allowed to grade anything.

The check is a round trip: hand the question back, take the SQL that comes back,
run both, compare. What makes it meaningful is *which* question form gets handed
back.

    question_spec     every decision pinned  ->  a competent solver MUST succeed
    question_natural  ambiguous, as asked    ->  a solver may well fail

So the validator solves the SPEC form. If a solver holding the schema and a fully
specified question still cannot reproduce the golden answer, the eval is not hard,
it is broken: the spec is incomplete, the golden SQL does not answer the question
it claims to, or the question has more than one defensible reading. Any of those
makes a later failure on the natural form uninterpretable — you would not know
whether the agent lacked context or the eval was simply unfair.

Failure on the *natural* form is the opposite: it is the measurement working.
That is why this module never solves the natural form. Doing so would quietly
delete every eval worth having.

Comparison is deterministic — no model judges the result. A judge introduces a
second source of error into the one place that has to be trustworthy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from eval_builder.spec import EvalRecord

# Relative tolerance for numeric comparison. Rounding conventions differ between
# a hand-written query and a regenerated one; 0.5% accepts that without
# accepting a genuinely different number.
NUMERIC_TOLERANCE = 0.005


_SOLVE_SYSTEM = """\
You are a careful analyst. Write ONE SQL query that answers the question exactly.

You are given a fully specified question and the schema of the tables involved. \
Every decision you need has already been made for you in the question — the \
population, the exclusions, the time window, the grain, the output convention. \
Follow them literally rather than substituting your own judgement.

Target dialect: Snowflake. Use fully qualified table names as given in the schema.

Respond with JSON only, no prose, no code fences:
{"sql": str, "assumptions": [str]}

If the question cannot be answered from the schema given, return \
{"sql": null, "assumptions": ["why not"]}.
"""


@dataclass
class Verdict:
    record_id: str
    fair: bool
    reason: str
    golden_rows: list | None = None
    candidate_rows: list | None = None
    candidate_sql: str = ""
    assumptions: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = "fair" if self.fair else "UNFAIR"
        return f"  [{mark}] {self.record_id}: {self.reason}"


# --------------------------------------------------------------------------
# The harness answers this; the module only frames it and checks the answer
# --------------------------------------------------------------------------

def build_solve_prompt(record: EvalRecord, schema_text: str) -> str:
    """Ask for SQL from the decision-pinned form of the question."""
    if not record.question_spec:
        raise ValueError(
            f"{record.id} has no question_spec — there is nothing to validate "
            "against. A record without a pinned form cannot be shown to be fair."
        )
    anchor = f"\nAnchor all time-relative logic to: {record.as_of}" if record.as_of else ""
    return (
        f"{_SOLVE_SYSTEM}\n\n---\n\n"
        f"Question:\n{record.question_spec}{anchor}\n\n"
        f"Schema:\n{schema_text}"
    )


def parse_solve(text: str) -> tuple[str | None, list[str]]:
    from eval_builder.reverse import _parse_json

    data = _parse_json(text)
    sql = data.get("sql")
    assumptions = [a for a in (data.get("assumptions") or []) if isinstance(a, str)]
    return (sql.strip() if isinstance(sql, str) and sql.strip() else None), assumptions


# --------------------------------------------------------------------------
# Deterministic comparison
# --------------------------------------------------------------------------

def _numeric(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("%", "").replace("$", "")
        if re.fullmatch(r"-?\d+(\.\d+)?", s):
            return float(s)
    return None


def values_match(a, b) -> bool:
    """One cell against another, type-aware."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    na, nb = _numeric(a), _numeric(b)
    if na is not None and nb is not None:
        if na == nb:
            return True
        scale = max(abs(na), abs(nb))
        if scale == 0:
            return False
        return abs(na - nb) / scale <= NUMERIC_TOLERANCE

    return str(a).strip().casefold() == str(b).strip().casefold()


def rows_match(golden: list[tuple], candidate: list[tuple]) -> tuple[bool, str]:
    """Compare two result sets.

    Row order is ignored unless the golden query asked for one — an unordered
    query's row order is not part of its answer, and treating it as significant
    fails correct SQL. Column order is respected, since a differently ordered
    projection is a different answer shape.
    """
    if len(golden) != len(candidate):
        return False, f"row count {len(candidate)} != golden {len(golden)}"
    if not golden:
        return True, "both empty"

    gw = len(golden[0]) if golden[0] is not None else 0
    cw = len(candidate[0]) if candidate[0] is not None else 0
    if gw != cw:
        return False, f"column count {cw} != golden {gw}"

    def key(row):
        return tuple("" if v is None else str(v).strip().casefold() for v in row)

    for g, c in zip(sorted(golden, key=key), sorted(candidate, key=key), strict=True):
        for i, (gv, cv) in enumerate(zip(g, c, strict=True)):
            if not values_match(gv, cv):
                return False, f"value differs in column {i}: {cv!r} vs golden {gv!r}"
    return True, f"{len(golden)} row(s) match"


def _execute(conn, sql: str, limit: int = 5000) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchmany(limit)


def check(conn, record: EvalRecord, candidate_sql: str | None,
          assumptions: list[str] | None = None) -> Verdict:
    """Run the golden and candidate SQL and decide whether the eval is fair."""
    assumptions = assumptions or []

    if candidate_sql is None:
        return Verdict(record.id, False,
                       "solver could not answer the pinned question from the schema",
                       assumptions=assumptions)

    try:
        golden_rows = _execute(conn, record.golden_sql)
    except Exception as e:  # noqa: BLE001
        # The golden SQL failing is the worst case: the eval's own answer key
        # does not run, so nothing downstream can be trusted.
        return Verdict(record.id, False,
                       f"golden SQL does not execute: {str(e).splitlines()[0][:120]}",
                       candidate_sql=candidate_sql, assumptions=assumptions)

    try:
        candidate_rows = _execute(conn, candidate_sql)
    except Exception as e:  # noqa: BLE001
        return Verdict(record.id, False,
                       f"solver SQL does not execute: {str(e).splitlines()[0][:120]}",
                       golden_rows=golden_rows, candidate_sql=candidate_sql,
                       assumptions=assumptions)

    ok, why = rows_match(golden_rows, candidate_rows)
    return Verdict(
        record.id, ok,
        why if ok else f"spec did not reproduce the golden answer — {why}",
        golden_rows=golden_rows, candidate_rows=candidate_rows,
        candidate_sql=candidate_sql, assumptions=assumptions,
    )


@dataclass
class ValidationReport:
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def fair(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.fair]

    @property
    def unfair(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.fair]

    def render(self) -> str:
        lines = [f"  validated {len(self.verdicts)}: "
                 f"{len(self.fair)} fair, {len(self.unfair)} unfair"]
        lines += [v.render() for v in self.unfair]
        return "\n".join(lines)
