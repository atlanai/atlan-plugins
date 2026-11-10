#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""D4b — does the card agree with itself, and does the layer agree with itself?

D4 asks whether a card is well-formed and whether its statement compiles. Neither question
catches a card whose own sentences contradict each other, and the layer this replaces was
full of those: a population naming four restrictions above a `filters` list holding one; a
column pinned as definitional and offered as a knob in the same card; an `entity` that is not
a table the statement reads.

A contradiction BLOCKS and an omission FLAGS, because the two fail differently: an omission
leaves a reader knowing less, a contradiction leaves them acting on something false while the
card looks complete. There is no force flag.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys


import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402

from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import consistency as K  # noqa: E402
from metrics_agent.lib import dialect as _dialect
from metrics_agent.lib import stage  # noqa: E402

STAGE = "d4b_consistency"
IN = "metric_cards.json"
OUT = "consistency_report.json"


def _tables(sql: str, dialect: str) -> set[str] | None:
    """The tables a card's `sql` reads. A card carries its FROM clause (`T`, or `T JOIN U ON
    ...`), not a statement, so it is read as the FROM of one; a whole statement is read as is.
    Parsed bare, a clause with a JOIN failed and a bare `DB.S.T` read as a column, so the
    entity check was skipped or wrong for every card."""
    text = str(sql or "").strip()
    if not text:
        return None
    if not re.match(r"(?is)^\(?\s*(select|with)\b", text):
        text = f"SELECT * FROM {text}"
    try:
        tree = sqlglot.parse_one(text, read=dialect)
    except Exception:
        return None                      # unparseable is D4's problem, not this gate's
    return {(t.name or "").lower() for t in tree.find_all(exp.Table)}


def _prior_report(ctx: stage.Ctx) -> dict:
    try:
        prior = json.loads((ctx.run / OUT).read_text())
    except (OSError, ValueError):
        return {}
    return prior if isinstance(prior, dict) else {}


def _withheld_before(ctx: stage.Ctx) -> set[str]:
    """Names and source_ids an earlier run of this gate already withheld."""
    return {str(x).lower() for x in _prior_report(ctx).get("withheld") or []}


def _extra(p) -> None:
    p.add_argument("--withhold", nargs="*", default=None, metavar="REF",
                   help="metric name or source_id to DROP from the layer, by name. Each is "
                        "recorded as a decision with the reason; every other contradiction "
                        "still blocks. This is not a force flag — a force flag overrides "
                        "every check blindly, this withholds cards a person named.")
    p.add_argument("--withhold-reason", default="",
                   help="why these cards were withheld; goes on the record beside them")


