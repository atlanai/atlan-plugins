---
name: accuracy-check
description: "Atlan Analytics: run an eval set against a project's context and score it: each question to a fresh agent, results compared by script, why each miss failed. Use when asked to test or score accuracy. Not for generating evals."
---

# Run and score an eval set

A capability. It takes an eval set, asks each question to a fresh agent that sees only the
context, compares each answer's result with the trusted result by script, and says why each
miss failed. It generates no questions and changes nothing: a metric, a saved query and the set
stay as they are. A person can call it directly, and another skill can call it as a step; it
behaves the same either way.

Read [shared conventions](../analytics-build/references/conventions.md) first: privacy,
read-only SQL and status reporting apply. Open this skill's files with the Read tool, one call
per file, never chained in a shell with `echo` separators: zsh reads a word like `=====` as a
command.

## What this skill owns, and what it hands off

| Step | Owner |
| --- | --- |
| Loading a set, its trusted answers, running it, the score, why each miss failed, testing again | this skill |
| Answering each question | `analytics-ask`, in a fresh subagent per question |
| Generating or saving a set | `eval-generation` |
| Choosing a set, acting on a miss, changing a metric | whoever called it, never this skill |

`CHECK`, `EVAL` and `PROJECT` are as [run and score a set](references/run.md) names them. Pass each
command as separate argv entries; zsh does not split a string holding one.

## Run a set

1. **Find the run.** `CHECK start --project <project>` returns the built run, `context_saved_to`
   (where the context lives; `null` means this machine only) and the project's sets.
2. **Load the set.** `CHECK load --project <project> --file <set>` puts it in a fresh eval folder
   in `.atlan-evals/`, runs each trusted SQL on the run's data, drops a question whose trusted SQL
   fails or returns nothing (`dropped`), and sets the rest up for scoring. Its
   `work/selection.json` holds each question's metric, source and expected answer.
3. **Run and score it** as [run it](references/run.md#run-it) describes, then show the score as
   [show the score](references/run.md#show-the-score) describes.

## Test again

After the context changed, [test again](references/run.md#test-again) on the same eval folder,
pinned to a metric version when the caller names one.

## What comes back

The summary's `score_line`, word for word, and one line per miss: the question, the metric it
tests, the expected value, the agent's value and why it failed. The eval folder, for whoever acts
on the misses. Never call a question checked when its SQL did not run.
