# Phase 7: baseline eval

Goal: the first honest number, run on every case at a fixed rep count and recorded as an
experiment in the registry.

1. **Scope and cost.** Run `EVAL todo --variant baseline --reps R` (default R=1, or 2 if the
   test set is under 25 cases). It lists the (case, rep) pairs still to run.
   - Run one case end to end first, as a canary (see `skills/atlan-analytics/references/eval.md`). Time
     it and read its usage from the transcript.
   - Then state: "N cases × R reps ≈ X min at 4 in parallel, about $Y."
   - Ask:
     - `<slug> · baseline · run it`
     - **Run the full baseline (Recommended)**
     - **Fewer reps**
     - **Not now**
2. **Run** by following `skills/atlan-analytics/references/eval.md` for variant `baseline`: a fresh
   subagent per case, gold and agent SQL executed by you, and `EVAL record` per case.
3. **Summarise**: `EVAL summary --variant baseline`. It gives:
   - the pass rate on train and on test, with the band;
   - the class counts: pass, wrong result, SQL error, no SQL, harness error;
   - tokens, cost and latency.

   Triage every zero before quoting a number. A harness error (tool failure, timeout) is not an
   agent failure, and it leaves the denominator.
4. **Publish**: `EVAL push --variant baseline`. It creates the experiment on the agent, with
   one result per case, a trace and a session each, and marks it completed. The user can open
   the agent's Evals tab to see it.
5. **Freeze the harness**: tell the user to run `EVAL approve-harness` themselves. That records
   the grader's digest, so a later edit to the scoring is caught.

**STOP gate.** "Baseline: test P% (±B), train Q%. Top failure classes: … Experiment pushed to
the registry." Set `phase` to `improve` and offer `atlan-analytics improve`.
