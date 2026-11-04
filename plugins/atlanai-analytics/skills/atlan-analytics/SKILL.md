---
name: atlan-analytics
description: Set up governed metrics and saved queries from a warehouse, build a talk-to-data agent on them, evaluate or improve it with isolated held-out evaluation, connect a warehouse, or propose a metric fix from a conversation. Use for analytics setup, turning a Power BI dashboard into a data agent, and agent work on Atlan. A one-off data question goes to atlan-analytics-ask, and business objects, joins and policies to atlan-ontology.
---

# Metrics and talk-to-data agents

Read [shared conventions](references/conventions.md) first. They define audit sessions,
publication approval, privacy and status reporting.

This workflow also ships inside the public Atlan marketplace's `atlanai` plugin.
Use the namespace of the installed skill throughout this run: `atlanai` for that bundle,
or `atlanai-analytics` for the separate add-on. Replace the `atlanai-analytics:` prefix in
examples and specialist-agent names below with the installed namespace. Do not install the
separate add-on or clone a repository to use this bundled workflow.

## 0. Dispatch the requested mode first

If the first argument is `dashboard`, or the person supplies a Power BI report link/file
and asks to turn it into an agent, follow [dashboard to agent](references/dashboard.md)
and return. This route runs in Claude Code and Codex with a shell, using the local helper
and the core Atlan connection. An explicit `metrics` request still follows metrics-only mode.
Keep all progress in native chat. Use existing inline dashboard cards only when their tools
are already installed; never register another MCP server as part of this workflow.

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

The remaining sections apply only to full-agent modes.

## 1. Preflight and resume

Follow [full-agent preflight and saved project routing](references/full-agent.md).
Use `.atlan-analytics/<slug>/` and the saved phase; an explicit mode wins over it.

| Mode | Procedure |
| --- | --- |
| `build` | [Build](references/build.md), purpose through baseline |
| `eval` | [Evaluate](references/eval.md), isolated cases and publishing over MCP |
| `improve` | [Improve](references/improve.md), adopt, plan and train-only iteration |

The single helper is `scripts/analytics_eval.py`. It uses only the standard library and never
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
