---
description: Record a deliverable this run produced as an Atlan `output`.
argument-hint: "[file-path-or-url] [output name]"
---

Record $1 as an Atlan `output` named "$2".

First decide the mode:

**The deliverable is a local file** → `mode: "upload"`. Upload it first
(`registry__request_file_upload` → `PUT` → `registry__create_artifact kind:file`) and keep **both**
the `file_...` handle and its `version_ordinal`. Then create the output with
`file_artifact_id` and `file_version_ordinal`, and no `external_url`.

**The deliverable is hosted elsewhere** → `mode: "link"` with `external_url`, and no pin
fields. The URL must be absolute `http`/`https` with no userinfo and no credential in the
query or fragment - a presigned URL is refused. If it is behind a credential, upload it
instead.

Then attribute it: set `subject_kind` (`agent` or `harness`) and `subject_id` **together**
in `metadata` - half a pair is a 400 - plus the `session_...` handle in `metadata.session_id`
if this came out of a recorded run.

Watch the two names that collide on this call: `metadata.session_id` is the run, the
top-level `session_id` is your MCP session; `metadata.subject_id` is the producer, while
`subject_id` as a tool argument on `create_artifact_linked` is a relationship target.

To also record which skill it drew on, use `registry__create_artifact_linked` with
`rel_type: "used_skill"` pointing at the skill - that is the only edge `output` declares.

Full detail: `skills/atlan-record-outputs/SKILL.md`.
