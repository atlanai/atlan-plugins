---
name: atlan-analytics
description: Prepare links, folders and BI exports for talk-to-data using models and query history. Use when asked to set up analytics, build an analytics agent, resume setup or choose an analytics workspace. Publishes metrics and SQL, then suggests questions. Not for one-off data questions (atlan-analytics-ask) or business policies (atlan-ontology).
---

# Analytics context and talk-to-data

Keep updates and intent questions under 80 words, including context and options. Only a
completed setup handoff may use 160: counts, one metric's calculation/population/source/checks,
then supported questions. Details may be longer. Do not list other metrics or
repeat the pipeline.
Attribute saved checks to the earlier run. After publication, answers retrieve definitions before SQL runs.

On fresh setup, your next assistant message is the opening, before reading references,
surveying files or calling helpers. This includes survey-only requests. In 40 to 50 words,
name the outcome, next step and controls: narrow scope, inspect details, keep local or pause.
Honor existing choices. Survey-only requests stop after inspection. On resume, name the
saved result and next step. Detail-only requests get only the requested evidence.

Then read [shared conventions](references/conventions.md). They define audit sessions,
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

Survey models, dashboards, SQL and query history. Explain the sources, business meaning and
possible analyses inside the first question. Follow the [intent flow](references/setup/intent.md):
business focus first, purpose second, using native choices and waiting for each missing answer.
Even clear source scope needs this checkpoint. Then show one relevant metric with its meaning and
honest check status before a destination question or publication/reuse. Use the read-only
`project.py experience --format chat`, adding `--engine-dir <within-run-engine>` during
engine work. `--detail` exposes that sample's SQL, source and checks. Show five or all only
when requested. A helper result or file link is not a visible preview.
This visible checkpoint applies on resume and reuse. In the final answer, one actual sample
includes calculation, mandatory population/filter, source and check status, alongside factual
published/local counts, including an access blocker. A plan-only request needs no preview.
For team reuse, release context and suggest questions. For understanding alone, show the
requested evidence and stop, without another question or publication pitch.
Use this host and its Atlan MCP connection. Reuse the selected run; no new agent, custom chat
or eval is required.
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
