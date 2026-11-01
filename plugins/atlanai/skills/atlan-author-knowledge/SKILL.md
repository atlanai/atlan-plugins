---
name: atlan-author-knowledge
description: Create or edit a knowledge bundle in Atlan, the organization's curated Markdown that agents read: a glossary, table and column documentation, metric definitions, runbooks or other reference notes. Use when the user wants to turn notes or documents into Atlan knowledge, add or change a term or concept in a bundle, or read a bundle in order to edit it. Not for uploading a single file (atlan-content) or finding what knowledge exists (atlan-find-artifacts).
license: Apache-2.0
---

# Author knowledge

Pass a `session_id` and a first-person `rationale` on every tool call. In search mode, the
default, the first `mcp__search_tools` or `mcp__call_tool` opens the session by sending
`objective`, each `registry__…` tool named here runs through `mcp__call_tool` with `session_id`
and `rationale` beside `name` and `arguments`, and a reference's `mcp__initialise` step is
skipped. In full mode, call `mcp__initialise` first. Resolve the workspace with
`registry__list_workspaces` and read `registry__get_kind_schema` before writing a kind for the
first time. Replies lead with the result in a few lines, and ask before every write.

Follow [the knowledge workflow](references/author.md) for creation, reading and editing.
Load only the references that procedure calls for.

Read pinned bundle bytes before editing. Knowledge edits use the app's `apply_changes`
workflow with `base_version_ordinal`; preserve its conflict handling. On a stale base,
re-read and reconcile the user's changes before retrying. Do not substitute a generic
artifact append or overwrite another editor's work. Validate the complete bundle and
read back the resulting version.
