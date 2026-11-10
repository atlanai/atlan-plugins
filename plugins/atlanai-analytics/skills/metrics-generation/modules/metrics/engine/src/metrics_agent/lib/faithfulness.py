# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Do the WORDS match the SQL?

The witness proves the statement runs. The column gate proves its columns exist. Neither looks
at the prose — and the prose is what a person reads before trusting the number. A description
saying *"units sold, retail channel only, excluding returns"* over a statement that filters
only on channel is worse than no description: the reader believes returns were excluded, the
number says otherwise, and nothing in the pipeline disagrees with them.

COPIED (rule 6: copy, never import) from an earlier benchmark tool's faithfulness
check — the two-leg structure, the absent-vs-unresolved distinction and the pre-registered pass
band — and from its predicate-in-body test. Copied rather than imported because that tree has
its own pipeline contract, its own canonicaliser and a package layout this agent does not
carry.

THE MECHANISM, and why it is not prose-mining. A claim is only checkable if it is STATED as a
predicate, so C3 and D3 emit `claims[]` beside the prose and the gate checks those against the
statement's own AST. Trying to parse assertions out of English would be a guess dressed as a
check. What free prose gets instead is the second leg below, which detects an assertion that
was never declared — it never tries to interpret one.

  deterministic   every claimed predicate must appear in the statement, compared through the
                  SAME normal form the shape key uses. Absent -> the object is flagged, never
                  silently published.
  undeclared      the description uses restriction language ("excluding…", "only…") while
                  `claims` is empty. That is an assertion nobody can check: flagged, with the
                  phrase named.

ABSENT IS NOT UNRESOLVED. A predicate that is genuinely missing is a defect. One the checker
cannot follow — hidden behind a CTE or a subquery — is a LIMIT OF THE CHECKER, and treating
the two alike would park honest objects for the crime of being written with a CTE. Unresolved
routes to review; it never parks on its own.

THE BAND. If most objects fail, the pipeline is broken, not the estate. Below the
pre-registered rate the run HALTS — a mass-flagging event must never look like a shipped
result with a low score.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import sqlshape as S

PRESENT, ABSENT, UNRESOLVED = "present", "absent", "unresolved"

# Restriction language. Deliberately a SMALL, explicit list: this leg detects an undeclared
# assertion, it does not interpret one, and a wider net would start guessing at meaning.
RESTRICTION = re.compile(
    r"\b(exclud\w*|excepting|omitt\w*|net of|only|solely|restricted to|limited to|"
    r"filtered to|without)\b", re.I)


def _predicates_of(tree, dialect: str) -> set[str]:
    """Every predicate the statement actually applies, in one normal form.

    WHERE, HAVING and QUALIFY are all restrictions on what is counted. A join's ON condition is
    NOT included: it is how tables meet, not a claim about the population, and folding it in
    here would let `ON d.id = f.dept_id` silently satisfy a claimed exclusion.
    """
    out: set[str] = set()
    for clause_type in (S.exp.Where, S.exp.Having, S.exp.Qualify):
        for clause in tree.find_all(clause_type):
            for node in clause.find_all(S.exp.Binary, S.exp.In, S.exp.Is, S.exp.Between,
                                        S.exp.Like, S.exp.ILike, S.exp.Not):
                if isinstance(node, S.exp.And):
                    continue        # a conjunction is not itself a predicate; an OR is one
                out.add(normalise(node.sql(dialect=dialect), dialect))
    return {p for p in out if p}


def normalise(predicate: str, dialect: str) -> str:
    """One normal form for both sides of the comparison.

    Qualifiers are dropped and literals kept: `f.channel = 'retail'` and `s.channel='RETAIL'`
    are the same claim, while `channel = 'wholesale'` is a different one. Dropping the literal
    too — which the shape key does, correctly, for its own purpose — would let ANY value
    satisfy a claim about a specific one, and this gate exists precisely to catch that.
    """
    text = str(predicate or "").strip().rstrip(";")
    if not text:
        return ""
    for wrapper in (f"SELECT 1 WHERE {text}", text):
        try:
            tree = S.parse(wrapper, dialect)
        except S.ParseError:
            continue
        where = tree.find(S.exp.Where)
        node = where.this if where is not None else tree
        while isinstance(node, S.exp.Paren):
            node = node.this        # brackets around the WHOLE claim change nothing
        node = node.copy()
        for col in node.find_all(S.exp.Column):
            col.set("table", None)
        node = _unwrap_value_parens(node)
        return re.sub(r"\s+", " ", node.sql(dialect=dialect).strip().lower())
    return re.sub(r"\s+", " ", text.lower())


# Brackets around ONE value are spelling, not meaning: `(t.flag) IS NOT NULL` and
# `flag IS NOT NULL` are the same claim, and a compiler that brackets every expression it
# emits would otherwise fail every claim made about its statements. Brackets around a
# comparison or a connective are left alone, since removing them can change precedence.
_VALUE = (S.exp.Column, S.exp.Literal, S.exp.Func, S.exp.Case, S.exp.Cast, S.exp.Paren,
          S.exp.Null, S.exp.Boolean)
