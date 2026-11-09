---
name: atlan-analytics
description: Set up analytics and answer workspace questions. Use for Atlan workspace access/creation, including explanation-only requests; BI sources, metrics, setup, resume, handoff or analytics agents. Not for data questions (atlan-analytics-ask).
---

# Analytics context and talk-to-data

For workspace access or creation questions, follow [workspace help](references/setup/workspace-access.md)
and return. This includes explanation-only requests; do not start source inspection or publication.

Keep updates and intent questions under 80 words total. Only a
completed setup handoff may use 160: counts, one metric's calculation/population/source/checks,
then supported questions. Details may be longer. Do not list other metrics or repeat the pipeline.
Attribute saved checks to the earlier run. After publication, answers retrieve definitions before SQL runs.
For final handoffs, read [handoff rules](references/setup/preview-publish.md) and return.
Keep execution, source review, holds and releases separate. Total definitions = metrics + queries.
Use plain text for check labels; no emoji checklist or repeated status headings.

On fresh setup, your next assistant message is the opening, before reading references,
surveying files or calling helpers. In 40 to 50 words,
name the outcome, next step and controls: narrow scope, inspect details, keep local or pause.
Honor existing choices. Survey-only requests stop after inspection. On resume, name the
saved result and next step. Detail-only requests get only the requested evidence.
Fresh-folder opening:
"I'll inspect the sources, explain what they cover, and ask what matters to you before
preparing metrics for questions here. You'll see an example and its check status first.
You can narrow the scope, ask for details, keep the work local, or pause at any point."
Retain controls without requesting approval for already-authorized work.

Then read [shared conventions](references/conventions.md). They define audit sessions,
task authorization, privacy and status reporting. Treat reads of the selected source,
packaged-runtime initialization and generated-file reads as ordinary task steps. Once a
publication destination is chosen for saving metrics, carry that authorization through the
checked plan without an extra publish confirmation. Respect explicit draft/review requests.
For new metric generation (metrics mode, or a setup run's Build stage), read
[the metrics module](references/metrics-engine.md), also to explain it. A setup with only local
files and no warehouse uses SQLite and installs nothing ([build and check](references/setup/build-check.md)). Dispatch before mode-specific project conventions or preflight. A
repository setup is not a full-agent build. Cloning a source repository the user asks to set
up is a separate, supported task.

The Registry marketplace bundles this workflow in `atlanai`. Use its installed namespace
instead of `atlanai-analytics:` below; do not install this add-on beside that bundle.

## 0. Dispatch the requested mode first

**Dispatch explicit modes first.** `dashboard`, `metrics`, `build`, `eval`, `improve`,
`ask`, `investigate`, `ontology`, `connect` and `propose` retain their documented routes. Only the `build`
mode or an explicit request to register, deploy or evaluate a separate agent uses the agent
workflow.

Otherwise, `start`, `setup`, `query-history`, a bare invocation, or a request to make a
supplied link, local folder, HTML dashboard or BI export ready for questions follows
[shared setup](references/setup.md), then returns. A request to build an analytics or
talk-to-data agent takes that route too: this chat with Atlan becomes the agent. Its first
reply adds one line offering a separately deployed agent with an eval set (`build`) instead,
and switches only when the person asks for it. Repository names and layouts never select a special workflow.

Open [the host view](references/setup/render.md) after inspection and update it through setup.
Explain the sources, business meaning and
possible analyses inside the first question. Before the choices, explicitly say what the
sources could help the person analyse. An inventory followed by option labels alone does
not explain their value. Follow the [intent flow](references/setup/intent.md): business focus
first, purpose second, using native choices and waiting for each missing answer.
Even clear scope needs this checkpoint. Then show one metric with its meaning and
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

For `dashboard`, use [dashboard through the metrics module](references/dashboard-agent.md).
Resume saved dashboard-helper jobs with [their original procedure](references/dashboard.md),
preserving capture, checks and agent routing. Use inline cards only with installed tools;
never register another MCP server. Keep progress in native chat.

If the first argument is exactly `metrics`, follow [the metrics workflow](references/metrics.md),
with remaining arguments as scope, then returns. Use `.atlan-metrics/<slug>/`.
Do not run full-agent preflight, eval, registration or deployment. Models and BI files
need no warehouse; query-log filtering and `scripts/metrics_mine.py` are permitted.
Starting an agent build later needs an explicit new request.

If the first argument is exactly `propose`, read [the proposal workflow](references/propose.md),
preview a proposal for its owners, and return: no metric save, full-agent preflight or eval.

If the first argument is exactly `ask`, or there is no mode and the request is a data question
(a number, a trend, why something changed) rather than metrics or agent work, load and follow
this plugin's `atlan-analytics-ask` skill by name (in Claude Code, the Skill tool's
`atlanai-analytics:atlan-analytics-ask`). It answers in the
conversation from published definitions, then returns: no full-agent preflight, project, eval
or agent. `investigate`, a root cause or a deep dive loads `atlan-analytics-investigate` the
same way.

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

Evaluation uses `scripts/analytics_eval.py`, standard library only, with no network access.
Registry writes, including eval results, use Atlan MCP with the person's sign-in.

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/`. With MCP only,
read `atlan://defaults/skills/atlan-analytics/SKILL.md` and cited relative references at that
base. Fetch needed scripts/resources locally with their relative layout before execution;
MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals. Keep gold SQL/results local; failure
analysis sees train traces only. Keep the `atlan-analytics-grader` and
`atlan-analytics-failure-analyst` agents isolated as documented.
