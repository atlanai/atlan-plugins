# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Does the card agree with ITSELF, and does the layer agree with itself?

Every other gate asks whether a card is well-formed or whether its SQL runs. None asks
whether the card's own sentences contradict each other, and the layer this replaces was full
of cards that did: a population reading "restricted by campaign = 'Spring Launch 2026'"
beside a `filters` list holding one unrelated predicate; an `exclusions` list restating every
filter it was meant to be a subset of; a column pinned as definitional and offered as a
tunable parameter in the same breath.

A contradiction is worse than an omission. An omission leaves a reader knowing less; a
contradiction leaves them acting on something false while the card looks complete. So the
checks that catch a card asserting two incompatible things BLOCK, and the checks that catch
a card leaving something undeclared FLAG.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import faithfulness as _faith

_OPS = re.compile(r"(?:>=|<=|<>|!=|=|\bILIKE\b|\bLIKE\b|\bIS\b|\bIN\b|>|<)")
_NOT = re.compile(r"^\s*NOT\s+", re.I)


def _norm(p: str) -> str:
    return re.sub(r"\s+", " ", str(p or "")).strip().lower()


def _column_of(declared: str) -> str:
    """The bare column a declared restriction is ABOUT, or "" when it is prose.

    Prose is a real and correct way to state a restriction the SQL expresses structurally —
    "the update was made through the automation engine" is true of a query that reaches it
    through a join and a JSON path. It has no operator, so there is nothing to look for, and
    it is skipped rather than reported absent.
    """
    body = _NOT.sub("", str(declared or "")).strip()
    m = _OPS.search(body)
    if not m:
        return ""
    names = re.findall(r"[A-Za-z_]\w*", body[:m.start()])
    return names[-1].lower() if names else ""


def _restriction_absent(declared: str, statements: list[str],
                        from_tables: set[str] | None) -> bool:
    """True when a declared restriction names a column the card's own queries never read.

    `statements` are the card's verified queries, lower-cased; `from_tables` the bare names of
    the tables its FROM clause reads (None when it has none or it did not parse). Prose has no
    column and is never absent.
    """
    col = _column_of(declared)
    if not col:
        return False
    # Qualified as DB.SCHEMA.TABLE.COLUMN or SCHEMA.TABLE.COLUMN: the table is named, and a
    # table the FROM clause does not read cannot be restricted by it. Two parts (`o.col`) may
    # be a query alias, so it says nothing about the table.
    body = _NOT.sub("", str(declared)).strip()
    op = _OPS.search(body)
    ref = re.search(r"([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)+)\s*$", body[:op.start()]) \
        if op else None
    parts = ref.group(1).lower().split(".") if ref else []
    if from_tables and len(parts) >= 3 and parts[-2] not in from_tables:
        return True
    if not statements:
        return False
    return not any(re.search(rf"\b{re.escape(col)}\b", s) for s in statements)


