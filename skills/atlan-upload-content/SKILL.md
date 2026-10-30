---
name: atlan-upload-content
description: Upload content to Atlan that is too large to pass through a tool call - a skill bundle, a document, a dataset, an image - and turn it into a `file` artifact. Use when a step needs an `upload_key`, a `file_...` handle, or a bundle uploaded before a skill or an output can reference it.
license: Apache-2.0
---

# Uploading content to Atlan

A tool argument is a poor place for bytes. A 10 MiB bundle base64'd into a call costs
more context than the work it is part of, and the request ceiling refuses it anyway. So
content arrives out of band: reserve a destination, `PUT` the bytes there, then create a
`file` artifact that names them.

## Before your first call

1. Open a session once, and keep the id:

   ```
   mcp__initialise {
     "objective": "<one sentence, for a human auditor reading this later>",
     "client": "claude-code",
     "rationale": "<why you are opening it>"
   }
   → { "session_id": "mcp_session_01J..." }
   ```

   Pass that as `session_id` on every later call - it is **required**, and a call that omits
   it is refused with a message telling you to open one. `mcp__initialise` is the only
   tool that does not take it, which is what makes this first call possible. Do not invent a
   value - an id that names no session of yours is refused, not ignored.

2. **`rationale` is required on every tool call.** It is a first-person sentence about why
   you are making *this* call, written for a human reading the audit trail. Not a
   restatement of the arguments.

3. Every create needs a `workspace_id`. Read it off `registry__list_workspaces`; if you can
   reach more than one, say which you are using rather than picking.

4. Before your first write to a kind you have not written to before, call
   `registry__get_kind_schema { "kind": "<kind>" }`. A missing required property comes back
   as a generic 400 that does **not** name the field.

## The three steps

### 1. Reserve a destination

```
registry__request_file_upload {
  "size_bytes": 20480,
  "session_id": "mcp_session_01J...",
  "rationale": "reserving a destination for the release-notes PDF"
}
→ {
    "upload_url": "...",
    "upload_key": "...",
    "is_presigned": true,
    "expires_in_seconds": 900,
    "max_bytes": 104857600
  }
```

`size_bytes` is advisory and worth sending: it is checked against `max_bytes` now, so an
over-size upload is refused before the transfer rather than after it.

### 2. `PUT` the bytes

Send the whole body, unencoded. **Read `is_presigned` before you send** - it decides
whether your bearer token goes with the request:

| `is_presigned` | Where the URL points | `Authorization` header |
|---|---|---|
| `true` | Straight at object storage, carrying its own authorization | **Do not send it.** The bytes never touch the gateway |
| `false` | The gateway's own ingest route, and the URL is **root-relative** (`/file/v1/...`) | **Send it.** Omitting it is a `401`, which reads like a bad URL |

A `false` URL being root-relative is not an oversight: behind an ingress the gateway cannot
know its own public origin, so it does not guess one. Resolve the path against the origin
you called `registry__request_file_upload` on.

Treat `upload_url` as a password. Anyone holding it can write to that destination until it
expires. Do not log it, echo it into a message, or hand it to another tool.

### 3. Create the `file` artifact

```
registry__create_artifact {
  "kind": "file",
  "metadata": {
    "name": "release-notes-2026-09.pdf",
    "workspace_id": "workspace_01J...",
    "path": "reports/release-notes-2026-09.pdf",
    "upload_key": "<from step 1>"
  },
  "session_id": "mcp_session_01J...",
  "rationale": "registering the uploaded release notes so the output can pin them"
}
→ { "id": "file_01J...", "version_ordinal": 1, ... }
```

**Keep both `id` and `version_ordinal`.** An `output` in `upload` mode pins an exact
version, not just the file, so it needs the ordinal - and the create response is the only
place it is handed to you without a second read.

## Rules that bite

- **`upload_key` or bytes, never both.** Both is a 400; neither is a 400. On this surface
  you always have the key, because a tool call cannot carry the bytes.
- **`path` is the file's identity in its workspace.** A second file at the same `path` is a
  409 naming it. Give each upload a distinct path - `bundles/deploy-preview-2026-09-04.zip`
  rather than `bundle.zip`.
- **`max_bytes` is enforced at step 3, not step 2.** A reserved URL binds a destination and
  a deadline and nothing else, so an over-size object is transferred, discarded, and the
  create refused. Check the size before you send.
- **The URL expires.** Ask for it when you are ready to send, not while you are still
  assembling the content.
- `media_type` and `size_bytes` on the artifact are derived from the bytes. Do not send
  them.

## When the `PUT` fails with a network error despite a valid URL

The sandbox running the request probably cannot reach the host. The gateway's domain has to
be allowed for network access. That is a configuration problem, not a bad `upload_url` -
re-minting one will not fix it.

More on the failure modes: `skills/atlan-upload-content/references/upload-troubleshooting.md`.

## Joining a file to something else

**Nothing is joined automatically**, and for a `file` there is usually nothing to join.

`file` declares **no edge types**, which does not mean any join works - it means any
`rel_type` from a file is stored *without complaint*, so a wrong one becomes a relationship
nothing will ever query. The one declared join that points *at* a file is
`experiment → uses_context → file`, created from the experiment.

**If a file belongs to a skill, it goes in the bundle, not on an edge.** A skill's files are
its `.zip`: the create hook unpacks it, creates a `file` per entry, and pins them in the
skill's `metadata.files` manifest. Uploading a file next to a skill and hoping they associate
leaves you with an orphan.

For what the other kinds may join, and which end declares it:
`skills/atlan-author-skills/references/skill-edges.md`.

## Reading content back out

`registry__get_file_content` with a `file_id`. `skills/atlan-read-content/SKILL.md` has it, including
how to read a skill's `SKILL.md` from its manifest.

## Where this is used from

- Publishing a skill: `skills/atlan-author-skills/SKILL.md`
- Recording a deliverable a run produced: `skills/atlan-record-outputs/SKILL.md`
