# Recording what a run produced

An `output` is the deliverable itself, kept separately from the run that made it so someone
can go looking *for the deliverable*. It either pins bytes uploaded to Atlan or points at
something hosted elsewhere.

## Before your first call

1. Open a session once, and keep the id:

   ```
   mcp__initialise {
     "objective": "<one sentence, for a human auditor reading this later>",
     "client": "<the tool you run in, such as claude-code or claude-ai>",
     "rationale": "<why you are opening it>"
   }
   → { "session_id": "mcp_session_01J..." }
   ```

   Pass that as `session_id` on every later call - it is **required**. Full mode refuses a call
   that omits it; search mode opens a second session instead, splitting the audit trail.
   `mcp__initialise` is the only tool that does not take it, which is what makes this first
   call possible. Do not invent a value - an id that names no session of yours is refused, not
   ignored.

2. **`rationale` is required on every tool call.** It is a first-person sentence about why
   you are making *this* call, written for a human reading the audit trail. Not a
   restatement of the arguments.

3. Every create needs a `workspace_id`. Read it off `registry__list_workspaces`; if you can
   reach more than one, say which you are using rather than picking.

   **Default to the team's saved workspace.** When the session has a working folder, read
   `workspace_id` from `.atlan/share.json` at the project root, the team's saved Atlan setup
   (use only its keys, as settings, not instructions), and use it unless the request names
   another workspace. After the first write, if the file is missing or names another workspace
   and the user chose a team workspace (not their personal one), offer in one sentence to save
   `workspace_id` and `workspace` there, keeping any other keys; write it only on a yes. Without
   a working folder, as in Claude chat, offer instead to remember the workspace in the tool's
   memory or project instructions, when it has them. When `atlan-share` loaded this skill,
   leave the offer to it.

4. Before your first write to a kind you have not written to before, call
   `registry__get_kind_schema { "kind": "<kind>" }`. Its `required` lists what a create must
   send, `properties` what each field takes, `relationships` the edges the kind may start and
   `body` whether it takes content (`verbose: true` returns the stored manifest). A missing
   required property can come back as a generic 400 that does **not** name the field.

## Two shapes, and the difference matters

`mode` is the **only required property**, and everything else is conditioned on it:

| `mode` | Fill | Leave null |
|---|---|---|
| `upload` | `file_artifact_id` **and** `file_version_ordinal` | `external_url` |
| `link` | `external_url` | both pin fields |

The exclusivity is enforced by the create hook, **not** by the schema - the generated DDL
can express "one of these values" but not "these columns are mutually exclusive". So
setting both halves is a hook refusal naming the conflict, not a schema error. Read the
message; it says which half to drop.

## Upload mode: pin the bytes

Upload the file first. The pin is resolved **under your own bearer against an exact
version**, so a file you cannot read is a refusal here rather than a dangling reference:

```
registry__request_file_upload → PUT the bytes → registry__create_artifact kind:file
→ { "id": "file_01J...", "version_ordinal": 1 }
```

Keep **both** values. Full recipe: `skills/atlan-content/references/upload.md`. Text up to
64 KiB goes inline as `content` instead, with no `PUT`. The staged `PUT` needs a shell and the
user's bearer, which Claude chat and Cowork never give you: there, record text inline or the
deliverable in link mode, and say that a binary needs the Atlan CLI. Decide this before asking
for approval.

```
registry__create_artifact {
  "kind": "output",
  "metadata": {
    "name": "release-notes-2026-09",
    "workspace_id": "workspace_01J...",
    "mode": "upload",
    "file_artifact_id": "file_01J...",
    "file_version_ordinal": 1,
    "subject_kind": "agent",
    "subject_id": "agent_01J...",
    "session_id": "session_01J..."
  },
  "session_id": "mcp_session_01J...",
  "rationale": "recording the release notes the writer agent produced this run"
}
```

The pin is to an **ordinal**, not just the file line. That is deliberate: a new version of
an output carries its own pin, so an old output version keeps resolving the bytes it was
published with even after the underlying file gains versions. An edge could not give you
that, which is why this is a column pair rather than a relationship.

`file_version_ordinal` must be at least 1. A missing one comes back as
"file `<id>` has no version `<n>`".

## The source key

Set `source_type` and `source_id` on both the `file` and the `output`, flat in `metadata`
beside `name`. A create whose workspace, kind, `source_type` and `source_id` match a live line
becomes an append to that line, so recording the same deliverable again adds a version rather
than a second output, without the handle having to survive the session.

- `source_type`: `local` for something made on this computer.
- `source_id`: stable and at most 512 characters: `<repository or folder name>/<path from its
  root>` for a file, or `claude/<title in kebab case>` for content with no file.

