#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""X3-loop — author, check, repair what refused, check again. No person in the middle.

    author ──▶ gate: check EVERY card ──▶ refusals? ──┐
       ▲                                               │ yes
       └──────── re-author ONLY those cards ◀──────────┘
                                                       │ no
                                                       ▼
                                                 card layer · continue

WHAT THE HUMAN WAS ACTUALLY DOING. X3 refuses a card and names the field — a reference that
wears a query alias, a measure that disagrees with its own aggregation, a join with no FROM
clause. Someone then read that message, edited the card and re-ran. Reading the message is
the only part that needed a person, and it does not: `x3_emit` writes `x3_repair.json` — the
card as written, the fields that are wrong, and why — which is a prompt, not a traceback.

RULE 3 STILL HOLDS: this stage holds no model key. `--author` is a command the operator
supplies, exactly as `a3_loop.py --review` does. It receives two paths, the repair bundle and
the file to write, and this script never knows what is behind it.

FOUR THINGS IT WATCHES, because the loop is autonomous and nobody else will:

  1. TERMINATION   a round cap, and a stop as soon as the gate accepts.
  2. OSCILLATION   the same set of faults twice means the author is not converging on them —
                   it is re-reading the same card and writing the same thing. Stop and say so,
                   rather than spend the cap proving it.
  3. NO WIDENING   a repair may only change cards that FAILED. An author handed one bad card
                   and 5MB of context will happily improve the other 124, and then the layer
                   nobody reviewed is not the layer anybody approved.
  4. NO SILENT GIVE-UP  the run ends with a verdict and the last refusals, never with a
                   half-written layer and a zero exit code.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

from metrics_agent._paths import child_env, stage_command
from metrics_agent.lib import artifacts


def _gate(run_dir: str, answers: list[str], extra: list[str]) -> subprocess.CompletedProcess:
    args = [*stage_command("x3_emit.py"), "--run", run_dir]
    for a in answers:
        args += ["--from", a]
    return subprocess.run(args + extra, capture_output=True, text=True, env=child_env())


