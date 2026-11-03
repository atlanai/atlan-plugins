# Upload failures, and how to tell them apart

## A `401` on the `PUT`

The gateway PUT requires your bearer in the Authorization header (`is_presigned` is `false` on
today's gateway). Authenticate the request; re-minting cannot fix missing credentials. When your
host never gives you the bearer, as in Claude chat or Cowork, follow
[without a shell or a bearer](upload.md#without-a-shell-or-a-bearer).

## A `404` on the `PUT`

The URL is **root-relative** - `/file/v1/...`. If you sent it as
given to whatever host your HTTP client defaults to, that host has no such route. Resolve
it against the origin you called `registry__request_file_upload` on.

## A `409` on the create

Another live file in that workspace already holds that `path`, or that `source_type` and
`source_id`; the message names which. Both are the file's identity there, deliberately: a
re-upload is a conflict rather than a silent second copy. A share that stopped halfway leaves
the second kind.

Pick a distinct path. If you meant to replace the content, that is a new version of the
existing file, not a new file at the same path: read the existing artifact first and append
to it.

## The create is refused after a successful `PUT`

`max_bytes` is enforced during streaming and again at create. An oversized staged object is
deleted and create is refused.

Send `size_bytes` on `registry__request_file_upload` when you know it. It is checked against the
ceiling immediately, which turns this into a refusal before the transfer.

## `upload_url` has expired

A PUT started at or after the 900-second deadline returns `400 invalid_request` with
"Upload ticket is invalid or expired. Request a new upload ticket." Unissued keys and old
tickets without an issuance record receive the same refusal. Request a fresh destination and
upload there. A transfer admitted before expiry may finish afterward, and completed bytes
can still be registered afterward.

## A parented file

`registry__create_artifact` on `file` also accepts a `parent_kind` / `parent_id` pair,
which homes the file on an owning artifact instead of on the workspace and skips the
`(workspace, path)` uniqueness check.

You almost certainly do not want to write one by hand. It is how a skill bundle's extracted
files are pinned to their skill, and the skill extension's own hook writes them. Uploading
a standalone file and parenting it manually is a way to create a child nothing will clean
up.

## Storage or service unavailable

`customer_store_unavailable` means the organisation’s selected storage could not be reached: ask an administrator to check its role, KMS key and endpoint, then retry. There is no fallback to managed storage. `object_store_unavailable` means managed storage is temporarily unavailable; retry later. A stale tool/server listing needs `tools/list` refreshed; reconnect the Atlan connection if the client continues using a stale listing. A missing artifact still returns `not_found`.

All upload URLs use the API host. For small text skills, inline `files` avoids that separate authenticated upload.
