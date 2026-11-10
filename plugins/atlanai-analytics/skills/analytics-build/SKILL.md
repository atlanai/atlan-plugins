---
name: analytics-build
description: Atlan Analytics setup on your own data: survey, use case, scope, metrics, save, questions, dashboard. Use when setting up, resuming or handing off a run, showing its details, or for workspace access. Not for a demo (analytics-demo).
---

# Atlan Analytics on your own data: seven guided steps

This skill takes a person's own dashboards, folders, BI exports and query history to checked
metrics they can question here. It strings together the capability skills in one flow:
`analytics-survey`, `analytics-metrics` and `analytics-ask`. Before the first helper call,
and on every resume, read [shared setup](references/setup.md): it defines `PROJECT`, the
stage files and the preview rule. This file sets their order and what the person sees.

For workspace access or creation questions, follow [workspace help](references/setup/workspace-access.md)
and return. This includes explanation-only requests; do not start source inspection or publication.

Then read [shared conventions](references/conventions.md): audit sessions, task
authorization, privacy and status reporting. Reads of the selected source, packaged-runtime
initialization and generated-file reads are ordinary task steps. Once a publication
destination is chosen, show the checked plan and publish only after the person approves that
concrete plan. Respect explicit draft or review requests.

## Opening

With no source and no saved run, ask once for a dashboard link, local folder or BI export, and
mention that the `analytics-demo` skill shows the same steps on made-up data. Otherwise your
next message is the opening, before surveying files or calling helpers:

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

1. **Survey your sources.** Follow the `analytics-survey` skill's procedure,
   [inspect and scope](../analytics-survey/references/inspect-scope.md) section 1. Explain
   what was found, its business meaning and what it could help analyse, inside the first
   question's visible prompt; an inventory followed by option labels alone is not enough.
2. **Clarify your use case.** Follow [the intent flow](references/setup/intent.md): business
   focus first, purpose second, as native questions, waiting for each answer.
3. **Select tables and dashboards.** Follow
   [inspect and scope](../analytics-survey/references/inspect-scope.md) section 2: pages,
   measures and the query-history tables the use case needs.
4. **Generate and check metrics.** Follow [build and check](references/setup/build-check.md)
   and [the metrics module](../analytics-metrics/references/metrics-engine.md). Show
   [the first metric](../analytics-metrics/references/first-metric.md) at its checkpoint,
   then build and check the rest on the bound data.
5. **Keep local or save to Atlan Trident.** Read
   [context built and where to keep it](references/setup/save.md) and
   [preview and publish](references/setup/preview-publish.md).
6. **Ask a question.** Read [ready for questions](references/setup/ready.md). Answer only the
   question the person picks, through the `analytics-ask` skill, then `PROJECT journey asked`.
7. **Build a dashboard.** Read [the dashboard](references/setup/dashboard.md).

Keep execution, source review, holds and releases separate, with factual check labels and
the tracker's step headings. Total definitions = metrics + queries. Attribute saved checks
to the earlier run. After publication, answers retrieve definitions before SQL runs. A
repository setup is not a full-agent build, and repository names or layouts never select a
special workflow; cloning a repository the person asks to set up is an ordinary step.

Use this host and its Atlan MCP connection. This chat with Atlan is the talk-to-data agent;
no new agent, custom chat or eval is required. If Registry tools are missing or sign-in is
required, follow [connection recovery](references/auth-recovery.md) inside this task and
resume the saved stage. Never infer a missing coding-host plugin from a generic
app-directory search.

## Explicit arguments

These routes are reached only by name; the person's guided flow never offers them.

- `metrics`: load the `analytics-metrics` skill and follow it with the remaining arguments
  as scope. Metrics Studio sends `/atlanai-analytics:analytics-build metrics`, so this
  hand-off stays. If the first argument is exactly `metrics`, no full-agent preflight, eval,
  registration or deployment runs.
- `dashboard` or `propose`: load the `analytics-metrics` skill with that argument.
- `connect`: load the `analytics-survey` skill with that argument.
- `ask`, or a data question with no setup work: load the `analytics-ask` skill.
- `build`, `eval` and `improve`, or an explicit request to register, deploy or evaluate a
  separate agent: the full-agent workflow below. A request to build a talk-to-data agent
  otherwise takes the guided flow, and its first reply offers a separately deployed agent
  with an eval set (`build`) in one line.

In Claude Code, load a sibling skill with the Skill tool as `atlanai-analytics:<name>`. The
Registry marketplace bundles these skills in `atlanai`; use its installed namespace instead,
and do not install this add-on beside that bundle.

## Full-agent modes

Read [project conventions](references/project-conventions.md), then follow
[full-agent preflight and saved project routing](references/full-agent.md). Use
`.atlan-analytics/<slug>/` and the saved phase; an explicit mode wins over it.

| Mode | Procedure |
| --- | --- |
| `build` | [Build](references/build.md), purpose through baseline |
| `eval` | [Evaluate](references/eval.md), isolated cases and publishing over MCP |
| `improve` | [Improve](references/improve.md), adopt, plan and train-only iteration |

Evaluation uses `scripts/analytics_eval.py`, standard library only, with no network access.
Registry writes, including eval results, use Atlan MCP with the person's sign-in.

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/analytics-build/`; the sibling
skills sit beside it. With MCP only, read `atlan://defaults/skills/analytics-build/SKILL.md`
and cited relative references at that base. Fetch needed scripts locally with their relative
layout before execution; MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals. Keep gold SQL and results local;
failure analysis sees train traces only. Keep the `atlan-analytics-grader` and
`atlan-analytics-failure-analyst` agents isolated as documented.
