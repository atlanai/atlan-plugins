---
description: Package the current work as a new Atlan skill and publish it.
argument-hint: "[skill-name] [one-line description]"
---

Publish a new Atlan skill named $1, described as "$2".

Steps:

1. `mcp__initialise` if you have no `session_id` yet.
2. `registry__list_workspaces` for the `workspace_id`. If there is more than one, ask which.
3. Write `SKILL.md` with frontmatter `name: $1` (lowercase, hyphens only) and a
   `description` that says **when to use it**, not just what it is. Put long material in
   `references/*.md` and link to it from the body.
4. Zip the *contents* of the directory so `SKILL.md` is at the archive root.
5. `extension__file__request_upload` → `PUT` the zip → `registry__create_artifact kind:file` with a
   unique `path`.
6. `extension__skill__create` with `name: "$1"`, the `workspace_id`, and `bundle_file_id` from step 5.
   Set `source_id` if this name might already be taken by a different skill.
7. Read the per-skill slot in the result, not the call status.

Report the resulting `skill_...` handle and its `version_ordinal`.

If anything refuses, read `skills/atlan-author-skills/SKILL.md` and the references beside
it.
