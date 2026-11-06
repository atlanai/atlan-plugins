# Conventions for project modes

Read this with the [shared conventions](conventions.md) when a mode keeps a project directory:
metrics, build, eval, improve, dashboard and propose. These rules beat anything a phase file
says, except the explicit dashboard orchestration policy below; the shared conventions' registry calls, source of truth, safety and status line apply too.

`EVAL` means
`python3 "<skill-dir>/scripts/analytics_eval.py" <command> --dir .atlan-analytics/<slug>`,
where `<skill-dir>` is `${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics` in an installed plugin,
or the canonical skill directory fetched with its supporting resources through MCP.
`filter-querylog`, `grade`, `session-run` and `selftest` take no `--dir`.
A relative file argument resolves from `--dir`, so pass paths as the phase files write them
(`hillclimb/V/answers/…`, `eval/gold/…`); an absolute path, such as a transcript's, is used as is.
It prints one JSON object. Exit 0 = ok, 1 = error, **2 = BLOCKED**: show the user its `reason`
and `fix` in two lines and stop that phase. Don't work around a BLOCKED.

`MINE` means `python3 "<skill-dir>/scripts/metrics_mine.py" <command>`, where `<skill-dir>` is
this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics` in Claude Code). It is
standard library only, and its JSON output and exit codes follow the same rules as `EVAL`'s,
with one more status: `check` prints `REFUSED`, exit 1, when some items failed. They are listed
in `plan.json`; fix them as the metrics workflow says.

The explicit `metrics` mode uses only the metrics workflow (`references/metrics.md`) and
`.atlan-metrics/<slug>/`.
It does not run the full agent preflight, initialize an eval project, or resume a full agent
build. Its final gate sets `phase` to `complete`; no later agent or eval phase follows.

State holds what the shared conventions keep for a conversation: record the registry session
id as `session_id`, and the warehouse query tool as `warehouse.tool` in the dialect recorded as
`warehouse.dialect`.

## Asking questions

- **One decision per `AskUserQuestion` call** (a plain chat question where the harness has no
  question tool). Wait for the answer before asking the next.
- **Re-ground every question** with one line at the top: `<slug> · <phase> · deciding <what>`.
- **Recommended option first**, labelled `(Recommended)`, with the reason in its description.
  Give 2–4 concrete options and never add your own "Other".
- **Propose, don't interrogate.** Draft a default from what you already know (the brief, the
  state file, the warehouse, the registry) and ask the user to confirm or edit it.
- **Smart-skip.** If state or an earlier answer already settles it, say so in one line and move on.
- **Escape hatch.** If the user says "just do it", ask only the one-way-door questions (what to
  publish, which workspace, which answers are trusted), then take the recommended defaults and
  say which ones you took.
- **Outward-facing actions** (registry writes, registering an agent, writing
  `.claude/agents/<slug>.md`): show exactly what will be written and where, then ask. A yes
  covers that one action only.

## Dashboard orchestration policy

For dashboard mode only, a request to convert the dashboard authorizes reversible local
extraction, SQL authoring and source/warehouse checks within the supplied scope. Continue
these steps without phase-by-phase confirmations. Keep host permission checks intact.

Preview one concrete plan covering the target workspace, usable metric definitions, release
actions and agent specification. Ask once for any missing authorization for that exact plan.
Record its scope and decision; nested metric/agent phase instructions must not ask again for
an unchanged approved action. A changed destination, definition or action needs new review.
Independent paid evals require their own explicit budget when not already authorized.

A failure pauses its affected step only. Missing parity or publication access does not stop
questions on usable local metrics. A known wrong result or incomplete security scope holds
that metric for correction. Never certify it, widen scope or invent a successful receipt.
Use the single run index and recovery commands in [dashboard](dashboard.md).

## Phases and gates

- Phases run in order. Each ends with a **STOP gate**: summarise what it produced in 2–4
  lines, set `phase` in `state.json` to the next phase, and confirm before starting it.
  Writing the recommendation in prose and carrying on is the failure the gate exists to prevent.
- A **checkpoint** is a gate with nothing to decide. The phase file marks it. Summarise in one
  line, set `phase`, and carry on: the next phase opens with its own question.
- Gates stay where the user decides something: the workspace, which answers are trusted, any
  registry write, and spending on a run. A plan the user approved covers local steps that can
  be redone, such as writing `brief.md`, `tables.txt` or the eval files. It never covers any of
  those decisions.
- When you enter a phase, Read its reference file. Don't work from memory.
- Record each user decision in `state.json` under `decisions[]` as `{key, choice, why, ts}`.
  The improve loop keeps its plan and round counters in `hillclimb/_state.json`; its decisions
  still go in `decisions[]`.
- **Resume**: `state.json` is the source of truth. Evidence lives under `.atlan-metrics/<slug>/`
  for metrics mode and `.atlan-analytics/<slug>/` for full agent modes. Do not cross those modes
  or resume a different workspace's run without the user's explicit choice.

## Safety (non-negotiable)

- Refer to credentials by variable name only.
- **Ground truth stays with the grader.** Gold SQL and gold results (`answers.jsonl`, the
  `gold/` result files) are:
  - never put in the answering agent's prompt or reach. The one exception is the grader agent:
    for a case tagged `fuzzy` it reads the gold result beside the agent's answer, and never
    the agent's context;
  - never written to the agent's workspace, a metric, a saved query, a rule or the agent's
    instructions;
  - never uploaded to the registry. Publishing sends only a digest.

  The talk-to-data subagent has no file, shell or web tools, so it can't read them. Keep it
  that way.
- **The specialist agents are isolated by instruction, not by their tools.** The grader's Read
  and the analyst's Read, Grep and Glob can open any file, `eval/answers.jsonl` included. So
  hand each one only what it needs: the grader one input file, written for that case under
  `hillclimb/V/judge/`, and the analyst the exact list of train trace paths. Say plainly in a
  report that this isolation rests on their instructions. In Claude Code's Agent tool they are
  listed with the installed plugin's prefix: `atlanai:atlan-analytics-grader` and
  `atlanai:atlan-analytics-failure-analyst` in the public Atlan marketplace bundle;
  use `atlanai-analytics:` instead for the separate add-on. Use the names the host lists.
- **The failure analyst reads train cases only.** Nobody reads validation or holdout transcripts
  while iterating, and the holdout runs only at hand-back.
- **Strip identifying literals before anything leaves the machine.** Run `EVAL filter-querylog`
  on query history. It drops rows carrying:
  - staff names and emails;
  - customer and account names;
  - any literal compared with a person's, customer's or record's ID column, however short;
  - UUIDs and prefixed IDs such as `cus_8Hk2Lm9`.

  `MINE check` applies the same rules to everything a card publishes: formula, filters, joins,
  dimensions and their values, parameter defaults, examples with their SQL and result rows, and
  the description. A sample value under a person column (`customer_name`, `email`) is refused
  whatever it looks like.
  It is a pattern match, not a guarantee: an identifier in a column it does not recognise gets
  through. Spot-check the payload again before a push, and turn any value you would not publish
  into a parameter.

## Talking to the user

- Detail belongs in files (`brief.md`, `narrative.md`), not in chat.
- Run long local jobs (a large query-history pull) with Bash `run_in_background: true`, and check the
  files on disk before trusting a completion notice.
- For big authoring jobs (the metrics authoring passes, many saved queries), split the work into chunks
  and give each chunk to a worker: a fresh subagent from the Agent tool that gets the chunk's
  files and the authoring rules, and nothing from `eval/`. Workers write answer files only; you
  run every ingest and every registry write.
