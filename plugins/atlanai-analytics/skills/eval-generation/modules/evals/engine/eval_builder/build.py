"""Turning one scored candidate plus its recovered question into a record.

This used to carry a second `build()` that re-implemented the CLI's assembly
with its own dedupe and selection. Nothing called it and nothing tested it, so
it was a divergent copy waiting to rot; `cli._assemble` is the only path.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from eval_builder.reverse import ReverseResult, reverse_engineer
from eval_builder.select import Candidate, diversify, domain_of, rank
from eval_builder.spec import EvalRecord, EvalSpec, Provenance


@dataclass
class BuildStats:
    mined: int = 0
    selected: int = 0
    reversed_ok: int = 0
    rejected_unanswerable: int = 0
    rejected_leaky: int = 0
    rejected_error: int = 0
    domains: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        return "\n".join([
            f"  mined                  {self.mined}",
            f"  selected               {self.selected}",
            f"  questions written      {self.reversed_ok}",
            f"  dropped, not a question {self.rejected_unanswerable}",
            f"  dropped, leaked schema  {self.rejected_leaky}",
            f"  dropped, error          {self.rejected_error}",
            f"  domains covered        {len(self.domains)}: {', '.join(self.domains)}",
        ])


def _to_record(cand: Candidate, rev: ReverseResult) -> EvalRecord:
    el = cand.element
    return EvalRecord(
        id=cand.query_id,
        # The natural form is what the agent sees: the ambiguity is the test.
        question=rev.question_natural,
        golden_sql=cand.sql,
        dialect=_dialect(cand),
        tables=list(cand.tables),
        as_of=rev.as_of,
        # The tile, and the SQL as crawled before it was cut: without them a
        # reviewer sees the cut but not what it was cut from. (The registry
        # row carries the element, which holds both.)
        element=el.to_dict() if el is not None else None,
        original_sql=cand.original_sql,
        provenance=Provenance(
            source=f"bi:{el.tool}" if el is not None else "query_history",
            query_id=cand.query_id,
            first_seen=cand.first_seen,
            last_seen=cand.last_seen,
            run_count=cand.run_count,
            distinct_users=cand.distinct_users,
            selection_scores={**cand.scores, "total": round(cand.total, 3)},
            selection_notes=[
                *cand.notes,
                f"spec: {rev.question_spec}",
                *(f"decision: {d}" for d in rev.decisions),
            ],
        ),
    )


def _dialect(cand) -> str:
    from eval_builder import dialect as D

    return D.of(cand)
