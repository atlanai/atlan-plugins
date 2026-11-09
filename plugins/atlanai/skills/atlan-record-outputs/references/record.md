# Recording what a run produced

An `output` is the deliverable itself, kept separately from the run that made it so someone
can go looking *for the deliverable*. It either pins bytes uploaded to Atlan or points at
something hosted elsewhere.

## Calling rules

One session and its id on every call,
a first-person `rationale` on every call, a `workspace_id` on every create, and
`registry__get_kind_schema` only after a write to a kind is refused.

## Two shapes, and the difference matters

`mode` is the **only required property**, and everything else is conditioned on it:

| `mode` | Fill | Leave null |
|---|---|---|
| `upload` | `file_artifact_id` **and** `file_version_ordinal` | `external_url` |
| `link` | `external_url` | both pin fields |

`registry__publish_output` sets all of these itself from `file`, `file_pin` or
`external_url`, and refuses them in `metadata`. They matter when you read an output back, and on
[the older route](#when-publish_output-is-not-served), where the create hook enforces the
exclusivity: setting both halves is a hook refusal naming the conflict, not a schema error.

## One call: `registry__publish_output`

```
mcp__call_write_tool {
  "name": "registry__publish_output",
  "arguments": {
    "metadata": {
      "name": "release-notes-2026-09",
      "display_name": "September release notes",
      "workspace_id": "workspace_01J...",
      "subject_kind": "agent",
      "subject_id": "agent_01J...",
      "session_id": "session_01J..."
    },
    "source_type": "local",
    "source_id": "docs-site/release-notes/2026-09.md",
    "file": { "path": "2026-09.md", "content": "<the text>", "media_type": "text/markdown" },
    "edges": [ { "rel_type": "used_skill", "to_id": "skill_01J..." } ]
  },
  "session_id": "mcp_session_01J...",
  "rationale": "Recording the release notes the writer agent produced this run."
}
```

It makes two commits, in order:

1. **The file.** The live `file` under the same source key in that workspace gains a version, or
   one is created. Text whose sha256 equals the file's current content writes nothing and pins
   the current version.
2. **The output and its edges, together.** A create under the same source key, which becomes the
   next version of a live output that holds it. A refused edge leaves no output version behind.

Give exactly one of:

| Argument | When | Holds |
|---|---|---|
| `file` | Text up to 64 KiB | `path`, `content`, optional `media_type` and `name` |
| `file_pin` | Larger or binary bytes you uploaded first | `file_artifact_id`, `version_ordinal` |
| `external_url` | The deliverable is hosted elsewhere | an absolute `http(s)` URL ([rules](output-modes.md#external_url-validation)) |

`metadata` may not set `mode`, `file_artifact_id`, `file_version_ordinal`, `external_url`,
`source_type`, `source_id` or `edges`. `edges` takes up to 8 `{rel_type, to_id}` entries.

**The result** is `{output, file, edges, created}`: `file.written` is `created`, `appended` or
`unchanged`, and `created` is `false` when the call published the next version of an existing
output. Say which in the reply, with the output's `version_ordinal`.

**When only the file was saved,** the result is still a success, with `output: null`,
`output_error: {code, error}` and `committed: {file: true, output: false}`. Fix what the error
names (usually an edge or a producer field) and send the same call again: the source key finds the
saved file and pins it rather than writing a second one. Any other refusal is an error result and
wrote nothing.

### Refused with no field

Some gateways refuse `publish_output` without naming what is wrong. Neither refusal writes anything.

- "The request is not valid." on a call that carries `idempotency_key`: the gateway derives a key
  per step that its registry does not accept, so every keyed call is refused. Leave
  `idempotency_key` out of `publish_output`.
- A bare `409` when the same source key should publish a new version: often a `file.media_type`
  that differs from the one the first version stored, which a version cannot change. Leave
  `media_type` out after the first version; the file keeps its type.

Send the call once more without `idempotency_key` and `file.media_type`. Refused again: record through
[the older route](#when-publish_output-is-not-served) with the same source key, which reaches the
same file and output. Never send the identical call a third time.

**Binary or larger files** follow the upload recipe in `skills/atlan-content/references/upload.md`
first, with the same `source_type` and `source_id` on the `file`, then pass the returned `id`
and `version_ordinal` as `file_pin`. The staged `PUT` needs a shell and the user's bearer, which
Claude chat and Cowork never give you: there, record text inline or the deliverable in link mode,
and say that a binary needs the Atlan CLI. Decide this before asking for approval.

`idempotency_key` recovers a lost response on the older route's single writes: send the same key
with identical arguments. Do not send it to `publish_output` (see above).

## The source key

Set `source_type` and `source_id` once, as `publish_output`'s own arguments. It puts them on both
the `file` and the `output`, so recording the same deliverable again adds a version rather than a
second output, without the handle having to survive the session.

- `source_type`: `local` for something made on this computer.
- `source_id`: stable and at most 512 characters: `<repository or folder name>/<path from its
  root>` for a file, or `claude/<title in kebab case>` for content with no file.

The key belongs to its workspace: the same key in another workspace is a separate output.

## Two names that mean different things on this one call

This is the section this recipe exists to have.

**`session_id` appears twice.** Inside `metadata` it is the `session_...` handle of the
*run* - which agent conversation produced this. Outside it, at the top level of the tool
call, it is the `mcp_session_...` id your first call returned. They are unrelated
and swapping them is two silent errors at once.

**`subject_id` also appears in two roles.** Inside `metadata` it is the **producer** - an
`agent_...` or `harness_...` handle, resolved against `subject_kind`. As a *tool argument*
on `registry__create_artifact_linked` it is the **target of a relationship**, which
`publish_output` spells as an entry in `edges`. Do not carry a value from one to the other.

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

## Saying which skill it drew on

`used_skill` → `skill` is the **only** edge `output` declares. Put one `edges` entry per
registry skill the run loaded; anything else is a 422 that names what is allowed, and it is
refused before the output is written.

## Publishing a new version

`output` is not create-only; iterating on a deliverable is the whole use case. Send
`registry__publish_output` again with the same `workspace_id`, source key and the new content.
It appends to the file (or writes nothing when the text is unchanged) and makes the output's next
version under the same handle. Every prior version stays resolvable at its own ordinal, pinned
to the file version it was published with.

Only an output recorded without a source key (one made before this skill set one) has no line
for a source key to match: call `registry__append_artifact_version` on the output itself, with
its `etag` as `if_match` and complete per-version `metadata` for its mode, omitting line
fields such as `name` and `workspace_id`. A stale ETag refuses the write: re-read and
reconcile before retrying.

Note what an output deliberately does **not** have: no `media_type`, `size_bytes` or
`content_uri` columns. All three belong to the pinned `file` and are embedded into the read,
so there is no second copy to drift.

## When `publish_output` is not served

A gateway older than the tool answers that no tool of that name exists, and a refusal with no
field ([above](#refused-with-no-field)) can send you here too. Then record in separate
calls, giving the `file` and the `output` the same source key flat in `metadata` beside `name`:

1. **The file.** Text up to 64 KiB: `registry__create_artifact` with `kind` `file`, `content`, and
   `metadata` `{name, path, workspace_id, source_type, source_id}`. Larger or binary bytes: the
   upload recipe above. Keep the returned `id` and `version_ordinal`.
2. **The output**: `registry__create_artifact_linked` with `kind` `output`, the `metadata` above
   plus `mode`, the pin fields or `external_url`, and the source key, `subject_id` the skill and
   `rel_type` `used_skill`. With no skill to name, `registry__create_artifact`. On a gateway this
   old a linked create is two writes: when the result carries the new handle beside an edge
   error, fix the edge with `registry__relate_artifact` on that handle. Calling it again creates
   a second output.
3. **A new version** appends to the file, because a second file create under the same source key
   is refused with `409`, changed bytes or not. Find the earlier output
   (`registry__search_artifacts`, `kind` `output`, matching `source_id`) and read it with
   `registry__get_artifact`: `file_artifact_id` is under `metadata`. `registry__get_artifact` on
   that file gives its `etag` (top-level) and `metadata.path`, and `registry__get_file_content`
   its text. If the text equals the new text, write nothing. Otherwise call
   `registry__append_artifact_version` on the **file** with that `etag` as `if_match`, the same
   `metadata.path` and the new content, then step 2 again with the new ordinal. A matching source
   key makes it the output's next version under the same handle.

## What `relate_artifact` will refuse

See `skills/atlan-get-started/references/edges.md` - it covers `output`'s edge
alongside `agent`'s two.

Mode rules in full: `skills/atlan-record-outputs/references/output-modes.md`.
