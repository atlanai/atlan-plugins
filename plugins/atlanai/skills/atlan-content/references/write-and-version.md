# Write and append file content

For non-empty UTF-8 up to 65,536 bytes, use `registry__create_artifact` with top-level
`content` outside `metadata`. For example (add `session_id` and `rationale`):

```json
{
  "kind": "file",
  "metadata": {"name": "notes", "workspace_id": "workspace_...", "path": "notes.md"},
  "content": "# Notes\n",
  "idempotency_key": "notes-create-unique-operation"
}
```

For binary or larger data, follow [staged uploads](upload.md) and use
`metadata.upload_key` instead of `content`. Never send both. Do not send base64 as text.
Read back the file's ID, path, ordinal and ETag with `registry__get_artifact`.

## Append to the existing file

1. Read the head with `registry__get_artifact` and review its current bytes. Keep its
   `etag`, ID and ordinal. Resolve concurrent work before writing.
2. Call `registry__append_artifact_version` with `artifact_id` set to that ID,
   `if_match` set to the exact observed `etag`, and the complete new per-version
   `metadata`, including the same `path`. Omit line fields such as `name`,
   `workspace_id`, `project_id` and `owner_id`.
3. Send top-level `content` for bounded UTF-8, or `metadata.upload_key` for staged bytes.
   The media type is fixed for the line. An append does not convert its type.
4. Read back that same artifact ID, verify the ordinal advanced and read the new bytes
   using `registry__get_file_content` with the returned ordinal.

A stale `if_match` refuses the write. Re-read and reconcile; never blindly replay with a
fresh token. An identical retry with the same `idempotency_key` recovers the committed
version after a lost response. Changed arguments require a new operation key, after review.
Keep prior pins resolvable; repeating create at an existing path is not versioning.

Skill bundles retain their typed upload/create publication flow. Knowledge edits retain
`apply_changes` with `base_version_ordinal`.
