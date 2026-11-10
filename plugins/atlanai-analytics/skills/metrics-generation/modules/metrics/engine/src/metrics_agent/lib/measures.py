# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""What a METRIC is, and which of the statements computing it speaks for it.

The D/R tracks this module serves exist because the first ones answered a different
question. They grouped candidates by the **output alias** a query happened to give its
column, so one measure written by five analysts became five metrics, and they derived each
metric's registry identity from that grouping — so changing how we group changed every
identity, and a re-push replaced the layer instead of revising it.

Three decisions live here, and nothing else in the pipeline may re-make them:

* **A metric is a measure.** Its identity is the normalised formula and the table it is
  computed over — never a name, never an aggregation label, never a set of base tables.
* **One measure, one metric.** The same formula computed over three tables is ONE metric.
  The most-executed statement speaks for it; the others stay retrievable as sample queries
  and ride on `provenance.derived_from`.
* **Not every aggregate is a measure.** `MAX(account_name)` grouped by an account id is a
  label carried through a GROUP BY, and publishing it as a metric is how a governed layer
  fills with things nobody would ever ask for.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

_ALIAS_QUALIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.")
_WRAPPER = re.compile(r"(?i)^\s*(MAX|MIN|ANY_VALUE)\(\s*([A-Za-z_][\w.]*)\s*\)\s*$")

# A column whose NAME says it holds a quantity or a point in time. Consulted only where the
# warehouse could not answer: a datatype read from the schema is a fact, and a suffix is a
# guess, so the guess never overrides the fact.
_DATE_NAME = re.compile(r"(?i)(_date|_dt|_at|_ts|_time)$|^(date|dt|ts)$|date_|timestamp")
_NUMERIC_NAME = re.compile(
    r"(?i)(amount|arr|acv|mrr|revenue|price|value|cost|count|total|sum|qty|quantity|"
    r"score|rate|pct|percent|ratio|days|age|duration|_ms$|_n$|num|volume|size|weight)")
_DATE_TYPE = ("date", "timestamp", "datetime")
_NUMERIC_TYPE = ("int", "num", "dec", "float", "double", "real", "bigint", "smallint", "money")


def normalise_formula(expr: str) -> str:
    """The measure, with the accidents of one query removed.

    Table qualifiers go because the alias a statement gives a table is an accident of that
    statement — `la.arr_c` and `v.arr_c` are one column read through two aliases, and keeping
    them apart is what split one measure into several.
    """
    t = " ".join(_ALIAS_QUALIFIER.sub("", str(expr or "").lower()).split())
    # Space AROUND punctuation is formatting, not meaning: `sum( x )` and `sum(x)` are one
    # measure, and leaving the difference in made them two identities.
    return re.sub(r"\s*([(),])\s*", r"\1", t)


def measure_identity(formula: str, entity: str) -> str:
    """The stable key for a metric: its formula and the table it is computed over.

    IDENTITY MUST OUTLIVE THE PIPELINE THAT DERIVED IT. The old key was
    `family_key(name, aggregation_type, base_tables)` — every part of which changes when the
    grouping rules change. Regrouping therefore minted 514 new identities, the registry saw a
    layer it had never met, and 581 artifacts were archived instead of revised. A metric that
    is still the same measure over the same table keeps this key, so the registry appends a
    revision and bumps `definition.version`, which is what it is for.

    `count(*)` over forty tables is forty metrics, so the entity is in the key; the name,
    the aggregation label and whatever else the query joined are not.
    """
    basis = f"{normalise_formula(formula)}|{str(entity or '').strip().lower()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def is_label_passthrough(formula: str, datatype_of: Any = None) -> bool:
    """Is this aggregate a MEASURE, or a label being carried through a GROUP BY?

    `SELECT account_id, MAX(account_name) ... GROUP BY account_id` wraps the label so the
    engine will project it; nobody is measuring anything. Measured on a real layer, 114 of
    514 published metrics were this shape, and 46 of them wrapped a text column.

    The test is narrow on purpose: exactly `MAX`/`MIN`/`ANY_VALUE` of ONE bare column, with
    no arithmetic, no CASE and no second argument. `MIN(first_touch_date)` is a real measure
    and so is `MAX(amount)`, so a date or a number is never refused.

    `datatype_of` maps a bare column name to its warehouse datatype. Where it answers, that
    answer decides — a datatype read from the schema is a fact and a suffix is a guess, so
    the guess never overrides the fact. Where it does not answer, the column NAME is
    consulted, and a column that suggests neither a quantity nor a time is REFUSED: this
    wrapper shape is a label far more often than it is a measure, and a governed layer that
    publishes `MAX(SALES_REGION)` as a metric is the complaint this track exists to answer.
    The count refused this way is reported by the stage, so the trade is visible rather than
    silent — and resolving datatypes from the warehouse first makes it small.
    """
    m = _WRAPPER.match(str(formula or ""))
    if not m:
        return False
    column = m.group(2).rsplit(".", 1)[-1]
    declared = ""
    if callable(datatype_of):
        declared = str(datatype_of(column) or "").strip().lower()
    elif isinstance(datatype_of, dict):
        declared = str(datatype_of.get(column) or datatype_of.get(column.lower()) or "").lower()
    if declared:
        if declared.startswith(_DATE_TYPE) or declared.startswith(_NUMERIC_TYPE):
            return False
        return True                      # the schema says text/boolean: a label
    return not (_DATE_NAME.search(column) or _NUMERIC_NAME.search(column))


