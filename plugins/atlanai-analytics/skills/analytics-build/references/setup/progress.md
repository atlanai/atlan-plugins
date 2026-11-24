# Setup progress, workers and presentation

Read this file before a long helper run.

## When to talk

One rule for every flow: the demo, own data, the dashboard branch and the accuracy check. Other
files link here and do not restate it. **A line earns its place only when it tells the person
something the screen does not already show**: the task list, a chip or card, the side page, and
the app's own labels for each command and helper.

- **Before work starts**, at each moment [the presentation flow](presentation.md) names (a demo's are
  in [the tour](../../../analytics-demo/references/tour.md)): one Doing
  and Why pair, at most 35 words together, once. When a question follows, the pair is the
  question's own text.
- **While a build or a check runs**, the task list, the chips and the side page carry it. Write
  nothing of your own: no heartbeat, no "still", no line that repeats a chip, no count the list
  already shows. A wake that changed nothing on screen gets no words. Four things break the
  silence, each in one line:
  - a step that no chip names starts: its start line, once, where the flow's file gives one;
  - a problem that changes something for the person: what happened, the count, what happens next;
  - the person asks ("what's happening?", "is it stuck?"): the current step and its real counts,
    then keep waiting in the same turn;
  - only where there is no task list (a terminal), when nothing new has reached the screen for
    about 3 minutes: during the build, the line the wait hands over in `say`, word for word, and
    nothing when it gives none; at other waits, the step under way and its count, once. Where a
    task list and the side page show the count live, write nothing: a printed count stays in the
    chat and reads wrong once they move on.

  Real counts come from `PROJECT checklist`, or `EVAL todo` during an accuracy check.
- **When it ends**: its result, once, as its chip or card, or one line in the past tense with
  counts where there is none.

Use `PROJECT progress --stage <stage> --status <status>` at parent-owned stage transitions.
`PROJECT progress` reads the latest state. Inspect/check operations also checkpoint their
own counts. Statuses are pending, running, waiting-for-user, complete, complete-with-held-items,
failed or canceled. Progress contains real counts, the checklist's named steps and steps remaining,
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
Read
`PROJECT experience --format json` for saved setup state; while the engine is active, add
`--engine-dir <within-run-engine>` so its manifest supplies engine progress. Use an absolute
engine path or `--engine-dir engine`, which is relative to the selected run.
When access evidence spans hosts, pass `--host <claude_code-or-codex> --server <observed-id>`
to `PROJECT experience` and `PROJECT journey`. Only a matching successful Registry read
can establish that host's readiness. It cannot clear another host's sign-in request.
Do not pass a cwd-relative engine path unchanged. What reaches the chat while work runs follows
[when to talk](#when-to-talk): never gate names, private paths or raw logs, and never imply that
engine gates equal the setup stages. Never invent an ETA/percentage;
the dashboard branch gives a rough spoken range instead, as [its file](dashboard-flow.md) says.
Report schema checks, executed examples and parity separately; diagnostics stay in details.

## Drafts as they land

The describe, propose, merge and card gates save their answers in numbered batch files. The side
page shows each batch as it lands; the chat shows each step's end:

1. **A fresh subagent writes each gate.** Hand each authored gate, from describe to cards, to its
   own subagent started in the background, never one this session waits on (a waiting session
   shows the person nothing), when the gate before it is accepted. Write the short gates before
   describe and the links gate in this session, named as their gate result names them, never by
   a gate's id. Give each subagent the engine directory, `ENGINE advance` until that gate is
   accepted, the gate's instructions, numbered batch files, and no `PROJECT drafts` and no chat
   of its own, saving each batch with its own write as soon as it is ready, never several in one
   script. The engine's refusal names each field to fix, so the subagent never reads the
   engine's code to check its answers first. It returns when its gate is accepted or the gate
   needs the person; then run the `visual next` of the step that ended (it is quick) and show what
   it gives in the same message that starts the next gate's subagent: nothing comes between a
   step's end and its chip, and the build never waits on a chip. The app shows each
   subagent's name to the person: name each for what it does ("Describing each saved query"), never a
   gate's name. For the card gate, run the gate result's `after_each_batch.split` (`PROJECT
   split`) first: up to 20 metrics it gives one brief, above that one per share, at most five.
   Start every share's subagent at once with its brief as the whole prompt; none advances the
   engine. When every share is back, run `ENGINE advance` once, and send each refused card to the
   subagent whose share holds its task number.
2. **This session follows the build in one turn.** On every host it waits for the build from its
   start: run the gate result's `wait` (`PROJECT drafts --wait --engine-dir <engine>`) in the
   foreground with a ten-minute timeout; a host with a shorter limit for a foreground command
   passes `--timeout` under it. It prints one line: do what it says, then run it again in the
   same message. Never end the turn between waits: a turn that ends without words is sent back
   for them, while a turn that keeps going is not.
   - **A step ended**: run `visual next`, then show what it gives in the same message that starts
     the next gate's subagent. Before the side page opens, it draws the step's chip. Once the page is open, where you keep a task list
     (the desktop app always does; in a terminal, as you recorded with `PROJECT journey
     tasks`), it hands back the task list and `say`, one line to write word for word as your
     only words; with no task list, each step's end shows its chip.
   - **Something ready to show**, with no step ended (the side page ready to open, or a chip a
     helper's step made due): run `visual next` and do what it says.
   - **A batch**, where you keep a task list, at most every 20 seconds: set your task list to its
     `task_list` in the same message as the next wait, and write nothing: the person sees the list.
   - **Every share of the definitions back**: run `ENGINE advance` once.
   - **A quiet spell**: check that each subagent is working, write its `say` line word for word when
     it gives one, then wait again.
   - **Done**: export the build into the run and run `PROJECT check` before any question. The
     build's card comes once, right before the save question, as [save](save.md) says.

   Add no words of your own between these, as [when to talk](#when-to-talk) says: a step's own
   chip or line names the step that starts, and the engine's results, counts and tooling are
   never the person's.
3. **What the app shows is the person's.** It shows each command's description, each helper's
   name, and each widget's title. Describe each command as what it does for them ("Showing
   what's new", "Building your agent", "Testing your agent's metrics"), never a chip, the engine, a
   gate, an answer folder, a record or a "visual moment"; give each widget the title its
   `visual` result names.

A host with no background subagent writes the gates in this session instead and runs `visual
next` once each gate is accepted. On every host the batches stay on the side page, never in the
chat. When a gate refuses a batch, the subagent that wrote it fixes it. Never show a value
before its check.

Use native chat, its choice controls and [the setup checklist](checklist.md) in chat and
the host's task list when available. The first time you print the tracker, record once whether you keep a task list:
`PROJECT journey tasks --answer yes` when you have a task-list tool (TaskCreate and TaskUpdate, or TodoWrite), else
`--answer no`; the desktop app needs none. Show the full tracker in chat only where there is no task list; refresh
`PROJECT checklist` with real counts during long work. Save phase changes and intent answers to the run, so the checklist stays
true. Open no panel or view unless the person asks for [a visual view](render.md), apart from
the run's own [side page](visuals.md). Do not
invent a pause or publish button. If the task tool fails, keep the full tracker in chat.
Missing task UI does not block work; required visible moments still precede their choices.