def within(metric: dict, *, dialect: str,
           sql_tables: set[str] | None = None) -> list[dict]:
    """Contradictions inside one card."""
    out: list[dict] = []

    def fail(rule: str, detail: str, blocking: bool = True) -> None:
        out.append({"rule": rule, "blocking": blocking, "detail": detail,
                    "metric": metric.get("name"), "metric_ref": metric.get("metric_ref")})

    filters = [str(x) for x in (metric.get("definitional_filters")
                                or metric.get("filters") or [])]
    exclusions = [str(x) for x in (metric.get("exclusions") or [])]
    mandatory = [str(x) for x in (metric.get("mandatory_filters") or [])]
    fnorm = {_norm(f) for f in filters}

    # W2 — AN EXCLUSION MUST BE EXPLICABLE, NOT IDENTICAL TO A FILTER. This rule matched the
    # exclusion text against the filter text, which worked when `exclusions` restated filters
    # verbatim. They are now prose saying what the query leaves out, and the cause is often a
    # JOIN rather than a predicate — "dropped by the inner join" has no filter to match. What
    # is still worth refusing is a card that claims exclusions while declaring nothing at all
    # that could exclude anything.
    # The FROM clause counts too: a two-pass measure is a derived table in `source.sql` (the
    # card's `sql`), and its own WHERE and joins are what leave rows out.
    restricts = bool(filters or mandatory or (metric.get("joins") or [])
                     or re.search(r"\b(?:JOIN|WHERE|QUALIFY|HAVING)\b",
                                  str(metric.get("sql") or ""), re.I))
    if exclusions and not restricts:
        fail("exclusion_not_a_filter",
             f"the card excludes {exclusions[0]!r} but declares no filter and no join that "
             f"could exclude anything")

    # W8 — A FILTER IS MANDATORY OR OPTIONAL, NOT BOTH. This rule read the other way round:
    # it wanted every mandatory filter to ALSO appear in `filters`, which held when the two
    # lists were one. They are now deliberately disjoint — `mandatory_filters` is what makes
    # the metric this metric, `filters` is what a caller may drop — so the old rule fired on
    # every correctly-authored card. Measured: 111 advisories on a 125-metric layer, none of
    # them a defect. What IS a contradiction is a filter declared in both, because the card
    # then says a caller may drop something the metric cannot survive without.
    both = {_norm(m) for m in mandatory} & fnorm
    if both:
        fail("filter_mandatory_and_optional",
             f"{len(both)} filter(s) are declared both mandatory and optional — a caller "
             f"cannot be told they may drop what the metric is defined by")

    # W1 — the population PROSE and the filters must describe the same restrictions. This is
    # the one you can read on the card and see is wrong: prose naming four restrictions above
    # a list holding one.
    # ONLY THE PROSE THIS STAGE WROTE, AND ONLY ITS PREDICATES. The population sentence ends
    # with a stock explanation and may quote a statement fragment; reading identifiers out of
    # the whole string picked up truncated table names (`list_f`) and reported them as
    # undeclared columns. Restrict to the clause after "restricted by"/"where", and to
    # identifiers that are followed by an operator AND are not the table itself.
    pop_full = str(metric.get("population") or "").split("Each restriction")[0]
    mres = re.search(r"(?i)(?:restricted by|where)\s+(.*)$", pop_full, re.S)
    pop = mres.group(1) if mres else ""
    entity_tail = str(metric.get("entity") or "").rsplit(".", 1)[-1].lower()
    pop_cols = {c.rsplit(".", 1)[-1].lower()
                for c in re.findall(r"([A-Za-z_][\w.]*)\s*(?:>=|<=|<>|!=|=|ILIKE|LIKE|IS|IN|>|<)", pop)}
    pop_cols = {c for c in pop_cols if c and c != entity_tail and not c.endswith("_")}
    fil_cols = {_column_of(f) for f in filters} | {p.get("column", "").rsplit(".", 1)[-1].lower()
                                                   for p in (metric.get("parameters") or [])
                                                   if isinstance(p, dict)}
    fil_cols |= {str(p.get("name", "")).lower() for p in (metric.get("parameters") or [])
                 if isinstance(p, dict)}
    # A function predicate (`CONTAINS(LOWER(TAGS_C), 'AI Ready')`) names its column inside a
    # call rather than beside an operator, so a filter list containing it still covers the
    # prose. Compare on the text of the filters as well as on their leading column.
    fil_text = " ".join(filters).lower()
    orphan = {c for c in pop_cols if c and c not in fil_cols and c not in fil_text}
    if orphan:
        fail("population_disagrees_with_filters",
             f"population names {sorted(orphan)[:4]} which appear in neither filters nor parameters")

    # W4 — a column cannot be both pinned and tunable.
    # A COLUMN MAY CARRY TWO PREDICATES. `close_date_as_of >= quarter_start` can be pinned
    # while `close_date_as_of <= :as_of` is a knob, and that is one restriction plus one
    # parameter, not a contradiction. What cannot stand is the SAME column AND operator
    # declared both ways: the card would then say one predicate is fixed and adjustable.
    def _op(pred: str) -> str:
        m = _OPS.search(_NOT.sub("", str(pred or "")))
        return (m.group(0) if m else "").strip().lower()

    pinned = {(_column_of(f), _op(f)) for f in filters}
    knobs = {(str(p.get("name", "")).lower(), str(p.get("op", "")).strip().lower())
             for p in (metric.get("parameters") or []) if isinstance(p, dict)}
    both = {c for c, o in pinned & knobs if c}
    if both:
        fail("pinned_and_tunable",
             f"{sorted(both)} is declared a definitional filter AND a parameter "
             f"with the same operator")

    # W6 — the declared aggregation must be what the formula does.
    # THE AGGREGATE NEED NOT BE THE OUTERMOST CALL. `COALESCE(MAX(x), 0)` and
    # `ROUND(COUNT(DISTINCT y) / z, 2)` declare `max` and `count` correctly; requiring the
    # formula to BEGIN with the name flagged 100+ cards that were right.
    agg = str((metric.get("aggregation") or {}).get("type") or "")
    # READ THE FORMULA. It used to hold a readable decomposition — "MAX of
    # TRY_TO_NUMBER(JSON_VALUE) where JSON_KEY is …" — which contains the word `max` and no
    # `max(`, so this check read `sql` instead. `formula` is now the measure expression and
    # `sql` is the FROM clause, which contains no aggregate at all and failed every card
    # that joined.
    formula = re.sub(r"\b[A-Za-z_][\w]*\.", "",
                     str(metric.get("formula") or "")).lower()
    if agg in ("sum", "count", "min", "max", "count_distinct"):
        # A dialect spells the same aggregate several ways: `APPROX_COUNT_DISTINCT` is a
        # count_distinct, `SUM` may arrive as `SUM_AGG`. Match the aggregate's name anywhere
        # a call is opened, not only the bare word.
        head = "count" if agg.startswith("count") else agg
        if not re.search(rf"\w*{head}\w*\s*\(", formula):
            fail("aggregation_not_the_formula",
                 f"declared {agg!r} but the formula calls no {head}(): {formula[:40]!r}")

    # W7 — the entity must be a table the statement reads.
    ent = str(metric.get("entity") or (metric.get("source") or {}).get("entity") or "")
    if sql_tables is not None and ent:
        if ent.rsplit(".", 1)[-1].lower() not in sql_tables:
            fail("entity_not_in_sql",
                 f"entity {ent.rsplit('.', 1)[-1]!r} is not a table the statement reads")

    # W3 — a dimension reached through a join that is not declared cannot be used.
    joins = {str(j.get("name")) for j in (metric.get("joins") or []) if isinstance(j, dict)}
    for d in (metric.get("dimensions") or []):
        rj = d.get("required_join") if isinstance(d, dict) else None
        if rj and rj not in joins:
            fail("required_join_undeclared", f"dimension {d.get('name')!r} needs join {rj!r}")
            break

    # W5 — a grain naming a column nobody declared is an undeclared dimension, not a lie.
    dims = {str(d.get("name", "")).lower() for d in (metric.get("dimensions") or [])
            if isinstance(d, dict)}
    grain = re.sub(r"^one row per ", "", str(metric.get("grain") or ""), flags=re.I)
    gcols = {c.strip().rsplit(".", 1)[-1].lower() for c in grain.split(",")}
    gcols = {c for c in gcols if c and " " not in c and not c.isdigit()}
    missing = {c for c in gcols if c not in dims}
    if gcols and missing == gcols:
        fail("grain_columns_undeclared",
             f"grain names {sorted(missing)[:4]}, none of them a declared dimension",
             blocking=False)

    # W9 — A RESTRICTION THE CARD'S OWN QUERY DOES NOT APPLY. The filter lists and
    # `source.sql` are two statements of one thing, and a reader who trusts the lists gets a
    # different population than a reader who runs the query.
    #
    # CHECKED ON THE COLUMN, NOT THE WHOLE PREDICATE. A declared filter deliberately keeps its
    # placeholder — `DATE >= :period_start` — so a caller can see the knob, while `source.sql`
    # has to be runnable and therefore pins a literal; the two can never match on the operand.
    # Comparing the column is what is both checkable and worth checking: a card that names a
    # column its query never touches is wrong whatever the operand says. A restriction written
    # as prose ("the update was made through the automation engine") has no column and is
    # skipped rather than reported, because no string search can confirm it and a rule that
    # cries wolf is a rule nobody reads.
    #
    # A WARNING, never blocking: the query may apply the restriction through a join or a
    # nested field under a name the predicate does not use.
    #
    # WHICH SQL. A card's `sql` is its FROM clause (`T`, or `T JOIN U ON …`), not a statement,
    # so it holds no WHERE and searching it for a filter's column flagged nearly every card
    # that declared one. The card's own queries are its `examples`: the verified statements it
    # was narrowed from, kept as they ran. A restriction is absent when its column is read by
    # none of them, or when it is qualified by a table the FROM clause does not read. With
    # neither to check against, nothing is said.
    sql_l = str(metric.get("sql") or "").lower()
    statements = [str(e.get("sql") or "").lower() for e in (metric.get("examples") or [])
                  if isinstance(e, dict) and e.get("sql")]
    if sql_l or statements:
        absent = [f for f in (mandatory + filters + exclusions)
                  if _restriction_absent(f, statements, sql_tables)]
        if absent:
            fail("declared_filter_absent_from_sql",
                 f"{len(absent)} declared restriction(s) name a column the card's own SQL "
                 f"never reads — {'; '.join(a[:80] for a in absent[:3])}", blocking=False)
        elif sql_l and _faith.RESTRICTION.search(str(metric.get("population") or "")) \
                and not (mandatory or filters or exclusions):
            fail("population_restricts_but_card_declares_nothing",
                 "the population says the metric is restricted and the card declares no "
                 "filter, no exclusion and no mandatory filter to say how", blocking=False)
    return out


