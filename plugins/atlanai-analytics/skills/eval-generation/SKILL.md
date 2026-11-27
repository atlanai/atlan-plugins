---
name: eval-generation
description: "Atlan Analytics: write an eval set to test your agent, 20 questions your team already asks, each with a known answer, kept here or saved to Atlan Trident. Use when asked to build evals or golden questions. Not for metrics (metrics-generation)."
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
| Use case, question pool, picking 20, drafting, the set file, saving the set | this skill |
| Running the set, scoring it, acting on a miss | whatever runs the set, never this skill |

Never edit a metric, a saved query or the run's files from here.

Paths. Pass each command as separate argv entries; zsh does not split a string holding one.
- `<evals-dir>` is this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/eval-generation` in
  Claude Code). `<build-dir>` is `analytics-build` beside it.
- `<run>` is the analytics run and `<gen>` the drafting folder, both as `SCOPE inputs` returns
  them. The drafting folder sits under `.atlan-analytics/evals/gen/`, outside the run: agents that answer
  from the run must never see an answer. Never use `PROJECT location` here: it names a new run.
- `SCOPE` is `python3 "<evals-dir>/scripts/scope.py"`. `EVAL` is
  `python3 "<build-dir>/scripts/analytics_eval.py" <command> --dir "<gen>"`.
- `EVALGEN` is `uv run --no-project --with "sqlglot>=30.20,<30.21" env
  PYTHONPATH="<evals-dir>/modules/evals/engine" python -m eval_builder.cli --work
  "<gen>/work"`. Without `uv`, say "Writing test questions needs uv, a Python tool, on this
  machine. Install it, then ask again." and stop.

## `generate`: use case first, metric aware

A helper result with `show_first` carries a chip the run has reached: show it before any other
words or call. Between calls, write nothing of your own: no working notes, no line about a card before or after it,
and never a file path or a step's, gate's or helper's own name.

1. **Scope.** `SCOPE inputs --project <project>`. It finds the built run, opens the drafting
   folder, reads the use case and focus metric, and writes the pool: every dashboard tile and
   analyst query the metrics engine verified, each linked to the metrics it computes. A caller
   that knows the run passes `--run <run-dir>`, and `--use-case "<which agent, answering what>"`
   when the run recorded none. It works on a local run and on a warehouse build alike: nothing
   here runs SQL. A blocked reply names what to ask: ask it once and stop until told.
2. **Gate and rank.** `EVALGEN init --local`, then
   `EVALGEN build --from-files "<gen>/src" --dialect <the dialect `SCOPE inputs` returned> --limit 100`. It drops a query
   pinned to one record and withholds personal data. Do not reopen its JSON.
3. **Pick 20.** `SCOPE pick --gen <gen>`, with `--limit <n>` when the caller names another size
   (the demo asks for 8): two questions on the focus metric, then one each on
   the metrics nearest the use case, round after round, then past queries no metric computes yet (`no_metric`). It leaves out
   (`skipped`) a query that lists records with no total or count, a test query that returns nothing,
   a data test and a freshness check: no person asks them as a question. It picks 4 spares beyond the 20, for a draft
   that finds a question unanswerable, and keeps only those prompts. When it picked
   fewer than 20, say "I found <N> questions your team asks that fit, fewer than 20. The set holds
   those <N>."
4. **Draft, in a subagent.** Give one subagent, in the foreground, the paths of `work/prompts/` and
   `work/answers.json` and this instruction: follow each prompt and fill its key in
   `answers.json` with the JSON it asks for, and ask for what the SQL measures, in its direction:
   SQL that returns a share approved gets a question about the share approved, never one about
   the share rejected. Name what the SQL fixes, in a person's words: the event, program, list
   or tag it filters on, each measure it returns, and its dates when they are fixed ("the first
   week of October", never "recently"). A period the SQL takes from today's date stays
   relative ("this quarter"). Then reply `done`. It never sees the run.
5. **Write the set.** `EVALGEN review --name <slug>`, then `SCOPE emit --gen <gen> --name <slug>`.
   It keeps the first 20 drafted questions in pick order, so a spare fills in for one the draft
   dropped, and writes `<project>/.atlan-analytics/evals/sets/<slug>.jsonl`, one question per line: `{"id",
   "question", "sql", "tests", "from", "generated"}`, the same shape as a set a person brings.

Budget: about 3 minutes. Keep every model step in a subagent and every count in a script.

## `save`: on this machine, or to Atlan

Any set file can be saved: one this skill wrote, or one the person brought. It is already on this
machine. Unless the caller already chose, ask one question, "Where should these test questions be kept?":

| Option | Description |
| --- | --- |
| Keep on this machine (Recommended) | They stay in this folder, ready to test your agent. |
| Save to Atlan Trident | Your team can see the questions. Their SQL stays on this machine, so tests start here. |

On Save to Atlan Trident: `SCOPE ready --set <set file>` returns a `dir` and `slug`; then, with
`--dir` set to that `dir`, `EVAL init --slug <slug>`, `EVAL import-spec --spec "<dir>/work/spec.json"`
and the loop `EVAL publish --variant baseline --dataset-only --workspace <id>` and `EVAL
publish-ack`, as [publish](references/publish.md) describes. The workspace is the one the context
was saved to, else the person's personal workspace (`registry__list_workspaces`,
`personal_workspace_id`).

## What comes back

A result, as [conventions](../analytics-build/references/conventions.md) describes: how many
questions the set holds, the metrics it covers, where each came from, and where the set lives.
The questions chip and the side page list each question; only where the chip's lines say no page
is open, each question in a plain line with the metric it tests. When a person asked for the
questions themselves, end on one native question, "Test your agent on these <N> questions?"
(Test them (Recommended), Not now), its text holding the chip's lines in a terminal; on Test them,
`accuracy-check` runs the set as it is. A skill that called this one asks its own question instead.
