# A visual view, on request

Read only when the person asks to see the definitions in a browsable view. Setup itself
shows progress as [the checklist](checklist.md) and the run's own [side page](visuals.md),
and opens nothing else.

`PROJECT experience --format html` writes a system-styled local snapshot, `experience.html`,
inside the run and returns its path with the chat summary. During engine work add
`--engine-dir <within-run-engine>`. The snapshot is a file of saved evidence at one moment:
it does not update itself, execute SQL or publish, and its controls only filter and copy text.

Open it with the host's own browser or preview tool when one exists, and check that tool's
result before saying the page is visible. With no such tool, give the path and keep the same
evidence in chat. Say once that the page is a snapshot. Refresh it only when asked, and do
not reopen a page the person closed. If writing the snapshot fails, continue in chat.
