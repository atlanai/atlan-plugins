# The guided-flow checklist

Read on starting or resuming setup. Show the flow's steps in chat and in the host's task list
when available, kept true to the saved run. The dashboard branch ("Make my dashboard agentic")
has nine items of its own, set out in [the dashboard branch](dashboard-flow.md); the rest of
this file applies to it too. Open no side panel, browser view or preview page
unless the person asks for a visual view, apart from the run's own [side page](visuals.md).

## Start with the list

Right after the opening message and before the first helper call, put the flow's steps in the
host's task list when available and show them in chat, the first one in progress. The task list
is the host's own: in Claude Code's terminal, TodoWrite (or TaskCreate and TaskUpdate); in the
desktop app, its own plan tool. Use it whenever the host has one, not only when asked.

| Step | Own data (`analytics-build`) | Demo (`analytics-demo`) |
| --- | --- | --- |
| 1 | Clarify your use case | Choose and load demo data |
| 2 | Survey your sources | Choose what to build from |
| 3 | Select tables and dashboards | Find metrics in your sources |
| 4 | Find metrics in your sources | Combine duplicates, one metric per number |
| 5 | Combine duplicates, one metric per number | Define and test each metric |
| 6 | Define and test each metric | Keep local or save to Atlan Trident |
| 7 | Keep local or save to Atlan Trident | Ask a question |
| 8 | Ask a question | |
| 9 | Build a dashboard | |

Drafting, merging and writing are the build, one step each, so the person sees how it moves.
Each ticks as the engine finishes its part and shows a time estimate once the count going into
it is known ("about 4 min"); use the helper's text as it is. Each of these steps' chips comes
with `task_list`, the list as it now stands: set the host's list to it in the same message.

Before a run exists, `PROJECT checklist --flow demo` returns the demo's list, and
`--flow dashboard` the dashboard branch's. That branch records its flow with
`PROJECT journey agentic` once the run exists. A demo records
its flow with `PROJECT journey demo` once the run exists. Record `PROJECT journey asked` after
the first chosen question is answered, and, for own data, `PROJECT journey dashboard` once a
dashboard is built or declined; nothing else ticks those two steps. A demo's list ends at the
question: a dashboard is one of the options after it, not a step.

Use the host's task tool, loading it first when it is listed but not loaded: `TaskCreate`
and `TaskUpdate` in Claude Code (`TodoWrite` where those are absent), the app's plan tool
(`update_plan`) in the Claude desktop app and in Codex, the todo tool in Cursor. Update it
in the same turn as every stage change, so the list on screen is never behind the run.
When each step starts, show the full tracker in chat. Finished steps carry their actual
results; the current step says `now`, with `your choice` when it needs an answer; the next
step says `next`. In a demo, step 2 is `Choose what to build from`, because focus and purpose
are already set, and steps 3 to 5 build and check without an extra decision. Own data's steps 4 to 6
run through the selected collection without an extra build decision. Do not substitute a task
update for the collection review or celebration.

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

## On resume

The first thing on screen is the host's task list as the run left it. Read `PROJECT checklist`
(or take the checklist the person gave), create one task per step with its `content` and
`active` as given, then set each to its `status`, before the reply and in the same turn, so
finished steps start ticked. A checklist the person pasted is the helper's output: rebuilding
the task list from it runs nothing, so do it even when told to run no commands. Then show the
same steps in chat and say where the run stands, except when the person asks what is ready or
what to ask: that reply is [the ready block](ready.md#on-resume) alone. Never say the task list was not updated, or
name a helper you ran or did not run.

## When a step ends

When a step completes, say its result in one line of chat, in the past tense, with its
counts: "Read 4 sources: 23 measures and 412 query records." Skip the line when nothing new
is known. Follow [the presentation flow](presentation.md) for the collection review, context-built
celebration and ready block. A step-complete line never replaces those moments.

## Without a task tool

Where the host has no task tool, keep the same full tracker in chat at each step change,
using `step`, `of`, status and actual counts from the helper.
