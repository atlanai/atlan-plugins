---
name: eval-generation
description: "Atlan Analytics: test how accurately a use case's context answers, from 5 questions its dashboard and analysts already ask, then propose fixes. Use when asked to check accuracy, build evals or golden questions. Not for metrics (metrics-generation)."
---

# Check accuracy for a use case

A capability. An eval set is built for one use case: the agent the context was built for,
answering what its dashboard and analysts already ask. It picks questions that test the
metrics that use case needs, runs them against the context, scores each answer, and turns a
miss into a fix proposal. A person can call it directly, and another skill can call it as a step;
it behaves the same either way.

Read [shared conventions](../analytics-build/references/conventions.md) first: privacy,
read-only SQL and status reporting apply.

## What this skill owns, and what it hands off

| Step | Owner |
| --- | --- |
| Use case, question pool, picking 5, drafting, trusted answers, scoring, the check's moments | this skill |
| Answering each question | `analytics-ask`, in a fresh subagent per question |
| Changing a metric | `metrics-generation`: `edit` in this run when the context is on this machine; `propose` when it is in Atlan Trident, approved by the owner in Metric Studio |
| Saving the set, scorer and runs to Atlan Trident | this skill, through `EVAL publish` |
| Anything a calling flow adds before or after the check | the caller |

Never edit a metric, a saved query or the run's files from here.

Paths. Pass each command as separate argv entries; zsh does not split a string holding one.
- `<evals-dir>` is this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/eval-generation` in
  Claude Code). `<build-dir>` is `analytics-build` beside it.
- `<run>` is the analytics run and `<evals-run>` its eval folder, both as `SCOPE inputs` returns
  them. The eval folder sits beside the run, never inside it: the answering agents read the run
  and must never see an answer. Never use `PROJECT location` here: it names a new run.
- `SCOPE` is `python3 "<evals-dir>/scripts/scope.py"`. `EVAL` is
  `python3 "<build-dir>/scripts/analytics_eval.py" <command> --dir "<evals-run>"`. `PROJECT` is
  `python3 "<build-dir>/scripts/project.py" --run-dir "<run>"`: without `--run-dir` it refuses
  every query.
- `EVALGEN` is `uv run --no-project --with "sqlglot>=30.20,<30.21" env
  PYTHONPATH="<evals-dir>/modules/evals/engine" python -m eval_builder.cli --work
  "<evals-run>/work"`. Without `uv`, name that one prerequisite and stop.

## Who is calling

- **A person called it** ("how accurate is my job profit agent"): run the
  [check](references/check.md) on the current folder. Its use case is the one the run recorded;
  when `SCOPE inputs` is blocked, ask its question once and stop until told.
- **A flow called it** (it named the run): follow the same check; the caller adds its lines.

Budget: the check, from "Check how accurate it is" to the score, takes 3 to 5 minutes. Keep
every model step in a subagent and every count in a script.

## `generate`: use case first, metric aware

1. **Scope.** `SCOPE inputs --project <project>` (a `choose` reply means eval sets exist: see
   the [check](references/check.md); add `--generate` once generating is chosen). It finds the built run, opens a fresh eval
   folder, reads the use case and focus metric, and writes the pool: every dashboard tile and
   analyst query the metrics engine verified, each linked to the metrics it computes. Use the
   `run` and `evals` it returns. A blocked reply names what to ask.
2. **Gate and score.** `EVALGEN init --local`, then
   `EVALGEN build --from-files "<evals-run>/src" --dialect <its dialect> --limit 100`. It drops
   a query pinned to one record and withholds personal data. Do not reopen its JSON.
3. **Pick 5.** `SCOPE pick --evals <evals-run>`: two questions on the focus metric, then one
   each on the metrics nearest the use case. It keeps only those prompts.
4. **Draft, in a subagent.** Give one subagent the paths of `work/prompts/` and
   `work/answers.json` and this instruction: follow each prompt and fill its key in
   `answers.json` with the JSON it asks for, and ask for what the SQL measures, in its direction:
   SQL that returns an on-time rate gets an on-time question, never a "run late" one. Then reply
   `done`. It never sees the run.
5. **Check and run.** `EVALGEN review --name <slug>`, then
   `SCOPE gold --run <run> --evals <evals-run>`. A question whose trusted
   SQL fails or returns nothing leaves the set; say how many.

Show the set with `work/selection.json`: the question, **the metric it tests**, where it came
from, and the expected answer in one short value. The person's decision to run it is the
approval: `EVALGEN review --approve all` only after it, never on your own.

## `load`: an eval set the person already has

`SCOPE sets --project <project>` lists the sets in `<project>/.atlan-evals/sets/`, each a JSONL
of `{"id", "question", "sql"}` plus optional `"tests"` (the metric) and `"note"` (the rule the SQL
encodes). `SCOPE load --project <project> --file <set>` puts one where `generate` would, approved
because the person brought it, and runs its trusted SQL (`gold_failed` names any that did not run).
It is then ready to test.

## `save`: local or Trident

`EVAL init --slug <slug>`, then `EVAL import-spec --spec "<evals-run>/work/spec.json"`.

- **Keep on this machine:** nothing more.
- **Save to Atlan Trident:** in the workspace the context was saved to. Loop
  `EVAL publish --variant baseline --dataset-only --workspace <that workspace id>` and
  `EVAL publish-ack`, as [Publish](references/test.md#publish) describes: the `result-match`
  scorer and the dataset with `registry__create_artifact`, one record per question in one
  `registry__create_artifacts` call, read back with `registry__list_artifacts`. Trident holds
  each question and a digest of its SQL, never the SQL.

## What comes back

Counts first: questions kept and dropped, the metrics covered, where the set lives. For `test`,
the score with its range and each miss with the metric involved and why it failed. Never call a
question checked when its SQL did not run.

Before sending: the first sentence is the result; one next step, not a menu; no apology, no
closing offer; commas and full stops, no em dashes or exclamation marks.