def body(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN)
    cards = doc.get("metrics") or []
    dialect = _dialect.of(doc, IN)

    findings: list[dict] = []
    for c in cards:
        findings += K.within(c, sql_tables=_tables(c.get("sql"), dialect),
                             dialect=dialect)
    findings += K.across(cards)

    # WITHHOLDING IS A DECISION, NOT AN OVERRIDE. A named card is dropped from the layer and
    # its contradictions go with it; everything else still blocks. D6 already withholds a
    # metric whose statement will not compile — this is the same act, made by a person, and
    # recorded the same way so a reader later asks "where did that metric go?" and finds out.
    want = {str(x).strip().lower() for x in (ctx.args.withhold or []) if str(x).strip()}
    withheld = _withheld_before(ctx)
    # WHY each card was withheld, on the report itself. The decision also goes to the trace,
    # but a run with no tracing observer has no trace: the reason must survive on disk.
    because = {str(k): str(v) for k, v in
               (_prior_report(ctx).get("withheld_because") or {}).items()} if withheld else {}
    if want:
        keep, dropped = [], []
        for c in cards:
            if str(c.get("name", "")).lower() in want or str(c.get("source_id", "")).lower() in want:
                dropped.append(c)
            else:
                keep.append(c)
        for c in dropped:
            ctx.decide("card", str(c.get("source_id") or c.get("name")), "withheld",
                       because=(ctx.args.withhold_reason
                                or "named by the operator at the consistency gate"),
                       evidence={"name": c.get("name"), "entity": c.get("entity"),
                                 "formula": c.get("formula")})
        ctx.gate("withhold_cards",
                 "which cards should be dropped so the layer can continue?",
                 sorted({str(c.get("name")) for c in dropped}),
                 options=sorted(want),
                 reason=ctx.args.withhold_reason or None,
                 n_requested=len(want), n_matched=len(dropped))
        unmatched = want - {str(c.get("name", "")).lower() for c in dropped} - {
            str(c.get("source_id", "")).lower() for c in dropped} - withheld
        if unmatched:
            # A name that matched nothing is a typo, and a typo that is silently ignored
            # leaves the operator believing they dropped a card they did not.
            raise SystemExit(
                f"d4b: --withhold named {len(unmatched)} card(s) this layer does not hold: "
                f"{', '.join(sorted(unmatched))}.\n  Nothing was dropped. Check the name "
                f"against metric_cards.json.")
        cards = keep
        withheld |= {str(c.get(k, "")).lower() for c in dropped for k in ("name", "source_id")}
        for c in dropped:
            because[str(c.get("name"))] = (ctx.args.withhold_reason
                                           or "named by the operator at the consistency gate")
        if dropped:
            # A re-run with the same --withhold finds the cards already gone: that is the
            # decision standing, not a typo, so it neither fails nor rewrites the layer.
            doc["metrics"] = keep
            ctx.write(IN, doc, "metric_spec.schema.json")
        ctx.say(f"d4b: withheld {len(dropped)} card(s) at the operator's instruction; "
                f"{len(keep)} remain.")
        findings = []
        for c in cards:
            findings += K.within(c, sql_tables=_tables(c.get("sql"), dialect),
                             dialect=dialect)
        findings += K.across(cards)
        by_rule = {}
        for f in findings:
            by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1

    blocking = [f for f in findings if f.get("blocking")]
    flagged = [f for f in findings if not f.get("blocking")]
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1

    artifacts.dump(
        ctx.run / OUT,
        {"contract_version": "1.0", "clean": not blocking, "counts": by_rule,
         "blocking": blocking, "flagged": flagged,
         # What was checked, so a stage that publishes the layer can tell a card edited
         # after this gate from one it passed.
         "cards_sha": K.cards_digest(cards),
         **({"withheld": sorted(w for w in withheld if w)} if withheld else {}),
         **({"withheld_because": dict(sorted(because.items()))} if because else {})},
        "consistency_report.schema.json")
    ctx.entry.outputs.append(str(ctx.run / OUT))
    ctx.count(metrics=len(cards), blocking=len(blocking), flagged=len(flagged))
    for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        ctx.say(f"d4b: {rule} — {n}")
    if blocking:
        for f in blocking[:8]:
            ctx.say(f"d4b BLOCKED {f.get('metric')}: [{f['rule']}] {f['detail']}")
        # A GATE, NOT A CRASH. The run stops for a person to decide, and says what deciding
        # looks like. Recorded as an open gate so the session reads `running` — a run waiting
        # on a human has not failed.
        ctx.gate("consistency", "these cards contradict themselves — drop them and continue, "
                 "or fix the cause upstream?",
                 answer=None, options=[str(f.get("metric")) for f in blocking],
                 n_blocking=len(blocking), awaiting_decision=True)
        ctx.entry.notes = "awaiting_decision"
        raise stage.AnswerRefused(
            f"d4b: {len(blocking)} contradiction(s). A card that asserts two incompatible "
            f"things is worse than an absent one.\n  Decide: re-run with --withhold "
            + " ".join(f"{f.get('metric')!r}" for f in blocking[:4])
            + " --withhold-reason '...'\n  to drop them and continue, or fix the cause "
            f"upstream. There is no force flag.")
    ctx.say(f"d4b: clean. {len(cards)} card(s), {len(flagged)} flagged for review.")


def main() -> None:
    stage.main(STAGE, "d4b_consistency.py", __doc__.splitlines()[0], body, extra=_extra)


if __name__ == "__main__":
    main()
