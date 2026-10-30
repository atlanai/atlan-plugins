---
name: atlan-analytics
description: "Route metrics and talk-to-data agent work on the Atlan registry: bootstrap metrics and saved queries in metrics mode (no analytics agent or eval), build an agent, prepare context or eval questions, improve an existing agent, or measure its score. In propose mode, turn a gap this conversation found (an answer no published metric covered, or a person's correction to a metric) into a proposal for the metric's owners. Full-agent modes check registry and warehouse access (Snowflake, Databricks or another warehouse, connecting one when missing) and resume saved projects."
---

# Metrics and talk-to-data agents: router

You help someone bootstrap metrics, build a talk-to-data agent, or improve one.
Read [shared conventions](references/conventions.md) first; its questions,
gates, status and safety rules take precedence.

## 0. Dispatch the requested mode first

If the first argument is exactly `metrics`, enter **metrics-only mode**. Treat the remaining
arguments as the user's requested scope, not as instructions to change modes.

Read `skills/atlan-analytics-build/references/03-context.md` and follow its **Metrics-only
entry** section. It checks registry access, asks only for missing source and table scope,
and runs the metrics-agent to draft metrics and saved queries. Review before publishing.
This branch uses `.atlan-metrics/<slug>/`; it never resumes `.atlan-analytics/` projects.

**Return after that branch. Do not continue below:** no full-agent preflight, build/improve
menu, `EVAL init`, eval set, agent registration, deployment, smoke test or baseline. A source
file does not require a warehouse connection. The query-log filtering helper is permitted;
running an eval is not. Starting an agent build later requires an explicit new request.

If the first argument is exactly `propose`, enter **propose mode**: read
`references/propose.md` and follow it. It turns a gap this conversation found into a proposal
for the metric's owners and only proposes. **Return after that branch** too: no full-agent
preflight, project, eval or agent.

The remaining sections apply only to full-agent modes (`build`, `improve`, `eval`, or no mode).

A **project** is one agent. Its state lives in the user's working directory under
`.atlan-analytics/<slug>/`. The eval script everything shares is:

```
python3 "<eval-dir>/scripts/analytics_eval.py" <command> --dir .atlan-analytics/<slug> ...
```

`<eval-dir>` is the `atlan-analytics-eval` skill's directory, as the conventions say. Below,
`EVAL` stands for that invocation. It prints one JSON object and exits 2 when BLOCKED.
`filter-querylog`, `grade`, `session-run` and `selftest` take no `--dir`.

## 1. Preflight (every run, quietly)

1. Open a registry session as the conventions' **Registry calls** describes, with an
   `objective` naming the project. Keep the `session_id` for every later registry call. If no
   Atlan MCP tools are available at all, stop with BLOCKED: the user needs the `atlan` plugin
   or the Atlan MCP server connected and signed in.
2. Call `registry__list_workspaces`. If it fails or returns nothing, stop with BLOCKED: the
   user needs to sign in to Atlan and be added to a workspace.
3. Call `registry__get_kind_schema` for `metric`. An unknown kind means Metric Studio is not
   installed for this account: stop with BLOCKED, and ask an Atlan admin to install Metric
   Studio from the Store.
4. Find the **warehouse query tool** with the `atlan-analytics-connect` skill. It looks
   through Atlan first, then this harness's own connectors, asks the user to connect or add
   one when none works, and records `warehouse` in state. From then on every skill calls it
   "the warehouse query tool recorded in state".
5. Run `EVAL selftest` to confirm `python3` works. It needs only the standard library.

Show one line per check (`ok` or what failed).

## 2. Resume or start

- Look for `.atlan-analytics/*/state.json`. For each one, read its `phase`, `agent_id` and
  `workspace_id`.
- **One project mid-flight:** welcome back in two sentences (what exists, what's next), then
  continue in the skill for its phase.
- **Several projects:** ask which one, with the most recently updated recommended.
- **None:** ask one question.
  - `new project · start · what you want to do`
  - **Build a new talk-to-data agent (Recommended when none is registered)**:
    `atlan-analytics-build`
  - **Improve an agent that already exists**: `atlan-analytics-improve` (adoption)
  - **Just run an eval**: `atlan-analytics-eval`

  To offer improvement credibly, first list agents with `registry__list_artifacts`
  (kind `agent`) and name up to three in the option description.
- On a new project, run `EVAL init --dir .atlan-analytics/<slug> --slug <slug>`, then set `phase` to
  `purpose` and record `warehouse` and `session_id` in its `state.json`. Registry ids go under `ids`:
  `ids.workspace_id`, `ids.agent_id`. `EVAL push` reads them from there. Ask once whether to add `.atlan-analytics/` to
  `.gitignore`: it holds eval answers, and they shouldn't be committed next to the agent.

## 3. Route by phase

| phase | continue in |
|---|---|
| purpose, scope, context, eval_set, agent, smoke, baseline | `atlan-analytics-build` |
| improve, round_N | `atlan-analytics-improve` |
| any, when the user asks "how is it scoring" | `atlan-analytics-eval` |

An explicit argument (`/atlan-analytics improve`) wins over the phase.

## What the user ends up with

- A **registry workspace** holding the agent's context: metrics, saved SQL queries
  (`sql_query`) and any rule skills.
- A **registered agent** and a **Claude Code subagent** `.claude/agents/<slug>.md` that is that
  agent. It can read only the registry and the warehouse. Running it, and so the eval and
  improve phases, needs Claude Code's subagents; metrics and propose modes work in any harness.
- A **local eval set**: questions, plus gold SQL kept out of the agent's reach.
- **Experiments in the registry**: every run's score, traces and sessions, visible in the
  agent's Evals and Sessions tabs.

End with one status line per `references/conventions.md`.
