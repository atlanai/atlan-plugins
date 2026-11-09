# Improve a talk-to-data agent

Read [shared conventions](conventions.md) and [project conventions](project-conventions.md) first. This follows the eval hill-climb
discipline:
- the split is frozen before round 1 and never redrawn (`split` refuses once a row is graded);
- the analyst reads train traces only;
- validation is scored every round and picks the winner;
- the holdout (the final test) is scored once, at hand-back, and never picks anything;
- one idea per round;
- every change is recorded so it can be reverted;
- the loop stops at a plateau instead of grinding.

`H` = `.atlan-analytics/<slug>/hillclimb/`. Runs use `atlan-analytics eval`.

| step | reference | the user decides |
|---|---|---|
| 0. Adopt (only for an agent not built here) | [00-adopt](improve/00-adopt.md) | which agent, where its questions and answers come from |
| 1. Plan | [01-plan](improve/01-plan.md) | the goal, the levers, what's off-limits, the stopping rule, sign-off |
| 2. Baseline check | [02-baseline-check](improve/02-baseline-check.md) | whether the eval can show the win they want |
| 3. Loop | [03-loop](improve/03-loop.md) | per round, only if they asked to approve each change |
| 4. Stall | [04-stall](improve/04-stall.md) | what to do when content rounds stop moving the score |
| 5. Report | [05-report](improve/05-report.md) | which changes to keep |

## Non-negotiables

- **No baseline, no loop.** If `H/baseline/results.jsonl` is missing, run the baseline via
  `atlan-analytics eval` first.
- **The held-out wall.**
  - The analyst gets an explicit list of train trace files.
  - You pick changes from train scores and validation totals only; never read validation or
    holdout transcripts, and never run the holdout before hand-back.
  - Proposed changes must not quote eval wording, gold SQL or gold values.
- **Definition changes append complete revisions.** Follow
  [Saving and undoing a definition change](improve/03-loop.md#saving-and-undoing-a-definition-change)
  for metric/query updates and restoration; PATCH cannot edit those definitions.
  A new saved query uses create. Record touched artifact/version ids in
  `H/vN/receipt.json` so restoration has an exact source.
- **Harness changes stop the loop.** The grader agent's instructions and the eval script's
  grading, record and scoring code are the harness; its publishing and command-line code are
  not, so a plugin upgrade that touches only those keeps the approval. If the harness changed
  since `EVAL approve-harness`, the next `record` refuses until
  the user reviews the change and approves it themselves, with the command written out as
  [the baseline](build/07-baseline.md) gives it.
- **Report the validation delta with its sign-test p-value.** If it's not significant, say so
  and recommend not keeping the change. At hand-back, report the holdout once beside it.

End with the useful result or one recovery action in natural language.
