# Setup progress, workers and presentation

Read this file before a long helper run or when the host has a panel API.

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

Use native chat and its normal task/choice controls alongside the Analytics view opened
after inspection. Verify that the host exposes its rendering API. `PROJECT experience
--format html` creates a system-styled local evidence snapshot, never a native or auto-updating
sidepanel. Use `--format json` for an available host renderer and `--detail` for full evidence.
If the person dismisses the surface, stay in chat until asked to reopen it. Do not invent a
pause/publish button or embed chat decisions in an inert HTML control. CLI users get the same
concise sample and factual progress as text, with native tasks/choices only when available.
If rendering fails, use chat; presentation must not block generation or publication.

The live Analytics view follows saved run state automatically. Save phase changes and intent
answers to the run, not presentation-only text. Follow [render.md](render.md) to bind the
view once; a legacy static view still needs explicit refreshes. Keep a dismissed view closed.