# sqlglot types some connectives and comparisons as functions too; never unwrap those.
_NOT_A_VALUE = (S.exp.Connector, S.exp.Binary, S.exp.Predicate, S.exp.Not)


def _unwrap_value_parens(node):
    def strip(n):
        while (isinstance(n, S.exp.Paren) and isinstance(n.this, _VALUE)
               and not isinstance(n.this, _NOT_A_VALUE)):
            n = n.this
        return n
    node = strip(node)
    return node.transform(lambda n: strip(n) if isinstance(n, S.exp.Paren) else n)


def has_indirection(tree) -> bool:
    """A CTE or subquery the checker cannot see through. The reason `unresolved` exists."""
    return bool(list(tree.find_all(S.exp.With)) or list(tree.find_all(S.exp.Subquery)))


def predicate_in_body(predicate: str, sql: str, dialect: str) -> str:
    """-> present | absent | unresolved."""
    want = normalise(predicate, dialect)
    if not want:
        return UNRESOLVED
    try:
        tree = S.parse(sql, dialect)
    except S.ParseError:
        return UNRESOLVED       # we could not read the statement; that is our limit, not a lie
    have = _predicates_of(tree, dialect)
    if want in have:
        return PRESENT
    # A claim may be written as the whole WHERE clause rather than one conjunct: it is
    # present when EVERY conjunct is. Never by substring — `a = 1` is inside `a = 10`.
    parts = _conjuncts(predicate, dialect)
    if len(parts) > 1 and all(p in have for p in parts):
        return PRESENT
    return UNRESOLVED if has_indirection(tree) else ABSENT


def _conjuncts(predicate: str, dialect: str) -> list[str]:
    """The AND-ed parts of a claim, each in normal form."""
    try:
        tree = S.parse(f"SELECT 1 WHERE {str(predicate or '').strip().rstrip(';')}", dialect)
    except S.ParseError:
        return []
    where = tree.find(S.exp.Where)
    if where is None:
        return []
    out, stack = [], [where.this]
    while stack:
        node = stack.pop()
        while isinstance(node, S.exp.Paren) and isinstance(node.this, S.exp.And):
            node = node.this
        if isinstance(node, S.exp.And):
            stack += [node.this, node.expression]
            continue
        out.append(normalise(node.sql(dialect=dialect), dialect))
    return [p for p in out if p]


def check(claims: Iterable[dict], sql: str, dialect: str, description: str = "") -> dict:
    """One object's verdict. Never raises, never rewrites anything."""
    results = []
    for c in claims or []:
        pred = str((c or {}).get("predicate") or "").strip()
        if not pred:
            continue
        results.append({"kind": (c or {}).get("kind") or "claim",
                        # Recorded in full: a predicate IS SQL, and a report that shows a cut
                        # version of what it checked cannot be audited against the statement.
                        "predicate": pred,
                        "verdict": predicate_in_body(pred, sql, dialect)})

    undeclared = []
    if not results:
        for m in RESTRICTION.finditer(description or ""):
            undeclared.append(m.group(0).lower())

    absent = [r for r in results if r["verdict"] == ABSENT]
    unresolved = [r for r in results if r["verdict"] == UNRESOLVED]
    if absent:
        route, passed = "deterministic", False
    elif undeclared:
        route, passed = "undeclared", False
    elif unresolved:
        route, passed = "review", True      # the checker's limit, not the object's defect
    else:
        route, passed = "deterministic", True
    return {"passed": passed, "route": route, "checked": len(results),
            "absent": [r["predicate"] for r in absent],
            "unresolved": [r["predicate"] for r in unresolved],
            "undeclared": sorted(set(undeclared))[:5],
            "results": results}


def band_violation(verdicts: list[dict], min_rate: float, min_sample: int) -> str:
    """-> a message when the run should HALT, else "".

    Only the deterministic route counts toward the rate. Objects routed to review are not
    evidence either way, and letting them dilute the denominator would hide a real collapse.
    """
    det = [v for v in verdicts if v["route"] in ("deterministic", "undeclared")
           and v["checked"] or v["route"] == "undeclared"]
    if len(det) < min_sample:
        return ""
    rate = sum(1 for v in det if v["passed"]) / len(det)
    if rate >= min_rate:
        return ""
    return (f"faithfulness pass rate {rate:.0%} over {len(det)} checked object(s) is below the "
            f"pre-registered band of {min_rate:.0%}. A mass-flagging event is a pipeline "
            f"defect, not a shippable low score — the run stops here.")


def gotcha(v: dict) -> str:
    """What a reviewer needs to see on the object itself."""
    if v["absent"]:
        return ("FAITHFULNESS: the description claims " + "; ".join(v["absent"][:3])
                + " — no such restriction is in the statement. The words are wrong, not the "
                  "SQL. Correct the description before relying on it.")
    if v["undeclared"]:
        return ("FAITHFULNESS: the description asserts a restriction ("
                + ", ".join(v["undeclared"]) + ") but declares no checkable claim, so nothing "
                "verified it against the statement.")
    if v["unresolved"]:
        return ("FAITHFULNESS: " + "; ".join(v["unresolved"][:3]) + " could not be located "
                "behind a CTE or subquery. Unverified, which is not the same as wrong.")
    return ""