def identity_filters(metric: dict) -> tuple[str, ...]:
    """A metric's mandatory filters, normalised: part of WHAT it counts. Two metrics with one
    formula over one table are different metrics when these differ ("Customer Count" and
    "AI-Ready Account Count" are both COUNT(*) over accounts), and the same metric when they
    agree."""
    out = set()
    for f in metric.get("mandatory_filters") or []:
        text = f.get("filter") if isinstance(f, dict) else f
        text = re.sub(r"\b[A-Za-z_][\w]*\.", "", str(text or "")).lower()
        text = re.sub(r"\s*([(),=<>])\s*", r"\1", " ".join(text.split()))
        if text:
            out.add(text)
    return tuple(sorted(out))


def across(metrics: list[dict]) -> list[dict]:
    """Contradictions between cards."""
    out: list[dict] = []

    def fail(rule: str, detail: str, blocking: bool = True, metric: str | None = None) -> None:
        # NAMED, so the gate can say which card to withhold rather than "None".
        out.append({"rule": rule, "blocking": blocking, "detail": detail,
                    **({"metric": metric} if metric else {})})

    by_name: dict[str, set[str]] = {}
    by_measure: dict[tuple, list[str]] = {}
    by_source: dict[str, list[str]] = {}
    refs: dict[str, list[str]] = {}
    for m in metrics:
        name = str(m.get("name") or "")
        formula = re.sub(r"\b[A-Za-z_][\w]*\.", "", str(m.get("formula") or "")).lower()
        formula = re.sub(r"\s*([(),])\s*", r"\1", " ".join(formula.split()))
        ent = str(m.get("entity") or "").lower()
        by_name.setdefault(name, set()).add(formula)
        # THE FROM CLAUSE IS PART OF WHAT IS COUNTED: one formula over one table, joined to
        # the top suppliers in one card and to nothing in the other, is two measures.
        source = " ".join(str(m.get("sql") or "").lower().split())
        by_measure.setdefault((formula, ent, source, identity_filters(m)), []).append(name)
        sid = str(m.get("source_id") or "")
        if sid:
            by_source.setdefault(sid, []).append(name)
        ref = str(m.get("metric_ref") or "")
        if ref:
            refs.setdefault(ref, []).append(name)

    for name, formulas in by_name.items():
        if len(formulas) > 1:
            fail("one_name_two_measures",
                 f"{name!r} is used by {len(formulas)} different formulas", metric=name)
    for (formula, ent, _source, _filters), names in by_measure.items():
        if len(names) > 1:
            fail("same_measure_published_twice",
                 f"{formula[:40]!r} over {ent.rsplit('.', 1)[-1]!r} published as {names[:3]}",
                 metric=names[-1])
    # NAMED, BOTH OF THEM: a collision is a fact about two cards, and a report that only
    # counts them leaves the operator to find which two by hand.
    for sid, names in by_source.items():
        if len(names) > 1:
            fail("source_id_collision",
                 f"{len(names)} metrics share source_id {sid!r}: {names[:5]}",
                 metric=names[-1])
    for ref, names in refs.items():
        if len(names) > 1:
            fail("metric_ref_collision",
                 f"{len(names)} metrics share metric_ref {ref!r}: {names[:5]}",
                 metric=names[-1])
    return out


