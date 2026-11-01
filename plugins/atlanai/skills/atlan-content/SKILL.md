---
name: atlan-content
description: Upload, read and version file content in Atlan, including small UTF-8 text, staged binary uploads and pinned skill files.
license: Apache-2.0
---

# File content

Open one audited session with `mcp__initialise`; pass its `session_id` and a first-person
`rationale` on every later tool call. Resolve the workspace with `registry__list_workspaces`
and read `registry__get_kind_schema` before writing a kind for the first time.

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

Skill bundles use `atlan-author-skills`' bundle publishing procedure. Knowledge edits
use `atlan-author-knowledge`'s `apply_changes`; neither is a generic file append.
