# Setup progress, workers and presentation

Read this file before a long helper run.

Use `PROJECT progress --stage <stage> --status <status>` at parent-owned stage transitions.
`PROJECT progress` reads the latest state. Inspect/check operations also checkpoint their
own counts. Statuses are pending, running, waiting-for-user, complete, complete-with-held-items,
failed or canceled. Progress contains real counts, seven named steps and steps remaining,
never a guessed percentage or ETA. Model metrics and saved queries have separate counts.

Run deterministic scanning, decoding, mining, checking and rendering through the local
helpers. Use host background task IDs/output files when available, otherwise bounded foreground
batches. A survey returning `continue` resumes with the same command and cached inventories.
The parent retains semantic decisions, permission UI and authenticated MCP/warehouse calls.
Serialize state mutation and publishing. Do not create a daemon, hidden model calls, hooks or
another MCP server. Cancellation preserves checkpoints; uncertain remote writes need readback.

Normal structured helper results over 4 KiB become private result files. Read relevant fields
from them instead of dumping logs. `--full` is for targeted diagnosis, not normal chat.
At phase changes and first previews, follow [the presentation flow](presentation.md).
For useful count changes between those moments, give a factual update. Read
`PROJECT experience --format json` for saved setup state; while the engine is active, add
`--engine-dir <within-run-engine>` so its manifest supplies engine progress. Use an absolute
engine path or `--engine-dir engine`, which is relative to the selected run.
When access evidence spans hosts, pass `--host <claude_code-or-codex> --server <observed-id>`
to `PROJECT experience` and `PROJECT journey`. Only a matching successful Registry read
can establish that host's readiness. It cannot clear another host's sign-in request.
Do not pass a cwd-relative engine path unchanged. For long work,
provide a factual heartbeat every 30 to 60 seconds, except while a subagent writes the gates: then the
side page and the step chips are the heartbeat, and the task list's count where you keep one, so nothing of
yours goes in the chat then. Surface useful evidence such as a first
metric or a resolved hold, not gate names, private paths or raw logs. Do not repeat unchanged
counts or imply that engine gates equal the seven setup stages. Never invent an ETA/percentage;
the dashboard branch gives a rough spoken range instead, as [its file](dashboard-flow.md) says.
Report schema checks, executed examples and parity separately; diagnostics stay in details.

## Drafts as they land

The describe, propose, merge and card gates save their answers in numbered batch files. The side
page shows each batch as it lands; the chat shows each step's chip when the step ends:

1. **A fresh subagent writes each gate.** Hand each authored gate, from describe to cards, to its
   own subagent started in the background, never one this session waits on (a waiting session
   runs no watch and shows the person nothing), when the gate before it is accepted. Write the
   short gates before describe and the links gate in this session, named as their gate result
   names them, never by a gate's id. Give each subagent the engine directory,
   `ENGINE advance` until that gate is accepted, the gate's instructions, numbered batch files,
   and no `PROJECT drafts` and no chat of its own, saving each batch with its own write as soon as
   it is ready, never several in one script. The engine's refusal names each field to fix, so the
   subagent never reads the engine's code to check its answers first. It returns when its gate is accepted or the gate
   needs the person; then start the next gate's subagent, before drawing the chip of the step that
   ended: the build waits on its subagent, never on a chip. The app shows each subagent's and
   watch's name to the person: name each for what it does, as the side page names its step
   ("Describe each query", "Following the build"), never a gate's name. For the card gate, run the gate
   result's `after_each_batch.split` (`PROJECT split`) first: up to 20 metrics it gives one
   brief, above that one per share, at most five. Start every share's subagent at once with its
   brief as the whole prompt; none advances the engine. When every share is back, run
   `ENGINE advance` once, and send each refused card to the subagent whose share holds its task
   number.
2. **This session draws each step's chip.** While it works, run the gate result's
   `after_each_batch.follow` (`PROJECT drafts --follow`) as a background watch that hands you
   each printed line: in Claude Code, the Monitor tool, never a background shell command, which
   never wakes you. It prints a line when a step the chat
   shows ends, naming the `visual next` that is due, and every minute while batches land,
   naming `visual progress`, the chip with how far the step has got: run what it names and show
   what it gives, with no words of your own. Re-arm the watch if it stops before the cards are
   accepted; it never repeats or skips a step. The desktop app sends a turn that ends without words
   back for words, so every turn ends on one line with something new: the line the `visual`
   result gives with its chip, unchanged. A step's own chip names the step that starts, so no
   line of yours names it again (a Doing line under a chip that says "Next: Combine duplicates"
   repeats it). Never the engine's results or counts (accepted, cards, pairs, a check's own name)
   nor your own tooling (scripts, retries, setup); never that a step is still running.
   **On every host** (the team, 2026-11-19), the session waits for the build in one turn from its start, and the
   gate result's `wait` names the command. Before the side page opens, a step's end draws its chip. Once it is open,
   it carries each metric step: where you keep a task list (the desktop app always does; in a terminal, as you
   recorded with `PROJECT journey tasks`), the task list carries the build and nothing goes in the chat until the
   build's card, not even a Doing line at a step's start; with no task list, each step's end shows its chip and
   nothing comes between. Run no background watch: run `PROJECT drafts --wait --engine-dir <engine>` in
   the foreground, with a ten-minute timeout, and do what its line says. On a batch, set your task list to its
   `task_list` in the same message as the next wait. On a step's end, start the next gate's subagent, then run
   `visual next`, which hands back the task list instead of a chip. On a quiet spell, check the subagents, then wait
   again. With no foreground command that can run ten minutes, run `follow` as the background watch instead. When the
   wait says the build is done, export it and run `PROJECT check` before any question. A turn that ends
   on a task-list update alone is sent back for words; a turn that keeps going is not, so never end the turn between
   waits. A helper's share coming back while others are still out needs no words: wait again.
3. **What the app shows is the person's.** It shows each command's description, each helper's and
   watch's name, and each widget's title. Describe each command as what it does for them
   ("Showing what's new", "Continuing the build", "Testing on the data"), never a chip, the engine,
   a gate, an answer folder, a record or a "visual moment"; give each widget the title its
   `visual` result names.

A host with no background subagent or no line-by-line watch writes the gates in this session
instead and runs `visual next` once each gate is accepted. On every host the batches stay on the
side page, never in the chat. When a gate refuses a batch, the subagent that wrote it fixes it. Never show a
value before its check.

Use native chat, its choice controls and [the setup checklist](checklist.md) in chat and
the host's task list when available. The first time you print the tracker, record once whether you keep a task list:
`PROJECT journey tasks --answer yes` when you have a task-list tool (TaskCreate and TaskUpdate, or TodoWrite), else
`--answer no`; the desktop app needs none. Show the full tracker when a step starts; refresh
`PROJECT checklist` with real counts during long work and report useful changes. Save phase changes and intent answers to the run, so the checklist stays
true. Open no panel or view unless the person asks for [a visual view](render.md), apart from
the run's own [side page](visuals.md). Do not
invent a pause or publish button. If the task tool fails, keep the full tracker in chat.
Missing task UI does not block work; required visible moments still precede their choices.