# Fields a later stage owns. X4 decides `depends_on` and `related` AFTER the consistency gate
# (on purpose: an edge may never name a withheld card), so they are not what the gate checked.
_SET_AFTER_THE_GATE = ("depends_on", "related")


def cards_digest(cards: list[dict]) -> str:
    """A digest of what the consistency gate checked: every card, minus the fields a later
    stage is meant to fill. A stage that publishes the layer compares it with the one the gate
    recorded, so a card edited after the gate is never published as checked."""
    body = [{k: v for k, v in c.items() if k not in _SET_AFTER_THE_GATE} for c in cards]
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def refuse_partial(doc: dict, who: str) -> None:
    """Refuse a card layer X3 marked partial. A stage that builds something to publish calls
    this first: a partial layer publishes some metrics and silently drops the rest, and the
    registry or the glossary would then hold a layer that looks complete and is not."""
    part = doc.get("partial")
    if not part:
        return
    missing = list(part.get("unauthored") or [])
    raise SystemExit(
        f"{who}: metric_cards.json is a PARTIAL layer (x3 --partial): {len(missing)} merged "
        f"metric(s) have no card — {', '.join(missing[:6])}"
        + (" …" if len(missing) > 6 else "")
        + ".\n  A partial layer is for reviewing the contract on a few cards, never for "
          "publishing. Author the rest and re-run x3 without --partial.")
