# Prepare existing context for questions

Use seven stages: **Inspect, Scope, Build, Check, Preview, Publish, Ready to ask**.
The person sees them as six steps in [the setup checklist](setup/checklist.md).
The result is released metrics and saved SQL in Atlan, used by this chat through MCP.
An input folder is ordinary customer context. Never depend on a particular repository,
sample schema or database name. Explicit agent and evaluation modes remain separate.

`PROJECT` means `python3 "<skill-dir>/scripts/project.py" --run-dir "<run-dir>"`.
It is command notation, not a shell string variable. Use separate argv entries or a shell
function; zsh does not split a scalar containing an executable and its arguments.
Use the metrics engine's pinned Python 3.11/3.12 runtime for generation and CSV-to-DuckDB
preparation. The project helper retains local checks and publication state.
The [local procedures](project.md) describe binding, preparation and complete release readbacks.
Missing Registry access follows [connection recovery](auth-recovery.md): inspect the current
host, start the host's sign-in when required, watch its native task and resume after a
successful Registry read. Continue independent local work while waiting; do not simply ask
for "continue".

## Orient before work

A bare `start` with no source or saved run reads [the start choice](setup/demo.md) first.
The demo explains its environment, then uses this file's technical stages on the demo repository.
All setup uses [one presentation flow](setup/presentation.md), with actual source facts.

Explain the outcome, next action and controls. For example: "I'll turn
these dashboards and query history into metrics you can question here. First I'll check the
sources, ask what matters to you and what you want to accomplish, then show the first metric's
rule and a worked example. I'll show the six steps and where your choices matter. You can narrow the scope, inspect details, keep
drafts local or pause." Continue authorized work without another go-ahead. On resume, state the saved result
and next action instead of repeating the introduction.

Read [native conversation and saved choices](setup/workbench.md) on starting or resuming.
Show [the setup checklist](setup/checklist.md) in chat and the host's task list when available
before the first helper call, and keep both true to the saved run.
Read `PROJECT journey status` for an existing run before continuing. A paused or local-only
run waits for an explicit resume; details requests do not resume work.

Explain what sources were found, what they reveal about the project's subject, and what
they could help analyse. Keep that explanation in the first question's visible prompt; a
count-only final reply or collapsed commentary is insufficient. Then establish [business focus and purpose](setup/intent.md)
before authoring. Demos use their preset intent and source question instead. Ask missing
own-data answers in two separate native question steps; retain answers
already given. Technical source scope does not establish business intent.
The purpose sets the stopping point. Understanding-only intent ends with requested evidence;
it does not require a destination or publication question. Render the question itself, without
explaining the interview mechanics or showing helper commands.

## Read one stage file at a time

Open only the file for the stage you are entering. A resume starts at the saved stage, and a
run whose candidates were never shown in chat starts at Preview.

| Entering | Read |
| --- | --- |
| Inspect or Scope | [inspect and scope](setup/inspect-scope.md) |
| After intent, before Build, on own data | [commit on names](setup/names.md) |
| Build or Check | [build and check](setup/build-check.md); new metrics also follow [the metrics module](metrics-engine.md) for every source, including demos |
| Preview, Publish or Ready to ask | [preview and publish](setup/preview-publish.md) |
| Starting, resuming or any stage change | [the setup checklist](setup/checklist.md) |
| A long helper run or a heartbeat | [progress and presentation](setup/progress.md) |
| The person asks for a visual view | [a view on request](setup/render.md) |

## Preview first, in this reply

**Visible preview is a required conversation step.** Before a destination question or
publication/reuse, show the full [first-metric block](setup/demo-first-metric.md), using
the [presentation flow](setup/presentation.md) for the selected source. Tool output, counts and a
file link alone do not satisfy this step. This applies on resume and reuse, and adds no
approval prompt. A plan-only request needs no generated preview.

- When candidates are already supplied or readable, show one in this reply before the next
  destination question. Fresh intake establishes intent first; saved-preview and details requests
  do not restart intake. Do not ask whether to show a sample or rerun generation. Expand to five/all only when
  requested, preserving the supplied SQL, filters and evidence exactly in details.
- If the previous turn only linked a file, resume at Preview and show one actual sample
  before announcing Ready. The final answer keeps that sample and factual published/local/
  held counts so it remains useful when commentary collapses.
- Use `PROJECT experience --format json` for the block's saved evidence. During engine work add
  `--engine-dir <within-run-engine>`. `--detail` exposes that sample's SQL, source and checks.
  This read-only view does not export, publish or change eligibility.

On resume, reuse a readable run or candidate output already supplied in the conversation.
If neither is available, ask only for the existing run directory. Do not search unrelated
folders or home caches, request the original source again, or restart extraction to satisfy
a preview choice. With no shell, read existing candidate/preview files through the host's
file tool and show them directly. If the path is missing, name that one missing input.

## Presentation and stopping point

Follow [one presentation flow](setup/presentation.md) through the first metric,
celebration and ready handoff. Explain material reconciled definition or count changes
with their actual check status. Details-only requests end at their evidence; they are
not completion handoffs and need no question suggestions or publication pitch.

For the first useful reveal or question handoff, read
[the workbench interaction](setup/workbench.md). Preserve the same metric identity through
refreshes.
