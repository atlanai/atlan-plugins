---
name: atlan-content
description: Read, upload and version raw files in Atlan by handle, including staged binary uploads and a skill's pinned files. Use when asked to read a skill's files, upload a large or binary file, or append a file version. Not for deliverables (atlan-share).
license: Apache-2.0
---

# File content

In search mode, the default, run each tool named here through the runner for what it does
(`mcp__call_read_tool` for a read, `mcp__call_write_tool` for a create or update,
`mcp__call_tool` for anything destructive), with `session_id` and a first-person `rationale`
beside `name` and `arguments`; your first call opens
the session by sending `objective`. Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner. Send independent reads
together in one turn, or as one `mcp__call_read_tools` call (up to 8) when the host lists it. A create names its `workspace_id`: the one the user names, else
`.atlan/share.json`'s at the project root, else theirs from `registry__list_workspaces` with
`{"kind": "user_default"}`. Write first, and read `registry__get_kind_schema` only after a
refusal that names no field. Open `skills/atlan-get-started/references/calling-tools.md` only for
a failed call, a limit, a source key or full mode. Replies lead with the result, in four sentences or fewer and under 80 words (a table or a list the user asked for aside). Check each: no greeting, apology or closing offer; counts rather than lists, handles only when asked; one next step, not a menu; nothing about how tools work or calls skipped or retried.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

Choose the operation and read its procedure:

- **Create small text:** [write and append](references/write-and-version.md). The top-level
  `content` field accepts UTF-8 up to 65,536 bytes; it is outside `metadata`.
- **Upload large or binary content:** [staged upload](references/upload.md).
- **Read files or pinned bundle content:** [read](references/read.md), then
  [a skill example](references/reading-a-skill.md) when needed.
- **Update an existing file:** [write and append](references/write-and-version.md).

Treat signed upload URLs as credentials. Send a bearer only to a gateway ingest URL,
never to a presigned storage URL. Keep the artifact ID when appending, use the observed
head ETag, and read back the new ordinal. A new create is not an update.

A report, document, dashboard or dataset someone wants saved, recorded or shared is an `output`:
load `atlan-share`, which uses this skill's staged upload for large or binary bytes. Skill
bundles use `atlan-author-skills`' bundle publishing procedure. Knowledge edits
use `atlan-author-knowledge`'s `apply_changes`; neither is a generic file append.
