# Phase 7: baseline eval

Goal: the first honest number, run on every train and validation case at a fixed rep count and
recorded as an experiment in the registry. The holdout stays unrun until an improve loop hands
back.

1. **Scope and cost.** Run `EVAL todo --variant baseline --reps R` (default R=1, or 2 if the
   validation set is under 25 cases). It lists the (case, rep) pairs still to run, holdout
   excluded.
   - Run one case end to end first, as a canary (see `skills/atlan-analytics/references/eval.md`). Time
     it and read its usage from the transcript.
   - Then state: "N cases × R reps ≈ X min at 4 in parallel, about T tokens", from the canary's
     measured usage. Give no dollar figure: the script has no pricing data, and a guess would
     read as measured.
   - Ask:
     - `<slug> · baseline · run it`
     - **Run the full baseline (Recommended)**
     - **Fewer reps**
     - **Not now**
2. **Run** by following `skills/atlan-analytics/references/eval.md` for variant `baseline`: a fresh
   subagent per case, gold and agent SQL executed by you, and `EVAL record` per case.
3. **Summarise**: `EVAL summary --variant baseline`. It gives:
   - the pass rate on validation (the headline) and on train, with the band;
   - the class counts: pass, wrong result, SQL error, no SQL, harness error, incomplete;
   - context engagement (the share of scored runs that cited a registry definition), runs
     whose isolation is unverified, tokens and latency.

   Triage every zero before quoting a number. A harness error (tool failure, timeout) is not an
   agent failure, and it leaves the denominator: record a run that failed that way with
   `EVAL record --class harness_error --reason "<what failed>"` instead of an answer file.
4. **Publish** variant `baseline` as [Publish in the eval procedure](../eval.md#publish)
   describes. It creates the experiment on the agent, with one result and one session per case,
   and marks it completed. The user can open the agent's Evals tab to see it.
5. **Freeze the harness**: ask the user to run `EVAL approve-harness` themselves. Give them the
   command with every path written out, since `${CLAUDE_PLUGIN_ROOT}` only expands inside the
   plugin: `python3 "/full/path/to/scripts/analytics_eval.py" approve-harness --dir "/full/path/to/.atlan-analytics/<slug>"`.
   It records the digest of the eval script and the grader agent's instructions, so a later
   edit to the scoring is caught.

**STOP gate.** "Baseline: validation P% (±B), train Q%. Top failure classes: … Experiment published to
the registry." Set `phase` to `improve` and offer `atlan-analytics improve`.
