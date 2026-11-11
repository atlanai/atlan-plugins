---
name: analytics-build
description: Atlan Analytics setup on your own data, from nothing, a dashboard or a subject: survey, use case, metrics, save, questions. Use when setting up, resuming or handing off a run, its details, or workspace access. Not for a demo (analytics-demo).
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

Three ways in. Read [the entries](references/setup/entry.md) for each one's survey, interview
and inventory; decide from the request and never ask which one the person wants.

| The person gave | Entry | First action |
| --- | --- | --- |
| Nothing, and no saved run | No input | `source-survey` `setup`: what this machine can already reach, and which of it can be built from |
| A source (link, folder, BI export), with or without a problem | A source | `source-survey` `inventory` on it, then build the whole report with no question |
| A subject or a problem only, such as "sales pipeline" or "churn by plan" | A domain | `source-survey` `domain`: what already exists about it, and where definitions disagree |

Your next message is the opening, before surveying or calling helpers. With no input:

"I'll turn the dashboards and queries your team already has into metrics you can ask
questions of here. First I'm looking at what this machine can reach; nothing gets changed.
You can narrow the scope, ask for details, keep the work local, or pause at any point."

With a source or a subject: "I'll survey `<it>`, show what it can become, and build metrics
you can ask questions of here. You can narrow the scope, ask for details, keep the work
local, or pause at any point." Then the entry's `**Doing:**` and `**Why:**` lines. Every
survey settles whether a source can be built from before any question.

The interview is this skill's own: `source-survey` never asks the person anything. Honor
existing choices. Survey-only requests stop after step 1. On resume, read
`PROJECT journey status`, name the saved result and the next step, and rebuild the tracker.
Detail-only requests get only the requested evidence.

## The seven steps

Show [the checklist](references/setup/checklist.md) in chat and in the host's task list from
the first action, with the full tracker at every step change. Follow
[one presentation flow](references/setup/presentation.md): read each moment's file when
reached and never replace a moment with a status line.

1. **Survey your sources.** The entry's survey: `source-survey` `setup`, `inventory` or
   `domain`. Say what was found, what it means and what can be built from, inside the first
   question's visible prompt; an inventory followed by option labels alone is not enough.
2. **Clarify your use case.** [The interview](references/setup/intent.md), as much as the
   entry leaves open: focus and purpose in one card with no input; a named source or subject
   stands in for it otherwise. Then [the inventory](references/setup/names.md): the metric
   names and the questions they answer, from `source-survey` `candidates`, shown, not asked.
3. **Select tables and dashboards.** A report is built whole: never ask which pages.
   `source-survey` `plan`, then [inspect and scope](../source-survey/references/inspect-scope.md)
   section 2 with all pages: reports and models first, query history second, table definitions
   third. Confirm it with no input; state it in one sentence when the person named the source.
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
| `analytics-ask` (no metric covers a question) | here | The question, as the subject of the domain entry |
| here, when the survey finds no readable source | `analytics-demo` | Nothing |

## MCP resources

Installed paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/analytics-build/`; the sibling
skills sit beside it. With MCP only, read `atlan://defaults/skills/analytics-build/SKILL.md`
and cited relative references at that base. Fetch needed scripts locally with their relative
layout before execution; MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals.
