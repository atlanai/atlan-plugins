# Updating a knowledge bundle

First decide which change this is. They take different tools, and the wrong one is refused.

## The content changed: apply a batch

Read `registry__get_artifact` for the bundle's head `version_ordinal` and `metadata.files`.
Use `registry__get_file_content` with the manifest's pinned ordinal for any file you need.

```json
{
  "bundle_id": "knowledge_01J...",
  "base_version_ordinal": 12,
  "changes": [
    {"op": "write", "path": "memory/MEMORY.md", "content": "---\ntype: Memory\n---\nUpdated memory.\n"},
    {"op": "move", "from": "draft.txt", "to": "notes/final.txt"},
    {"op": "delete", "path": "obsolete.txt"}
  ],
  "session_id": "...", "rationale": "I am saving the decisions from this session."
}
```

Call `extension__knowledge__apply_changes`. One batch produces one bundle version. Supply
1–100 operations, at most 65,536 UTF-8 bytes per write and 1 MiB total new content. Every path
may participate once (including both move ends). A move requires an existing source and an
unoccupied destination. At least one file must remain. Non-reserved `.md` files require valid
OKF frontmatter with a nonempty `type`; reserved index/log files and other UTF-8 files are allowed.

Changes to unrelated paths since the base version survive. If a touched path changed, the
entire batch refuses with `412`; re-read and resolve it. A competing commit can trigger up to
two automatic retries. A batch already fully reflected in head is an authorized no-op, with
`unchanged: true`; this is content convergence, not a durable operation receipt. Untouched
files retain exact pins. Removed concepts are archived and historical file bytes remain.
Re-adding an archived path starts a new concept identity without requiring restore permission.

`extension__knowledge__write_concept` remains compatible for one Markdown concept. It uses
the same editor and requires a concrete `if_match` bundle etag.

**Create never updates a bundle.** A duplicate name or source identity refuses with `409`.
The generic registry archive append remains available for deliberate complete-tree imports,
and replaces the full tree; do not use it for routine memory edits. The current registry can
also refuse a tree that exactly matches a historical version with `409`; head remains intact.
Re-read before choosing a meaningful content change.

## Only the name or description changed: patch it

```
registry__patch_artifact {
  "artifact_id": "knowledge_01J...",
  "patch": { "description": "Tables, metrics and glossary for retail, refreshed weekly." },
  "session_id": "...", "rationale": "..."
}
```

A patch carries no archive. Do **not** patch `files`, `concept_count`, `total_bytes` or
`upload_sha256`: they describe the archive the current version ingested, so a patch that
changes them is refused ; use `apply_changes` for content edits.

## Retiring a bundle

`registry__archive_artifact` with the bundle's id archives the bundle and its concepts. The
`file` artifacts holding the bytes are left in place, because they are shared content.
