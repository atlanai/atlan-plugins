#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""C3 — author the question and its business context. The only stage whose product is prose.

THREE EXPLICIT MODES, never a silent fallback:

  --emit-prompt          write the bundle the `author-questions` skill consumes, then stop
  --from <file>          ingest a model's output, VALIDATE it, and merge
  --deterministic-draft  no model: derive a structural question and mark every object
                         `question_source: structural-draft` with a gotcha saying so

The third is how the pipeline is tested without a model, and it is honest because it is an
explicit CLI choice recorded in the manifest and stamped on every object it touches. It is
not a fallback the run can take by accident.

WHAT C3 CANNOT DO, structurally: change any SQL. It runs AFTER the SQL is written and emits prose only, so
nothing it reads — including a dashboard text box an author controls — can alter what
executes. The validator refuses an output carrying a `sql` key at all. That, plus the
closed-world rule at C4, is the containment for carrying widget and dashboard source verbatim.
"""
from __future__ import annotations

import json
import pathlib
import sys


from metrics_agent.lib import artifacts  # noqa: E402
from metrics_agent.lib import evidence as _ev  # noqa: E402
from metrics_agent.lib import stage  # noqa: E402

STAGE = "c3_questions"
IN = "merged_candidates.json"
OUT = "questioned_candidates.json"
PROMPT = "c3_prompt.json"

FORBIDDEN_KEYS = {"sql", "filters", "tables", "group_by", "aggregations", "variants"}


def _dump(ctx: stage.Ctx, name: str, schema: str, doc: dict) -> None:
    artifacts.dump(ctx.run / name, doc, schema)


def _extra(p) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit-prompt", action="store_true",
                   help="write the prompt bundle for the author-questions skill and stop")
    p.add_argument("--allow-structural-fallback", action="store_true",
                   help="merge a PARTIAL authoring pass: authored candidates keep their "
                        "question, the rest get a structural one. The count is said out loud "
                        "because a structural question is a weaker object, not an equal one.")
    g.add_argument("--from", dest="from_file", default=None,
                   help="ingest and validate a model's output")
    g.add_argument("--deterministic-draft", action="store_true",
                   help="no model: structural questions, every object stamped and flagged")


def _emit_prompt(ctx: stage.Ctx, cands: list[dict]) -> None:
    """Hand the model the SQL and a BOUNDED metadata block — never the BI files.

    The business context for a BI candidate still comes from the combination of what is
    computed (the SQL), how it is read (the widget) and why it matters (the dashboard). What
    changed is HOW that reaches the model: an inlined `bi_context` block of a few hundred
    bytes rather than two file reads per unit.

    That is not just cheaper. It is REVIEWABLE — you can see exactly what the model was told —
    identical across both vendors, and testable. A file path the model may or may not have
    opened is none of those things.
    """
    items = []
    for c in cands:
        bi = (c["provenance"].get("bi") or {})
        items.append({
            "candidate_id": c["candidate_id"],
            "lane": c["lane"],
            "sql": c["sql"],
            "tables": c["tables"],
            "group_by": c["group_by"],
            "aggregations": c["aggregations"],
            "filters": [{"column": f["column"], "op": f["op"]} for f in c["filters"]],
            "grain": c["grain"],
            "klass": c["klass"],
            "domain": c["domain"],
            "title": c.get("title"),
            "container_path": bi.get("container_path", []),
            # INLINE, not a path. Every field settles something the SQL cannot: the number
            # format is the unit, a reference line is the target, the annotations and siblings
            # are the audience and the decision.
            "bi_context": bi.get("bi_context"),
            "adoption": c["provenance"].get("history"),
        })
    _dump(ctx, PROMPT, "c3_prompt.schema.json", {
        "contract_version": "1.0",
        "instruction": "See skills/author-questions/SKILL.md. Everything you need "
                       "is INLINE below — do not open any BI file. Emit PROSE ONLY: question, "
                       "description, gotchas, aliases, evidence_citations. Any SQL or filter "
                       "key in your output is refused outright.",
        "evidence_sources": list(_ev.SOURCES),
        "items": items,
    })
    ctx.entry.outputs.append(str(ctx.run / PROMPT))
    ctx.entry.verdict = "ok"
    size = len(json.dumps(items))
    ctx.count(prompted=len(items), prompt_bytes=size)
    ctx.say(f"c3: wrote {PROMPT} for {len(items)} candidates, {size} bytes total "
            f"(~{size // max(len(items), 1)} each). Everything is inline — the skill opens no "
            f"BI file.")
    ctx.say(f"c3: run the author-questions skill, then re-run with --from <output.json>.")


def _forbidden_keys_in(node) -> set[str]:
    """Every forbidden key anywhere in an output row, at any depth.

    NESTED COUNTS. Checking only the row's own keys let `{"gotchas": [{"sql": ...}]}` or
    `{"context": {"filters": [...]}}` through: the row is prose on its face and carries a
    statement one level down, which a later stage reading that field would treat as data.
    """
    found: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            found |= FORBIDDEN_KEYS & set(cur)
            stack.extend(cur.values())
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return found


def _validate_model_output(rows: list[dict], cands: list[dict],
                           allow_partial: bool = False) -> dict[str, dict]:
    """The validator IS the tested surface. Model output is not reproducible, so what must be
    reliable is the rejection — and that is deterministic."""
    known = {c["candidate_id"] for c in cands}
    out: dict[str, dict] = {}
    for i, r in enumerate(rows):
        cid = r.get("candidate_id")
        if cid not in known:
            raise SystemExit(f"c3 output row {i}: unknown candidate_id {cid!r}")
        stray = _forbidden_keys_in(r)
        if stray:
            raise SystemExit(
                f"c3 output row {i} ({cid}) carries {sorted(stray)}. C3 emits PROSE ONLY — it "
                f"runs after the SQL is frozen and may not change it. Refused."
            )
        if not str(r.get("question", "")).strip():
            raise SystemExit(f"c3 output row {i} ({cid}): empty question")
        _ev.validate(r.get("evidence_citations", []))
        # A claim is only checkable if it is STATED as a predicate. Free prose cannot be
        # verified against a statement without guessing, so C4's faithfulness gate reads
        # these — and an empty predicate would be a claim that passes by being unreadable.
        for j, cl in enumerate(r.get("claims", []) or []):
            if not isinstance(cl, dict) or not str(cl.get("predicate", "")).strip():
                raise SystemExit(
                    f"c3 output row {i} ({cid}) claim {j} has no predicate. A claim without "
                    f"one cannot be checked against the SQL, which is the only thing that "
                    f"makes it a claim rather than a sentence. Refused.")
        out[cid] = r
    missing = known - set(out)
    if missing and not allow_partial:
        raise SystemExit(
            f"c3 output is missing {len(missing)} candidates: {sorted(missing)[:5]}... "
            "A partial authoring pass is not merged — re-run the skill over the full set. "
            "Pass --allow-structural-fallback to merge what was authored and give the rest a "
            "STRUCTURAL question, which is a weaker object and is counted as such."
        )
    return out


def _structural_question(c: dict) -> str:
    """A mechanical reading of the structure. Deliberately plain: it must not look like an
    authored business question, because it is not one."""
    aggs = ", ".join(a["alias"] or a["expr"] for a in c["aggregations"]) or "rows"
    by = " by " + ", ".join(c["group_by"]) if c["group_by"] else ""
    where = ""
    named = [f["column"] for f in c["filters"] if not f.get("is_identity")]
    if named:
        where = " filtered on " + ", ".join(sorted(set(named)))
    return f"What is {aggs}{by}{where}?"


def body(ctx: stage.Ctx) -> None:
    doc = ctx.read(IN, "candidates.schema.json")
    cands = doc["candidates"]

    if not cands:
        # NOTHING TO AUTHOR IS A SKIP, in every mode. A prompt with no items costs a session
        # and reads afterwards as an authoring pass; a `--from` for the same run has nothing
        # its rows could name. The output is still written — empty and well-formed — so C4
        # reads the same artifact either way, and the manifest says why it is empty.
        why = "nothing to author: the merged layer holds 0 candidates"
        stale = ctx.run / PROMPT
        if stale.is_file():
            stale.unlink()
        if ctx.args.from_file:
            ctx.note_input_path(ctx.args.from_file)
        ctx.write(OUT, {"contract_version": "1.0", "dialect": doc["dialect"],
                        "candidates": []}, "candidates.schema.json")
        _dump(ctx, "c3_authored.json", "c3_authored.schema.json",
              {"contract_version": "1.0", "question_source": "none", "authored": {},
               "skipped_reason": why})
        ctx.entry.outputs.append(str(ctx.run / "c3_authored.json"))
        ctx.entry.verdict = "skipped"
        ctx.entry.notes = why
        ctx.count(candidates=0, prompted=0, skipped=True)
        ctx.decide("authoring", STAGE, "skipped", because=why)
        ctx.say(f"c3: SKIPPED — {why}. No prompt was written; nothing to answer.")
        return

    if ctx.args.emit_prompt:
        _emit_prompt(ctx, cands)
        return

    if ctx.args.from_file:

        ctx.authored(ctx.args.from_file)
        path = pathlib.Path(ctx.args.from_file).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"--from file not found: {path}")
        ctx.note_input_path(path)
        rows = json.loads(path.read_text())
        rows = rows.get("items", rows) if isinstance(rows, dict) else rows
        authored = _validate_model_output(rows, cands,
                                          allow_partial=ctx.args.allow_structural_fallback)
        source = "model"
        ctx.entry.model = ctx.args.model or "unrecorded"
        if ctx.entry.model == "unrecorded":
            ctx.say("c3: --model was not given, so the manifest cannot attribute a later "
                    "regression to a model version. Pass it.")
    else:
        authored = {}
        source = "structural-draft"

    out = []
    for c in cands:
        c = json.loads(json.dumps(c))  # deep copy; never mutate the input artifact
        a = authored.get(c["candidate_id"])
        if a:
            c["question_draft"] = a["question"]
            c["_c3"] = {
                "question": a["question"],
                "description": a.get("description", ""),
                "gotchas": a.get("gotchas", []),
                "aliases": a.get("aliases", []),
                "evidence_citations": a.get("evidence_citations", []),
                # Checked against the statement at C4. NEVER merged into it: a claim that is
                # not in the SQL means the WORDS are wrong, not the SQL.
                "claims": a.get("claims", []),
                "question_source": "model",
            }
        else:
            c["question_draft"] = _structural_question(c)
            c["_c3"] = {
                "question": c["question_draft"],
                "description": "",
                "gotchas": ["Question derived structurally from the SQL, not authored against "
                            "the business context. Review before relying on the wording."],
                "aliases": [],
                "evidence_citations": [],
                "claims": [],
                "question_source": "structural-draft",
            }
        out.append(c)

    # `_c3` is a working key, not part of the candidates contract, so it is carried in a
    # sidecar rather than smuggled into a schema'd artifact.
    sidecar = {c["candidate_id"]: c.pop("_c3") for c in out}
    ctx.write(OUT, {"contract_version": "1.0", "dialect": doc["dialect"], "candidates": out},
              "candidates.schema.json")
    _dump(ctx, "c3_authored.json", "c3_authored.schema.json",
          {"contract_version": "1.0", "question_source": source, "authored": sidecar})
    ctx.entry.outputs.append(str(ctx.run / "c3_authored.json"))
    # SAY THE FALLBACK COUNT. The flag promises it, and without it a run that was 86%
    # structural reports `question_source: model` on the manifest and reads as fully authored
    # — which is exactly how a layer of machine-generated questions ("What is accounts,
    # min_score by 1, 2?") passed for a layer of business ones. A structural question is a
    # weaker object; the ledger has to show how many there are.
    n_fallback = sum(1 for v in sidecar.values() if v["question_source"] == "structural-draft")
    ctx.count(candidates=len(out), question_source=source,
              authored_by_model=len(out) - n_fallback, structural_fallback=n_fallback)
    ctx.say(f"c3: {len(out)} questions — {len(out) - n_fallback} authored by the model, "
            f"{n_fallback} structural.")
    if n_fallback and source == "model":
        ctx.say(f"c3: {n_fallback} of {len(out)} candidates ({100*n_fallback/len(out):.0f}%) "
                f"fell back to a STRUCTURAL question. They are stamped individually, but the "
                f"run-level source still reads 'model' — read the per-object "
                f"`question_source`, not this one.")
    if source == "structural-draft":
        ctx.say("c3: STRUCTURAL DRAFT — an explicit choice, stamped on every object and "
                "recorded here. These are not authored business questions.")


def _extra_all(p) -> None:
    _extra(p)
    p.add_argument("--model", default=None,
                   help="model id, recorded on the manifest entry so a later regression is "
                        "attributable")


def main() -> None:
    stage.main(STAGE, "c3_questions.py", __doc__.splitlines()[0], body, _extra_all)


if __name__ == "__main__":
    main()
