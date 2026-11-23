---
name: analytics-build
description: "Atlan Analytics: set up, resume or hand off metrics on your data. Use for metrics in .atlan-metrics/: a metric's details, SQL or checks, what's ready, saving, a new workspace, or questions on a folder's dashboards. Not for a demo (analytics-demo)."
---

# Atlan Analytics on your own data: a guided build

An orchestrator: it owns the conversation, intent, the tracker and approvals, and turns a
person's dashboards, folders, BI exports and past queries into checked metrics to question. It calls the capability skills without letting them ask the person anything:
`source-survey`, `metrics-generation` and `analytics-ask`. Before the first helper call, and
on every resume, read [shared setup](references/setup.md): it defines `PROJECT`, the stage
files and the review rule. This file sets their order and what the person sees.
Read plugin files one per call. Shell commands print no separators or decoration.

For workspace access or creation questions, follow [workspace help](references/setup/workspace-access.md)
and return. Even for an explanation, start no source inspection or publication.

Then read [shared conventions](references/conventions.md): audit sessions, task
authorization, privacy and status reporting. Reads of the selected source, runtime
setup and generated files are ordinary task steps. Once a publication
destination is chosen, show the checked plan and publish only after the person approves that
concrete plan. Respect explicit draft or review requests.

## Opening

[The interview](references/setup/intent.md) has every question and its options. Its shape:

1. **Card 1**, two questions in one card where the host allows it: which warehouse (those
   already connected are labelled so), and what the person wants to do.
2. **One of three branches**, by that answer:

   | They chose | Then |
   | --- | --- |
   | Suggest from your warehouse | `source-survey` `discover`, then "What should your agent answer?" over up to three use cases with their tables plus "Add a dashboard". After the build, one offer to add a dashboard |
   | Start from a dashboard | One card for the dashboard: files in the folder as options, a link or path as free text. Then [the dashboard branch](references/setup/dashboard-flow.md), which replaces the steps below |
   | Start from tables | One card asking for the use case and tables as free text, with a skip to discovery, then `source-survey` `access` on them |

3. **The go-ahead.** Picking a use case, or naming tables that all read, starts the build, with
   one line of what it builds and any choices made. A table that can't be read is asked about first.

A question the request already answers is not asked. Before card 1, look silently at what is
already connected (`source-survey` `connections`) and at BI file names in the working folder
(`PROJECT discover`). Say nothing about that look or where the options came from (at most
"Getting set up."), and read no data before the answers. The person's first sight is the card, whose own text is the
opening:

"Let's build an agent that answers your data questions in plain English and shows what each
answer is based on.

I'll extract all the context I can from your dashboards and tables, add detailed information
to it and validate it on your data.

First, where does your data live, and what do you want your agent to do?"

The interview is this skill's own: `source-survey` never asks the person anything. Honor
existing choices. Survey-only requests stop after the survey; detail-only ones get only that
evidence.

From the demo's "Build one on your own data", ask
[its one question](references/setup/intent.md#arriving-from-the-demo), not card 1.
**On resume**, rebuild the task list first ([the checklist](references/setup/checklist.md#on-resume)),
then name the saved result and the next step. What's ready, or what to ask, is
[the ready block](references/setup/ready.md#on-resume).

## The steps

Take the task list as the checklist gives it; never say how many steps there are.
Show [the checklist](references/setup/checklist.md) in the host's task list from the first
action, and in chat with each step's result line, never inside a question's text. Follow
[one presentation flow](references/setup/presentation.md): read each moment's file when
reached and never replace a moment with a status line. Show each moment's chip or card, from
the first survey on, as [visuals](references/setup/visuals.md) says.

1. **Choose what your agent answers.** [The interview](references/setup/intent.md)'s card 1, and the
   branch's own ask. Skip what the request already said.
2. **Find your sources.** By branch: `source-survey` `discover` through
   [discovery](../source-survey/references/discovery.md), or `access` on the dashboard and
   tables the person named. The side page opens once the queries are in plain words,
   when `PROJECT visual next` names `connect`.
3. **Pick tables and dashboards.** In the discovery branch,
   [the use cases](references/setup/names.md), whose pick starts the build. From tables, a clean
   access check starts it. A report is built whole.
4. **Build your context: metrics and saved queries.** `metrics-generation` `generate` and `check` with the selection
   and the whole survey, through [build and check](references/setup/build-check.md). A fresh
   subagent writes each metric gate; chips show as [drafts as they land](references/setup/progress.md#drafts-as-they-land)
   says. The
   review of the completed collection is the save question's own text (step 5).
5. **Choose where to store your context.** Read
   [context built and where to keep it](references/setup/save.md) and
   [preview and publish](references/setup/preview-publish.md); `metrics-generation` `save`.
   After a save to Atlan with `show_widget`, draw the `saved` visual; in a terminal the
   ready question holds it.
6. **Ask your agent a question.** Read [check accuracy](references/setup/accuracy.md), then
   [ready for questions](references/setup/ready.md): the ready question offers the check when it
   is available (`eval-generation` and `accuracy-check`, run only when picked). Answer only the
   question the person picks or types, through `analytics-ask`, then `PROJECT journey asked`.
7. **Build a dashboard.** Read [the dashboard](../analytics-ask/references/dashboard.md).

Keep execution, source review, holds and releases separate, with factual check labels and
the tracker's step headings. Total = metrics + saved queries. Attribute saved checks
to the earlier run. After publication, answers retrieve definitions before SQL runs. Cloning
a repository the person names is an ordinary step; its name or layout never selects a special workflow.

Use this host and its Atlan MCP connection. This chat is the talk-to-data agent; this add-on
deploys or evaluates no separate agent, and says so in one line when asked.
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
- `ask`, or a data question with no setup work: load the `analytics-ask` skill. When this
  folder holds a run, pass its directory, so the answer uses the run's own rules.

In Claude Code, load a sibling skill with the Skill tool as `atlanai-analytics:<name>`. The
Registry marketplace bundles these skills in `atlanai`; use its installed namespace instead,
and do not install this add-on beside that bundle.

## Hand-offs

| From | To | Carries |
| --- | --- | --- |
| `analytics-demo` ("Build one on your own data") | here | Nothing but the connection. A demo run never becomes a build |
| here | `analytics-ask` | The run, so answers read its context |
| `analytics-ask` (no metric covers a question) | here | The question, as the subject for the discovery branch |
| here, when the survey finds no readable source | `analytics-demo` | Nothing |

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/analytics-build/`; the sibling
skills sit beside it. With MCP only, read `atlan://defaults/skills/analytics-build/SKILL.md`
and its references there, and fetch scripts locally in their layout before running them.

Never expose credentials or identifying query literals.
