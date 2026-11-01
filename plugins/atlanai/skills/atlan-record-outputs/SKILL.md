---
name: atlan-record-outputs
description: Record a deliverable an agent produced as an Atlan output, by uploading its file or linking where it is hosted, and publish new versions of it. Use when the user wants a report, dashboard, document, dataset or other result of an agent run saved, registered or shared in Atlan, or a recorded output updated. Not for uploading a file that no agent run produced (atlan-content) or finding outputs (atlan-find-artifacts).
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
- **Publish another version:** follow the append procedure in [recording](references/record.md#publishing-a-new-version).

Append to the existing output ID with the observed head ETag. Repeating create makes a
second output. Pin exact file ordinals in upload mode; read the result back. If an edge
write fails after creation, repair that edge using the returned ID instead of creating again.