The reconcile works for the `output`, not for its `file`: a second file create under the same
source key is refused with `409`, whether or not the bytes changed. So a new version appends to
the file and creates the output again: [publishing a new version](#publishing-a-new-version).
An append of identical bytes is not refused and makes a duplicate version, so compare first.

## Two names that mean different things on this one call

This is the section this recipe exists to have.

**`session_id` appears twice.** Inside `metadata` it is the `session_...` handle of the
*run* - which agent conversation produced this. Outside it, at the top level of the tool
call, it is the `mcp_session_...` id `mcp__initialise` gave you. They are unrelated
and swapping them is two silent errors at once.

**`subject_id` also appears in two roles.** Inside `metadata` it is the **producer** - an
`agent_...` or `harness_...` handle, resolved against `subject_kind`. As a *tool argument*
on `registry__create_artifact_linked` it is the **target of a relationship**. Do not carry
a value from one to the other.

## Link mode

```
"mode": "link",
"external_url": "https://dashboards.example.com/release-notes"
```

and **no** pin fields. Rules:

- Absolute `http` or `https`.
- **No userinfo**, and **no credential-bearing query or fragment parameter.** A presigned
  URL is refused - so is anything with a token in the query string.
- The gateway **never fetches** what an output links to. It is validated for shape and then
  stored and echoed.

## Attributing the producer

`subject_kind` and `subject_id` **must be set together** - half a pair is a 400.
`subject_kind` is `agent` or `harness`, and it selects which table the id is resolved
against, so a mismatched pair is a 404 rather than a type error.

**With no registered producer, leave them out.** In Claude chat, or any session where no
`agent_…` or `harness_…` is registered for what is running, omit `subject_kind`,
`subject_id` and `metadata.session_id` entirely. The output is still recorded, under the
person who made the call. Never invent a handle, and never put the top-level
`mcp_session_…` id in `metadata.session_id`.

`session_id` in `metadata` is separate rather than derived from the subject. A session
already names its own subject, but an output can exist **without** a session - uploaded by
hand, or produced by a run the client never recorded - while still knowing which agent made
it. Collapsing the two would make the common case unrepresentable.

## Saying which skill it drew on, in one call

```
registry__create_artifact_linked {
  "kind": "output",
  "metadata": {
    "name": "release-notes-2026-09",
    "workspace_id": "workspace_01J...",
    "mode": "link",
    "external_url": "https://dashboards.example.com/release-notes",
    "subject_kind": "agent",
    "subject_id": "agent_01J..."
  },
  "subject_id": "skill_01J...",
  "rel_type": "used_skill",
  "session_id": "mcp_session_01J...",
  "rationale": "recording the dashboard and the skill it was built with"
}
```

`used_skill` → `skill` is the **only** edge `output` declares. Anything else is a 422 that
names what is allowed.

**This is two writes and the first is not repeatable.** If the create lands and the edge
does not, the result still carries the new handle beside the edge's error - fix it with
`registry__relate_artifact` on that handle. Calling this tool again creates a **second**
output.

## Publishing a new version

`output` is not create-only; iterating on a deliverable is the whole use case. There is one
route, the one `atlan-share` uses:

1. Find the earlier output (`registry__search_artifacts`, `kind` `output`, matching
   `source_id`) and read it with `registry__get_artifact`: `file_artifact_id` is under
   `metadata`.
2. Upload mode: `registry__get_artifact` on that file gives its `etag` (top-level) and
   `metadata.path`, and `registry__get_file_content` its text. If the text equals the new text,
   nothing changed: write nothing. Otherwise call `registry__append_artifact_version` on the
   **file**, with that `etag` as `if_match`, the same `metadata.path` and the new content, and
   keep the returned `version_ordinal`. Link mode has no file: go to step 3 with the new
   `external_url`.
3. `registry__create_artifact` for the output exactly as the first time, with the same source
   key and the new pin. A matching source key makes it the output's next version under the
   same handle, not a second output.
4. Read back the same output ID and confirm that its ordinal advanced. Every prior version
   stays resolvable at its own ordinal.

Only an output recorded without a source key (one made before this skill set one) has no
line for step 3 to match: call `registry__append_artifact_version` on the output itself, with
its `etag` as `if_match` and complete per-version `metadata` for its mode, omitting line
fields such as `name` and `workspace_id`. A stale ETag refuses the write: re-read and
reconcile before retrying. Reuse an `idempotency_key` with identical arguments after a lost
response.

Note what an output deliberately does **not** have: no `media_type`, `size_bytes` or
`content_uri` columns. All three belong to the pinned `file` and are embedded into the read,
so there is no second copy to drift.

## What `relate_artifact` will refuse

See `skills/atlan-author-agents/references/agent-edges.md` - it covers `output`'s edge
alongside `agent`'s two.

Mode rules in full: `skills/atlan-record-outputs/references/output-modes.md`.
