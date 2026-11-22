---
name: accuracy-check
description: "Atlan Analytics: run an eval set against a project's context and score it: each question to a fresh agent, results compared by script, why each miss failed. Use when asked to test or score accuracy. Not for a demo (analytics-demo) or making evals."
---

# Run and score an eval set

A capability. It works on any built run: a local SQLite or DuckDB one, or a warehouse build
(Snowflake, Databricks or another) whose SQL runs through this host's warehouse query tool. It
takes an eval set, asks each question to a fresh agent that sees only the context, compares each answer's result with the trusted result (by script, with a judge where the script refuses an answer that ran), and says why each
miss failed. Every check also asks the same questions with schema only, so the result shows
what the context changed. It generates no questions and changes nothing: a metric, a saved query and the set
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
   and the project's sets, and `before_after`, always true. A caller that knows the run passes
   `--run <run-dir>` here and to `load`. With several built runs and none named, the reply lists
   them: ask which one once.
   With no set in the project, or the person asking for new questions, generate one: run
   `eval-generation` `generate` with its default of 20 questions, then load the set it wrote. A
   caller that wants another size passes `--questions <n>` here and `--limit <n>` to its
   `SCOPE pick` (the demo passes 8).
2. **Load the set.** `CHECK load --project <project> --file <set>` puts it in a fresh eval folder
   in `.atlan-evals/`. On a local run it runs each trusted SQL on the run's data, drops a question
   whose trusted SQL fails or returns nothing (`dropped`), and sets the rest up for scoring. On a
   warehouse run it returns `pending_gold`: get the trusted results as
   [trusted answers on a warehouse run](references/run.md#trusted-answers-on-a-warehouse-run)
   describes, and `CHECK gold` finishes the same way. `work/selection.json` holds each
   question's metric, source and expected answer.
3. **Show a set the person has not seen, and confirm it.** A set generated in this request is
   shown before it runs: the table `CHECK show` prints, as a chat message, then one native
   question, "Run these <N> questions?", with Run them, Edit questions and Skip for now, as the
   demo's [generated questions](../analytics-demo/references/accuracy.md#2-generated-questions-show-them-then-ask-to-run-them)
   step words it. The questions never go in the question's text. `CHECK edit` rewords a question
   and `CHECK drop` takes questions out. This holds when a person calls this skill directly too.
   A set the person brought and picked runs as it is.
4. **Run and score it** as [run it](references/run.md#run-it) describes, with the raw-source
   pass of [before and after](references/run.md#before-and-after) on every check, started with
   the context pass, whatever the size of the set and whoever started the check,
   then show the score as [show the score](references/run.md#show-the-score) describes.

## A new check is a new check

- A request to check accuracy starts a new eval folder, every time. `CHECK start` lists earlier
  `checks`; read them only when the person asks to compare, never to decide what to run.
- Ask only what these steps ask: which set, and whether to run a generated one. Never ask which
  model, how many runs per question, where to run or whether to publish: this skill fixes them.
- When `CHECK start` is blocked because the folder holds no built context, say its reason in one
  line and stop. This skill scores a context built with Atlan Analytics; it does not score a
  registry agent's own eval set, and it never falls back to another harness or to memory of an
  earlier run.

## Test again

After the context changed, [test again](references/run.md#test-again) on the same eval folder,
pinned to a metric version when the caller names one.

## Save to Atlan Trident

After every check's result, offer to [save the check](references/run.md#save-to-atlan-trident):
the set, the schema only run and every run with the context, each linked to the run before it.
When a person called this skill directly, end on the demo's result message and its plain save
offer yourself, and save on "save". No agent is registered: the runs save without one. A skill that called this one
asks it instead.

## What comes back

The run report as `EVAL report` prints it: the `score_line`, word for word, every run on the
set, the run it is compared with, and the misses under their numbers, grouped by cause. The eval folder, for whoever acts
on the misses. Never call a question checked when its SQL did not run.
