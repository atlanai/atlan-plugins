#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""X4 — what does this metric DERIVE FROM?

A metric layer is not a list. `Account Credit Utilisation` is credits burned over credits
allocated, and both of those are published metrics on the same table; `Event Hot Leads` is
`Event Attendees` narrowed to one status; `Accounts Responded` and `Contacts Responded` are the
same question asked at two grains. A consumer who cannot see those edges will divide one metric
by another that does not nest inside it, or compare an account count to a contact count, and
the layer gives them no way to know.

WHY THIS IS A JUDGMENT AND NOT A QUERY. The shape is easy to spot and the MEANING is not. Two
metrics sharing a formula might be a derivation, a coincidence, or two different questions —
`COUNT(*)` over one table is all three somewhere in a real estate. And a derivation that is
exact matters differently from one that is nearly exact: on this estate `CSM AI-Signal ARR`
calls itself "a subset of the CSM's total book" and is not one, because the base applies
`CUSTOMER_STATUS_C = 'Active'` and the subset does not. A rule cannot tell those apart; it can
only put the candidates in front of somebody who reads SQL.

So this stage computes the CANDIDATES deterministically — same query body, same expression,
shared source statements, a ratio whose halves look like published metrics — and asks a person
which are real, what role each part plays, and how sure they are.

WHERE THE ANSWER GOES. Two places, because the registry has two and they are not the same:

  * `definition.depends_on` — the metric's own payload, a flat list of metric references. This
    is what travels with the card and what a consumer holding one metric reads.
  * the typed artifact graph (`POST /artifacts/metric/{id}/relationships`) — `rel_type`, `role`,
    `label`, `confidence` and free `attributes`, keyed on the whole edge so a re-run upserts.
    This is what the UI renders and what a reader browsing the estate walks.

