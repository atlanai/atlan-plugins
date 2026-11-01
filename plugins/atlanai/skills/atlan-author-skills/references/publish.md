# Authoring skills in Atlan

A skill is a bundle of files. Small text bundles can be sent inline; the server builds the `.zip`. Every property the registry shows you - the summary, the
license, the file manifest, the structural counts - is derived from the bundle by the
create hook. None of it is yours to send. For larger or binary bundles, upload the ZIP and pass its file handle.

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

## Read the kind first

```
registry__get_kind_schema {
  "kind": "skill",
  "session_id": "mcp_session_01J...",
  "rationale": "checking which skill properties are writable before uploading"
}
```

Worth doing here for the opposite of the usual reason: it tells you what **not** to send.
Almost every `skill` property is hook-derived, and sending one is either ignored or a
refusal.

## Small text skills: send files inline

When the create tool's schema includes `files`, prefer:

```json
{
  "skills": [{
    "name": "deploy-preview",
    "workspace_id": "workspace_...",
    "files": {
      "SKILL.md": "---\nname: deploy-preview\ndescription: Deploy a preview.\n---\nFollow references/deploy.md.\n",
      "references/deploy.md": "Build, deploy, and report the preview URL."
    }
  }]
}
```

Pass these arguments to `extension__skill__create` using your client's session and
rationale rails. Supply exactly one of `files` or `bundle_file_id` for each item.
The entire batch allows 256 KiB of UTF-8 content and 128 inline files. Root
`SKILL.md` is required. Use relative slash-separated paths (up to eight components,
1024 bytes); traversal, empty components, backslashes, colons and control characters
are refused. Inline files are regular text files, mode 0644. Use the ZIP flow below
for binaries, executable files, larger content, or a server whose schema lacks `files`.
Do not include secrets: MCP arguments may be retained in audit records.

Inline creation uses the same workspace permissions, bundle validation, scan and
publication gate as ZIP creation. Read every result slot; creation does not mean
publication or a passed scan. Match workspace, source_type and source_id when
appending a version.

## 1. Write the bundle

The minimum viable bundle is one file:

```
SKILL.md          <- at the ARCHIVE ROOT, not inside a folder
```

with frontmatter:

```yaml
---
name: deploy-preview
description: Deploy a branch to a preview environment and report the URL. Use when asked to put a branch somewhere reviewable.
---
```

Then a non-empty Markdown body. Three rules do most of the rejecting:

- **`SKILL.md` sits at the root of the zip.** If your zip has a top-level directory, the
  create is refused with "skill bundle missing SKILL.md at root". Zip the *contents*, not
  the folder.
- **`name` is lowercase `a-z`, `0-9` and hyphens**, no leading or trailing hyphen, at most
  64 characters.
- **`description` is required and non-empty.** It is what a model reads to decide whether
  to use the skill, so write it as a trigger rather than a title: say when to reach for
  this, not what it is called.

Longer material goes in files beside `SKILL.md` and is linked from the body - that is the
progressive-disclosure shape, and it is why the body has a 500,000-character ceiling you
should never approach.

The full frontmatter contract, every optional key and every size limit:
`skills/atlan-author-skills/references/bundle-format.md`.

## 2. Upload the zip

The same three steps as any upload - reserve, `PUT`, register:

```
registry__request_file_upload { "size_bytes": <zip size>, "session_id": "...", "rationale": "..." }
→ { "upload_url": ..., "upload_key": ..., "is_presigned": false, ... }
```

`PUT` the whole zip to the authenticated API-host `upload_url` with your Authorization
header. The URL is root-relative: resolve it against the API origin you called the tool on.
Start the PUT within `expires_in_seconds` (900 seconds); an expired or unissued ticket returns
400 with instructions to reserve a fresh destination. A transfer started in time may finish
and its completed bytes may be registered afterward.

During rollout an older server may still return `is_presigned: true`. Such a storage URL
carries its own authorization: never send your API bearer to it. Current servers return false.
If your connector keeps its bearer private, use inline `files` for small text skills instead
of trying to extract its credentials into a shell.

