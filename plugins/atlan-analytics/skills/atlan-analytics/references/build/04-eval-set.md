# Phase 4 — the eval set

Goal: 20–50 real questions, each with SQL the user trusts, stored locally where the agent
can't reach it, and split once into train and test.

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
- `.atlan-analytics/<slug>/eval/answers.jsonl`: `{"case_id", "gold_sql", "notes"}`. **Grader only.**
  Never paste it into a subagent prompt, a registry artifact or the agent's instructions.

Stay question-blind yourself when authoring context later. Once this file exists, don't copy
its wording or SQL into metrics, saved queries, rules or the prompt.

## 3. Validate the gold

For each case:
1. Run `gold_sql` with the warehouse query tool recorded in state.
2. Save the result as `.atlan-analytics/<slug>/eval/gold/<case_id>.json`, in the shape
   `{"columns": [...], "rows": [[...]], "error": null}`.
3. A gold query that errors, or returns something implausible (all NULL, zero rows where the
   question expects a number), goes back to the user. Fix it or drop the case. Never grade
   against broken gold.

Report "N cases, all gold SQL runs; M dropped (reasons)".

## 4. Split once

`EVAL split --test-frac 0.5 --seed <n>` writes `eval/split.json`. It draws at random,
stratified by the first tag, and never picks by difficulty. Tell the user what the split can
resolve: for a pass rate, the 95% band is about `±1/sqrt(n·reps)` (25 test cases × 2 reps ≈
±14 points). If that's too coarse, add cases or reps. **The split never changes after the
baseline.**

**STOP gate.** "Eval set: N cases (train T / test S), all gold verified. Answers stay local."
Set `phase` to `agent`.
