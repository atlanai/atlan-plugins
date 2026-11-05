# Publishing a skill from a ZIP

For a bundle inline `files` cannot carry: binaries, executable files, more than 256 KiB or 128
files in one batch, or a server whose `extension__skill__create` schema has no `files`. Everything
else is in [publishing](publish.md): the workspace, the bundle rules, reading the slot, the
release and appending a version. This adds the upload.

## 1. Zip the contents, not the folder

`SKILL.md` must sit at the archive root. A zip with a top-level directory is refused with "skill
bundle missing SKILL.md at root". Zip from inside the skill folder:

```sh
cd path/to/deploy-preview && zip -r ../deploy-preview.zip .
```

## 2. Reserve, upload, register

```
registry__request_file_upload { "size_bytes": <zip size> }
→ { "upload_url": ..., "upload_key": ..., "is_presigned": false, "expires_in_seconds": 900 }
```

`PUT` the whole zip to `upload_url` exactly as [staged upload](../../atlan-content/references/upload.md)
step 2 says: the Authorization header, the root-relative URL, the 900-second ticket and the
presigned case are all there, once. If your client keeps its bearer private, as Claude chat and
Cowork do, you cannot `PUT`: use inline `files` for text, and for a binary say that it needs the
Atlan CLI. Never extract the credential into a shell.

```
registry__create_artifact {
  "kind": "file",
  "metadata": {
    "name": "deploy-preview.zip",
    "workspace_id": "workspace_01J...",
    "path": "bundles/deploy-preview-2026-09-04.zip",
    "upload_key": "<from the reservation>"
  }
}
→ { "id": "file_01J...", ... }
```

Give each upload a unique `path`: a file's path is its identity in its workspace, and a second
upload to the same path is a `409`. Dating the path avoids it. Upload failure modes:
`atlan-content`'s [upload](../../atlan-content/references/upload.md).

## 3. Create the skill from the file

```
extension__skill__create {
  "skills": [{
    "name": "deploy-preview",
    "workspace_id": "workspace_01J...",
    "idempotency_key": "deploy-preview-7f3k9q2m",
    "bundle_file_id": "file_01J..."
  }],
  "wait_for_release": true
}
```

Send exactly one of `files` or `bundle_file_id` per skill, and never base64 a bundle into the call.
`idempotency_key` and `wait_for_release` work as for inline files: a new key per skill, the same
call resent unchanged after a lost reply ([publishing](publish.md#3-create)).
`registry__create_artifact` cannot create a skill: the skill kind takes no `upload_key`, and its
properties are derived from the bundle by the create hook. Then read the slot as in
[publishing](publish.md#4-read-the-slot).