def _popularity(c: dict) -> int:
    for key in ("execution_count", "executions", "popularity"):
        v = (c.get("provenance") or {}).get(key) if isinstance(c.get("provenance"), dict) else None
        v = c.get(key) if v is None else v
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def _last_seen(c: dict) -> str:
    for key in ("last_seen", "last_executed_at", "observed_at"):
        v = c.get(key) or ((c.get("provenance") or {}).get(key)
                           if isinstance(c.get("provenance"), dict) else None)
        if v:
            return str(v)
    return ""


# ------------------------------------------------------------------ provenance
# The lane a verified query came from is the FIRST ranking key. A BI measure was named and
# validated by someone in the business; an analyst's ad-hoc statement was not. Where both
# compute the same measure, the BI one is the metric and the SQL one is corroboration.
#
# `merged` is the contract's word for a BI statement the query log also carries verbatim:
# evidenced by both lanes, so it outranks a BI statement nobody has been seen running.
# `bi` was declared by a modeller. `history` is an analyst's own SQL.
#
# LEXICOGRAPHIC, DELIBERATELY. A weighted score has a crossover point, and the crossover lands
# exactly where the rule matters most — the estate where an analyst query has 50,000 executions
# and the BI measure sits on one dashboard. The gap between `bi` and `history` is a wall, not
# a magnitude. The values live in policy (rule 5); the ORDER is asserted here, because a
# policy file that could place history above BI would let a config overturn a governance
# decision.
LANES = ("merged", "bi", "history")
LANE_RANK_DEFAULT: dict[str, int] = {"merged": 3, "bi": 2, "history": 0}
RANK_MODE = "lexicographic"

# Statements a modeller authored — the ones whose scoped filters are slicing rather than
# definition, so the LEAST-scoped one is the least-adulterated expression of the measure.
_AUTHORED_LANES = ("bi", "merged")


class LaneRankError(ValueError):
    """The lane ranking in policy contradicts the governance decision it is meant to carry."""


def assert_lane_rank(rank: dict[str, int], mode: str = RANK_MODE) -> None:
    if mode != RANK_MODE:
        raise LaneRankError(
            f"metrics.provenance.rank_mode={mode!r}; the only supported value is "
            f"{RANK_MODE!r}. A weighted rank has a crossover where popularity overturns "
            f"provenance, and that is the case the rule exists to prevent.")
    missing = [lane for lane in LANES if lane not in rank]
    if missing:
        raise LaneRankError(f"metrics.provenance.lane_rank names no rank for {missing}")
    if not rank["bi"] > rank["history"]:
        raise LaneRankError(
            f"metrics.provenance.lane_rank places history ({rank['history']}) at or above bi "
            f"({rank['bi']}). No amount of query-log popularity may outrank a BI-declared "
            f"measure; that is a decision, not a threshold.")
    if rank["merged"] < rank["bi"]:
        raise LaneRankError(
            f"metrics.provenance.lane_rank places merged ({rank['merged']}) below bi "
            f"({rank['bi']}); a BI statement the log also carries is evidenced by both lanes.")


def lane_rank(policy=None) -> dict[str, int]:
    """The lane ranking, from policy where one is given, asserted either way."""
    rank, mode = dict(LANE_RANK_DEFAULT), RANK_MODE
    if policy is not None:
        rank = {str(k): int(v) for k, v in dict(policy.get("metrics.provenance.lane_rank")).items()}
        mode = str(policy.get("metrics.provenance.rank_mode"))
    assert_lane_rank(rank, mode)
    return rank


def lane_of(c: dict) -> str | None:
    lane = c.get("lane")
    if not lane and isinstance(c.get("provenance"), dict):
        lane = c["provenance"].get("lane")
    return str(lane) if lane else None


