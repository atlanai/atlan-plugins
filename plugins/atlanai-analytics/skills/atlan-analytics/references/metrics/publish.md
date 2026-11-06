# Publish (preview, yes, write)

Every write goes through the registry's MCP tools with the person's own sign-in. Nothing here
calls the registry's REST API, holds a token or runs a push script. Publish only what
`plan.json` marks `ok`.

## 1. Preview

Show the plan from `plan.json`, then ask:
- the counts to create, revise and reuse, for metrics and for saved queries, and the number of
  links;
- the target workspace;
- one sample metric body in full;
- the tested count against the parsed-only count;
- every held item, with its reason;
- every revision of a live metric, by name, with the fields that change;
- the exact link list: both ends, `rel_type` and `attributes`.

If publication intent is missing, offer these actions:
- `<slug> · context · publish to <workspace>`
- **Publish N metrics, Q saved queries and E links (Recommended)**
- **Show me 5 sample cards first**
- **Not yet**

When publication was already requested and this exact scope is authorized, continue without
asking for the same intent again. Obtain only missing authorization for the reviewed plan.

**Show me 5 sample cards first** has an immediate action: run
`python3 "<skill-dir>/scripts/project.py" --run-dir "<run-dir>" preview --limit 5 --format markdown`
and display its returned text in this response. Use `--kind sql_query` for saved queries.
If fewer than five exist, show all; if zero, show the reason. Do not publish, restart extraction
or ask for a workspace to show samples. Optional rich cards must fall back to this text.
**Show all** uses `--all`. **Not yet / Keep local** preserves the run with an unshared status.

A yes covers exactly this plan. A metric to **create** freezes its name forever, so the preview
names every create. If the person drops some, rerun `MINE check` without them before writing.

## 2. Metrics

- **Create:** `registry__create_artifacts` with kind `metric`, each body exactly as checked, in
  calls of at most 100 items and about 256 KB of JSON (the core plugin's **Batches** rule in
  `calling-tools.md`): about 25 full metric definitions to a call. Save each returned id against
  its `source_id` in `<context-dir>/published.json`.
- **Revise:** append a new version to the matched artifact (`match_id`), as
  [appending a revision](#appending-a-revision) below says. Keep its `name` and `display_name`.
  Raise `definition.version` by one over the live one.
- **Reuse:** nothing to write. Record the live id.
- A `409` on create means the name is taken. Report it and hold the item. Never rename to get
  around it.

Never archive, and never rename.

### Appending a revision

The one procedure for every new version of a metric or saved query, here and in an improve
round. Definitions are immutable within a revision, and `registry__patch_artifact` cannot edit
either one. Read the current head with `registry__get_artifact` (no release selector) and keep
its `etag`, then call `registry__append_artifact_version` with:

- `artifact_id`: the existing metric or saved-query ID;
- `if_match`: the exact head ETag reviewed;
- `metadata`: the complete `definition`, preserved `source_version` and `source_metadata`, and
  `auto_release` as the caller says (an improve round sends `false`); omit line fields such as
  `name` and `workspace_id`;
- `idempotency_key`: one unique key for this reviewed change, reused with identical arguments
  only to recover an uncertain write.

Do not copy server-owned hashes or validation receipts. Confirm the head still matches the
reviewed base before appending. A metric's positive-integer `definition.version` must never
decrease; execution-input changes require a higher value. A usage-note-only query change needs
no metric semantic version. Read back the same artifact ID and new ordinal. A stale token
refuses the write: re-read and reconcile instead of blindly retrying.

## 3. Saved queries

The same three actions, with kind `sql_query`.

Writes follow the workspace's release policy for each channel:
- under `auto`, each new revision is published;
- under `manual`, it stays unreleased;
- a deliberate draft can send `auto_release: false` where the policy permits.

A producer's `verified` claim does not decide publication. Untouched queries get no backfilled
releases. Record the actual release id, channel and version id of each published query, and
never assume a create published.

For a write that stays unreleased, publication happens in Metric Studio, or through an
authenticated registry release client when publication is authorized. Discover that capability
and never invent an MCP release tool. If none exists, leave publication pending and say that
agents can't read those drafts yet.

## 4. Links

Only after both ends exist, and only the approved list:
1. Resolve each end to its artifact id from `published.json` or the plan's `match_id`. Confirm
   both are in the workspace. Hold any link whose end is missing or ambiguous, and never guess
   by name. Metrics and saved queries can have the same name: index endpoints by kind and
   source identity (or kind and name within the reviewed workspace), never by name alone.
2. Read `registry__get_kind_schema` for each source kind, and write only the relation types and
   target kinds it declares.
3. Read the existing links first, including the `depends_on` links the metric posthook derives.
   A link that matches on source id, target id and `rel_type` is reused unchanged. One whose
   metadata conflicts is held for review.
4. Write each missing link with `registry__relate_artifact`, passing `artifact_id`,
   `subject_id`, `rel_type`, and the link's `attributes` as attributes (`why`, `relation`,
   `confidence`). For `source_of`, the saved query is the `artifact_id`.
5. Read each one back, and record which links were created, reused and held.

Never derive links from other workspace artifacts, and never write workspace-wide.

## 5. Verify

Count by kind in one call: `registry__get_artifact_counts` with the `workspace_id` and
`group_by` `kind` (on a gateway without it, `registry__list_artifacts` with `limit` 1 per kind
and read `page.total`). Then `registry__search_artifacts` for one sample
question with kind `sql_query`. It should come back near the top. Report the counts at the
workflow's STOP gate.
