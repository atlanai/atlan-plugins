# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""M1 — union the two lanes into ONE verified-query layer. BI takes precedence.

WHY THIS STAGE EXISTS. Both arms produce verified queries, and until now both wrote
`verified_queries.json`. `ctx.write` overwrites, so on an estate with BI *and* query history
whichever lane ran last silently replaced the other's work — no error, no warning, a
plausible-looking layer missing half the estate. Neither producer read the other's output, so
there was nowhere for the merge to happen. Here it is.

BI WINS A TIE, and the reason is evidence rather than preference. A BI statement was compiled
from a declared measure in a governed model: someone named the thing and the name is
authoritative. The same statement arriving from query history is an analyst reproducing that
logic ad hoc — identical SQL, weaker claim about what it is called.

But precedence is not deletion. Where both lanes hold the same statement, the BI object is
kept AND the history object's evidence is folded into it: adoption counts, the shapes that
produced it, its observed values. Dropping those would throw away the one thing BI cannot
know — how often real people actually ran this, and with which literals.

Two different things are reconciled here and they are not the same problem:

  * **Duplicates** — the same statement in both lanes. One object survives, carrying both
    lanes' evidence.
  * **Name conflicts** — different statements that happen to share a name. Both survive; the
    registry requires names unique per (workspace, kind), so the history one is disambiguated
    by its own id. Renaming the BI object instead would move the governed name, which is the
    thing that made BI authoritative in the first place.

DESCRIPTION IS NOT OPTIONAL, AND THIS IS WHERE THAT IS ENFORCED. The overlay used to be
applied `if the file happens to exist`, so a pipeline that simply never ran `m2_describe`
produced a complete-looking layer in which every object carried the estate's own alias and no
question at all. Nothing failed; the layer was just mute, and a mute layer is unsearchable —
a measured 14-point drop on the benchmark, from a stage nobody noticed had been skipped.

So a merge now REFUSES an undescribed layer. Two flags, and neither is a default:

  * `--pre-describe` is the bootstrap pass. `m2_describe` reads THIS stage's output, so the
    first merge necessarily precedes the prose; it says so and passes.
  * `--undescribed-ok REASON` is the human gate. An operator may decide to publish a layer
    with holes in it, and that decision is recorded with its reason — what is refused is
    the SILENT version, not the deliberate one.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys


from metrics_agent.lib import stage  # noqa: E402

STAGE = "m1_merge"
IN_BI = "verified_queries.bi.json"
IN_HISTORY = "verified_queries.history.json"
OVERLAY = "sql_descriptions.json"
OUT = "verified_queries.json"

_WS = re.compile(r"\s+")


def _key(sql: str) -> str:
    """What makes two verified queries THE SAME QUERY: the statement, whitespace-insensitive.

    Not the name (each lane names differently) and not the id (each lane derives it from its
    own identity). The statement is the only thing both lanes agree on.
    """
    return _WS.sub(" ", str(sql or "").strip()).lower()


def _fold_evidence(bi: dict, hist: dict) -> dict:
    """The BI object, plus what only history knows. BI's own fields are never overwritten."""
    out = dict(bi)
    hp = (hist.get("provenance") or {}).get("history")
    if hp:
        prov = dict(out.get("provenance") or {})
        prov["history"] = hp
        # The lane label becomes honest: this statement is evidenced by both lanes.
        # `merged` is the contract's word for that — the vocabulary is the schema's, not ours.
        prov["lane"] = "merged"
        cites = list(prov.get("evidence_citations") or [])
        cites.extend((hist.get("provenance") or {}).get("evidence_citations") or [])
        prov["evidence_citations"] = cites[:12]
        out["provenance"] = prov
    # Observed values are history's to contribute: BI knows the filters an author placed, the
    # log knows the values people actually passed.
    if not out.get("observed_values") and hist.get("observed_values"):
        out["observed_values"] = hist["observed_values"]
    for extra in (hist.get("aliases") or []):
        if extra not in (out.get("aliases") or []):
            out.setdefault("aliases", []).append(extra)
    return out


