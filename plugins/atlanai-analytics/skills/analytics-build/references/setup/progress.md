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
provide a factual heartbeat every 30 to 60 seconds. Surface useful evidence such as a first
metric or a resolved hold, not gate names, private paths or raw logs. Do not repeat unchanged
counts or imply that engine gates equal the seven setup stages. Never invent an ETA/percentage.
Report schema checks, executed examples and parity separately; diagnostics stay in details.

## Drafts as they land

The describe, propose, merge and card gates save their answers in numbered batch files. One line per
batch reaches the person, inline in chat, from the session that talks to them:

1. **One subagent writes the gates.** Hand every authored gate, from describe to cards, to a single
   background subagent: the engine directory, `ENGINE advance` until the cards are accepted,
   each gate's instructions, numbered batch files, and no `PROJECT drafts` and no chat of its
   own. It returns when the cards are accepted or a gate needs the person.
2. **This session posts.** While it works, run the gate result's `after_each_batch.follow`
   (`PROJECT drafts --follow`) as a background watch that hands you each printed line as it
   lands (in Claude Code, the Monitor tool), and send each line as its own chat message,
   exactly as printed. Re-arm the watch if it stops before the subagent returns; it never
   repeats or skips a batch.

A host with no background subagent or no line-by-line watch writes the gates in this session
instead: after each file, run `after_each_batch.command` (`PROJECT drafts`) and send its line
before any other tool call. The person does not see command output, only your messages. Each
line covers every batch saved since the previous one. For example:

```text
These queries are described: Weekly active accounts, Pipeline by stage (20 of 152).
These additional metrics are drafted: Win rate, Pipeline created (15 of 40 queries read). Found again: Deal size.
Win rate + Win % → Win rate.
Kept as their own metric (11): Pipeline created, Average deal size, …. 22 drafts are now 15 metrics.
These metrics are complete: Win rate, Pipeline created, Average deal size, Deal count, Churn rate (10 of 15).
```

These are drafts until the gate accepts them; when a gate refuses a batch, say it is being
fixed. Never show a value before its check.

Use native chat, its choice controls and [the setup checklist](checklist.md) in chat and
the host's task list when available. Show the full tracker when a step starts; refresh
`PROJECT checklist` with real counts during long work and report useful changes. Save phase changes and intent answers to the run, so the checklist stays
true. Open no panel or view unless the person asks for [a visual view](render.md). Do not
invent a pause or publish button. If the task tool fails, keep the full tracker in chat.
Missing task UI does not block work; required visible moments still precede their choices.
