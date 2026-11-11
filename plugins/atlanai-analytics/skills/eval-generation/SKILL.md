---
name: eval-generation
description: "Atlan Analytics: generate an eval set for a use case, 5 questions its dashboard and analysts already ask with trusted SQL, kept locally or saved to Atlan Trident. Use when asked to build evals or golden questions. Not for metrics."
---

# Generate an eval set for a use case

A capability. An eval set is built for one use case: the agent the context was built for,
answering what its dashboard and analysts already ask. This skill picks the questions that test
the metrics that use case needs, drafts each in a person's words beside its trusted SQL, and
writes the set. It runs no test: answering, scoring and acting on a score belong to whatever
runs the set. A person can call it directly, and another skill can call it as a step; it behaves
the same either way.

Read [shared conventions](../analytics-build/references/conventions.md) first: privacy,
read-only SQL and status reporting apply. Open this skill's files with the Read tool, one call
per file, never chained in a shell with `echo` separators: zsh reads a word like `=====` as a
command.

## What this skill owns, and what it hands off

| Step | Owner |
| --- | --- |
| Use case, question pool, picking 5, drafting, the set file, saving the set | this skill |
| Running the set, scoring it, acting on a miss | whatever runs the set, never this skill |

Never edit a metric, a saved query or the run's files from here.

Paths. Pass each command as separate argv entries; zsh does not split a string holding one.
- `<evals-dir>` is this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/eval-generation` in
  Claude Code). `<build-dir>` is `analytics-build` beside it.
- `<run>` is the analytics run and `<gen>` the drafting folder, both as `SCOPE inputs` returns
  them. The drafting folder sits under `.atlan-evals/gen/`, outside the run: agents that answer
  from the run must never see an answer. Never use `PROJECT location` here: it names a new run.
- `SCOPE` is `python3 "<evals-dir>/scripts/scope.py"`. `EVAL` is
  `python3 "<build-dir>/scripts/analytics_eval.py" <command> --dir "<gen>"`.
- `EVALGEN` is `uv run --no-project --with "sqlglot>=30.20,<30.21" env
  PYTHONPATH="<evals-dir>/modules/evals/engine" python -m eval_builder.cli --work
  "<gen>/work"`. Without `uv`, name that one prerequisite and stop.

## `generate`: use case first, metric aware

1. **Scope.** `SCOPE inputs --project <project>`. It finds the built run, opens the drafting
   folder, reads the use case and focus metric, and writes the pool: every dashboard tile and
   analyst query the metrics engine verified, each linked to the metrics it computes. A blocked
   reply names what to ask: ask it once and stop until told.
2. **Gate and rank.** `EVALGEN init --local`, then
   `EVALGEN build --from-files "<gen>/src" --dialect <its dialect> --limit 100`. It drops a query
   pinned to one record and withholds personal data. Do not reopen its JSON.
3. **Pick 5.** `SCOPE pick --gen <gen>`: two questions on the focus metric, then one each on
   the metrics nearest the use case. It keeps only those prompts.
4. **Draft, in a subagent.** Give one subagent the paths of `work/prompts/` and
   `work/answers.json` and this instruction: follow each prompt and fill its key in
   `answers.json` with the JSON it asks for, and ask for what the SQL measures, in its direction:
   SQL that returns a share approved gets a question about the share approved, never one about
   the share rejected. Then reply `done`. It never sees the run.
5. **Write the set.** `EVALGEN review --name <slug>`, then `SCOPE emit --gen <gen> --name <slug>`.
   It writes `<project>/.atlan-evals/sets/<slug>.jsonl`, one question per line: `{"id",
   "question", "sql", "tests", "from"}`, the same shape as a set a person brings.

Budget: about 2 minutes. Keep every model step in a subagent and every count in a script.

## `save`: on this machine, or to Atlan Trident

Any set file can be saved: one this skill wrote, or one the person brought. It is already on this
machine. Unless the caller already chose, ask one question, "Where should the set live?":

| Option | Description |
| --- | --- |
| Keep on this machine (Recommended) | It stays in this folder, ready to run. |
| Save to Atlan Trident | Your team can find and rerun it. Questions and a digest of each SQL, never the SQL. |

On Save to Atlan Trident: `SCOPE ready --set <set file>` returns a `dir` and `slug`; then, with
`--dir` set to that `dir`, `EVAL init --slug <slug>`, `EVAL import-spec --spec "<dir>/work/spec.json"`
and the loop `EVAL publish --variant baseline --dataset-only --workspace <id>` and `EVAL
publish-ack`, as [publish](references/publish.md) describes. The workspace is the one the context
was saved to, else the person's personal workspace (`registry__list_workspaces`,
`personal_workspace_id`).

## What comes back

A result, as [conventions](../analytics-build/references/conventions.md) describes: how many
questions the set holds, the metrics it covers, where each came from, and where the set lives.
Each question in a plain line with the metric it tests.
