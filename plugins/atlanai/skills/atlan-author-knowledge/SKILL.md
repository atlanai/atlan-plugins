---
name: atlan-author-knowledge
description: "Create or edit a knowledge bundle in Atlan, the organization's curated Markdown that agents read: a glossary, table and column documentation, metric definitions, runbooks or other reference notes. Use when the user wants to turn notes or documents into Atlan knowledge, add or change a term or concept in a bundle, or read a bundle in order to edit it. Not for uploading a single file (atlan-content) or finding what knowledge exists (atlan-find-artifacts)."
license: Apache-2.0
---

# Author knowledge

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

Follow [the knowledge workflow](references/author.md) for creation, reading and editing.
Load only the references that procedure calls for.

Read pinned bundle bytes before editing. Knowledge edits use the app's `apply_changes`
workflow with `base_version_ordinal`; preserve its conflict handling. On a stale base,
re-read and reconcile the user's changes before retrying. Do not substitute a generic
artifact append or overwrite another editor's work. Validate the complete bundle and
read back the resulting version.