def popularity_of(provenance: dict | None) -> tuple[int, str]:
    """How often the estate ran a statement, and what that number is MEASURED IN.

    A history statement is popular because analysts ran it. A BI statement has no execution
    count at all, so the NAMED substitute is how many dashboards surface it — recorded on the
    object rather than defaulted silently, the same rule the estate profiles follow for an
    absent lane. Presence, not a threshold: any placement at all is placement.

    A BI unit with NO dashboard placement is what a `.bim` yields: it has no report layer, so
    its units come from the model's own curation and carry an empty `dashboards` list. Calling
    that "dashboard placement" would assert a placement nobody made — curation must not be
    counted as one. The substitute is named for what it
    actually is, and it ranks below any real placement because it is 0.
    """
    prov = provenance or {}
    hist = prov.get("history") or {}
    bi = prov.get("bi") or {}
    n_dash = int(bi.get("n_dashboards") or 0)
    if hist.get("n_exec") is not None:
        return int(hist["n_exec"]), "query-log executions"
    if bi and n_dash:
        return n_dash, "bi dashboard placement"
    if bi:
        return 0, "bi model curation (no dashboard placement)"
    return 0, "none available"


def scoped_filter_count(c: dict) -> int:
    """Every predicate the statement carries beyond the measure's own expression."""
    keys = ("definitional_filters", "slicing_filters", "parameter_filters", "escalated_filters")
    if any(k in c for k in keys):
        return sum(len(c.get(k) or []) for k in keys)
    return len(c.get("filters") or [])


def rank_key(c: dict, rank: dict[str, int] | None = None) -> tuple:
    """The total order over the statements computing one measure. Bigger wins.

    1. lane — lexicographic; a lower lane never overtakes a higher one.
    2. fewest scoped filters — among AUTHORED statements only: several
       widgets computing one measure differ in the filters their dashboards fold in, and
       the least-scoped statement is the closest to the measure's own definition. An
       analyst's statement is ranked on adoption instead: its filters are its question.
    3. popularity — executions for history, dashboard placement for BI.
    4. recency — where two statements are run equally often, the newer one reflects how the
       estate computes it now.
    5. vq_id — so a re-run over identical inputs picks the same winner.
    """
    rank = rank or LANE_RANK_DEFAULT
    lane = lane_of(c)
    # A statement whose lane is unrecorded ranks as an analyst's: nothing may be PROMOTED by
    # a missing field.
    lane_r = rank.get(lane, rank["history"]) if lane else rank["history"]
    fewest = -scoped_filter_count(c) if lane in _AUTHORED_LANES else 0
    return (lane_r, fewest, _popularity(c), _last_seen(c), str(c.get("vq_id") or ""))


def pick_winner(candidates: Iterable[dict], rank: dict[str, int] | None = None) -> dict | None:
    """Which statement SPEAKS FOR the measure. See `rank_key` for the order.

    A measure computed by four statements is one metric, not four, and something has to say
    which statement its canonical body is built from. Provenance says it first — a BI-declared
    measure over an analyst's reproduction of it, always — and only among equals does the
    estate's own query log vote.
    """
    ranked = sorted((c for c in candidates if c), key=lambda c: rank_key(c, rank), reverse=True)
    return ranked[0] if ranked else None


def winner_basis(candidates: Iterable[dict], winner: dict | None,
                 rank: dict[str, int] | None = None) -> str:
    """Names the rule that chose the winner, so a reviewer can judge whether it chose well.

    Recorded on the object rather than felt — the fewest-filters default is marked for review
    after the first real run, and a review needs to see which key decided each case.
    """
    members = [c for c in candidates if c]
    if winner is None or not members:
        return "no statements"
    rank = rank or LANE_RANK_DEFAULT
    w_lane = lane_of(winner) or "unrecorded"
    lanes = sorted({lane_of(c) or "unrecorded" for c in members},
                   key=lambda l: -rank.get(l, rank["history"]))
    peers = [c for c in members if (lane_of(c) or "unrecorded") == w_lane]
    parts: list[str] = []
    if len(lanes) > 1:
        parts.append(f"lane {w_lane} outranks {', '.join(l for l in lanes if l != w_lane)} "
                     f"(lexicographic)")
    if len(peers) > 1:
        if w_lane in _AUTHORED_LANES:
            n_f = scoped_filter_count(winner)
            tied = [c for c in peers if scoped_filter_count(c) == n_f]
            parts.append(f"fewest scoped filters ({n_f}) among {len(peers)} {w_lane} statements")
            if len(tied) > 1:
                parts.append(f"tie among {len(tied)} broken by dashboard placement, then unit id")
        else:
            pop = _popularity(winner)
            tied = [c for c in peers if _popularity(c) == pop]
            parts.append(f"most executions ({pop}) among {len(peers)} {w_lane} statements")
            if len(tied) > 1:
                parts.append(f"tie among {len(tied)} broken by recency, then vq_id")
    if not parts:
        parts.append(f"sole statement ({w_lane})")
    return "; ".join(parts)


# Verdicts under which the warehouse REFUSED a statement. `empty` ran; `skipped_no_substrate`
# and `parse_ok` are the absence of an answer, not a refusal.
WITNESS_REFUSED = ("fail", "missing_object", "parse_failed")