def _extra(p) -> None:
    p.add_argument("--pre-describe", action="store_true",
                   help="the bootstrap pass that produces the layer m2_describe will read; "
                        "the overlay is not expected yet")
    p.add_argument("--undescribed-ok", dest="undescribed_ok", default="",
                   help="publish a layer with undescribed statements anyway, giving the "
                        "reason. Recorded as a gate; never a default.")


def _without_times(value):
    """`value` with every `*_at` timestamp removed, for hashing content only."""
    if isinstance(value, dict):
        return {k: _without_times(v) for k, v in value.items() if not k.endswith("_at")}
    if isinstance(value, list):
        return [_without_times(v) for v in value]
    return value


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")

    def _lane(name: str) -> dict | None:
        if not (ctx.run / name).is_file():
            return None
        return ctx.read(name, "verified_queries.schema.json")

    bi_doc, hist_doc = _lane(IN_BI), _lane(IN_HISTORY)
    if bi_doc is None and hist_doc is None:
        raise SystemExit(
            f"m1: neither {IN_BI} nor {IN_HISTORY} exists. One lane must have run — this "
            f"stage merges layers, it does not invent one.")

    bi = (bi_doc or {}).get("objects") or []
    hist = (hist_doc or {}).get("objects") or []

    merged: list[dict] = []
    by_sql = {_key(o["sql"]): o for o in bi}
    used_names = {o["name"] for o in bi}

    folded = renamed = 0
    for o in bi:
        merged.append(o)
    for o in hist:
        k = _key(o["sql"])
        twin = by_sql.get(k)
        if twin is not None:
            # SAME STATEMENT, BOTH LANES. BI's object stands; history's evidence joins it.
            merged[merged.index(twin)] = _fold_evidence(twin, o)
            folded += 1
            continue
        obj = dict(o)
        if obj["name"] in used_names:
            # A NAME COLLISION IS NOT A DUPLICATE. Different statements that happen to share
            # a name both belong in the layer; the registry only requires the NAME be unique.
            obj["name"] = f"{obj['name']}-{str(obj['vq_id']).rsplit('-', 1)[-1][:8]}"
            renamed += 1
        used_names.add(obj["name"])
        merged.append(obj)

    # THE DESCRIPTIONS ARE APPLIED LAST, over the merged layer, because that is the only
    # point at which one statement has one object to carry them. Absent, every object keeps
    # the estate's own alias and says so on `name_provisional`.
    described = renamed_after = 0
    if (ctx.run / OVERLAY).is_file():
        desc = (ctx.read(OVERLAY, "sql_descriptions.schema.json").get("descriptions") or {})
        used_after: set[str] = set()
        for o in merged:
            d = desc.get(o["vq_id"])
            if not d:
                continue
            o["question"] = d["question"]
            if d.get("description"):
                o["description"] = d["description"]
            if d.get("name"):
                o["name"] = d["name"]
            o["name_provisional"] = False
            described += 1
        # A NAME THAT ARRIVED FROM PROSE CAN COLLIDE LIKE ANY OTHER. Re-checked here rather
        # than trusted, because the registry refuses the whole batch over one duplicate.
        for o in merged:
            if o["name"] in used_after:
                o["name"] = f"{o['name']}-{str(o['vq_id']).rsplit('-', 1)[-1][:8]}"
                renamed_after += 1
            used_after.add(o["name"])

    merged.sort(key=lambda o: o["vq_id"])
    layer = {
        "contract_version": "1.0",
        # THE LAYER'S VERSION, OVER ITS CONTENT. Hashed over the ids alone, a re-described
        # layer kept its version, so the registry could not tell the new prose from the old.
        # Identity is each object's `source_id`, never this.
        # A timestamp (a witness's `executed_at`) is when, not what: left in, every re-run
        # would be a new version.
        "layer_sha": hashlib.sha256(
            json.dumps(_without_times(merged), sort_keys=True).encode()).hexdigest()[:12],
        "dialect": (bi_doc or hist_doc)["dialect"],
        "generated_at": (bi_doc or hist_doc).get("generated_at"),
        "scope": ri["scope"],
        "profile": ri["profile"],
        "objects": merged,
    }
    ctx.write(OUT, layer, "verified_queries.schema.json")
    ctx.count(bi=len(bi), history=len(hist), merged=len(merged),
              folded_duplicates=folded, renamed_for_name_conflict=renamed,
              renamed_after_description=renamed_after)
    if described:
        ctx.say(f"m1: applied {described} authored description(s) from {OVERLAY}.")
    ctx.say(f"m1: {len(bi)} BI + {len(hist)} history -> {len(merged)} verified queries. "
            f"{folded} statement(s) were in BOTH lanes: the BI object stands and history's "
            f"adoption and observed values were folded into it, never dropped.")
    if bi_doc is None or hist_doc is None:
        only = "history" if bi_doc is None else "BI"
        ctx.say(f"m1: only the {only} lane produced a layer. That is the estate's shape, not "
                f"a merge failure — but if you expected both, one lane did not run.")

    # THE LAYER IS WRITTEN FIRST AND JUDGED SECOND, because `m2_describe` reads exactly this
    # file: refusing before writing it would make the prose impossible to author.
    # WHAT COUNTS AS MUTE, and why it is two conditions rather than one. A lane does not leave
    # `question` empty — lane H fills it with the placeholder "measure" and a description that
    # says no description has been written — so an empty-string test would pass a layer of 450
    # stubs. `name_provisional` is the flag the producing lane sets for exactly this, and it is
    # the authority. The empty-question test stays beside it to catch a lane that never sets
    # the flag at all, which would otherwise read as described.
    mute = [o["vq_id"] for o in merged
            if o.get("name_provisional") or not str(o.get("question") or "").strip()]
    ctx.count(described=described, undescribed=len(mute))
    if not mute:
        ctx.decide("descriptions", "layer", "complete",
                   because="every statement carries an authored question",
                   evidence={"described": described})
        return

    if ctx.args.pre_describe:
        ctx.decide("descriptions", "layer", "pending",
                   because="--pre-describe: this pass exists to produce the bundle m2 reads",
                   evidence={"undescribed": len(mute)})
        ctx.say(f"m1: {len(mute)} statement(s) are undescribed, which is expected on this "
                f"pass. Next: `metrics-agent stage m2_describe --run {ctx.run} "
                f"--emit-prompt`, author it with the `describe-verified-sql` skill, ingest "
                f"with --from, then re-run m1.")
        return

    if ctx.args.undescribed_ok:
        # A HUMAN GATE, NOT A SWITCH. The operator may publish a layer with holes; what the
        # trace must hold is that a person chose to, and why.
        ctx.gate("undescribed_layer",
                 f"publish {len(mute)} of {len(merged)} statement(s) with no authored "
                 f"question?", "yes", options=["yes", "no"],
                 reason=ctx.args.undescribed_ok,
                 evidence={"undescribed": len(mute), "described": described,
                           "examples": mute[:20]})
        ctx.decide("descriptions", "layer", "incomplete_accepted",
                   because=ctx.args.undescribed_ok,
                   evidence={"undescribed": len(mute), "described": described})
        ctx.say(f"m1: {len(mute)} statement(s) go out undescribed by decision — "
                f"{ctx.args.undescribed_ok!r}. They keep the estate's own alias and stay "
                f"flagged `name_provisional`.")
        return

    ctx.entry.verdict = "blocked"
    raise SystemExit(
        f"m1: {len(mute)} of {len(merged)} statement(s) have no authored question.\n"
        f"  A layer whose objects carry only the estate's own alias is unsearchable — the "
        f"reason this\n  check exists is a run that skipped `m2_describe` and lost 14 points "
        f"on the benchmark\n  without anything failing.\n\n"
        f"  Either:\n"
        f"    * run `metrics-agent stage m2_describe --run {ctx.run} --emit-prompt`, "
        f"answer the\n      bundle with the `describe-verified-sql` skill, ingest it with "
        f"--from, and re-run\n      this stage; or\n"
        f"    * re-run with --undescribed-ok \"<why>\" to publish the holes deliberately.\n\n"
        f"  First undescribed: {', '.join(mute[:5])}")


def main() -> None:
    stage.main(STAGE, "m1_merge.py", __doc__.splitlines()[0], body, _extra)


if __name__ == "__main__":
    main()
