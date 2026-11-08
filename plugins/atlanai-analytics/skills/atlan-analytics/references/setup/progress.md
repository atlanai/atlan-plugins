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
At phase changes, first previews and useful count changes, show concise progress. For long
work, consume `progress.json` and provide a truthful heartbeat every 30 to 60 seconds.
Put the stage and remaining steps in the assistant message, not only helper output, for
example `Check, step 4 of 7; 3 steps remain`, followed by actual counts. Report schema checks,
SQL examples, parameter samples and formula comparisons separately. Do not call their sum
"SQL checks" when some checks did not execute SQL.
Keep diagnostics private and give one recovery step on failure.

When the host has a panel API, open one surface and update it. `PROJECT preview --format html`
creates an escaped local snapshot with working details controls and a refresh link. It is a
snapshot, not a live service; regenerate before refreshing. Keep publication decisions in chat.
CLI users always get text and samples, with native task tools only if available. If the rich
renderer fails, display the Markdown result; optional UI never blocks the workflow.
