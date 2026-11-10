#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""C5 — emit the verified-query layer. The run's canonical artifact.

Warehouse-neutral and registry-neutral by design, so the layer can be diffed, reviewed and
re-pushed without re-running anything upstream. `layer_sha` binds every downstream artifact to
exactly this content: one edit changes it, and a re-push is then visibly a new source revision
rather than a silent overwrite.

REFUSES A DIRTY VERIFY REPORT. C4 is the gate; this stage will not launder one.

DOMAIN is derived here for BI candidates and carried through for history ones, and it is
DETERMINISTIC in both cases — the workbook/report name or the warehouse schema, verbatim. No
clustering, no operator confirmation, no model. The per-domain dataset design was rejected
precisely because it needed those; domain survives as a `categories` value instead.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sys
from collections import Counter


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import slugs  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402
from metrics_agent.lib import witness as _witness  # noqa: E402

STAGE = "c5_emit"
OUT = "verified_queries.bi.json"


def body(ctx: stage.Ctx) -> None:
    ri = ctx.read("run_inputs.json", "run_inputs.schema.json")
    report = ctx.read("verify_report.json", "verify_report.schema.json")
    if not report["clean"]:
        raise SystemExit(
            "c5 refuses a dirty verify report. C4 is the gate and this stage will not launder "
            "it — fix the failures and re-run c4."
        )

    # THE SIDECARS ARE INPUTS LIKE ANY OTHER. A missing one is refused by name, with the
    # stage that writes it — not a FileNotFoundError from somewhere inside this stage.
    for name, producer in (("c4_candidates.json", "c4_gates"), ("c3_authored.json",
                                                                 "c3_questions"),
                           ("c4_flagged.json", "c4_gates")):
        if not (ctx.run / name).is_file():
            raise SystemExit(f"c5: {name} is missing from {ctx.run}. It is written by "
                             f"{producer}; run it (and the stages after it) before emitting.")
    cands = json.loads((ctx.run / "c4_candidates.json").read_text())["candidates"]
    ctx.entry.inputs.append(str(ctx.run / "c4_candidates.json"))
    authored = json.loads((ctx.run / "c3_authored.json").read_text())["authored"]
    ctx.entry.inputs.append(str(ctx.run / "c3_authored.json"))
    _c4 = json.loads((ctx.run / "c4_flagged.json").read_text())
    flagged = set(_c4["flagged"])
    # What C4 found out about the PROSE. It belongs on the object a reader sees, not only in
    # the verify report a reader does not.
    gate_gotchas = _c4.get("gotchas", {})
    ctx.entry.inputs.append(str(ctx.run / "c4_flagged.json"))

    objects = []
    for c in cands:
        cid = c["candidate_id"]
        a = authored.get(cid, {})
        w = c.get("witness")
        label = _witness.label_for(w, cid in flagged)

        # The ranking basis is recorded ON THE OBJECT so a reviewer can see WHY it ranked —
        # which matters most on a single-lane run, where a named substitute stands in for the
        # absent lane's signal.
        basis = c["provenance"].get("ranking_basis") or (
            "bi dashboard placement" if c["lane"] == "bi" else "query-log adoption")

        # A HISTORY statement that will not compile is NOT a defect in the statement. It came
        # verbatim from the query log, so it ran on the estate — what it did not survive is
        # OUR substrate: a clone that does not hold every table the original did, a two-part
        # name left unqualified by the run's own rewrite, or a role with narrower grants than
        # the session that wrote it. Rejecting it would blame the analyst for our rewrite, so
        # it is FLAGGED (`needs_review`, via label_for) and carries the reason. A BI-lane
        # statement is different: that one we authored, and a failure there is ours.
        witness_gotchas: list[str] = []
        _r = (w or {}).get("result")
        if _r in ("fail", "missing_object") and c["lane"] != "bi":
            witness_gotchas.append(
                f"SUBSTRATE, NOT THE QUERY: this statement is the one the estate ran — it "
                f"comes verbatim from the query log — but it does not compile against the "
                f"warehouse this run was pointed at ({_r}). Read that as a limit of the "
                f"substrate (a table the clone does not hold, a name this run left "
                f"unqualified, or a narrower role), not as a defect in the question. It is "
                f"flagged rather than withheld for exactly that reason. "
                f"{(w or {}).get('error') or ''}".strip())

        # FILTERED TO THE VIEWING USER: said on the object every consumer reads, by the
        # pipeline, not left to the author (the placeholders, never a name).
        vb = c.get("viewer_binds")
        if vb:
            witness_gotchas.append(
                "Filtered to the viewing user: bind "
                + " and ".join(f"{b['placeholder']} to the {b['of'].replace('_', ' ')}"
                               for b in vb["placeholders"])
                + " of the person asking. The statement returns that person's rows only, and "
                  "no name is stored with it.")

        # COMPARES WITH A WITHHELD LITERAL: the placeholders a consumer binds, never a value.
        wb = c.get("withheld_binds")
        if wb:
            witness_gotchas.append(
                "Compares with a person's address or name the BI file holds, never kept here: "
                "bind " + " and ".join(b["placeholder"] for b in wb["placeholders"])
                + " to the value the BI file's own formula or filter holds.")

        objects.append({
            "vq_id": cid,
            # Disambiguated below: the slug truncates, so two different questions sharing an
            # opening phrase collide on a name the gateway requires to be unique.
            "name": slugs.artifact_name("vq", a.get("question") or cid),
            "question": a.get("question") or c.get("question_draft") or cid,
            "description": a.get("description", ""),
            "sql": c["sql"],
            "domain": c["domain"],
            "tables": c["tables"],
            "joins": c.get("joins", []),
            "filters": [{"column": f["column"], "op": f["op"],
                         "value": (str(f["value"]) if f.get("value") is not None else None),
                         "stage": f.get("stage")}
                        for f in c.get("filters", [])],
            "group_by": c["group_by"],
            "grain": c["grain"],
            "klass": c["klass"],
            "aliases": a.get("aliases", []),
            "gotchas": a.get("gotchas", []) + gate_gotchas.get(cid, []) + witness_gotchas,
            "variants": c.get("variants", []),
            "observed_values": c.get("observed_values"),
            # Always empty HERE, and deliberately: D2 does not write back into this layer.
            # It emits `metric_refs.json` instead, so no stage mutates another's output and
            # `layer_sha` keeps identifying exactly what C5 produced. R1 does the join.
            "metrics": [],
            "label": label,
            "provenance": {
                "lane": c["provenance"]["lane"],
                "bi": c["provenance"].get("bi"),
                "history": c["provenance"].get("history"),
                "evidence_citations": (c["provenance"].get("evidence_citations", [])
                                       + a.get("evidence_citations", [])),
                "ranking_basis": basis,
            },
            "witness": w,
            **({"viewer_binds": vb} if vb else {}),
            **({"withheld_binds": wb} if wb else {}),
            # How close the SQL is to the BI tool's number (A3 + A4), carried as earned.
            **({"quality": c["quality"]} if c.get("quality") else {}),
        })

    objects.sort(key=lambda o: o["vq_id"])

    # NAMES MUST BE UNIQUE PER (workspace, kind) — the gateway refuses a duplicate outright
    # (`artifacts_name_idx`). The slug is built from the QUESTION and truncated, so two
    # genuinely different queries whose questions open with the same phrase collide: measured
    # here, 5 collisions across 680 records, including a pair whose questions differ only
    # after the truncation point.
    #
    # Disambiguated with the shape key the vq_id already carries, which is unique by
    # construction and stable across runs — so the name stays deterministic and a re-run
    # reconciles onto the same record rather than creating a second one. Only the LATER
    # member of a collision is suffixed, so the common case keeps its readable name.
    seen_names: dict[str, int] = {}
    n_disambiguated = 0
    for o in objects:
        base = o["name"]
        if base not in seen_names:
            seen_names[base] = 1
            continue
        suffix = o["vq_id"].split("-", 1)[-1][:6]
        o["name"] = slugs.artifact_name(base, suffix)
        seen_names[o["name"]] = 1
        n_disambiguated += 1
    if n_disambiguated:
        ctx.say(f"c5: {n_disambiguated} record name(s) collided after slug truncation and were "
                f"suffixed with their shape key. Two different questions sharing an opening "
                f"phrase must not share a name the gateway requires to be unique.")

    layer = {
        "contract_version": "1.0",
        "layer_sha": "",
        "dialect": ri["dialect"],
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": ri["scope"],
        "profile": ri["profile"],
        "objects": objects,
    }
    # layer_sha is computed over the content WITHOUT the sha field and without any timestamp
    # (generated_at, a witness's executed_at), so it identifies the layer's substance rather
    # than the moment it was written or checked.
    body_for_hash = {k: v for k, v in layer.items() if k not in ("layer_sha", "generated_at")}
    layer["layer_sha"] = artifacts.sha256_of(artifacts.times_blanked(body_for_hash))[:16]

    ctx.write(OUT, layer, "verified_queries.schema.json")
    by_label = Counter(o["label"] for o in objects)
    by_domain = Counter(o["domain"] for o in objects)
    ctx.count(objects=len(objects), layer_sha=layer["layer_sha"],
              **{f"label_{k}": v for k, v in by_label.items()},
              domains=len(by_domain))
    ctx.say(f"c5: {len(objects)} verified queries, layer_sha={layer['layer_sha']}. "
            f"Labels: {', '.join(f'{k}={v}' for k, v in sorted(by_label.items()))}. "
            f"Domains: {', '.join(sorted(by_domain))}.")


def main() -> None:
    stage.main(STAGE, "c5_emit.py", __doc__.splitlines()[0], body)


if __name__ == "__main__":
    main()
