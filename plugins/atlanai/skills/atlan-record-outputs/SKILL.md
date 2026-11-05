---
name: atlan-record-outputs
description: Record a deliverable an agent produced as an Atlan output, by uploading its file or linking where it is hosted, and publish new versions of it. Use when a report, dashboard, document or dataset from an agent run should be saved or recorded in Atlan. Not for sharing it with colleagues (atlan-share) or uploading a file no run produced (atlan-content).
license: Apache-2.0
---

# Record outputs

In search mode, the default, run each tool named here through the runner for what it does
(`mcp__call_read_tool` for a read, `mcp__call_write_tool` for a create or update,
`mcp__call_tool` for anything destructive), with `session_id` and a first-person `rationale`
beside `name` and `arguments`; your first call opens
the session by sending `objective`. Send independent reads
together in one turn. A create names its `workspace_id`: the one the user names, else
`.atlan/share.json`'s at the project root, else theirs from `registry__list_workspaces` with
`{"kind": "user_default"}`. Write first, and read `registry__get_kind_schema` only after a
refusal that names no field. Open `skills/atlan-get-started/references/calling-tools.md` only for
a failed call, a limit, a source key or full mode. Replies lead with the result in a few lines, and ask before every write.

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
