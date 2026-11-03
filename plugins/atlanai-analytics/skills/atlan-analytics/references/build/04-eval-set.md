# Phase 4: the eval set

Goal: 20–50 real questions, each with SQL the user trusts, stored locally where the agent
can't reach it, and split once into train, validation and a held-out final test.

## 1. Source the questions

Ask:
- `<slug> · eval set · where the questions come from`
- **Questions the team really asked, with the SQL an analyst trusted (Recommended)**: from a
  ticket queue, a Slack channel, or a dashboard's own queries
- **An existing benchmark file** (questions plus gold SQL)
- **The brief's sample questions**: we write and check the gold SQL together
- **Mark saved queries as gold**: only for questions the agent will **not** see as context.
  Warn that a gold query also published as context makes that case unmeasurable, and remove
  such overlaps.

Aim for 20–50 cases. Mix counts, rates, breakdowns, time windows and at least a few
definitional traps: a status that looks like "resolved" but isn't, a denominator that
excludes unanswered surveys, a date field that has several meanings. Anthropic's guidance:
"20–50 tasks from real failures is a great start."

## 2. Write the files (local only)

- `.atlan-analytics/<slug>/eval/cases.jsonl`: `{"case_id", "question", "tags": [<primary tag>, …]}`.
  This is the only eval text the agent ever sees, and only one question at a time.
- `.atlan-analytics/<slug>/eval/answers.jsonl`: `{"case_id", "gold_sql", "notes"}`, plus `sig` for a
  case whose answer is approximate. **Grader only.** Never paste it into a subagent prompt, a
  registry artifact or the agent's instructions.

Whole numbers are graded exactly: counts, ids and sums of whole amounts. Any other number is
compared at 4 significant figures unless the case sets `sig` (1 to 15). Set it lower for a rate a
reasonable query rounds differently, and higher for money with cents.

Fix every window in dates. "Revenue last quarter" means a different quarter once the calendar
moves, and gold SQL that reads `CURRENT_DATE` gives a different answer every day, while the
cached gold stays the same. Write the question and the gold with the same fixed window
("revenue in Q3 2026", `BETWEEN '2026-07-01' AND '2026-09-30'`).

Stay question-blind yourself when authoring context later. Once this file exists, don't copy
its wording or SQL into metrics, saved queries, rules or the prompt.

## 3. Validate the gold

For each case:
1. Run `gold_sql` with the warehouse query tool recorded in state.
2. Save the result as `.atlan-analytics/<slug>/eval/gold/<case_id>.json`, in the shape
   `{"columns": [...], "rows": [[...]], "error": null, "ran_at": "<ISO 8601 time it ran>"}`.
3. A gold query that errors, or returns something implausible (all NULL, zero rows where the
   question expects a number), goes back to the user. Fix it or drop the case. Never grade
   against broken gold.

Report "N cases, all gold SQL runs; M dropped (reasons)".

## 4. Split once

`EVAL split --holdout-frac 0.2 --test-frac 0.5 --seed <n>` writes `eval/split.json` and
`eval/freeze.json`. It draws at random, stratified by the first tag, and never picks by
difficulty. It makes three sets:
- **train** (`train_ids`): the failure analyst reads these traces;
- **validation** (`test_ids`, its name from before the holdout): scored every round, it picks
  which round wins;
- **holdout** (`holdout_ids`): the final test. Nothing runs on it until hand-back, so no choice
  is ever made from it.

`split` lists any case whose question names a relative window or whose gold reads the clock,
under `dates`. Fix each window as above, re-run its gold and run `refreeze` before the
baseline.

**Refreshing gold.** A fixed window's answer changes only when the tables under it do. Re-run a
case's gold when its gold SQL changes, or after a backfill or correction to those tables, then
`refreeze` and re-run the baseline. `summary` gives the oldest gold's age and flags it past 30
days. `compare` refuses two variants graded against different gold for the same case.

Tell the user what validation can resolve: for a pass rate, the 95% band is about
`±1/sqrt(n·reps)` (20 validation cases × 2 reps ≈ ±16 points). If that's too coarse, add cases
or reps before the baseline. `freeze.json` holds a digest of the cases, the gold SQL, the split
and the grader. Once a variant has a graded row, `split` refuses to redraw, and `record`
refuses while any of those four differ from the freeze.

**STOP gate.** "Eval set: N cases (train T / validation V / holdout H), all gold verified.
Answers stay local."
Set `phase` to `agent`.
