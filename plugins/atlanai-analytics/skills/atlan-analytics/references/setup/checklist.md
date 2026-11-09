# The setup checklist

Read on starting or resuming setup. The checklist is the whole progress display: the host's
own task list, kept true to the saved run. Open no side panel, browser view or preview page
unless the person asks for a visual view.

## Start with the list

Right after the opening message and before the first helper call, put these six steps in the
host's task list, the first one in progress:

1. Read your sources
2. Choose focus and goal
3. Write metric definitions
4. Check each definition's SQL
5. Review and choose where to save
6. Ready for questions

Use the host's task tool, loading it first when it is listed but not loaded: `TaskCreate`
and `TaskUpdate` in Claude Code (`TodoWrite` where those are absent), the app's plan tool
(`update_plan`) in the Claude desktop app and in Codex, the todo tool in Cursor. Update it
in the same turn as every stage change, so the list on screen is never behind the run.
Steps 2 and 5 are where
the person decides. The opening says so in a few words, for example "The task list shows the
six steps; two need you." Do not print the list in chat as well. The list is the plan, so the
opening stays within its word limit.

## Keep it true to the saved run

`PROJECT checklist` returns the six steps from saved progress. Each step has `content` (its
name, sharpened with real counts once they exist, such as `Read 4 sources` or `Check 15
definitions`), `active` (what to show while it runs, such as `Checking definition 5 of 9`),
`status` (`pending`, `in_progress` or `completed`) and `needs_you`. Add `--engine-dir` during
engine work. `PROJECT journey` results carry the same `checklist`.

- Read it after every stage change, after each intent answer, and about once a minute during
  a long step. Copy `content`, `active` and `status` into the task list as they are.
- Never tick a step from memory. If the helper still shows a step pending, it is pending.
- One step is in progress at a time. A step with `needs_you` in progress means the run waits
  on the person: ask its native question now.
- `state` is `paused`, `blocked` or `access_needed` when nothing is in progress. Leave the
  list as returned and say the one thing needed to continue.
- On resume, read it first and rebuild the list, so finished steps start ticked.

## One line when a step ends

When a step completes, say its result in one line of chat, in the past tense, with its
counts: "Read 4 sources: 23 measures and 412 query records." Skip the line when nothing new
is known. The first metric and the final handoff keep their own rules.

## Without a task tool

Where the host has no task tool, say `Step 3 of 6: Write metric definitions` once when each
step starts, using `step` and `of` from the helper. Never reprint the whole list.
