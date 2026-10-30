---
name: atlan-read-content
description: Read the actual contents of a file or a skill held in Atlan - a skill's SKILL.md and its references, an uploaded document, a dataset's README. Use when asked what a registry skill does, to open or read a file by its handle, to review a skill before updating it, or when a search turned up an artifact and the next question is what is inside it.
license: Apache-2.0
---

# Reading content out of Atlan

Finding an artifact and reading it are two different calls. `registry__search_artifacts` and
`registry__get_artifact` return **metadata** - a skill's name, its summary, the list of files
in its bundle and how big each one is. None of that is the content. `registry__get_file_content` is
what returns the content.

Do not infer what a skill does from its file names, and do not substitute a different skill
that happens to be in your toolset. If you cannot read it, say so.

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

2. **`rationale` is required on every tool call.** A first-person sentence about why you are
   making *this* call, for a human reading the audit trail.

## Reading a skill

Two hops. The skill's manifest gives you the handles; the handles give you the bytes.

### 1. Get the skill

```
registry__get_artifact {
  "artifact_id": "skill_01m1pb66fxek99t3fxjv0mqeg2",
  "session_id": "mcp_session_01J...",
  "rationale": "reading the slide-design skill before applying it"
}
```

Its `metadata.files` is the pinned manifest - one entry per file in the bundle:

```json
{ "path": "SKILL.md", "file_id": "file_01K...", "file_version_ordinal": 3,
  "size_bytes": 17966, "media_type": "text/markdown" }
```

If you only have a name, `registry__search_artifacts { "query": "slide design",
"kind": "skill" }` first, and read the artifact by the handle it returns.

### 2. Read `SKILL.md`, then only what it cites

```
registry__get_file_content {
  "file_id": "file_01K...",
  "version_ordinal": 3,
  "session_id": "mcp_session_01J...",
  "rationale": "reading slide-design's SKILL.md to follow its instructions"
}
→ { "file_id": "...", "version_ordinal": 3, "path": "SKILL.md",
    "media_type": "text/markdown", "size_bytes": 17966, "content": "---\nname: ..." }
```

**Read the entry point first and stop.** A bundle lists its files together but they are not
one document: `SKILL.md` is the instructions, and the files under `references/` are material
it points at when it needs them. Pulling all four up front spends context on pages you may never reach.
Open a reference when `SKILL.md` tells you to.

**Pass the ordinal the manifest gave you.** A manifest is a snapshot of exact versions. Omit
`version_ordinal` and you get the file's latest, which may be bytes that snapshot never
referred to.

## Reading a standalone file

Same call, one hop - a `file_…` handle is all it needs:

```
registry__get_file_content { "file_id": "file_01K...", "session_id": "...", "rationale": "..." }
```

Omit `version_ordinal` for the latest. `registry__get_artifact` on the `file` handle shows
you what versions exist and what the file is.

## What it refuses, and what each refusal means

| Refusal | What happened |
|---|---|
| `not found` | The file does not exist, **or** you cannot read it, **or** it has been withdrawn. Deliberately the same answer for all three - the refusal is not a way to find out which. |
| over the byte ceiling | The file is larger than a tool result may carry. The message gives the actual size. It is never truncated: a partial file that looks whole is worse than no file. |
| not valid UTF-8 | An image, an archive, a binary. Content rides in a tool result as text or not at all. |

For the last two the message names `GET /file/v1/files/{file_id}/content`, the HTTP route with
no such limits. Use it if you have a way to make HTTP requests with your bearer token; if you
do not, say what you could not read rather than guessing at it.

## Where the content goes

Into your context. This gateway hands back the text; it cannot write to your disk. If your
harness has a filesystem and you want the file saved, that is your own write step, and say
where you put it.

## Related

- `skills/atlan-read-content/references/reading-a-skill.md` - the whole loop worked through,
  and the mistakes that make it look broken.
- `atlan-upload-content` - the other direction.
- `atlan-author-skills` - reading a skill before publishing a new version of it.