```
registry__create_artifact {
  "kind": "file",
  "metadata": {
    "name": "deploy-preview.zip",
    "workspace_id": "workspace_01J...",
    "path": "bundles/deploy-preview-2026-09-04.zip",
    "upload_key": "<from step 1>"
  },
  "session_id": "...", "rationale": "..."
}
→ { "id": "file_01J...", ... }
```

Give each bundle a **unique `path`**: a file's path is its identity in its workspace, and a
second upload to the same path is a 409. Dating or versioning the path is the easy way out.

Details and failure modes: `skills/atlan-content/references/upload.md`.

## 3. Create the skill

```
extension__skill__create {
  "skills": [
    {
      "name": "deploy-preview",
      "workspace_id": "workspace_01J...",
      "bundle_file_id": "file_01J...",
      "description": "Deploy a branch to a preview environment.",
      "source_id": "platform/skills/deploy-preview"
    }
  ],
  "session_id": "mcp_session_01J...",
  "rationale": "publishing the deploy-preview skill from the uploaded bundle"
}
→ { "skills": [ { "index": 0, "status_code": 201, "skill": {...}, "error": null } ] }
```

## 4. Read the slots, not the call

The result carries **one slot per input, in order, each with its own status**. A `200` on
the call says the batch was processed, not that every skill landed.

Retry only the failed slots. Resending the whole batch re-runs the ones that worked, and
what that does is the next section.

A slot with `409` and an `existing_artifact_id` means the bundle is byte-identical to what
is already at the head of that line - nothing changed, and nothing needed to.

## Reuse the existing source identity to append a version

A skill's identity is `(workspace_id, source_type, source_id)`. `source_type` accepts
`local`, `git`, `atlan` or `external`, and defaults to `local`. `source_id` defaults to
`name` when omitted.

To update an existing skill, read it first and send its exact `workspace_id`, `source_type`
and `source_id`. Changed bundle content then appends a version, subject to the caller's
update permission. Compare `version_ordinal` in the slot to confirm the new version.
A matching name with a different source identity can return `409`; retrying it unchanged
will not resolve the conflict.

Set a distinct `source_id` for a distinct source object, and use a distinct skill name
for a separate line in the same workspace.

## `registry__create_artifact` cannot create a skill

It will refuse. A skill is its bytes and the generic bulk create route is JSON-only, so
every item would arrive with an empty body. Three steps then `extension__skill__create` is the only
route, and that is why `extension__skill__create` exists at all when almost nothing else has a
per-kind create.

## To change a skill

Two different verbs depending on what changed - and one of them cannot do what you might
expect. See `skills/atlan-author-skills/references/update-a-skill.md`.

## Reading a skill before you change it

A new version means editing the bundle that exists, not writing a fresh one from the skill's
description. `registry__get_artifact` gives you `metadata.files`; `registry__get_file_content` on each
entry's `file_id` and `file_version_ordinal` gives you the text. The `atlan-content`
skill this gateway serves has the loop and its traps.

## If you created more than one artifact

**Nothing is joined automatically.** A skill and an agent uploaded in the same conversation
are two unrelated rows until you say otherwise.

- Creating the *other* artifact anyway? Use `registry__create_artifact_linked` - the create
  and the edge in one call. It cannot create a skill (a skill is its bytes), so upload the
  skill first and link from the other end.
- Already created both? `registry__relate_artifact`.
- Either way, confirm with `registry__get_related_artifacts`.

**The direction is the trap.** An edge is declared by its *source* kind, and `skill` may only
point at another `skill` - so an agent-to-skill join is created from the **agent**
(`uses_skill`), and an output-to-skill join from the **output** (`used_skill`). Reading only
the five skill edge types and concluding no such join exists is the usual mistake.

The full cross-kind table, the refusals, and why a file never joins a skill by an edge:
`skills/atlan-author-skills/references/skill-edges.md`.
