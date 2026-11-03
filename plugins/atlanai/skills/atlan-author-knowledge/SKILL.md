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
the session by sending `objective`. **Before your first call** in
`skills/atlan-get-started/references/calling-tools.md` covers the rest: full mode, the workspace
and the kind schema. Replies lead with the result in a few lines, and ask before every write.

Follow [the knowledge workflow](references/author.md) for creation, reading and editing.
Load only the references that procedure calls for.

Read pinned bundle bytes before editing. Knowledge edits use the app's `apply_changes`
workflow with `base_version_ordinal`; preserve its conflict handling. On a stale base,
re-read and reconcile the user's changes before retrying. Do not substitute a generic
artifact append or overwrite another editor's work. Validate the complete bundle and
read back the resulting version.
