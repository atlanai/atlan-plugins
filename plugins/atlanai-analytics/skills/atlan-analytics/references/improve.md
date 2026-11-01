# Improve a talk-to-data agent

Read [shared conventions](conventions.md) first. This follows the eval hill-climb
discipline:
- the train/test split is fixed before round 1 and never redrawn to chase a score;
- the analyst reads train traces only;
- test is scored every round and is the only number reported;
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
  - You pick changes from train scores and test totals only; never read test transcripts.
  - Proposed changes must not quote eval wording, gold SQL or gold values.
- **Definition changes append complete revisions.** Follow
  [Saving and undoing a definition change](improve/03-loop.md#saving-and-undoing-a-definition-change)
  for metric/query updates and restoration; PATCH cannot edit those definitions.
  A new saved query uses create. Record touched artifact/version ids in
  `H/vN/receipt.json` so restoration has an exact source.
- **Harness changes stop the loop.** The grader and the eval script are the harness. If either
  changed since `EVAL approve-harness`, the next `record` refuses until the user reviews the
  change and approves it themselves.
- **Report the test delta with its band.** If it's inside noise, say so and recommend not
  keeping the change.

End with one status line.
