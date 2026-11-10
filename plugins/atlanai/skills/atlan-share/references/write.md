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

**The write is one call** for a first share and a new version alike. Text up to 64 KiB goes
inline as `file`:

```
mcp__call_write_tool {
  "name": "extension__agent__publish_output",
  "arguments": {
    "metadata": { "name": "churn-q3", "display_name": "Q3 churn report",
                  "description": "<the two-sentence summary>", "workspace_id": "workspace_…" },
    "source_type": "local",
    "source_id": "analytics/reports/churn-q3.md",
    "file": { "path": "churn-q3.md", "content": "<the file's text>" }
  },
  "session_id": "mcp_session_…",
  "rationale": "Sharing the churn report with Platform Engineering as an output its workspace can open."
}
```

- **Larger or binary files** follow the upload recipe in `skills/atlan-content/references/upload.md`
  with the same `source_type` and `source_id`, then pass the returned `id` and `version_ordinal`
  as `file_pin` in place of `file`. That recipe needs a shell and a bearer; Claude chat and Cowork
  have neither. There, decide before the approval: offer the text inline, or `external_url` when
  it is hosted elsewhere, and say that a binary needs the Atlan CLI or the dashboard's upload.
- **Something hosted elsewhere** is `external_url` with no file.
- Add `project_id` to `metadata` when the settings or the user name one. Producer and session
  fields follow `skills/atlan-share/references/record.md`. Add one `edges` entry,
  `{"rel_type": "used_skill", "to_id": "skill_…"}`, for each registry skill this session loaded.

**A new version** is the same call with the earlier share's `source_id` and the new content. It
appends to the share's file, or writes nothing to it when the text is unchanged, and makes the
output's next version under the same handle.

Read the result: `created: false`, or a `version_ordinal` above 1 on `output`, means an earlier
share was updated, and the reply says so; `file.written: "unchanged"` means the text was the same.
`output: null` with `output_error` means the file was saved and the output was not: fix what the
error names and send the same call again, which pins the saved file. A `422` with `field` names
the property to fix. A `400` or `422` that names no field means the metadata does not match the
kind: read `registry__get_kind_schema` for `output` and fix it. Any other refusal goes to
`atlan-troubleshoot`; never retry with a new source key to get past it.

**When the gateway has no `publish_output`,** record in separate calls as
`skills/atlan-share/references/record.md` describes under its older route: the `file`
by `registry__create_artifact` (or `registry__append_artifact_version` on the earlier share's
file for a new version, after comparing its text), then the `output` by
`registry__create_artifact` with the same source key and the file pin.
