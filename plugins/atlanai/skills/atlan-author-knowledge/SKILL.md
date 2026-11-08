---
name: atlan-author-knowledge
description: Create or edit a knowledge bundle in Atlan, the curated Markdown agents read, such as a glossary, table docs or runbooks. Use when the user wants to turn notes into Atlan knowledge, change a term in a bundle, or read a bundle to edit it. Not for uploading a single file (atlan-content) or finding what knowledge exists (atlan-find-artifacts).
license: Apache-2.0
---

# Author knowledge

In search mode, the default, run each tool named here through the runner for what it does
(`mcp__call_read_tool` for a read, `mcp__call_write_tool` for a create or update,
`mcp__call_tool` for anything destructive), with `session_id` and a first-person `rationale`
beside `name` and `arguments`; your first call opens
the session by sending `objective`. Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner. Send independent reads
together in one turn, or as one `mcp__call_read_tools` call (up to 8) when the host lists it. A create names its `workspace_id`: the one the user names, else
`.atlan/share.json`'s at the project root, else theirs from `registry__list_workspaces` with
`{"kind": "user_default"}`. Write first, and read `registry__get_kind_schema` only after a
refusal that names no field. Open `skills/atlan-get-started/references/calling-tools.md` only for
a failed call, a limit, a source key or full mode. Replies lead with the result in a few lines, and ask before every write.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

Before the first write, check that this organization has the knowledge app:
`mcp__describe_tools { "names": ["extension__knowledge__create"] }`. When it is not listed, or a
call answers `unknown_tool`, say in one line that the organization does not have Atlan's
knowledge app and an admin can install it. Do not record the bundle as a generic `file` or
artifact in its place.

Follow [the knowledge workflow](references/author.md) for creation, reading and editing.
Load only the references that procedure calls for.

Read pinned bundle bytes before editing. Knowledge edits use the app's `apply_changes`
workflow with `base_version_ordinal`; preserve its conflict handling. On a stale base,
re-read and reconcile the user's changes before retrying. Do not substitute a generic
artifact append or overwrite another editor's work. Validate the complete bundle and
read back the resulting version.
