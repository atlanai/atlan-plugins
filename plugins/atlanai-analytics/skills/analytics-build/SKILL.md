---
name: analytics-build
description: "Atlan Analytics Build: build metrics and saved SQL from your own dashboards, BI exports or query history. Use for setup on a source or business problem, resume, handoff, details or workspace access. Not for demos (analytics-demo)."
---

# Analytics context and talk-to-data

For workspace access or creation questions, follow [workspace help](references/setup/workspace-access.md)
and return. This includes explanation-only requests; do not start source inspection or publication.

For setup, follow [one presentation flow](references/setup/presentation.md): the full
welcome, visible step tracker, completed-collection review, context-built
celebration and ready block. Read each moment's file when reached; do not replace it
with a compact status update.
Attribute saved checks to the earlier run. After publication, answers retrieve definitions before SQL runs.
For final handoffs, read [handoff rules](references/setup/preview-publish.md) and return.
Keep execution, source review, holds and releases separate. Total definitions = metrics + queries.
Use factual check labels and the presentation flow's step headings.

With no source, problem or saved run, load `analytics-demo`, which offers a demo or own
data. This skill owns intent, the tracker and approvals; surveying is `analytics-survey`'s
job and generation `analytics-metrics`'s. Otherwise, on fresh setup, your next message is
the opening, before surveying files or calling helpers:
name the outcome, next step and controls: narrow scope, inspect details, keep local or pause.
Honor existing choices. Survey-only requests stop after inspection. On resume, name the
saved result and next step. Detail-only requests get only the requested evidence.
Fresh-folder opening:
"I'll inspect the sources, explain what they cover, and ask what matters to you before
preparing metrics for questions here. I'll show the six steps and where your choices matter.
You can narrow the scope, ask for details, keep the work local, or pause at any point."
Retain controls without requesting approval for already-authorized work.

Then read [shared conventions](references/conventions.md). They define audit sessions,
task authorization, privacy and status reporting. Treat reads of the selected source,
packaged-runtime initialization and generated-file reads as ordinary task steps. Once a
publication destination is chosen for saving metrics, show the checked plan and publish only
after the user approves that concrete plan. Respect explicit draft/review requests.
For new metric generation (metrics mode, or a setup run's Build stage), read
[the metrics module](references/metrics-engine.md), also to explain it. Demos and local-only
setup all use this engine for the complete selected collection. Dispatch
before mode-specific project conventions or preflight. Cloning a source repository the user asks to set
up is a separate, supported task.

The Registry marketplace bundles this workflow in `atlanai`. Use its installed namespace
instead of `atlanai-analytics:` below; do not install this add-on beside that bundle.

## 0. Dispatch the requested mode first

**Dispatch explicit modes first.** `dashboard`, `metrics`, `ask`, `connect` and `propose` retain their
documented routes. This add-on does not deploy or evaluate a separate agent: when asked for
one, say so in one line and offer setup here.

Otherwise, `start`, `setup`, `query-history`, a bare invocation, or a request to make a
supplied link, local folder, HTML dashboard or BI export ready for questions follows
[shared setup](references/setup.md), then returns. A request to build an analytics or
talk-to-data agent takes that route too: this chat with Atlan becomes the agent. Repository names and layouts never select a special workflow.

Show [the setup checklist](references/setup/checklist.md) in chat and in the host's task
list when available, from the first action. Open no panel or view unless requested.
Explain the sources, business meaning and
possible analyses inside the first question. Before the choices, explicitly say what the
sources could help the person analyse. An inventory followed by option labels alone does
not explain their value. Follow the [intent flow](references/setup/intent.md): business focus
first, purpose second, using native choices and waiting for each missing answer.
A stated problem answers the focus question. After intent,
[commit on names](references/setup/names.md) before generation.
Then follow the presentation flow's completed-collection review before a destination question
or publication/reuse. Read evidence with `project.py experience --format json`, adding
`--engine-dir <within-run-engine>` during engine work; `--detail` exposes SQL and checks.
The block applies on resume and reuse as well. A plan-only request needs no preview.
For team reuse, release context and suggest questions. For understanding alone, show the
requested evidence and stop, without another question or publication pitch.
Use this host and its Atlan MCP connection. Reuse the selected run; no new agent, custom chat
or eval is required.
If Registry tools are missing or sign-in is required, follow [connection recovery](references/auth-recovery.md)
inside this task: diagnose the selected host connection, start the host's own sign-in, watch
the real login task, verify a current-host Registry read and resume the saved stage.
Give the core Registry plugin's installation commands if recovery cannot complete. Never
infer a missing coding-host plugin from a generic app-directory search.

For `dashboard`, use [dashboard through the metrics module](references/dashboard-agent.md).
Read saved dashboard-helper jobs through [legacy readback](references/dashboard.md),
preserving capture and checks. Generating or revising their metrics uses a new main-engine run. Use inline cards only with installed tools;
never register another MCP server. Keep progress in native chat.

If the first argument is exactly `metrics`, follow [the metrics workflow](references/metrics.md),
with remaining arguments as scope, then returns. Use `.atlan-metrics/<slug>/`.
Models and BI files
need no warehouse; query-log filtering and `scripts/context_contracts.py` are permitted.

If the first argument is exactly `propose`, read [the proposal workflow](references/propose.md),
preview a proposal for its owners, and return: no metric save.

If the first argument is exactly `ask`, or there is no mode and the request is a data question
(a number, a trend, why something changed) rather than metrics or agent work, load and follow
this plugin's `analytics-ask` skill by name (in Claude Code, the Skill tool's
`atlanai-analytics:analytics-ask`). It answers in the
conversation from published definitions, then returns: no project or agent.

If it is `connect`, follow [warehouse discovery](references/connect.md), then return.
Use the selected project’s state when one exists; never resume another project silently.

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/analytics-build/`. With MCP only,
read `atlan://defaults/skills/analytics-build/SKILL.md` and cited relative references at that
base. Fetch needed scripts/resources locally with their relative layout before execution;
MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals.
