# Recording a share

Step 3 of `atlan-share`: one approval, then the writes. Step numbers in "go to step 4" are
the skill's.

Every share carries a **source key**, so the same thing shared again becomes a new version of
one output rather than a second copy. Use:

- `source_type`: `local`;
- `source_id`: `<repository or folder name>/<path from its root>`, such as `analytics/reports/churn-q3.md`,
  or `claude/<title in kebab case>` for content with no file. At most 512 characters.

**First, look for an earlier share.** Call `registry__search_artifacts` with `kind` `output`, the
title as `query` and the chosen `workspace_id`. A hit is the earlier share when its `source_id`
equals this share's, or when the user asked for a new version of it; then use its `source_id`.
Anything else is a different output.

Then show the user what will be written, in at most four lines: the title, a two-sentence
summary you wrote, the workspace in the words above, and whether it is new or a new version.
That is the approval. A request that already names the audience and says to go ahead is the
approval too.

**A first share** writes the bytes as a `file`, then the `output` that pins it:

1. **The file.** Text up to 64 KiB goes inline:

   ```
   mcp__call_write_tool {
     "name": "registry__create_artifact",
     "arguments": {
       "kind": "file",
       "content": "<the file's text>",
       "metadata": { "name": "churn-q3", "path": "churn-q3.md", "workspace_id": "workspace_…",
                     "source_type": "local", "source_id": "analytics/reports/churn-q3.md" }
     },
     "session_id": "mcp_session_…",
     "rationale": "Storing the churn report the user asked to share with Platform Engineering."
   }
   ```

   Larger or binary files follow the upload recipe in `skills/atlan-content/references/upload.md`,
   with the same `source_type` and `source_id`. Keep the returned `id` and `version_ordinal`.
   That recipe needs a shell and a bearer; Claude chat and Cowork have neither. There, decide
   before the approval: offer the text inline, or a `mode: "link"` output when it is hosted
   elsewhere, and say that a binary needs the Atlan CLI or the dashboard's upload.
2. **The output**, with the same source key and the file pin:

   ```
   mcp__call_write_tool {
     "name": "registry__create_artifact",
     "arguments": {
       "kind": "output",
       "metadata": { "name": "churn-q3", "display_name": "Q3 churn report",
                     "description": "<the two-sentence summary>", "workspace_id": "workspace_…",
                     "mode": "upload", "file_artifact_id": "file_…", "file_version_ordinal": 1,
                     "source_type": "local", "source_id": "analytics/reports/churn-q3.md" }
     },
     "session_id": "mcp_session_…",
     "rationale": "Recording the churn report as an output its workspace can open."
   }
   ```

   Something hosted elsewhere is `mode: "link"` with `external_url` and no file. Add `project_id`
   when the settings or the user name one. Producer, session and skill edges follow
   `skills/atlan-record-outputs/references/record.md`; add a `used_skill` edge for each registry
   skill this session loaded.

**A new version** appends to the earlier share's file, because a second file create under the
same source key is refused with `409`, changed bytes or not:

1. `registry__get_artifact` on the earlier output gives its `metadata.file_artifact_id`;
   `registry__get_artifact` on that file gives its `etag` (top level, beside `id`) and
   `metadata.path`.
2. `registry__get_file_content` with that `file_id` reads the current text. If it equals what is
   being shared, nothing changed: write nothing, and go to step 4 with the current version. The
   registry does not catch this for you; an append of identical bytes makes a duplicate version.
3. `registry__append_artifact_version` on the file, with the `etag` as `if_match`, the same
   `metadata.path` and the new `content` (or `metadata.upload_key` for staged bytes). Keep the
   returned `version_ordinal`.
4. `registry__create_artifact` for the output exactly as in a first share, pinning the new file
   ordinal. Its matching source key makes it the output's next version, under the same handle.

A `409` on a first share's file create names its cause. The same source key means a file
already holds it without an output pinning it, usually a share that stopped halfway: find it with
`registry__search_artifacts` (`kind` `file`, the file name as `query`, the same workspace), match
the `source_id`, and continue from step 2 of a new version. The same `path` with another source
key is a different file: give this one a distinct path, such as the `source_id`'s own path.

Read the result: a `version_ordinal` above 1 on the output means an earlier share was updated,
and the reply says so. A `422` with `field` names the property to fix. A `400` or `422` that
names no field means the metadata does not match the kind: read `registry__get_kind_schema` for
that kind and fix it. A stale `if_match` means someone else added a version: read the file
again and start the new version over. Any other refusal goes to
`atlan-troubleshoot`; never retry a create with a new source key to get past it.
