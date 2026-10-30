# Reading a knowledge bundle

There is no knowledge-specific read tool. The registry's own tools read a bundle, because the
bundle is a manifest of ordinary `file` artifacts.

## Find it

```
registry__search_artifacts {
  "query": "retail revenue metric",
  "kind": "knowledge_concept",
  "session_id": "...", "rationale": "..."
}
```

Each concept is its own `knowledge_concept` artifact, and its markdown body is in the search
corpus, so searching concepts usually beats searching bundles. A concept's `metadata` carries
its `bundle_id`, `path`, `okf_type`, `tags` and `trust_tier`, and pins its bytes as
`content_file_artifact_id` + `content_file_version_ordinal`.

Search `kind: "knowledge"` to find whole bundles by name and description.

## Read the manifest

```
registry__get_artifact { "artifact_id": "knowledge_01J...", "session_id": "...", "rationale": "..." }
→ { "metadata": { "files": [ { "path": "tables/orders.md", "file_id": "file_01J...", "file_version_ordinal": 1, "concept_id": "knowledge_concept_01J...", ... } ], ... } }
```

`files` lists every path in this version, including non-concept files, each pinned to an exact
`file_id` and `file_version_ordinal`. It is **withheld** while the bundle is archived or
blocked: a caller who may not read the bytes may not read the pointers either.

## Read a document

```
registry__get_file_content {
  "file_id": "file_01J...",
  "version_ordinal": 1,
  "session_id": "...", "rationale": "..."
}
→ { "path": "tables/orders.md", "media_type": "text/markdown", "content": "---\ntype: ..." }
```

Always pass the pinned `version_ordinal`. Without it you read the file's latest version, which
may belong to a newer bundle version than the one you are reading.

The text is returned exactly as it was uploaded, frontmatter included. A document over 256 KiB,
or one that is not UTF-8, is refused rather than truncated; read it over HTTP instead.

## Over HTTP

The bundle is also a static tree at `/knowledge/v1/bundles/{bundle_id}/`, with the synthesized
`index.md` and `log.md`, and as whole-bundle downloads at `bundle.zip` and `bundle.tar.gz`.
Every bundle you can read is listed at `/.well-known/okf`.
