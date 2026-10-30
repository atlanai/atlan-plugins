---
description: Upload a file to Atlan and register it as a `file` artifact.
argument-hint: "[path-to-local-file]"
---

Upload $ARGUMENTS to Atlan and register it as a `file` artifact.

Steps:

1. `mcp__initialise` if you have no `session_id` yet.
2. `registry__list_workspaces` to get the `workspace_id` to write to. If there is more than
   one, ask which.
3. `registry__request_file_upload` with `size_bytes` set to the real size.
4. `PUT` the bytes to `upload_url`. Read `is_presigned` first - `true` means send **no**
   `Authorization` header; `false` means send it, and the URL is root-relative.
5. `registry__create_artifact` with `kind: "file"`, a `path` that is unique in the
   workspace, and the `upload_key` from step 3.

Report the resulting `file_...` handle and its `version_ordinal`.

If anything refuses, read `skills/atlan-upload-content/SKILL.md`.
