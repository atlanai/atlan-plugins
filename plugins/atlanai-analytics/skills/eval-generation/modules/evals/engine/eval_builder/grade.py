"""Grade the eval, not the agent.

`select.py` decides which query deserves an eval. This decides whether the
*question written over it* is worth asking — a different judgement, and the one
a review of an eval set turns on. A question can carry every structural marker
of difficulty and still be worthless: where the question spells out the very
attributes the metric is defined over, it hands the agent the definition and the
trap cannot fire. The metric can be one of the sharpest in the set while the
question is close to trivial.

That is why `gives_away` caps the other axes instead of averaging with them. An
eval that states its own answer is not a hard eval with a flaw; it is not an
eval.

Three axes, all computed from the record itself:

    intent_hops   decisions the natural question leaves for the agent to resolve
    sql_depth     buried decisions the SQL actually makes (select.py's modes)
    gives_away    how much of the answer the question already states

The first two are earned difficulty. The third is leakage, and it subtracts.

Deliberately NOT here: contested definitions ("three sources disagree"), which
need catalog access to detect and a reviewer to adjudicate. That stays an open
question rather than pretending a heuristic settles it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from eval_builder.select import FAILURE_MODES, Candidate, score as score_candidate

# Values a question may legitimately restate: the subject it is asking about has
# to appear somewhere, or there is no question. What must NOT appear is the
# decision that resolves an ambiguity — the qualifier, the boundary, the rule.
_STOPWORDS = frozenset("""
a an and are as at be by for from how in is it its many much of on or our that
the to was were what when where which who why with we us do does did all each
""".split())

# A quoted literal or number in the golden SQL is a decision the query makes.
_LITERAL = re.compile(r"'([^']{2,60})'|(?<![\w.])(\d{4}-\d{2}-\d{2})(?![\w.])")


@dataclass
class Grade:
    """One record's score, with the reasoning kept alongside it."""

    record_id: str
    intent_hops: int = 0
    sql_depth: int = 0
    gives_away: float = 0.0
    modes: list[str] = field(default_factory=list)
    leaked: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """0.0-1.0. Leakage caps it rather than averaging against it."""
        earned = min(1.0, (self.intent_hops + self.sql_depth) / 6.0)
        return round(earned * (1.0 - self.gives_away), 3)

    @property
    def verdict(self) -> str:
        if self.gives_away >= 0.6:
            return "hands over its own answer"
        if self.score >= 0.6:
            return "discriminating"
        if self.score >= 0.3:
            return "usable"
        return "too easy to separate agents"

    def render(self) -> str:
        head = (f"  {self.record_id:<30} {self.score:>5.2f}  {self.verdict}\n"
                f"      intent hops {self.intent_hops} · sql depth {self.sql_depth}"
                f" · gives away {self.gives_away:.0%}")
        if self.leaked:
            head += f"\n      question already states: {', '.join(self.leaked[:4])}"
        for n in self.notes:
            head += f"\n      {n}"
        return head


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", (text or "").lower())
            if w not in _STOPWORDS}


def _decision_is_stated(decision: str, question: str) -> bool:
    """Does the natural question already settle this decision?

    Not string containment — a question rarely quotes a decision verbatim. The
    test is whether the question carries the decision's distinguishing words. A
    decision reading "test stores are excluded" is given away by "excluding
    test stores" and not by "how many orders".
    """
    dw = _words(decision)
    if not dw:
        return False
    qw = _words(question)
    # Numbers and dates are the sharpest tell: a window decision restated in the
    # question is no longer a decision.
    hard = {w for w in dw if any(c.isdigit() for c in w)}
    if hard and hard <= qw:
        return True
    overlap = len(dw & qw) / len(dw)
    return overlap >= 0.6


def grade_record(record) -> Grade:
    """Score one EvalRecord.

    Takes the whole record on purpose: `gives_away` is a comparison between what
    the question states and what the SQL resolves, so it cannot be computed from
    the agent's view alone. That is also why this runs at build time and not
    inside the runner.
    """
    g = Grade(record_id=record.id)

    question = record.question or ""
    decisions = [d for d in (record.decisions or []) if d.strip()]

    stated = [d for d in decisions if _decision_is_stated(d, question)]
    g.intent_hops = len(decisions) - len(stated)
    g.leaked = stated

    modes = list(record.modes or [])
    if not modes and record.golden_sql:
        # Re-derive from the SQL rather than trusting an empty field: a record
        # hand-written or pulled back from the registry carries no modes.
        scored = score_candidate(Candidate(query_id=record.id, sql=record.golden_sql))
        modes = [m for m in scored.modes if m in FAILURE_MODES]
    g.modes = modes
    g.sql_depth = len(modes)

    # The literals the SQL filters on. A question that names them has done the
    # resolution the agent was meant to do.
    if record.golden_sql:
        literals = {(a or b).lower()
                    for a, b in _LITERAL.findall(record.golden_sql) if (a or b)}
        qw = _words(question)
        named = [lit for lit in literals
                 if lit in " ".join(qw) or _words(lit) <= qw and _words(lit)]
        if named:
            g.notes.append(
                f"{len(named)} filter value(s) from the SQL appear in the "
                f"question: {', '.join(sorted(named)[:3])}")
            g.leaked.extend(sorted(named)[:3])

    if decisions:
        g.gives_away = round(len(set(g.leaked)) / max(len(decisions), 1), 3)
        g.gives_away = min(1.0, g.gives_away)
    elif g.leaked:
        g.gives_away = 1.0
    else:
        # No decisions recorded at all is its own problem: nothing was pinned,
        # so there is nothing for the question to hide or hand over.
        g.notes.append("no decisions recorded — nothing was pinned to hide")

    if not record.question_spec:
        g.notes.append("no spec form — fairness cannot be checked")

    return g


def grade_all(records) -> list[Grade]:
    return [grade_record(r) for r in records]


def summarise(grades: list[Grade]) -> dict:
    """Aggregates in the shape the registry's experiment `summary` expects."""
    if not grades:
        return {}
    scores = [g.score for g in grades]
    leaky = [g for g in grades if g.gives_away >= 0.6]
    return {
        "scores": {
            "eval_quality": {
                "mean": round(sum(scores) / len(scores), 3),
                "min": min(scores), "max": max(scores), "count": len(scores),
            },
            "hands_over_answer": {
                "count": len(leaky), "of": len(grades),
            },
        },
        "modes_covered": sorted({m for g in grades for m in g.modes}),
    }
