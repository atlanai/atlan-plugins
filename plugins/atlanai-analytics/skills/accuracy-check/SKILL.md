---
name: accuracy-check
description: "Atlan Analytics: run an eval set against a project's context and score it: each question to a fresh agent, results compared by script, why each miss failed. Use when asked to test or score accuracy. Not for generating evals."
---

# Run and score an eval set

A capability. It works on any built run: a local SQLite or DuckDB one, or a warehouse build
(Snowflake, Databricks or another) whose SQL runs through this host's warehouse query tool. It
takes an eval set, asks each question to a fresh agent that sees only the context, compares each answer's result with the trusted result (by script, with a judge where the script refuses an answer that ran), and says why each
miss failed. Asked for a before and after, it also runs the questions on the raw source. It generates no questions and changes nothing: a metric, a saved query and the set
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
| Saving a check's set and runs to Atlan Trident, when asked | this skill |
| Generating a set, or saving a set on its own | `eval-generation` |
| Choosing a set, acting on a miss, changing a metric | whoever called it, never this skill |

`CHECK`, `EVAL` and `PROJECT` are as [run and score a set](references/run.md) names them. Pass each
command as separate argv entries; zsh does not split a string holding one.

## Run a set

1. **Find the run.** `CHECK start --project <project>` returns the built run, `context_saved_to`
   (where the context lives; `null` means this machine only), `execution` (where its SQL runs)
   and the project's sets. A caller that knows the run passes `--run <run-dir>` here and to
   `load`. With several built runs and none named, the reply lists them: ask which one once.
2. **Load the set.** `CHECK load --project <project> --file <set>` puts it in a fresh eval folder
   in `.atlan-evals/`. On a local run it runs each trusted SQL on the run's data, drops a question
   whose trusted SQL fails or returns nothing (`dropped`), and sets the rest up for scoring. On a
   warehouse run it returns `pending_gold`: get the trusted results as
   [trusted answers on a warehouse run](references/run.md#trusted-answers-on-a-warehouse-run)
   describes, and `CHECK gold` finishes the same way. `work/selection.json` holds each
   question's metric, source and expected answer.
3. **Run and score it** as [run it](references/run.md#run-it) describes, with the raw-source
   pass of [before and after](references/run.md#before-and-after) when the caller asks for one,
   then show the score as [show the score](references/run.md#show-the-score) describes.

## Test again

After the context changed, [test again](references/run.md#test-again) on the same eval folder,
pinned to a metric version when the caller names one.

## Save to Atlan Trident

When the caller asks, [save the check](references/run.md#save-to-atlan-trident): the set and
every run of it on that eval folder, each linked to the run before it, to the workspace and agent
the caller names.

## What comes back

The summary's `score_line`, word for word, and one line per miss: the question, the metric it
tests, the expected value, the agent's value and why it failed. The eval folder, for whoever acts
on the misses. Never call a question checked when its SQL did not run.
