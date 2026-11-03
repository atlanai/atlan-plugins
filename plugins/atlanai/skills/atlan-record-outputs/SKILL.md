---
name: atlan-record-outputs
description: Record a deliverable an agent produced as an Atlan output, by uploading its file or linking where it is hosted, and publish new versions of it. Use when the user wants a report, dashboard, document, dataset or other result of an agent run saved, registered or recorded in Atlan, or a recorded output updated. Not for sharing it with colleagues or posting it to Slack (atlan-share), uploading a file that no agent run produced (atlan-content) or finding outputs (atlan-find-artifacts).
license: Apache-2.0
---

# Record outputs

Pass a `session_id` and a first-person `rationale` on every tool call. In search mode, the
default, the first `mcp__search_tools` or `mcp__call_tool` opens the session by sending
`objective`, each `registry__…` tool named here runs through `mcp__call_tool` with `session_id`
and `rationale` beside `name` and `arguments`, and a reference's `mcp__initialise` step is
skipped. In full mode, call `mcp__initialise` first. Resolve the workspace with
`registry__list_workspaces` and read `registry__get_kind_schema` before writing a kind for the
first time. Replies lead with the result in a few lines, and ask before every write.

- **Register a deliverable:** [recording](references/record.md).
- **Choose upload or link mode:** [mode rules](references/output-modes.md).
- **Publish another version:** [recording](references/record.md#publishing-a-new-version).

Give the file and the output the same source key (`source_type` and `source_id`, see
[recording](references/record.md#the-source-key)), so recording the same deliverable again
appends a version instead of creating a second output.

A new version is one route: append the new bytes to the earlier output's file, then create
the output again with the same source key and the new file ordinal; the registry makes it
the next version under the same handle. Only an output recorded without a source key is
appended to directly, with its head ETag. Pin exact file ordinals in upload mode; read the
result back. If an edge write fails after creation, repair that edge using the returned ID
instead of creating again.