Rule 3: the agent holds no model key. `--emit-prompt` bundles, `--from` ingests and checks.

    x4_relate.py --run R --emit-prompt
    x4_relate.py --run R --from answers/*.json
"""
from __future__ import annotations

import glob
import json
import pathlib
import re
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import card_sql as _card_sql  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "x4_relate"
IN = "metric_cards.json"
# What a grain variant or alternative becomes: `metric` declares no edge for either.
RELATED_NOT_EDGE = ("definition.extensions.related on both cards, no edge "
                    "(symmetric: assert once)")
OUT = "metric_relationships.json"
PROMPT = "x4_prompt.json"

# WHAT THE AUTHOR SAYS, AND WHAT GOES ON THE WIRE. These are two vocabularies and conflating
# them costs either precision or compatibility.
#
# **THE `metric` KIND DECLARES EXACTLY TWO EDGE TYPES, AND THE GATEWAY NOW ENFORCES THEM.**
# `GET /registry/v1/kinds/metric` advertises `x-gateway-edges` = `depends_on` and `replaced_by`,
# both directed at `metric`. Anything else is a bare 422 from the relationships route, which
# checks `rel_type` against the source kind's `EdgeSpec` list before it even looks up the target.
#
# This is a change under us and it is worth writing down. `rel_type` used to be an unchecked
# 64-character string: the first run of this stage wrote eight `alternative` edges and the
# registry took them, because nothing on that stack read the manifest it published. The check
# now exists, so those eight are edges of a type their own kind does not declare — written by a
# door that has since been shut.
#
# So a grain variant HAS NO EDGE TO BE. It is still a true and useful fact, and the schema that
# is there now has a place for it: `definition.extensions`, an open object the manifest
# describes as "experimental data preserved without assigning execution semantics". That is
# precisely what this is — a relation the vocabulary cannot type yet. It rides on the metric's
# own payload, needs no change to the gateway, and a reader that does not know the key ignores
# it instead of resolving a dangling neighbour.
#
#   relation        -> rel_type      role                                  channel
RELATIONS = {
    "derivation":    ("depends_on",  {"numerator", "denominator", "component"}, "edge"),
    "restriction":   ("depends_on",  {"base"}, "edge"),
    "grain_variant": (None,          set(), "extension"),
    "alternative":   (None,          set(), "extension"),
}
# An extension relation is symmetric in meaning — "the same question at another grain" reads
# the same backwards — so it is asserted once, exactly as the symmetric edge types were.
SYMMETRIC = {None}


def _extra(p) -> None:
    p.add_argument("--emit-prompt", action="store_true",
                   help="write the relationship bundle and stop")
    p.add_argument("--from", dest="answers", nargs="*", default=None,
                   help="reviewed answer file(s); globs allowed")


# ── reading a card mechanically ─────────────────────────────────────────────────────────

def _squash(t) -> str:
    """Whitespace- and case-blind, with every qualifier stripped: `f.units` == `v.units`."""
    t = re.sub(r"\s+", " ", str(t or ""))
    t = re.sub(r"\b[A-Za-z_][\w$]*\.(?=[A-Za-z_])", "", t)
    return re.sub(r"[\"']", "", t).strip().lower()


def _body_of(card: dict) -> str:
    """What this card measures OVER: its source, and the restrictions that define it.

    THE STRONGEST SIGNAL THERE IS. Two cards with the same body whose measures differ are
    the same query read two ways, which on this estate is what a ratio and its two halves
    look like: `Account Credit Utilisation`, `Account Credits Burned` and `Account Credits
    Allocated` share one source and differ only in the formula.

    It used to be cut out of the old `sql` with a string search for " FROM ". Both halves
    are declared fields now — `source.sql` IS the FROM clause and `formula` IS the measure
    — so the signal is read rather than recovered, and a card over one plain table (which
    carries no FROM text at all) compares on its entity instead of comparing as empty.
    """
    src = card.get("source") if isinstance(card.get("source"), dict) else {}
    frm = (card.get("sql") or src.get("sql") or card.get("entity") or src.get("entity") or "")
    mand = sorted(str(f) for f in (card.get("mandatory_filters") or []))
    return _squash(" ".join([str(frm), *mand]))


def _measure_of(card: dict) -> str:
    """The measure expression: what this card actually computes."""
    return _squash(str(card.get("formula") or ""))


def _is_ratio(c: dict) -> bool:
    return str((c.get("aggregation") or {}).get("type") or "").lower() == "ratio"


def _substantial(measure: str) -> bool:
    """Enough shape to be a measurement rather than a shared idiom.

    A named column inside the aggregate, and more than a bare `count(*)`: it is the column that
    makes one metric's expression recognisable inside another's.
    """
    m = str(measure or "")
    return len(m) >= 14 and bool(re.search(r"\w+\s*\(\s*(distinct\s+)?[a-z_]\w{2,}", m))


def _entity(c: dict) -> str:
    return str(c.get("entity") or "").rsplit(".", 1)[-1].lower()


def _counted(measure: str) -> tuple[str, str]:
    """(aggregate function, the thing counted) — `count(distinct contact_id)` -> (count, contact_id).

    What separates a GRAIN TWIN from a derivation: same table, same function, DIFFERENT column.
    `Accounts Responded` and `Contacts Responded` are one question asked of accounts and of
    people, and nothing about their expressions is shared, so every other rule here is blind to
    them. A consumer who compares the two without knowing is comparing a count of companies to
    a count of humans.
    """
    m = re.match(r"\s*(\w+)\s*\(\s*(distinct\s+)?([a-z_]\w*)", str(measure or ""))
    return (m.group(1), m.group(3)) if m else ("", "")


def _candidates(card: dict, others: list[dict]) -> list[dict]:
    """The metrics this one MIGHT derive from, each carrying why it is being offered.

    A SHORTLIST, NOT A VERDICT. Every reason here is structural — a shared query body, a
    contained expression, a shared source statement — and structure is evidence, never the
    answer. Two metrics can share all three and be unrelated: `COUNT(*)` over one table is a
    different question in a sign-off report and in a per-rep breakdown. The point is to put
    the twenty cards worth reading in front of a person instead of the other hundred and four.
    """
    mine_body, mine_measure = _body_of(card), _measure_of(card)
    mine_srcs = set(card.get("derived_from") or [])
    out = []
    for o in others:
        if o["name"] == card["name"]:
            continue
        why = []
        o_body, o_measure = _body_of(o), _measure_of(o)
        if mine_body and o_body == mine_body:
            why.append("same query body — identical FROM, joins, restrictions and grouping")
        # CONTAINMENT, BUT NOT OF A TRIVIAL EXPRESSION. `count(*)` is inside half the estate's
        # formulas and means nothing there; a nomination on that basis offered 324 pairs, most
        # of them noise. Only an expression with enough shape to be a measurement counts.
        if o_measure and mine_measure and o_measure != mine_measure \
                and o_measure in mine_measure and _substantial(o_measure):
            why.append("its measure appears inside this metric's own expression")
        if o_measure and mine_measure and o_measure == mine_measure \
                and _entity(o) == _entity(card):
            why.append("same expression over the same table — one may restrict the other")
        # A SUPPORTING REASON, NEVER A NOMINATING ONE. Two metrics lifted from one query share
        # its statements by construction — on this estate that alone offered 726 pairs, and a
        # sibling is not a derivation. It is worth SAYING once a candidate is already here for
        # a structural reason, because it tells the reader the two were written together.
        # A GRAIN TWIN: same table, same aggregate, a different thing counted.
        f_mine, col_mine = _counted(mine_measure)
        f_o, col_o = _counted(o_measure)
        if (f_mine and f_mine == f_o and col_mine and col_o and col_mine != col_o
                and _entity(o) == _entity(card)):
            why.append(f"same {f_mine}() over the same table, counting {col_o} where this "
                       f"counts {col_mine} — possibly one question at two grains")
        # A RATIO'S HALVES ARE PUBLISHED NEARBY. A ratio spells its denominator however the
        # statement did — `COUNT of evaluated tickets` against a standalone `COUNT(*)` — so
        # matching expressions misses most of them. Every measure on the ratio's own table is
        # worth a look, and a ratio has few enough neighbours for that to stay a shortlist.
        if not why and _is_ratio(card) and _entity(o) == _entity(card) and not _is_ratio(o):
            why.append("a measure on this ratio's own table — one of its halves may be here")
        shared = mine_srcs & set(o.get("derived_from") or [])
        if shared and why:
            why.append(f"{len(shared)} shared source statement(s)")
        if why:
            out.append({
                "metric": o["name"],
                "metric_ref": o.get("metric_ref"),
                "entity": o.get("entity"),
                "aggregation": (o.get("aggregation") or {}).get("type"),
                "formula": o.get("formula"),
                "mandatory_filters": o.get("mandatory_filters") or [],
                "filters": o.get("filters") or [],
                # A CANDIDATE IS JUDGED ON ITS SQL, so its SQL travels with it —
                # composed, because the card carries a FROM clause and a measure
                # rather than a statement.
                "sql": _card_sql.canonical_statement(o),
                "offered_because": why,
            })
    # Strongest first: a shared body outranks a shared source, which outranks nothing.
    return sorted(out, key=lambda d: (-len(d["offered_because"]), d["metric"]))


def _emit(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "metric_spec.schema.json")
    cards = doc.get("metrics") or []
    if not cards:
        raise SystemExit("x4: the card layer is empty. Nothing to relate.")

    tasks = []
    for c in cards:
        cands = _candidates(c, cards)
        tasks.append({
            "metric": c["name"],
            "metric_ref": c.get("metric_ref"),
            "entity": c.get("entity"),
            "aggregation": (c.get("aggregation") or {}).get("type"),
            "description": c.get("description"),
            "formula": c.get("formula"),
            "population": c.get("population"),
            "grain": c.get("grain"),
            "mandatory_filters": c.get("mandatory_filters") or [],
            "filters": c.get("filters") or [],
            "sql": c.get("sql"),
            "derived_from": c.get("derived_from") or [],
            "n_candidates": len(cands),
            "candidates": cands,
        })

    artifacts.dump(ctx.run / PROMPT, {
        "contract_version": "1.0", "stage": STAGE,
        "instruction": (
            "See skills/relate-metrics/SKILL.md. For each metric, say what it is "
            "genuinely derived from — and say nothing where it is derived from none, which is "
            "the common case. `candidates` is a shortlist built from STRUCTURE ALONE: a shared "
            "query body is evidence, never a verdict, and two metrics can share one and be "
            "unrelated. READ THE SQL: a derivation is real only if you can say what the target "
            "contributes. THE SHORTLIST DOES NOT BOUND THE ANSWER — every metric in this "
            "layer is listed under `all_metrics`, and a derivation across two tables will not "
            "be on any shortlist because nothing structural connects them. Name it anyway. "
            "State whether the relationship is EXACT: if the populations do not nest, the edge "
            "still holds but `exact` is false and `differs` must say what does not line up, "
            "because a consumer who divides one by the other needs exactly that sentence."),
        "relations": {
            "derivation": {"means": "this metric is COMPUTED FROM the target",
                           "roles": ["numerator", "denominator", "component"],
                           "writes": "depends_on"},
            "restriction": {"means": "this metric is the target with a further restriction — "
                                     "every row it counts is a row the target counts",
                            "roles": ["base"], "writes": "depends_on"},
            "grain_variant": {"means": "the same question as the target, counted at a "
                                       "different grain (accounts rather than contacts). NOT "
                                       "interchangeable — swapping them changes the number",
                              "roles": [],
                              "writes": RELATED_NOT_EDGE},
            "alternative": {"means": "two spellings of one measurement a reader may pick "
                                     "between", "roles": [],
                            "writes": RELATED_NOT_EDGE},
        },
        "n_metrics": len(tasks),
        "n_with_candidates": sum(1 for t in tasks if t["candidates"]),
        # EVERY METRIC, COMPACTLY. The shortlists are structural and a derivation that crosses
        # two tables has no structure to find it by — `Untouched Accounts` over the prospecting
        # table is the AI-Ready book from the accounts dimension minus those reached, and no
        # rule here proposes it. The answer is validated against this list, not the shortlists.
        "all_metrics": [{"metric": c["name"], "entity": c.get("entity"),
                         "aggregation": (c.get("aggregation") or {}).get("type"),
                         "description": c.get("description")} for c in cards],
        "tasks": tasks,
    }, "x4_prompt.schema.json", indent=1)
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.count(metrics=len(tasks),
              with_candidates=sum(1 for t in tasks if t["candidates"]),
              candidate_pairs=sum(t["n_candidates"] for t in tasks))
    ctx.say(f"x4: wrote {PROMPT} — {len(tasks)} metric(s), "
            f"{sum(1 for t in tasks if t['candidates'])} with at least one candidate "
            f"({sum(t['n_candidates'] for t in tasks)} pair(s) to judge). Answer with the "
            f"`relate-metrics` skill, then re-run with --from. THE AGENT HOLDS NO MODEL KEY.")


# What each edge says on its face, when the author wrote nothing better. The registry renders
# `label` per edge and it was left null on 29 of this estate's 37, so a reader saw a bare line
# between two names and had to open both metrics to learn what joined them.
_SAYS = {
    ("derivation", "numerator"): "numerator of this ratio",
    ("derivation", "denominator"): "denominator of this ratio",
    ("derivation", "component"): "a component this metric sums",
    ("restriction", "base"): "this metric narrows it",
    ("grain_variant", None): "the same question at another grain",
    ("alternative", None): "another spelling of the same measurement",
}


def _label(relation: str, role: str | None, exact: bool, authored) -> str | None:
    """The edge's own sentence, and a WARNING on it when the two do not nest.

    `exact` is the most decision-relevant fact on the graph — it answers "can I divide these?"
    — and it lived only in `attributes`, of which the registry says plainly that nothing reads
    it structurally. Four of this estate's 37 edges are inexact, which is exactly why those
    four should not look like the other 33. `CSM AI-Signal ARR` over `CSM Total ARR` can exceed
    100%, and a reader deserves to see that on the line rather than after computing it.

    An authored label always wins: the author saw the two queries.
    """
    said = str(authored or "").strip() or _SAYS.get((relation, role)) \
        or _SAYS.get((relation, None)) or relation.replace("_", " ")
    return said if exact else f"{said} — does not nest, see `differs`"


def _cycles(edges: list[dict]) -> list[list[str]]:
    """Every `depends_on` cycle, as the path that closes it.

    A METRIC CANNOT BE COMPUTED FROM ITSELF, however many hops away. This is the one thing
    about a derivation a rule can settle outright, and it is worth settling: a cycle published
    to the graph is a consumer walking the edges for ever, and it is silent — each edge on its
    own reads as sensible.
    """
    graph: dict[str, list[str]] = {}
    for e in edges:
        if e["rel_type"] == "depends_on":
            graph.setdefault(e["from"], []).append(e["to"])
    found, seen = [], set()

    def walk(node: str, path: list[str], on_path: set) -> None:
        for nxt in graph.get(node, []):
            if nxt in on_path:
                cycle = path[path.index(nxt):] + [nxt]
                if tuple(cycle) not in seen:
                    seen.add(tuple(cycle))
                    found.append(cycle)
                continue
            walk(nxt, path + [nxt], on_path | {nxt})

    for start in list(graph):
        walk(start, [start], {start})
    return found


def _ingest(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "metric_spec.schema.json")
    cards = doc.get("metrics") or []
    by_name = {c["name"].strip().lower(): c for c in cards}

    paths: list[pathlib.Path] = []
    for pat in (ctx.args.answers or []):
        paths.extend(pathlib.Path(p) for p in sorted(glob.glob(pat)))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        ctx.gate("metric_relationships", "have the relationships been assessed?", "not yet",
                 options=["ingest", "not yet"], evidence={"prompt": str(ctx.run / PROMPT)})
        ctx.entry.verdict = "blocked"
        raise SystemExit(
            f"x4: nothing to ingest. This run is WAITING on a person, not broken — "
            f"{ctx.run / PROMPT}\n  holds each metric with its candidates; answer it with the "
            f"`relate-metrics` skill and re-run.")

    edges, problems, seen_pairs = [], [], {}
    for path in paths:
        ctx.authored(path, kind="assessed_metric_relationships")
        ctx.note_input_path(path)
        payload = json.loads(path.read_text())
        try:
            artifacts.validate(payload, "metric_relationships.schema.json",
                               what=f"relationships in {path.name}")
        except artifacts.ArtifactError as exc:
            problems.append(str(exc))
            continue

        for i, item in enumerate(payload.get("relationships") or []):
            src = str(item.get("metric") or "").strip()
            where = f"{path.name} entry {i} ({src or 'unnamed'})"
            if src.lower() not in by_name:
                problems.append(f"{where}: no metric by that name in this layer")
                continue
            for d in (item.get("depends_on") or []):
                tgt = str(d.get("metric") or "").strip()
                relation = str(d.get("relation") or "").strip()
                role = (d.get("role") or "").strip() or None
                if tgt.lower() not in by_name:
                    problems.append(f"{where}: names {tgt!r}, which this layer does not hold")
                    continue
                if tgt.lower() == src.lower():
                    problems.append(f"{where}: relates {src!r} to itself")
                    continue
                if relation not in RELATIONS:
                    problems.append(
                        f"{where}: relation {relation!r} is not one this stage asserts "
                        f"({', '.join(sorted(RELATIONS))})")
                    continue
                rel, allowed, channel = RELATIONS[relation]
                if allowed and role not in allowed:
                    problems.append(
                        f"{where}: a {relation} to {tgt!r} needs a role from "
                        f"{sorted(allowed)}; got {role!r}")
                if not allowed and role:
                    problems.append(f"{where}: a {relation} takes no role; got {role!r}")
                # A SYMMETRIC EDGE IS ONE FACT. The registry stores `alternative` once and
                # reads it the same backwards, so asserting both directions would be two rows
                # claiming the same thing — and the second would be the first, reversed.
                key = (src.lower(), rel, tgt.lower())
                back = (tgt.lower(), rel, src.lower())
                if key in seen_pairs or (rel in SYMMETRIC and back in seen_pairs):
                    prior = seen_pairs.get(key) or seen_pairs[back]
                    problems.append(
                        f"{where}: {relation} between {src!r} and {tgt!r} was already "
                        f"asserted in {prior}" +
                        (" — the relation reads the same backwards, so one "
                         "direction states it"
                         if rel in SYMMETRIC else " — the edge is its own key"))
                    continue
                seen_pairs[key] = where
                conf = d.get("confidence")
                if conf is not None and not (0.0 <= float(conf) <= 1.0):
                    problems.append(f"{where}: confidence {conf} is outside 0..1")
                if not str(d.get("why") or "").strip():
                    problems.append(
                        f"{where}: {rel} to {tgt!r} carries no `why`. An edge nobody can "
                        f"check is worse than no edge — say what the target contributes.")
                if d.get("exact") is False and not str(d.get("differs") or "").strip():
                    problems.append(
                        f"{where}: {rel} to {tgt!r} is marked not exact and does not say what "
                        f"differs. That difference is the whole reason to record it.")
                edges.append({
                    "channel": channel,
                    "from": by_name[src.lower()]["name"],
                    "from_ref": by_name[src.lower()].get("metric_ref"),
                    "to": by_name[tgt.lower()]["name"],
                    "to_ref": by_name[tgt.lower()].get("metric_ref"),
                    "rel_type": rel,
                    "relation": relation,
                    "role": role,
                    "label": _label(relation, role, bool(d.get("exact", True)),
                                    d.get("label")),
                    "confidence": conf,
                    "exact": bool(d.get("exact", True)),
                    "attributes": {
                        # THE PRECISION THE SHARED VOCABULARY CANNOT CARRY. `depends_on` is
                        # the same word for a ratio's numerator and for a narrowed population;
                        # `alternative` is the same word for a substitute and for a sibling at
                        # another grain that is NOT a substitute. This says which.
                        "relation": relation,
                        "why": str(d.get("why") or "").strip(),
                        "exact": bool(d.get("exact", True)),
                        "differs": str(d.get("differs") or "").strip() or None,
                        "asserted_by": STAGE,
                    },
                })

    for cycle in _cycles(edges):
        problems.append("a metric cannot be computed from itself: " + " -> ".join(cycle))

    if problems:
        raise SystemExit("x4: the assessed relationships failed validation:\n  "
                         + "\n  ".join(problems[:40]))
    _write(ctx, doc, cards, edges)


def _write(ctx: stage.Ctx, doc: dict, cards: list[dict], edges: list[dict]) -> None:
    """The edge list, `depends_on` filled in on the cards, and `related` for what has no edge.

    THE CARDS ARE OTHERWISE UNTOUCHED. `depends_on` is a field `x3_emit` already writes (empty)
    and `r1b_envelopes` already carries into the payload, so filling it needs no change to
    either. `related` is the one addition, and it is carried to `definition.extensions` — the
    open object the metric manifest publishes for exactly this: a fact worth keeping that the
    typed vocabulary has no slot for. Every other key on every card is left as the author wrote
    it.
    """
    by_name = {c["name"]: c for c in cards}
    refs: dict[str, list[str]] = {}
    rel_ext: dict[str, list[dict]] = {}
    for e in edges:
        if e["channel"] != "edge":
            # NO EDGE EXISTS FOR THIS, so it rides on the metric instead of being dropped. Both
            # ends carry it, because an extension is not a graph the registry will traverse for
            # a reader — a consumer holding one card must be able to see the sibling without
            # already knowing to look at the other.
            for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
                rel_ext.setdefault(a, []).append({
                    "metric": b,
                    "relation": e["relation"],
                    "why": e["attributes"]["why"],
                    "exact": e["attributes"]["exact"],
                    "differs": e["attributes"]["differs"],
                    "confidence": e.get("confidence"),
                })
            continue
        # `depends_on` on the card is the COMPUTED-FROM half only. A consumer reading it is
        # asking what this number is built out of, and a sibling at another grain is not an
        # answer to that — which is now also why that sibling is not an edge.
        #
        # THE NAME, NOT OUR SLUG. `depends_on` is a list of METRIC REFERENCES and the only
        # identifier a reader can resolve is the one the registry registered: `name`, unique in
        # the workspace and frozen at create. `metric_ref` is this agent's own invention and is
        # never sent — writing it here published references that matched nothing, and the
        # console rendered every one as "declared in depends_on, not registered in this
        # workspace".
        refs.setdefault(e["from"], []).append(e["to"])

    # A RE-INGEST REPLACES THIS STAGE'S OWN ANSWER, never piles on top of it. `related` is
    # this stage's alone; of `depends_on`, only what the previous ingest asserted is taken
    # back — anything the card's author wrote stays. Without this, an edge removed from the
    # answer stayed on the card it was removed from.
    try:
        prior = json.loads((ctx.run / OUT).read_text()).get("edges") or []
    except (OSError, ValueError):
        prior = []
    asserted: dict[str, set] = {}
    for e in prior:
        asserted.setdefault(e.get("from"), set()).add(e.get("to"))
    for name, card in by_name.items():
        card.pop("related", None)
        if name in asserted and card.get("depends_on"):
            card["depends_on"] = [t for t in card["depends_on"] if t not in asserted[name]]
    for name, targets in refs.items():
        by_name[name]["depends_on"] = sorted(dict.fromkeys(
            [*(by_name[name].get("depends_on") or []), *targets]))
    for name, rels in rel_ext.items():
        by_name[name]["related"] = sorted(rels, key=lambda r: (r["relation"], r["metric"]))
    ctx.write(IN, doc, "metric_spec.schema.json")

    publishable = [e for e in edges if e["channel"] == "edge"]
    out = {"contract_version": "1.0", "stage": STAGE,
           "n_edges": len(publishable),
           # The registry writes an edge from the artifact it hangs off, so the list is
           # grouped the way `POST /artifacts/metric/{id}/relationships` is called. Only edge
           # types the `metric` kind declares appear here; the rest went to the cards.
           "edges": publishable}
    ctx.write(OUT, out, "metric_relationships.schema.json")
    kinds: dict[str, int] = {}
    for e in edges:
        kinds[e["relation"]] = kinds.get(e["relation"], 0) + 1
    ctx.count(edges=len(publishable), on_card=len(edges) - len(publishable),
              metrics_with_depends_on=len(refs), **kinds)
    shape = ", ".join(f"{k}={kinds[k]}" for k in sorted(kinds))
    ctx.say(f"x4: {len(edges)} relationship(s) across "
            f"{len({e['from'] for e in edges})} metric(s) — {shape}.")
    ctx.say(f"x4: {len(publishable)} publishable as `depends_on` edges; "
            f"{len(edges) - len(publishable)} carried on the cards instead, because the "
            f"`metric` kind declares no edge type for them. "
            f"{len(refs)} card(s) carry `depends_on`, {len(rel_ext)} carry `related`.")


def body(ctx: stage.Ctx) -> None:
    if ctx.args.emit_prompt:
        _emit(ctx)
    elif ctx.args.answers:
        _ingest(ctx)
    else:
        raise SystemExit("x4: pass --emit-prompt to assess, or --from to ingest")


def main() -> None:
    stage.main(STAGE, "x4_relate.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
