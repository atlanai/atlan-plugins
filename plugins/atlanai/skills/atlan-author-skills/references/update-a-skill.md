# Changing a skill

Two verbs, and they do not overlap. Picking the wrong one is a refusal in one direction and
a silent no-op in the other.

## The content changed → publish a new version

Repeat the whole authoring recipe with the **same identity**: `workspace_id`,
`source_type` and `source_id`. Read and reuse them from the existing skill;
matching only its name is insufficient.

That is an append, not a duplicate. The registry reconciles the create onto the existing
line, runs the same materialization a first create runs, and the previous version stays
resolvable at its own ordinal.

For small text bundles, send the complete `files` map to `extension__skill__create`
as in [publishing](publish.md#3-create).
For larger bundles, binaries or executable files, use [the ZIP path](publish-zip.md):

```
registry__request_file_upload → PUT → registry__create_artifact kind:file → extension__skill__create
```

with the identity unchanged. Check `version_ordinal` in the returned slot to confirm it
appended rather than started a new line.

**A byte-identical re-upload is refused with a `409`** whose slot carries
`existing_artifact_id`, the head it matches, and writes **no** new version. So "nothing changed"
has two causes worth telling apart:

- the bytes were identical (that `409`) - nothing needed to change;
- the `name` or `source_id` differed - you started a *new* line and the old skill is
  untouched.

## Only the envelope changed → patch it

```
registry__patch_artifact {
  "artifact_id": "skill_01J...",
  "patch": { "description": "..." },
  "session_id": "...",
  "rationale": "..."
}
```

Reaches `name` and `description`. That is it.

## What `patch_artifact` cannot do

**It cannot change a skill's content.** The patch carries no body, so the skill's update
hook takes its metadata-only branch and compares the `files` manifest against the stored
one. A patch that mentions `files` is refused:

> the skill `files` manifest is managed by the upload hook and cannot be edited via update;
> upload a new version to change content

Content is immutable per version, by design. There is no route that edits a published
version's bytes, and this extension deliberately declares no per-skill update tool.

## Merge semantics on the patch

- An **omitted** key is preserved.
- An explicit `null` clears the value.
- Nested objects merge key by key rather than being replaced wholesale.
- `workspace_id` cannot be patched. Moving a skill is a different operation.

## Removing a skill

`registry__archive_artifact { "artifact_id": "skill_01J...", ... }`.

Archiving frees the identity, so a later create under the same `name` or `source_id` starts
a fresh line rather than appending to the archived one.
