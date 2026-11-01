# Uploading content to Atlan

For binary content or text above the inline ceiling, content arrives out of band: reserve a destination, `PUT` the bytes there, then create a
`file` artifact that names them.

## Before your first call

1. Open a session once, and keep the id. How depends on the mode your client connected in:

   - **Search mode**, the default, lists `mcp__search_tools`, `mcp__describe_tools` and
     `mcp__call_tool`. Send `objective`, one sentence for a human auditor reading this later,
     on your first search or call; it returns the id (`session_id` from a search,
     `mcp_session_id` from a call). Run each tool this procedure names with `mcp__call_tool`:
     its `name` and `arguments`, with `session_id` and `rationale` at the top level beside
     `arguments`, never inside it. Search mode does not list `mcp__initialise`.
   - **Full mode** lists every tool by name. Open the session with `mcp__initialise`:

     ```
     mcp__initialise {
       "objective": "<one sentence, for a human auditor reading this later>",
       "client": "claude-code",
       "rationale": "<why you are opening it>"
     }
     → { "session_id": "mcp_session_01J..." }
     ```

   Pass that id as `session_id` on every later call. Full mode refuses a call without it;
   search mode opens a second session, splitting the audit trail. Do not invent a value - an
   id that names no session of yours is refused, not ignored.

2. **`rationale` is required on every tool call.** It is a first-person sentence about why
   you are making *this* call, written for a human reading the audit trail. Not a
   restatement of the arguments.

3. Every create needs a `workspace_id`. Read it off `registry__list_workspaces`; if you can
   reach more than one, say which you are using rather than picking.

4. Before your first write to a kind you have not written to before, call
   `registry__get_kind_schema { "kind": "<kind>" }`. A missing required property comes back
   as a generic 400 that does **not** name the field.

Uploads use the authenticated API host. Storage placement remains registry-owned. If your
connector does not expose a bearer for shell HTTP requests, use the skill create tool's inline
`files` input for small text skills. Do not copy connector credentials into tool arguments.

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
    "is_presigned": false,
    "expires_in_seconds": 900,
    "max_bytes": 104857600
  }
```

`size_bytes` is advisory and worth sending: it is checked against `max_bytes` now, so an
over-size upload is refused before the transfer rather than after it.

### 2. `PUT` the bytes

Send the whole body, unencoded, with your **Authorization header**. `is_presigned` is always
`false`; `upload_url` is root-relative (`/file/v1/...`). Resolve it against the API origin you
called `registry__request_file_upload` on. A missing bearer returns 401.

Start the PUT within `expires_in_seconds` (900 seconds). Expired or unissued tickets return
400 with instructions to request a fresh ticket. A transfer started in time may finish after
the deadline. You can register its completed bytes afterward. Keep your bearer private.

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

- **`upload_key` or bytes, never both.** Both is a 400; neither is a 400. For small UTF-8,
  use top-level `content` instead, following [write and append](write-and-version.md).
- **`path` is the file's identity in its workspace.** A second file at the same `path` is a
  409 naming it. Give each upload a distinct path - `bundles/deploy-preview-2026-09-04.zip`
  rather than `bundle.zip`.
- **`max_bytes` is enforced during streaming and at step 3.** Check the size before sending.
- **Upload tickets expire.** Reserve when ready to send; request a fresh ticket after expiry.
- `media_type` and `size_bytes` on the artifact are derived from the bytes. Do not send
  them.

## When the `PUT` fails with a network error despite a valid URL

The sandbox must allow the API host. All reserved uploads use that host, including uploads
stored in a customer bucket. Re-minting does not fix a blocked host or missing authentication.

More on the failure modes: `skills/atlan-content/references/upload-troubleshooting.md`.

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

`registry__get_file_content` with a `file_id`. `skills/atlan-content/references/read.md` has it, including
how to read a skill's `SKILL.md` from its manifest.

## Where this is used from

- Publishing a skill: `skills/atlan-author-skills/references/publish.md` (inline), and
  `skills/atlan-author-skills/references/publish-zip.md` for an uploaded bundle
- Recording a deliverable a run produced: `skills/atlan-record-outputs/references/record.md`

Uploads enforce `max_bytes` during streaming and again at adoption. The gateway enforces `expires_in_seconds` when the PUT starts; reserve just before sending.