def _fault_key(repair: dict) -> str:
    """What this round refused, as one comparable string.

    Keyed on card + field + problem, sorted: two rounds that refuse the same things are the
    loop arguing with itself, and the cap should not be spent discovering that slowly.
    """
    items = sorted(
        (t["name"], f.get("field", ""), f.get("problem", ""))
        for t in repair.get("tasks", []) for f in t.get("faults", []))
    return json.dumps([items, sorted(repair.get("unattributed", []))], sort_keys=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--from", dest="answers", action="append", default=[], required=True,
                    help="the authored answers file(s), as x3_emit takes them")
    ap.add_argument("--author", default=None,
                    help="command that repairs refused cards. It receives two paths: the "
                         "repair bundle to read, and the corrected answers file to write. "
                         "Without it the loop runs the gate once and reports.")
    ap.add_argument("--max-rounds", type=int, default=4,
                    help="a repair that has not converged in this many rounds is not going to")
    ap.add_argument("--partial", action="store_true")
    a = ap.parse_args(argv)

    run_dir = pathlib.Path(a.run)
    extra = ["--partial"] if a.partial else []
    answers = list(a.answers)
    seen_faults: set[str] = set()
    history: list[dict] = []
    stop = "accepted"

    repair_path = run_dir / "x3_repair.json"
    for rnd in range(1, a.max_rounds + 1):
        # THIS ROUND'S REFUSAL, NEVER AN EARLIER ONE. x3_emit writes the repair bundle only
        # when it can attribute a refusal; a gate that fails for any other reason leaves the
        # previous round's bundle on disk, and reading that as this round's verdict reports a
        # crash as the author "oscillating".
        repair_path.unlink(missing_ok=True)
        proc = _gate(a.run, answers, extra)
        sys.stdout.write(proc.stdout)
        if proc.returncode == 0:
            history.append({"round": rnd, "outcome": "accepted"})
            break

        if not repair_path.is_file():
            # The gate failed for a reason it could not attribute — missing inputs, a bad
            # run directory. That is not something an author can repair.
            stop = "not_repairable"
            sys.stderr.write(proc.stderr[-2000:])
            history.append({"round": rnd, "outcome": "not_repairable"})
            break

        repair = json.loads(repair_path.read_text())
        key = _fault_key(repair)
        n = len(repair.get("tasks", []))
        history.append({"round": rnd, "outcome": "refused", "n_cards": n,
                        "n_file_problems": len(repair.get("unattributed", [])),
                        "cards": [t["name"] for t in repair.get("tasks", [])]})

        if not a.author:
            stop = "no_author"
            print(f"x3-loop: round {rnd} refused {n} card(s) and no --author was given. "
                  f"{repair_path} says which field on each.", file=sys.stderr)
            break
        if key in seen_faults:
            stop = "oscillating"
            print(f"x3-loop: round {rnd} refused exactly what round {rnd - 1} refused. The "
                  f"author is not converging on these faults — stopping rather than spending "
                  f"the cap. {repair_path}", file=sys.stderr)
            break
        seen_faults.add(key)

        fixed = run_dir / f"x3_repaired_r{rnd}.json"
        rv = subprocess.run([*a.author.split(), str(repair_path), str(fixed)],
                            capture_output=True, text=True)
        if rv.returncode != 0 or not fixed.is_file():
            stop = "author_failed"
            print(f"x3-loop: the author did not produce {fixed} "
                  f"({rv.stderr[-400:]}).", file=sys.stderr)
            break

        # ---- NO WIDENING ----------------------------------------------------------------
        # Only the cards that refused may come back. Anything else the author decided to
        # improve is dropped, with a line saying so, because a layer that quietly changed
        # under a repair is a layer nobody reviewed.
        refused = {t["name"] for t in repair.get("tasks", [])}
        # AN ANSWER THAT CANNOT BE READ IS AN AUTHOR THAT FAILED, not a crash of the loop. A
        # traceback here would end the run with no report (rule 4: no silent give-up).
        try:
            doc = json.loads(fixed.read_text())
            metrics = [m for m in (doc.get("metrics") or []) if isinstance(m, dict)]
        except (ValueError, AttributeError, TypeError) as exc:
            stop = "author_failed"
            print(f"x3-loop: the author wrote {fixed}, which is not an answers file "
                  f"({type(exc).__name__}: {exc}).", file=sys.stderr)
            break
        kept = [m for m in metrics if m.get("name") in refused]
        dropped = [m.get("name") for m in metrics if m.get("name") not in refused]
        if dropped:
            print(f"x3-loop: round {rnd} — ignoring {len(dropped)} card(s) the repair "
                  f"touched but nothing had refused: {', '.join(map(str, dropped[:6]))}",
                  file=sys.stderr)
        missing = sorted(refused - {m.get("name") for m in kept})
        if missing:
            stop = "incomplete_repair"
            print(f"x3-loop: round {rnd} — the author returned nothing for "
                  f"{', '.join(missing)}.", file=sys.stderr)
            break
        fixed.write_text(json.dumps({"metrics": kept}, indent=1, ensure_ascii=False) + "\n")

        # The repair file is appended, and x3_emit refuses a card authored twice — so the
        # originals it replaces have to go. Rewriting the author's own file would destroy
        # the input, so the superseded cards are filtered into a round-scoped copy instead.
        answers = _supersede(run_dir, answers, refused, rnd) + [str(fixed)]
        print(f"x3-loop: round {rnd} repaired {len(kept)} card(s): "
              f"{', '.join(sorted(refused))}")
    else:
        stop = "max_rounds"

    report = {"contract_version": "1.0", "stop": stop, "rounds": len(history),
              "history": history}
    artifacts.validate(report, "x3_loop.schema.json", what="x3_loop.json")
    (run_dir / "x3_loop.json").write_text(json.dumps(report, indent=1) + "\n")
    ok = stop == "accepted"
    print(f"x3-loop: {stop} after {len(history)} round(s) -> {run_dir / 'x3_loop.json'}")
    return 0 if ok else 1


def _supersede(run_dir: pathlib.Path, answers: list[str], refused: set, rnd: int) -> list[str]:
    """The original answers with the repaired cards removed, written beside the run.

    The author's own file is never edited: it is the record of what was first written, and a
    loop that overwrites its input leaves nothing to compare a repair against.
    """
    out = []
    for i, src in enumerate(answers):
        doc = json.loads(pathlib.Path(src).read_text())
        keep = [m for m in (doc.get("metrics") or []) if m.get("name") not in refused]
        if not keep:
            continue
        p = run_dir / f"x3_answers_r{rnd}_{i}.json"
        p.write_text(json.dumps({"metrics": keep}, indent=1, ensure_ascii=False) + "\n")
        out.append(str(p))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
