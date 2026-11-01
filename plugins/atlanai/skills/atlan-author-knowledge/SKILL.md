---
name: atlan-author-knowledge
description: Create, read and edit governed Atlan knowledge bundles while preserving pinned bytes and concurrent edits.
license: Apache-2.0
---

# Author knowledge

Open one audited session with `mcp__initialise`; pass its `session_id` and a first-person
`rationale` on every later tool call. Resolve the workspace with `registry__list_workspaces`
and read `registry__get_kind_schema` before writing a kind for the first time.

Follow [the knowledge workflow](references/author.md) for creation, reading and editing.
Load only the references that procedure calls for.

Read pinned bundle bytes before editing. Knowledge edits use the app's `apply_changes`
workflow with `base_version_ordinal`; preserve its conflict handling. On a stale base,
re-read and reconcile the user's changes before retrying. Do not substitute a generic
artifact append or overwrite another editor's work. Validate the complete bundle and
read back the resulting version.
