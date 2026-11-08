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
the session by sending `objective`. Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner. Send independent reads
together in one turn. A create names its `workspace_id`: the one the user names, else
`.atlan/share.json`'s at the project root, else theirs from `registry__list_workspaces` with
`{"kind": "user_default"}`. Write first, and read `registry__get_kind_schema` only after a
refusal that names no field. Open `skills/atlan-get-started/references/calling-tools.md` only for
a failed call, a limit, a source key or full mode. Replies lead with the result in a few lines, and ask before every write.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

- **Register a deliverable or publish its next version:** [recording](references/record.md).
- **Choose upload or link mode:** [mode rules](references/output-modes.md).

Record with one call, `registry__publish_output`: the output's `metadata` (`name`,
`workspace_id`, producer fields), a source key (`source_type` and `source_id`, see
[recording](references/record.md#the-source-key)), and exactly one of `file` (text up to
64 KiB), `file_pin` (a file already uploaded) or `external_url`, plus `edges` such as
`used_skill`. It writes the file, then the output and its edges together. The same call with the
same source key publishes the next version, and unchanged text writes no new file version, so
there is nothing to look up first. When it answers `output: null` with `output_error`, the
file was saved: fix what the error names and send the same call again. When the gateway answers
that the tool does not exist, follow [the older route](references/record.md#when-publish_output-is-not-served).
