---
name: analytics-build
description: Atlan Analytics setup on your own data: pick a warehouse, then discover use cases, share a dashboard or name tables, and build. Use when setting up, resuming or handing off a run, its details, or workspace access. Not for a demo (analytics-demo).
---

# Atlan Analytics on your own data: seven guided steps

An orchestrator. It owns the conversation, intent, the tracker and approvals, and takes a
person's own dashboards, folders, BI exports and query history to checked metrics they can
question here. It calls the capability skills without letting them ask the person anything:
`source-survey`, `metrics-generation` and `analytics-ask`. Before the first helper call, and
on every resume, read [shared setup](references/setup.md): it defines `PROJECT`, the stage
files and the review rule. This file sets their order and what the person sees.
Read plugin files with the Read tool, one file per call. In a shell, never print a separator such as
`echo =====`: zsh reads a word that starts with `=` as a command, fails and skips the rest of the line.

For workspace access or creation questions, follow [workspace help](references/setup/workspace-access.md)
and return. This includes explanation-only requests; do not start source inspection or publication.

Then read [shared conventions](references/conventions.md): audit sessions, task
authorization, privacy and status reporting. Reads of the selected source, packaged-runtime
initialization and generated-file reads are ordinary task steps. Once a publication
destination is chosen, show the checked plan and publish only after the person approves that
concrete plan. Respect explicit draft or review requests.

## Opening

[The interview](references/setup/intent.md) has every question and its options. Its shape:

1. **Card 1**, two questions in one card where the host allows it: which warehouse (those
   already connected are labelled so), and what the person wants to do.
2. **One of three branches**, by that answer:

   | They chose | Then |
   | --- | --- |
   | Let discovery decide | `source-survey` `discover`, then "Which should I build?" over up to three use cases with their tables plus "I have a dashboard", then at most two clarifying questions if something is unsettled. After the build, one offer to add a dashboard |
   | Make my dashboard agentic | One card for the dashboard: files in the folder as options, a link or path as free text. A link is opened with that tool's sign-in started when needed, then `source-survey` `access` on it and its tables |
   | I have a use case and know my tables | One card asking for the use case and tables as free text, with a skip to discovery, then `source-survey` `access` on them |

3. **Confirm and trigger**, the same in every branch: one line of what will be built and
   from what, then "Build it?". Nothing is built before the yes.

A question the request already answers is not asked. Before card 1, look silently at what is
already connected (`source-survey` `connections`) and at BI file names in the working folder
(`PROJECT discover`). Say nothing about that look or where the options came from (at most
"Getting set up."), and read no data before the answers. The person's first sight is the card, whose own text is the
opening:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on.

First, let me understand your tools and your domain."

The interview is this skill's own: `source-survey` never asks the person anything. Honor
existing choices. Survey-only requests stop after the survey. On resume, read
`PROJECT journey status`, name the saved result and the next step, and rebuild the tracker.
Detail-only requests get only the requested evidence.

## The seven steps

Show [the checklist](references/setup/checklist.md) in the host's task list from the first
action, and in chat with each step's result line, never inside a question's text. Follow
[one presentation flow](references/setup/presentation.md): read each moment's file when
reached and never replace a moment with a status line. Show each moment's chip or card, from
the first survey on, as [visuals](references/setup/visuals.md) says.

1. **Clarify your use case.** [The interview](references/setup/intent.md)'s card 1, and the
   branch's own ask. Skip what the request already said.
2. **Survey your sources.** By branch: `source-survey` `discover` through
   [discovery](../source-survey/references/discovery.md), or `access` on the dashboard and
   tables the person named. Once the survey creates the run, `PROJECT visual connect` opens the
   side view.
3. **Select tables and dashboards.** In the discovery branch,
   [the use cases](references/setup/names.md) and any clarification. In every branch, the
   interview's confirmation, "Build it?". A report is built whole.
4. **Generate and check metrics.** `metrics-generation` `generate` and `check` with the selection
   and the whole survey, through [build and check](references/setup/build-check.md). Then
   review the completed collection with actual counts and one representative metric.
5. **Keep local or save to Atlan Trident.** Read
   [context built and where to keep it](references/setup/save.md) and
   [preview and publish](references/setup/preview-publish.md); `metrics-generation` `save`.
   Then show the `saved` visual.
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
- `ask`, or a data question with no setup work: load the `analytics-ask` skill. When this
  folder holds a run, pass its directory, so the answer uses the run's own rules.

In Claude Code, load a sibling skill with the Skill tool as `atlanai-analytics:<name>`. The
Registry marketplace bundles these skills in `atlanai`; use its installed namespace instead,
and do not install this add-on beside that bundle.

## Hand-offs

| From | To | Carries |
| --- | --- | --- |
| `analytics-demo` ("Continue building on real data") | here | Nothing but the connection. A demo run never becomes a build |
| here | `analytics-ask` | The run, so answers read its context |
| `analytics-ask` (no metric covers a question) | here | The question, as the subject for the discovery branch |
| here, when the survey finds no readable source | `analytics-demo` | Nothing |

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/analytics-build/`; the sibling
skills sit beside it. With MCP only, read `atlan://defaults/skills/analytics-build/SKILL.md`
and cited relative references at that base. Fetch needed scripts locally with their relative
layout before execution; MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals.
