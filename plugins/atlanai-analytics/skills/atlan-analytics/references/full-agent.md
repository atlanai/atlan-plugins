# Full-agent preflight and routing

## 1. Preflight (once per conversation, quietly)

0. Check the harness first. Build, eval and improve need a shell and either Claude Code's
   isolated Agent tool or the installed Claude/Codex CLI for the
   [question-only runner](eval/hosts.md). Record the selected runner in state. Without either,
   stop with BLOCKED before Registry calls; ask and connect remain available. Never replace
   an unavailable isolated runner with the current conversation.
1. Open a registry session as the conventions' **Registry calls** describes, with an
   `objective` naming the project. Keep the `session_id` for every later registry call. If no
   Atlan MCP tools are available at all, stop with BLOCKED: the user needs the `atlan` plugin
   or the Atlan MCP server connected and signed in.
2. Call `registry__list_workspaces` with `limit: 1`: it proves access and returns the total
   without listing every workspace. If it fails or returns nothing, stop with BLOCKED: the
   user needs to sign in to Atlan and be added to a workspace.
3. Call `registry__get_kind_schema` for `metric`. An unknown kind means Metric Studio is not
   installed for this account: stop with BLOCKED, and ask an Atlan admin to install Metric
   Studio from the Store.
4. Find the **warehouse query tool** with the [warehouse discovery procedure](connect.md). It looks
   through Atlan first, then this harness's own connectors, asks the user to connect or add
   one when none works, and records `warehouse` in state. From then on every skill calls it
   "the warehouse query tool recorded in state".
5. Run `EVAL selftest` to confirm `python3` works. It needs only the standard library.

Once step 2 has opened the session, steps 3 and 5 depend on nothing else: send them in the same
turn as parallel calls, beside step 4's first search.

Show one line per check (`ok` or what failed).

The preflight runs once per conversation. When this conversation already passed it, skip it on a
later run: reuse the session, the metric schema's answer and the warehouse tool in state. Run it
again only after a check failed, a call was refused for sign-in or access, or the user says they
connected or changed something.

## 2. Resume or start

- Look for `.atlan-analytics/*/state.json`. For each one, read its `phase`, `ids.agent_id` and
  `ids.workspace_id` (a project adopted before 0.4.4 keeps the two ids at the top level).
- **One project mid-flight:** welcome back in two sentences (what exists, what's next), then
  continue in the skill for its phase.
- **Several projects:** ask which one, with the most recently updated recommended.
- **None:** ask one question.
  - `new project · start · what you want to do`
  - **Build a new talk-to-data agent (Recommended when none is registered)**:
    `atlan-analytics build`
  - **Improve an agent that already exists**: `atlan-analytics improve` (adoption)
  - **Just run an eval**: `atlan-analytics eval`

  To offer improvement credibly, first list agents with `registry__list_artifacts`
  (kind `agent`) and name up to three in the option description.
- On a new project, run `EVAL init --dir .atlan-analytics/<slug> --slug <slug>`, then set `phase` to
  `purpose` and record `warehouse` and `session_id` in its `state.json`. Registry ids go under `ids`:
  `ids.workspace_id`, `ids.agent_id`. `EVAL publish` reads them from there. Ask once whether to add `.atlan-analytics/` to
  `.gitignore`: it holds eval answers, and they shouldn't be committed next to the agent.

## 3. Route by phase

| phase | continue in |
|---|---|
| purpose, scope, context, eval_set, agent, smoke, baseline | `atlan-analytics build` |
| improve, round_N | `atlan-analytics improve` |
| any, when the user asks "how is it scoring" | `atlan-analytics eval` |

An explicit argument (`/atlanai-analytics:atlan-analytics improve`) wins over the phase.

## What the user ends up with

- A **registry workspace** holding the agent's context: metrics, saved SQL queries
  (`sql_query`) and any rule skills.
- A **registered agent** and a **Claude Code subagent** `.claude/agents/<slug>.md` that is that
  agent when Claude's Agent tool is used. In Codex, keep the registered identity and run
  isolated cases through the question-only CLI runner. Its context contains released
  definitions; the parent validates and executes its proposed SQL. Record which execution
  mode was evaluated. Metrics and propose modes need a shell, not subagents.
- A **local eval set**: questions, plus gold SQL kept out of the agent's reach.
- **Experiments in the registry**: every run's score and session, visible in the
  agent's Evals and Sessions tabs.

End with one status line per [shared conventions](conventions.md).
