---
name: atlan-content
description: Upload, read and version file content in Atlan, including small UTF-8 text, staged binary uploads and pinned skill files. Use when someone wants to put a file, document or dataset into Atlan, read a file or a skill's bundled files by handle, or add a new version of an existing file. Not for publishing a skill bundle (atlan-author-skills) or editing knowledge (atlan-author-knowledge).
license: Apache-2.0
---

# File content

Open one audited session and pass its id as `session_id`, with a first-person `rationale`,
on every later tool call. In search mode, the default, your first `mcp__search_tools` or
`mcp__call_tool` opens it by sending `objective`, and each tool named here runs through
`mcp__call_tool`; in full mode, call `mcp__initialise` first. Resolve the workspace with
`registry__list_workspaces` and read `registry__get_kind_schema` before writing a kind for
the first time. Replies lead with the result: the first sentence is the answer or the step, counts rather
than lists, handles only when asked, one next step rather than a menu, and no greeting,
apology or closing offer.

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
