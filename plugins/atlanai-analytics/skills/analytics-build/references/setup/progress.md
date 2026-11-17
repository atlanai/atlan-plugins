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
side page and the step chips are the heartbeat. Surface useful evidence such as a first
metric or a resolved hold, not gate names, private paths or raw logs. Do not repeat unchanged
counts or imply that engine gates equal the seven setup stages. Never invent an ETA/percentage.
Report schema checks, executed examples and parity separately; diagnostics stay in details.

## Drafts as they land

The describe, propose, merge and card gates save their answers in numbered batch files. The side
page shows each batch as it lands; the chat shows each step's chip when the step ends:

1. **A fresh subagent writes each gate.** Hand each authored gate, from describe to cards, to its
   own background subagent, started when the gate before it is accepted: the engine directory,
   `ENGINE advance` until that gate is accepted, the gate's instructions, numbered batch files,
   and no `PROJECT drafts` and no chat of its own, saving each batch with its own write as soon as
   it is ready, never several in one script. The engine's refusal names each field to fix, so the
   subagent never reads the engine's code to check its answers first. It returns when its gate is accepted or the gate
   needs the person; then start the next gate's subagent. The app shows each subagent's and
   watch's name to the person: name each for what it does, as the side page names its step
   ("Put each query in plain words", "Following the build"), never a gate's name. For the card gate, run the gate
   result's `after_each_batch.split` (`PROJECT split`) first: up to 20 metrics it gives one
   brief, above that one per share, at most five. Start every share's subagent at once with its
   brief as the whole prompt; none advances the engine. When every share is back, run
   `ENGINE advance` once, and send each refused card to the subagent whose share holds its task
   number.
2. **This session draws each step's chip.** While it works, run the gate result's
   `after_each_batch.follow` (`PROJECT drafts --follow`) as a background watch that hands you
   each printed line (in Claude Code, the Monitor tool). It prints a line when a step the chat
   shows ends, naming the `visual next` that is due, and every two and a half minutes while batches land,
   naming `visual progress`, the chip with how far the step has got: run what it names and show
   what it gives, with no words of your own. Re-arm the watch if it stops before the cards are
   accepted; it never repeats or skips a step. The desktop app sends a turn that ends without words
   back for words, so every turn ends on one line with something new: a step's start on its Doing
   line, with nothing about what will show or when; a so-far turn on the line the watch gives,
   unchanged; never that a step is still running.

A host with no background subagent or no line-by-line watch writes the gates in this session
instead and runs `visual next` once each gate is accepted. On every host the batches stay on the
side page, never in the chat. When a gate refuses a batch, the subagent that wrote it fixes it. Never show a
value before its check.

Use native chat, its choice controls and [the setup checklist](checklist.md) in chat and
the host's task list when available. Show the full tracker when a step starts; refresh
`PROJECT checklist` with real counts during long work and report useful changes. Save phase changes and intent answers to the run, so the checklist stays
true. Open no panel or view unless the person asks for [a visual view](render.md), apart from
the run's own [side page](visuals.md). Do not
invent a pause or publish button. If the task tool fails, keep the full tracker in chat.
Missing task UI does not block work; required visible moments still precede their choices.
