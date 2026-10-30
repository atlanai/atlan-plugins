# Upload failures, and how to tell them apart

## A `401` on the `PUT`

Almost always `is_presigned` read the wrong way.

- `is_presigned: true` and you sent `Authorization`: some object stores refuse a signed
  request carrying a second credential. Send it without.
- `is_presigned: false` and you did not send `Authorization`: the URL is the gateway's own
  ingest route and it authenticates like every other route. Send your bearer.

The reason this is worth a section: a `401` from an object store looks like an expired or
malformed URL, so the instinct is to re-mint. Re-minting produces the same URL shape and
the same `401`, which reads as the gateway being broken.

## A `404` on the `PUT`

With `is_presigned: false` the URL is **root-relative** - `/file/v1/...`. If you sent it as
given to whatever host your HTTP client defaults to, that host has no such route. Resolve
it against the origin you called `registry__request_file_upload` on.

## A `409` on the create

Another file already occupies that `path` in that workspace. A file's `path` is its
identity there, deliberately - it is what makes a re-upload to the same path a conflict
rather than a silent second copy.

Pick a distinct path. If you meant to replace the content, that is a new version of the
existing file, not a new file at the same path: read the existing artifact first and append
to it.

## The create is refused after a successful `PUT`

`max_bytes` is checked when the artifact is created, not while uploading. A reserved URL
binds a destination and a deadline and nothing else - there is no size gate on the
transfer itself. So an over-size object goes across, is discarded, and the create is
refused.

Send `size_bytes` on `registry__request_file_upload` when you know it. It is checked against the
ceiling immediately, which turns this into a refusal before the transfer.

## `upload_url` has expired

`expires_in_seconds` says when. There is no renewal - call `registry__request_file_upload` again for
a fresh destination, and send promptly. Reserving a URL while still assembling the content
is what makes this happen.

## A parented file

`registry__create_artifact` on `file` also accepts a `parent_kind` / `parent_id` pair,
which homes the file on an owning artifact instead of on the workspace and skips the
`(workspace, path)` uniqueness check.

You almost certainly do not want to write one by hand. It is how a skill bundle's extracted
files are pinned to their skill, and the skill extension's own hook writes them. Uploading
a standalone file and parenting it manually is a way to create a child nothing will clean
up.
