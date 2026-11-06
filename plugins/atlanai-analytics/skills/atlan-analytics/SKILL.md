---
name: atlan-analytics
description: Set up a GitHub repo or local project for talk-to-data: survey dashboards and query history, publish metrics and SQL to Trident, then suggest questions. Use when asked to set up analytics or build an analytics agent from a repo. Also supports explicit agent builds. Not for one-off data questions (atlan-analytics-ask) or business policies (atlan-ontology).
---

# Analytics context and talk-to-data

Read [shared conventions](references/conventions.md) first. They define audit sessions,
publication approval, privacy and status reporting. Dispatch before loading mode-specific
project conventions or preflight. A repository setup is not a full-agent build.

This workflow also ships inside the public Atlan marketplace's `atlanai` plugin.
Use the namespace of the installed skill throughout this run: `atlanai` for that bundle,
or `atlanai-analytics` for the separate add-on. Replace the `atlanai-analytics:` prefix in
examples and specialist-agent names below with the installed namespace. Do not install the
separate add-on to use this bundled workflow. Cloning a source repository the user asks to
set up is a separate, supported task.

## 0. Dispatch the requested mode first

**Repository intent comes first.** A supplied GitHub/repository URL, local folder, or current
project with a request to set up analytics, make it agentic ready, ask its data, or "build an
analytics agent from it" follows [project setup](references/project.md), then returns.
This includes "Help me set up this repo: <GitHub URL>. Once ready, use Atlan Analytics to
build an analytics agent from its dashboards, query history and data."
Clone or reuse the source project, then survey it before choosing inputs. Discovering a PBIT
inside that repo does not switch the workflow to dashboard mode. A GitHub page is not a
hosted Power BI report URL.

Survey every available source, combine model and history evidence, publish metrics and saved
SQL, then suggest supported questions. When the user asks, retrieve the released context
from Trident and execute against the selected data source. The current harness plus Trident
MCP is the talk-to-data interface. Do not generate an agent package, custom chat, registration
or evals for this route. Explicit mode arguments (`metrics`, `dashboard`, `build`, `eval`,
`improve`) and a clear request to register or deploy a separate agent keep their routes;
the phrase "analytics agent" alone in a repo setup request is not that request.

`start` and `setup`, or invoking Analytics without a task, follow
[getting started](references/start.md). `query-history <file, folder or scope>` follows the
same route with query history already selected. These routes finish with a business answer
in this host, not mandatory agent registration. Reuse an explicitly selected run when resuming.
Do not run full-agent preflight or present build/eval/improve as the initial menu.
Natural requests to turn supplied queries or a repository of models into context for questions
also use this route. An explicit metrics-only or full-agent mode still takes precedence.

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
