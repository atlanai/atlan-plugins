# The guided-flow checklist

Read on starting or resuming setup. Show the flow's steps in chat and in the host's task list
when available, kept true to the saved run. Open no side panel, browser view or preview page
unless the person asks for a visual view.

## Start with the list

Right after the opening message and before the first helper call, put the flow's steps in the
host's task list when available and show them in chat, the first one in progress.

| Step | Own data (`analytics-build`) | Demo (`analytics-demo`) |
| --- | --- | --- |
| 1 | Survey your sources | Choose and load demo data |
| 2 | Clarify your use case | Choose what to build from |
| 3 | Select tables and dashboards | Build and check metrics |
| 4 | Generate and check metrics | Keep local or save to Atlan Trident |
| 5 | Keep local or save to Atlan Trident | Ask a question |
| 6 | Ask a question | Build a dashboard |
| 7 | Build a dashboard | |

Before a run exists, `PROJECT checklist --flow demo` returns the demo's list. A demo records
its flow with `PROJECT journey demo` once the run exists. Record `PROJECT journey asked` after
the first chosen question is answered, and `PROJECT journey dashboard` once a dashboard is
built or declined; nothing else ticks those two steps.

Use the host's task tool, loading it first when it is listed but not loaded: `TaskCreate`
and `TaskUpdate` in Claude Code (`TodoWrite` where those are absent), the app's plan tool
(`update_plan`) in the Claude desktop app and in Codex, the todo tool in Cursor. Update it
in the same turn as every stage change, so the list on screen is never behind the run.
When each step starts, show the full tracker in chat. Finished steps carry their actual
results; the current step says `now`, with `your choice` when it needs an answer; the next
step says `next`. Own data's step 4 includes the first-metric choice; the demo shows its
metric names before building instead. Do not substitute a task update for the full metric
block or celebration.

## Keep it true to the saved run

`PROJECT checklist` returns the flow's steps from saved progress. Each step has `content` (its
name, sharpened with real counts once they exist, such as `Read 4 sources` or `Check 15
metrics and queries`), `active` (what to show while it runs, such as `Checking 5 of 9`),
`status` (`pending`, `in_progress` or `completed`) and `needs_you`. Add `--engine-dir` during
engine work. `PROJECT journey` results carry the same `checklist`.

- Read it after every stage change, after each intent answer, and about once a minute during
  a long step. Use its actual counts, `active` and `status`, with the step labels above.
- Never tick a step from memory. If the helper still shows a step pending, it is pending.
- One step is in progress at a time. A step with `needs_you` in progress means the run waits
  on the person: use that moment's visible payload and delivery method for its native question.
- `state` is `paused`, `blocked` or `access_needed` when nothing is in progress. Leave the
  list as returned and say the one thing needed to continue.
- On resume, read it first and rebuild the list, so finished steps start ticked.

## When a step ends

When a step completes, say its result in one line of chat, in the past tense, with its
counts: "Read 4 sources: 23 measures and 412 query records." Skip the line when nothing new
is known. Follow [the presentation flow](presentation.md) for the first metric, context-built
celebration and ready block. A step-complete line never replaces those moments.

## Without a task tool

Where the host has no task tool, keep the same full tracker in chat at each step change,
using `step`, `of`, status and actual counts from the helper.
