# Publish within the selected task scope

When only explaining the next publication step, use one short paragraph: counts, selected
workspace, scoped authorization, then publication and readbacks under the release policy.
Omit helper command blocks unless commands were requested. Do not turn the explanation into
another approval request.

Every write goes through the registry's MCP tools with the person's own sign-in. Nothing here
calls the registry's REST API, holds a token or runs a push script. Publish only what
`plan.json` marks `ok`. The checker enforces `publication-holds.json` for engine exports.
Preserve the hold manifest and source receipt; never reintroduce held bodies or links, infer
status from prose, or delete evidence to make a plan pass. Repair through the engine gates
and export a new context. A held-only export stops with its reasons and sends no write.

## 1. Preview

Before previewing the plan, follow [existing-work reconciliation](reconciliation.md). Read
its review requests and preserve coverage limitations in the handoff, including when the
final plan reuses every artifact. A reusable total does not imply every proposed cut is covered.

For setup runs, first show one actual sample from `project.py experience --format chat`
in an assistant message if none has been displayed; details remain available on request. A file link, tool result or counts alone
does not count. This also applies when every artifact is being reused; no extra approval
is required for read-only reuse. Show changed definitions again after reconciliation.

Review the plan from `plan.json`. Keep the detailed bodies and link list in the private
preview file; show a short progress summary with:
- the counts to create, revise and reuse, for metrics and for saved queries, and the number of
  links;
- the target workspace;
- one representative metric name and definition summary;
- the tested count against the parsed-only count;
- every held item, with its reason;
- every revision of a live metric, by name, with the fields that change;
- link counts and relationship types. Keep both ends and full attributes in the private preview.

Use the project conventions' authorization record. Check it with the shared
`scripts/analytics_flow.py --authorization <project>/authorization.json --context <context-dir>
--workspace <workspace-id> --source <original-asset-identity>` helper. Repeat `--source` for
multiple selected assets. It reads the checked plan and all three body files, counts revisions
from checked actions and returns a plan ID bound to those exact bodies, source identities and
target. Record that ID only when the user approves this concrete plan. The helper returns:

- `continue`: this publication is the requested outcome in the selected workspace, or this
  exact plan was already approved. State the counts and target, then write without waiting
  for a second yes.
- `deferred`: the person requested drafts or a preview. Save the preview and stop.
- `needs_context` or `needs_confirmation`: resolve the named missing target, changed scope,
  unauthorized revision or explicit manual-review checkpoint once, then record the decision.

A checked plan is not authorization by itself. The source and workspace must match the trusted
conversation record. If the person drops items, rerun `MINE check` and keep the new plan within
that scope. Existing producer logic, batching, ETags, idempotency, retries and release readbacks
below are unchanged. Never ask again for file reads, or because the publication phase started.

**Show me 5 sample cards first** has an immediate action: run
`python3 "<skill-dir>/scripts/project.py" --run-dir "<run-dir>" preview --limit 5 --format markdown`
and display its returned text in this response. Use `--kind sql_query` for saved queries.
If fewer than five exist, show all; if zero, show the reason. Do not publish, restart extraction
or ask for a workspace to show samples. Optional rich cards must fall back to this text.
**Show all** uses `--all`. **Not yet / Keep local** preserves the run with an unshared status.


## 2. Metrics

- **Create:** `registry__create_artifacts` with kind `metric`, each body exactly as checked, in
  calls of at most 100 items and about 256 KB of JSON (the core plugin's **Batches** rule in
  `calling-tools.md`): about 25 full metric definitions to a call. Save each returned id against
  its `source_id` in `<context-dir>/published.json`.
- **Revise:** append a new version to the matched artifact (`match_id`), as
  [appending a revision](#appending-a-revision) below says. Keep its `name` and `display_name`.
  The checked body already carries `definition.version` one above the reviewed live version.
  Do not change it after plan approval.
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
3. **`depends_on` is the metric app's to write**, from each metric's `definition.depends_on`,
   after the metric is written. Never create one with `registry__relate_artifact`: an edge you
   write first carries no `derived_from` marker, so the app never removes it when the
   definition later drops that input. Read each metric's edges with
   `registry__get_related_artifacts`. For a `depends_on` in the approved list that is missing,
   call `extension__metric__resync_lineage` (with `metric_id`) once and read again; if it is
   still missing, hold it and report it. Once the derived edge exists, restate it with `registry__relate_artifact`
   passing only `attributes` (`why`, `relation`, `role`, `confidence`): the gateway merges them
   into the derived edge and keeps its marker.
4. For `source_of` and `uses_metric`, the saved query is the `artifact_id`. Read its existing
   links first. A link that matches on source id, target id and `rel_type` is reused unchanged;
   one whose metadata conflicts is held for review. Write each missing one with
   `registry__relate_artifact`, passing `artifact_id`, `subject_id`, `rel_type`, and the
   link's `attributes` (`why`, `confidence`).
5. Read each one back, and record which links were created, reused, restated and held.

Never derive links from other workspace artifacts, and never write workspace-wide.

## 5. Verify

Count by kind in one call: `registry__get_artifact_counts` with the `workspace_id` and
`group_by` `kind` (on a gateway without it, `registry__list_artifacts` with `limit` 1 per kind
and read `page.total`). Then `registry__search_artifacts` for one sample
question with kind `sql_query`. It should come back near the top. Report the counts at the
workflow's STOP gate.
