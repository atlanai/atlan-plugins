---
name: atlan-analytics
description: Set up talk-to-data from links, folders, BI exports and query history, publishing metrics and SQL to Atlan. Use when asked to set up or resume analytics, define a metric, or build an analytics agent. Not for data questions (atlan-analytics-ask).
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
checked plan without an extra publish confirmation. Respect explicit draft/review requests.
For new metric generation (metrics mode, or a setup run's Build stage), read
[the metrics module](references/metrics-engine.md), also to explain it; it preserves the plug architecture and authored gates and hands artifacts to the
existing publisher. Dispatch before mode-specific project conventions or preflight. A
repository setup is not a full-agent build. Cloning a source repository the user asks to set
up is a separate, supported task.

The registry's own marketplace (`api.atlan.com/public/marketplace.git`) bundles this workflow
in its `atlanai` plugin. Use the installed skill's namespace for the `atlanai-analytics:`
prefixes below (`atlanai` in that bundle), and do not install this add-on beside it.

## 0. Dispatch the requested mode first

**Dispatch explicit modes first.** `dashboard`, `metrics`, `build`, `eval`, `improve`,
`ask`, `ontology`, `connect` and `propose` retain their documented routes. Only the `build`
mode or an explicit request to register, deploy or evaluate a separate agent uses the agent
workflow.

Otherwise, `start`, `setup`, `query-history`, a bare invocation, or a request to make a
supplied link, local folder, HTML dashboard or BI export ready for questions follows
[shared setup](references/setup.md), then returns. A request to build an analytics or
talk-to-data agent takes that route too: this chat with Atlan becomes the agent. Its first
reply adds one line offering a separately deployed agent with an eval set (`build`) instead,
and switches only when the person asks for it. A repository
is an ordinary source input; its name and layout never select a special workflow.

Survey before choosing inputs. Combine relevant declared models, dashboards, saved SQL and
query history. Render actual metric names, meanings, calculations, sources and check status
in chat before asking for a publication destination or proceeding to publish/reuse. Run
`project.py preview --format chat` for the first five metrics and a saved SQL example.
A helper result, counts, a file link or a generated HTML page alone does not show a preview.
This visible checkpoint also applies on resume and when all definitions can be reused.
Keep the metric table and saved SQL in the final answer too, including an authentication
blocker. Commentary and linked files alone are not the final deliverable.
Finish with released context in Atlan and supported questions in this conversation.
The current harness plus the Atlan MCP connection is the talk-to-data interface. No agent package,
registration, custom chat or eval is required. Reuse the selected run when resuming.
If Registry tools are missing or sign-in is required, follow [connection recovery](references/auth-recovery.md)
inside this task: diagnose the selected host connection, start the host's own sign-in, watch
the real login task, verify a current-host Registry read and resume the saved stage.
Give the core Registry plugin's installation commands if recovery cannot complete. Never
infer a missing coding-host plugin from a generic app-directory search.

An explicit `dashboard` argument starts a new dashboard project with
[dashboard through the metrics module](references/dashboard-agent.md). A saved dashboard-helper
job resumes with [its original procedure](references/dashboard.md), which keeps the existing
capture, independent checks and agent route. Optional inline cards
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
