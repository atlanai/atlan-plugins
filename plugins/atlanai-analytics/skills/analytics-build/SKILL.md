---
name: analytics-build
description: Atlan Analytics setup on your own data: survey, use case, scope, metrics, save, questions, dashboard. Use when setting up, resuming or handing off a run, showing its details, or for workspace access. Not for a demo (analytics-demo).
---

# Atlan Analytics on your own data: seven guided steps

An orchestrator. It owns the conversation, intent, the tracker and approvals, and takes a
person's own dashboards, folders, BI exports and query history to checked metrics they can
question here. It calls the capability skills without letting them ask the person anything:
`source-survey`, `metrics-generation` and `analytics-ask`. Before the first helper call, and
on every resume, read [shared setup](references/setup.md): it defines `PROJECT`, the stage
files and the review rule. This file sets their order and what the person sees.

For workspace access or creation questions, follow [workspace help](references/setup/workspace-access.md)
and return. This includes explanation-only requests; do not start source inspection or publication.

Then read [shared conventions](references/conventions.md): audit sessions, task
authorization, privacy and status reporting. Reads of the selected source, packaged-runtime
initialization and generated-file reads are ordinary task steps. Once a publication
destination is chosen, show the checked plan and publish only after the person approves that
concrete plan. Respect explicit draft or review requests.

## Opening

Two ways in:

| The person gave | Do |
| --- | --- |
| A source (link, folder, BI export), with or without a problem | Open as below, then survey it |
| A problem only, such as "churn by plan" | The problem is the focus: ask once for a source, then open |
| Nothing, and no saved run | Ask once for a dashboard link, local folder or BI export, and mention that `analytics-demo` shows the same steps on made-up data |

Your next message is the opening, before surveying files or calling helpers:

"I'll survey your sources, explain what they cover, and ask what you want to use them for
before generating metrics you can ask questions of here. I'll show the seven steps and where
your choices matter. You can narrow the scope, ask for details, keep the work local, or pause
at any point."

Honor existing choices. Survey-only requests stop after step 1. On resume, read
`PROJECT journey status`, name the saved result and the next step, and rebuild the tracker.
Detail-only requests get only the requested evidence.

## The seven steps

Show [the checklist](references/setup/checklist.md) in chat and in the host's task list from
the first action, with the full tracker at every step change. Follow
[one presentation flow](references/setup/presentation.md): read each moment's file when
reached and never replace a moment with a status line.

1. **Survey your sources.** `source-survey` `inventory`, through
   [inspect and scope](../source-survey/references/inspect-scope.md) section 1. Explain
   what was found, its business meaning and what it could help analyse, inside the first
   question's visible prompt; an inventory followed by option labels alone is not enough.
2. **Clarify your use case.** Follow [the intent flow](references/setup/intent.md): business
   focus first, purpose second, as native questions, waiting for each answer. A stated problem
   answers the focus question. Then [commit on names](references/setup/names.md): the person
   picks the metric names to build from `source-survey` `candidates`.
3. **Select tables and dashboards.** Follow
   [inspect and scope](../source-survey/references/inspect-scope.md) section 2: pages,
   measures and the query-history tables the chosen names need.
4. **Generate and check metrics.** `metrics-generation` `generate` and `check` with the selection
   and the whole survey, through [build and check](references/setup/build-check.md). Then
   review the completed collection with actual counts and one representative metric.
5. **Keep local or save to Atlan Trident.** Read
   [context built and where to keep it](references/setup/save.md) and
   [preview and publish](references/setup/preview-publish.md); `metrics-generation` `save`.
6. **Ask a question.** Read [ready for questions](references/setup/ready.md). Answer only the
   question the person picks, through `analytics-ask`, then `PROJECT journey asked`.
7. **Build a dashboard.** Read [the dashboard](../analytics-ask/references/dashboard.md).

Keep execution, source review, holds and releases separate, with factual check labels and
the tracker's step headings. Total definitions = metrics + queries. Attribute saved checks
to the earlier run. After publication, answers retrieve definitions before SQL runs. Cloning
a repository the person asks to set up is an ordinary step; repository names or layouts never
select a special workflow.

Use this host and its Atlan MCP connection. This chat with Atlan is the talk-to-data agent;
this add-on does not deploy or evaluate a separate agent, and says so in one line when asked.
If Registry tools are missing or sign-in is required, follow
[connection recovery](references/auth-recovery.md) inside this task and resume the saved
stage. Never infer a missing coding-host plugin from a generic app-directory search.

## Explicit arguments

These routes are reached only by name; the guided flow never offers them.

- `metrics`: load the `metrics-generation` skill and follow it with the remaining arguments
  as scope. Metrics Studio sends `/atlanai-analytics:analytics-build metrics`, so this
  hand-off stays. If the first argument is exactly `metrics`, no guided step runs.
- `dashboard` or `propose`: load the `metrics-generation` skill with that argument.
- `connect`: load the `source-survey` skill with that argument.
- `ask`, or a data question with no setup work: load the `analytics-ask` skill.

In Claude Code, load a sibling skill with the Skill tool as `atlanai-analytics:<name>`. The
Registry marketplace bundles these skills in `atlanai`; use its installed namespace instead,
and do not install this add-on beside that bundle.

## Hand-offs

| From | To | Carries |
| --- | --- | --- |
| `analytics-demo` ("Continue building on real data") | here | Nothing but the connection. A demo run never becomes a build |
| here | `analytics-ask` | The run, so answers read its context |
| `analytics-ask` (no metric covers a question) | here | The question, as the problem |
| here, when the survey finds no readable source | `analytics-demo` | Nothing |

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/analytics-build/`; the sibling
skills sit beside it. With MCP only, read `atlan://defaults/skills/analytics-build/SKILL.md`
and cited relative references at that base. Fetch needed scripts locally with their relative
layout before execution; MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals.
