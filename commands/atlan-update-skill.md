---
description: Publish a new version of an existing Atlan skill, or patch its name and description.
argument-hint: "[skill-handle-or-name]"
---

Update the Atlan skill $ARGUMENTS.

First decide which change this is:

**The content changed** → publish a new version. Repeat the full authoring recipe with the
**same identity** (`source_id`, or `name` in the same workspace if there is no `source_id`).
That appends a version; it does not create a sibling. Confirm by reading `version_ordinal`
in the returned slot.

**Only the name or description changed** → `registry__patch_artifact` with just those keys.

Do **not** try to patch `files`, or any derived property (`summary`, `license`,
`allowed_tools`, the structural counts). A patch carries no body, so it is refused with a
message telling you to upload a new version. Content is immutable per version.

If a re-upload reports success but no new version appeared, the bytes were identical - that
is an idempotent no-op, not a failure.

Full detail: `skills/atlan-author-skills/references/update-a-skill.md`.
