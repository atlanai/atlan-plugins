#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""A3-loop — compile every widget, review, learn a rule, compile EVERY widget again.

    compile all ──▶ review ──▶ new rule? ──┐
         ▲                                 │ yes
         └──────── recompile ALL ◀─────────┘
                                           │ no
                                           ▼
                                     witness · continue

WHY *ALL* WIDGETS, EVERY ROUND. If a rule learned at widget 41 were applied only from there
on, widget 1 and widget 41 would have been compiled by two different compilers and the run's
output would not correspond to any single rule table. Recompiling everything means that when
the loop settles, every statement in the run was produced by the SAME final table. That is the
property that makes the loop safe, and it is the reason this file exists rather than a flag on
A3.

THREE THINGS IT WATCHES, because the loop is autonomous and nobody else will:

  1. TERMINATION      a round cap, and a stop when no rule is accepted (a fixpoint). A rule
                      proposed again is refused by A3b's gate as one that already exists, so a
                      loop that keeps proposing the same rules ends here, as a fixpoint, and
                      cannot cycle.
  2. NO SILENT DRIFT  the SQL of every already-compiled widget is snapshotted each round. A
                      new rule that changes a statement which was ALREADY produced halts the
                      loop. This is the failure that matters: round 3 adds a rule for widget
                      41 and quietly rewrites twelve widgets that were already right, all of
                      them still "successful" and all of them now different.
  3. COVERAGE         it must not go down. A round that compiles fewer widgets than the last
                      one is a regression however good its reasoning was.

THE WITNESS RUNS ONCE, AFTER. Executing every round would multiply warehouse cost across
answers that get thrown away, and the only statement worth proving is the final one.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

from metrics_agent._paths import child_env, stage_command
from metrics_agent.lib import artifacts
from metrics_agent.lib import policy as _policy


def _run(script: str, run_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*stage_command(script), "--run", run_dir, *args],
                          capture_output=True, text=True, env=child_env())


def _snapshot(run_dir: pathlib.Path) -> dict[str, str]:
    doc = json.loads((run_dir / "bi_candidates.json").read_text())
    return {c["candidate_id"]: c["sql"] for c in doc["candidates"]}


def _report(run_dir: pathlib.Path) -> dict:
    return json.loads((run_dir / "a3_compile_report.json").read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--policy", default=None)
    ap.add_argument("--max-rounds", type=int, default=None,
                    help="the round cap (default: compiler.max_rounds in the policy)")
    ap.add_argument("--review", default=None,
                    help="command that produces the reviewer's findings file. It receives the "
                         "prompt path and must write the findings path. Without it the loop "
                         "runs ONE compile and says so — it does not pretend to have learned.")
    a = ap.parse_args()
    run_dir = pathlib.Path(a.run)
    policy = ["--policy", a.policy] if a.policy else []
    if a.max_rounds is None:
        a.max_rounds = int(_policy.load(a.policy).get("compiler.max_rounds"))

    history: list[dict] = []
    rules_arg: list[str] = []
    previous: dict[str, str] = {}
    previous_coverage = -1
    stop = "completed"

    for rnd in range(1, a.max_rounds + 1):
        proc = _run("a3_translate.py", str(run_dir), *policy, *rules_arg)
        if proc.returncode != 0:
            print(proc.stderr[-2000:], file=sys.stderr)
            return proc.returncode
        rep = _report(run_dir)
        current = _snapshot(run_dir)

        # -- 2. no silent drift --------------------------------------------------------
        changed = [cid for cid, sql in current.items()
                   if cid in previous and previous[cid] != sql]
        if changed:
            stop = "drift"
            history.append({"round": rnd, "coverage_pct": rep["coverage_pct"],
                            "changed_already_compiled": changed})
            print(f"a3-loop: HALTED. A new rule changed {len(changed)} statement(s) that were "
                  f"already compiled: {changed[:5]}. A rule may ADD widgets; it may not "
                  f"silently rewrite ones that were already right.", file=sys.stderr)
            break

        # -- 3. coverage must not go down ----------------------------------------------
        if previous_coverage >= 0 and rep["coverage_pct"] < previous_coverage:
            stop = "coverage_regression"
            print(f"a3-loop: HALTED. Coverage fell from {previous_coverage}% to "
                  f"{rep['coverage_pct']}%.", file=sys.stderr)
            break

        history.append({"round": rnd, "coverage_pct": rep["coverage_pct"],
                        "compiled": rep["compiled"], "units": rep["units"],
                        "rules_sha": rep["rules_sha"], "gaps": len(rep["gaps"])})
        previous, previous_coverage = current, rep["coverage_pct"]

        if not a.review:
            stop = "no_reviewer"
            _run("a3b_review.py", str(run_dir), *policy, "--skip", *rules_arg)
            break

        # -- the review ------------------------------------------------------------------
        # A prompt left by the previous round must never be answered as this round's: it is
        # removed first, and an emit that FAILS stops the loop rather than letting a stale
        # bundle stand in for it.
        (run_dir / "a3b_prompt.json").unlink(missing_ok=True)
        em = _run("a3b_review.py", str(run_dir), *policy, "--emit-prompt", *rules_arg)
        if em.returncode != 0:
            print(em.stderr[-2000:], file=sys.stderr)
            return em.returncode
        if not (run_dir / "a3b_prompt.json").is_file():
            # A3b found nothing to review and recorded a skip; there is no bundle to answer.
            stop = "nothing_to_review"
            break
        findings = run_dir / f"a3b_findings_r{rnd}.json"
        rv = subprocess.run([*a.review.split(), str(run_dir / "a3b_prompt.json"),
                             str(findings)], capture_output=True, text=True)
        if rv.returncode != 0 or not findings.is_file():
            stop = "review_failed"
            print(f"a3-loop: the reviewer did not produce findings "
                  f"({rv.stderr[-400:]}). Stopping with what the compiler managed alone.",
                  file=sys.stderr)
            break

        proc = _run("a3b_review.py", str(run_dir), *policy, "--from", str(findings), *rules_arg)
        if proc.returncode != 0:
            print(proc.stderr[-2000:], file=sys.stderr)
            return proc.returncode
        review = json.loads((run_dir / "a3b_review.json").read_text())

        # -- 1. termination ---------------------------------------------------------------
        # A re-proposed rule is refused at A3b as already existing, so a round that learns
        # nothing new ends here: the loop cannot cycle on the same rules.
        if not review["needs_recompile"]:
            stop = "fixpoint"
            break
        rules_arg = ["--rules", str(run_dir / "compiler_rules_learned.yml")]
    else:
        stop = "round_cap"

    out = {"contract_version": "1.0", "stop_reason": stop, "rounds": history,
           "final_coverage_pct": history[-1]["coverage_pct"] if history else 0}
    artifacts.validate(out, "a3_loop.schema.json", what="a3_loop.json")
    (run_dir / "a3_loop.json").write_text(json.dumps(out, indent=2) + "\n")
    first = history[0]["coverage_pct"] if history else 0
    last = out["final_coverage_pct"]
    print(f"a3-loop: {len(history)} round(s), stop={stop}, coverage {first}% -> {last}%.")
    return 0 if stop in ("fixpoint", "completed", "no_reviewer", "nothing_to_review",
                         "round_cap") else 1


if __name__ == "__main__":
    raise SystemExit(main())
