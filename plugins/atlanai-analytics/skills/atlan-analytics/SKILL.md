---
name: atlan-analytics
description: Prepare links, local folders and BI exports for talk-to-data using dashboards, models and query history. Use when asked to set up analytics or build an analytics agent from existing context. Publishes metrics and SQL to Trident, then suggests questions. Not for one-off data questions (atlan-analytics-ask) or business policies (atlan-ontology).
---

# Analytics context and talk-to-data

Keep progress updates and next-step explanations under 80 words unless a detailed procedure
is requested. A next-step explanation is one paragraph about the requested action and its
immediate checks. Use an inline option or command when useful; expand into command blocks,
optional source formats or a full pipeline only when that detail was requested.

Read [shared conventions](references/conventions.md) first. They define audit sessions,
task authorization, privacy and status reporting. Treat reads of the selected source,
packaged-runtime initialization and generated-file reads as ordinary task steps. Once a
publication destination is chosen for saving metrics, carry that authorization through the
checked plan without an extra publish confirmation. Respect explicit draft/review requests. For new metric generation, read
[the metrics module](references/metrics-engine.md); it preserves the plug architecture and
original authored gates and hands artifacts to the existing publisher. Dispatch before mode-specific project conventions or preflight. A repository setup is not a full-agent build.

This workflow also ships inside the public Atlan marketplace's `atlanai` plugin.
Use the namespace of the installed skill throughout this run: `atlanai` for that bundle,
or `atlanai-analytics` for the separate add-on. Replace the `atlanai-analytics:` prefix in
examples and specialist-agent names below with the installed namespace. Do not install the
separate add-on to use this bundled workflow. Cloning a source repository the user asks to
set up is a separate, supported task.

## 0. Dispatch the requested mode first

**Dispatch explicit modes first.** `dashboard`, `metrics`, `build`, `eval`, `improve`,
`ask`, `ontology`, `connect` and `propose` retain their documented routes. An explicit request
to register or deploy a separate agent uses the agent workflow.

Otherwise, `start`, `setup`, `query-history`, a bare invocation, or a request to make a
supplied link, local folder, HTML dashboard or BI export ready for questions follows
[shared setup](references/setup.md), then returns. "Build an analytics agent from this"
uses that route unless the person explicitly requests a separate deployed agent. A repository
is an ordinary source input; its name and layout never select a special workflow.

Survey before choosing inputs. Combine relevant declared models, dashboards, saved SQL and
query history; preview actual definitions before asking for a publication destination.
Finish with released context in Trident and supported questions in this conversation.
The current harness plus Trident MCP is the talk-to-data interface. No agent package,
registration, custom chat or eval is required. Reuse the selected run when resuming.

An explicit `dashboard` argument follows [dashboard to agent](references/dashboard.md).
It retains the existing capture, independent checks and agent route. Optional inline cards
are used only when their tools are already installed. Do not register another MCP server. Keep progress in native chat.

If the first argument is exactly `metrics`, enter metrics-only mode. Treat the remaining
arguments as scope. Read [the metrics workflow](references/metrics.md) and follow it from
section 0. It checks registry access, asks only for the missing table scope and sources, then
drafts metrics and saved queries from declared models, BI files or query history, whichever
are in reach. Everything is previewed before it is published. Use `.atlan-metrics/<slug>/`,
never `.atlan-analytics/` state.

**Return after that branch. Do not continue below.** It runs none of these:
- the full-agent preflight or the build/improve menu;
- `EVAL init`, an eval set or an eval run;
- agent registration, deployment, a smoke test or a baseline.

A declared model or a BI file needs no warehouse connection. The query-log filter and
`scripts/metrics_mine.py` are permitted. Starting an agent build later needs an explicit new
request.

If the first argument is exactly `propose`, read [the proposal workflow](references/propose.md),
preview a proposal for its owners, and return: no metric save, full-agent preflight or eval.

If the first argument is exactly `ask`, or there is no mode and the request is a data question
(a number, a trend, why something changed) rather than metrics or agent work, load and follow
this plugin's `atlan-analytics-ask` skill by name (in Claude Code, the Skill tool's
`atlanai-analytics:atlan-analytics-ask`). It answers in the
conversation from published definitions, then returns: no full-agent preflight, project, eval
or agent.

If the first argument is exactly `ontology`, or the request is about business objects, their
relationships, governed policies or modelling the business, load and follow this
plugin's `atlan-ontology` skill by name (in Claude Code, `atlanai-analytics:atlan-ontology`), then
return.

If it is `connect`, follow [warehouse discovery](references/connect.md), then return.
Use the selected project’s state when one exists; never resume another project silently.

The remaining sections apply only to full-agent modes. Read
[project conventions](references/project-conventions.md) when entering those modes.

## 1. Preflight and resume

Follow [full-agent preflight and saved project routing](references/full-agent.md).
Use `.atlan-analytics/<slug>/` and the saved phase; an explicit mode wins over it.

| Mode | Procedure |
| --- | --- |
| `build` | [Build](references/build.md), purpose through baseline |
| `eval` | [Evaluate](references/eval.md), isolated cases and publishing over MCP |
| `improve` | [Improve](references/improve.md), adopt, plan and train-only iteration |

Evaluation uses `scripts/analytics_eval.py`. It uses only the standard library and never
touches the network: every registry write, eval results included, goes through the Atlan MCP
server with the person's own sign-in.

## MCP resources

With an installed plugin, paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/`.
With MCP only, read this skill at `atlan://defaults/skills/atlan-analytics/SKILL.md`;
read a cited reference at the same base plus its relative path. Fetch supporting references
and scripts through MCP resources when needed, preserving their relative directory layout
locally before running a script. MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals. Gold SQL and results stay local and
out of agent context; the failure analyst sees train traces only. Keep the two specialist
agents, `atlan-analytics-grader` and `atlan-analytics-failure-analyst` (listed with the
`atlanai-analytics:` prefix in Claude Code), isolated as documented.
