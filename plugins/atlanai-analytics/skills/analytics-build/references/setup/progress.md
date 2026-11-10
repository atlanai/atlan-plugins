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
At phase changes, first previews and useful count changes, show one concise update. Use
`PROJECT experience --format chat` for saved setup state; while the engine is active, add
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

Use native chat, its choice controls and [the setup checklist](checklist.md) in the host's
task list. The heartbeat for long work is the checklist's active step, refreshed from
`PROJECT checklist` with its real count; add a chat line only when a step ends or useful
evidence appears. Save phase changes and intent answers to the run, so the checklist stays
true. Open no panel or view unless the person asks for [a visual view](render.md). Do not
invent a pause or publish button. If the task tool fails, use the one-line fallback in the
checklist reference; presentation must not block generation or publication.
