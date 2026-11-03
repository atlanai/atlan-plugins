# Authoring knowledge bundles in Atlan

A knowledge bundle **is** its `.zip`: a tree of markdown concept documents, each with a YAML
frontmatter block. Every property the registry shows you - the file manifest, the concept
count, the byte total - is derived from the archive by the create hook. None of it is yours to
send. Create from inline UTF-8 files, or zip and upload a local tree. Edit existing memory with `apply_changes`.

## Before your first call

Follow **Before your first call** in
`skills/atlan-get-started/references/calling-tools.md`: one session and its id on every call,
a first-person `rationale` on every call, a `workspace_id` on every create, and
`registry__get_kind_schema` before your first write to a kind.

## 1. Write the tree

One concept per `.md` file. Its concept ID is its path minus `.md`, so `tables/orders.md` is
the concept `tables/orders`. Every concept opens with frontmatter carrying a non-empty `type`:

```markdown
---
type: BigQuery Table
title: Orders
description: One row per customer order.
tags: [sales, revenue]
---
# Schema
| order_id | STRING |
```

Three rules do most of the rejecting, and **one bad document refuses the whole bundle** -
nothing is written:

- **Every non-reserved `.md` has a `---` frontmatter block that parses as a YAML mapping.**
- **That block carries a non-empty `type`.**
- **A `resource`, if present, is an absolute `http(s)` URL** with no userinfo and no
  credential-bearing query parameter.

`index.md` and `log.md` are reserved anywhere in the tree and are not concepts. Leave them out
and they are synthesized on read; author them and yours wins. Non-markdown files ride along
without becoming concepts.

The full frontmatter contract, the reserved names and every size limit:
[bundle format](bundle-format.md).

## 2. Choose inline files or an uploaded archive

An agent without a shell or filesystem passes inline files directly. Do not manufacture a ZIP
or base64 content. Supply exactly one of `files` and `bundle_file_id` for each bundle:

```json
{
  "bundles": [{
    "name": "claw-memory",
    "workspace_id": "workspace_01J...",
    "project_id": "project_01J...",
    "files": [
      {"path": "memory/MEMORY.md", "content": "---\ntype: Memory\n---\nRemember this.\n"},
      {"path": "assets/context.txt", "content": "Exact UTF-8 text"}
    ]
  }],
  "session_id": "...", "rationale": "I am creating project memory."
}
```

Send this to `extension__knowledge__create`. Inline trees require 1–100 files, at most 65,536
UTF-8 bytes each and 1 MiB total. Empty non-concept files are allowed. Validate the entire tree
before publishing. Paths are relative, at most 512 bytes and 16 levels, with no `.` or `..`
segments, backslashes, drive prefixes or synthesized archive names. Duplicate paths and a
file occupying another file's parent directory are refused.

For a local tree or plugin, use an uploaded ZIP instead:

Zip the **contents** of the bundle directory, so `tables/orders.md` is a path inside the zip
rather than `my-bundle/tables/orders.md`. The zip must be at most 16 711 680 bytes (16 MiB less
64 KiB); the decompressed tree under 64 MiB.

Then the three steps every upload takes - reserve, `PUT`, register:

```
registry__request_file_upload { "size_bytes": <zip size>, "session_id": "...", "rationale": "..." }
→ { "upload_url": ..., "upload_key": ..., "is_presigned": false, ... }
```

`PUT` the whole zip to `upload_url` with your `Authorization` header; the URL is root-relative,
so resolve it against the origin you called the tool on. `is_presigned` is `false` on today's
gateway; if a server ever answers `true`, send no `Authorization` header to that URL. A host
that never gives you the bearer, as Claude chat and Cowork do, cannot `PUT`: use inline `files`
above, and say that a tree too large for them needs the Atlan CLI.

```
registry__create_artifact {
  "kind": "file",
  "metadata": {
    "name": "retail-knowledge.zip",
    "workspace_id": "workspace_01J...",
    "path": "bundles/retail-knowledge-2026-10-21.zip",
    "upload_key": "<from the reservation>"
  },
  "session_id": "...", "rationale": "..."
}
→ { "id": "file_01J...", ... }
```

Give each upload a **unique `path`**: a second file at the same path is a 409. Dating the path
is the easy way out. Details and failure modes: `skills/atlan-content/references/upload.md`.

## 3. Create the bundle

```
extension__knowledge__create {
  "bundles": [
    {
      "name": "retail-knowledge",
      "workspace_id": "workspace_01J...",
      "bundle_file_id": "file_01J...",
      "description": "Tables, metrics and glossary for the retail domain."
    }
  ],
  "session_id": "mcp_session_01J...",
  "rationale": "publishing the retail knowledge bundle from the uploaded archive"
}
→ { "bundles": [ { "index": 0, "status_code": 201, "knowledge": { "id": "knowledge_01J...", ... }, "error": null } ] }
```

## 4. Read the slots, not the call

The result carries **one slot per input, in order, each with its own status**. A success on
the call says the batch was processed, not that every bundle landed. A refused bundle's slot
says why in `error.detail`: a handle you cannot read, an archive over the limit, or the rule a
path in the tree broke. A `429` or `503` slot is worth retrying as is; a `400` or `422` is not.
A malformed item refuses the whole call before anything is read, and the `detail` names the
field, as `bundles[3].bundle_file_id`.

Retry only failed slots. A duplicate create refuses with `409`; it never replaces an existing
bundle, even when two callers create the same name concurrently. If the result of a create is
uncertain, find and read the existing bundle before deciding what to do next.

## 5. Update memory through apply_changes

A name is an identity within a workspace (or set a stable `source_id`). **Do not call create
again to update a bundle.** Existing identities are refused. Read its `version_ordinal` with
`registry__get_artifact`, then batch writes, deletes and moves into one `apply_changes` call.
Unrelated concurrent edits are preserved; overlapping edits refuse the batch. Keep at least
one file. Full recipe and conflict handling: [update a bundle](update-a-bundle.md).

## Reading a bundle back

`registry__get_artifact` returns the bundle with its `metadata.files` manifest; each entry pins
a path to a `file_id` and `file_version_ordinal`, which `registry__get_file_content` reads.
Each concept is also its own `knowledge_concept` artifact, searchable with
`registry__search_artifacts`. The full read recipe: [read a bundle](read-a-bundle.md).

## What not to send

- **Derived manifest properties, `concept_count`, `total_bytes`, `upload_sha256`** - do not send
  these as metadata. Inline `files` is an input tree of path/content pairs, not a registry manifest.
- **`trust_tier` or `content_digest` on a concept** - recomputed from the document on every
  write; a value you send is discarded.
- **The archive as base64 in a tool argument.** Upload it.