def compiler_suspect(winner: dict | None, members: Iterable[dict]) -> dict | None:
    """The BI statement fails the witness, an analyst's passes.

    The winner is NOT swapped. The BI statement was compiled by us from a modeller's
    definition; the analyst's reproduction running where ours does not says the COMPILER is
    wrong, and promoting the analyst's SQL would hide a defect the compile loop exists to fix.
    So the bucket is named here and routed to `a3b_review` as a candidate rule row.
    """
    if not winner or lane_of(winner) not in _AUTHORED_LANES:
        return None
    if winner.get("witness_result") not in WITNESS_REFUSED:
        return None
    passing = sorted({str(m["vq_id"]) for m in members
                      if m is not winner and lane_of(m) == "history"
                      and m.get("witness_result") == "pass" and m.get("vq_id")})
    if not passing:
        return None
    return {"bi_vq_id": str(winner.get("vq_id")),
            "witness_result": winner["witness_result"],
            "witness_error": winner.get("witness_error"),
            "history_passing": passing}


TIERS =("bi_declared", "bi_confirmed", "history_only")


def tier_of(body_lane: str | None, bucket_lanes: Iterable[str | None]) -> tuple[str, str]:
    """The trust tier of a card, from the lane of the closure ACTUALLY PUBLISHED.

    Named by provenance, never by correctness — `bi_declared`, not `verified` — and a
    separate axis from the witness label: `bi_declared` + `parse_only` is a coherent state and
    both halves are useful.

    | tier           | condition                                                       |
    | `bi_declared`  | body from a BI statement, no history statement in the bucket    |
    | `bi_confirmed` | body from BI, >= 1 history statement computes the same measure  |
    | `history_only` | body from an analyst's statement — whether or not BI exists     |

    The last row includes the case where a BI closure is withheld and the history body
    published in its place is tiered by what shipped, and the basis says BI was there.
    """
    lanes = {l for l in bucket_lanes if l}
    has_hist = bool(lanes & {"history", "merged"})
    has_bi = bool(lanes & {"bi", "merged"})
    if body_lane in _AUTHORED_LANES:
        if has_hist:
            return "bi_confirmed", (f"body from a {body_lane} statement; history computes the "
                                    f"same measure")
        return "bi_declared", f"body from a {body_lane} statement; no history statement in bucket"
    if has_bi:
        return "history_only", ("body from a history statement although BI computes this "
                                "measure — the BI closure was not published")
    return "history_only", "no BI statement computes this measure"


def group_by_measure(candidates: Iterable[dict],
                     into: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """Candidates grouped into one bucket per measure, keyed by `measure_identity`.

    `into` maps a candidate_id to the measure it FOLDS into (see `lib/fold.py`): a history
    statement whose entity is lineage-equivalent to a BI measure's joins that measure's bucket
    instead of its own, where the lane rank makes it a loser. The fold never changes the BI
    bucket's key, so the BI metric's `source_id` is untouched.
    """
    out: dict[str, list[dict]] = {}
    for c in candidates:
        key = (into or {}).get(str(c.get("candidate_id"))) or measure_identity(
            c.get("expr") or c.get("formula") or "",
            c.get("source_table") or c.get("entity") or "")
        out.setdefault(key, []).append(c)
    return out


def is_foreign_population(formula: str, entity: str) -> bool:
    """Is this 'measure' computed over a population the card does not declare?

    A SCALAR SUBQUERY BRINGS ITS OWN FROM. `SELECT (SELECT COUNT(*) FROM
    landing.support.ticket WHERE account = 'A-1001') AS new_tickets` has no top-level FROM at
    all: the entity is inferred from whatever else the shape touched, and the card then
    states a population its own statement never reads. Measured on this estate, one such card
    reached D4b claiming `ANALYTICS.CX.CALL_RECORDINGS` while counting support tickets for one
    hardcoded account, and blocked the layer — correctly, but at the last gate, after 464
    cards had been built.

    The test is narrow on purpose. A subquery over a CTE the statement defines is a real
    measure over the statement's own data and stays: six of the twelve subquery formulas on
    this estate are exactly that (`(SELECT COUNT(*) FROM ai_ready_accounts)`), and refusing
    them would throw away good cards. Only a QUALIFIED name — a physical table, dotted — that
    differs from the declared entity is a foreign population.
    """
    f = str(formula or "").strip()
    if not re.match(r"^\(\s*select\b", f, re.I):
        return False
    tail = str(entity or "").rsplit(".", 1)[-1].lower()
    for t in re.findall(r"\bfrom\s+([A-Za-z0-9_\.]+)", f, re.I):
        if "." in t and t.rsplit(".", 1)[-1].lower() != tail:
            return True
    return False
