# Publish within the selected task scope

When only explaining the next publication step, use one short paragraph: counts, selected
workspace, scoped authorization, then publication and readbacks under the release policy.
Omit helper command blocks unless commands were requested. Do not turn the explanation into
another approval request.

For an explanation of an explicit pre-dispatch helper refusal, explain the result and next action.
Do not enumerate argument names or commands unless asked. For example: "Nothing was saved
yet. I'll send the same save another way, through the same Atlan connection. Your checked
metrics and workspace stay as they are. You don't need to change any settings."

Every write goes through Atlan Trident's MCP tools with the person's own sign-in. Nothing here
calls Trident's REST API, holds a token or runs a push script. Publish only what
`plan.json` marks `ok`. The checker enforces `publication-holds.json` for engine exports.
Preserve the hold manifest and source receipt; never reintroduce held bodies or links, infer
status from prose, or delete evidence to make a plan pass. Repair through the engine gates
and export a new context. A held-only export stops with its reasons and sends no write.

## Contents

- Contract preflight
- One connection, from inventory to readback
- 1. Preview
- 2. Metrics
- 3. Saved queries
- 4. Links
- 5. Verify
- 6. Move to another workspace

## Contract preflight

Follow the [publication contract](../../modules/metrics/contracts/README.md) for every
input route, including local files and demos through the same engine. Read
`registry__get_kind_schema` once per kind in this publication session and compare its
`properties.definition` with the checked-in metric and saved-query schemas. Reuse those
schema reads throughout the session. Hold affected writes on a mismatch and report the field;
never send an unsupported shape or bypass validation. Run `CONTEXT check` again on the exact
bodies after any repair. Keep the existing publication authorization and transport.

## One connection, from inventory to readback

A host can hold more than one Atlan connection (a plugin's own server, a claude.ai connector,
a second environment), each signed in to its own organization with its own personal
workspace. Read the live inventory, approve the plan and send every write through the same
connection. Before reconciling, read `registry__list_workspaces` with `limit: 1` on that
connection and confirm the target workspace is its own; record
`{"workspace_id": "<target>", "personal_workspace_id": "<from that read>"}` in the context's
`inventory.json`. `analytics_flow.py` refuses a plan whose inventory rows or record name
another workspace. If the connection that can write is a different one, read the inventory
again through it and get the plan approved again: an approval covers one workspace.

## 1. Preview

Before previewing the plan, follow [existing-work reconciliation](reconciliation.md). Read
its review requests and preserve coverage limitations in the handoff, including when the
final plan reuses every artifact. A reusable total does not imply every proposed cut is covered.

For setup runs, show the completed-collection review from
[the presentation flow](../../../analytics-build/references/setup/presentation.md) if it has not been displayed. A file link, tool result or counts alone
does not count. This also applies when every artifact is being reused; no extra approval
is required for read-only reuse. Show changed definitions again after reconciliation.

Review the plan from `plan.json`. Keep the detailed bodies and link list in the private
preview file; show a short progress summary with:
- "Ready to save to <workspace>: <N> new metrics, <N> updated and <N> already there, plus <N>
  saved queries."
- "Moving metrics and saved queries later means copying them and archiving the originals, so
  check <workspace> is the right place now."
- "For example, <metric>: <definition in plain words>."
- "<N> were tested on your data. <N> weren't tested."
- for every held item: "Held back: <name>, because <reason>." After the last one: "To fix one,
  tell me here and I'll rebuild it."
- for every revision of a live metric: "Updates <metric>: changes <fields>."

Fill every count as it reads: "1 new metric", "1 saved query", "1 was tested".

Use the project conventions' authorization record. Check it with the shared
`scripts/analytics_flow.py --authorization <project>/authorization.json --context <context-dir>
--workspace <workspace-id> --source <original-asset-identity>` helper. Repeat `--source` for
multiple selected assets. It reads the checked plan and all three body files, counts revisions
from checked actions and returns a plan ID bound to those exact bodies, source identities and
target. Record that ID only when the user approves this concrete plan. The helper returns:

- `continue`: the user approved this exact plan. State the counts and target ("Saving <N>
  metrics and <N> saved queries to <workspace>."), then write.
- `deferred`: the person requested drafts or a preview. Save the preview and stop.
- `needs_context`: resolve the named missing target, changed scope or unauthorized revision
  once, then record the decision.
- `needs_confirmation`: show the counts, target and every revision, ask once ("Save these to
  <workspace>?"), and record the
  plan ID in `approved_plans` only on a yes. A request to publish is not approval of a plan
  the user has not seen.

A checked plan is not authorization by itself. The source and workspace must match the trusted
conversation record. If the person drops items, rerun `CONTEXT check` and keep the new plan within
that scope. Follow the version checks, retries and release readbacks below. The approved
plan covers its file reads and publication scope.

**Show me 5 first** has an immediate action: run
`python3 "<build-dir>/scripts/project.py" --run-dir "<run-dir>" preview --limit 5 --format markdown`
and display its returned text in this response. Use `--kind sql_query` for saved queries.
If fewer than five exist, show all; if zero, show the reason. Do not publish, restart extraction
or ask for a workspace to show samples. Optional rich cards must fall back to this text.
**Show all** uses `--all`. **Keep it on this machine** preserves the run with an unshared status.


## 2. Metrics

### Send the prepared native batches

Use the same procedure in Codex and Claude Code. Send ordinary Atlan MCP requests using
the existing signed-in connection. File paths and local recording arguments stay on the machine.

```bash
python3 "<build-dir>/scripts/publication_batches.py" --context "<context-dir>" --authorization "<project>/authorization.json" --workspace "<workspace-id>" --source "<original-asset-identity>"
```

Repeat `--source` for each selected asset. The helper binds the checked bodies, holds and
target to the approved plan. Keep the returned manifest and receipt directory. Send
`preflight.name` and `preflight.arguments` through `mcp__call_read_tool` first. Wait
for its successful reply before claiming any batch. Its counts are the before side of
the final count check.

In the commands below, replace `NATIVE` with
`python3 "<build-dir>/scripts/publication_native.py" --run-dir "<run-dir>" --workspace "<workspace-id>" --manifest "<manifest>"`.

1. Run `NATIVE status` and use its `unsent_batches`, in order. For each index, run
   `NATIVE claim <index>`. Send the returned `name` and `arguments` to the write runner
   with the current session and rationale. Claim several indices in one command when the
   requests fit, for example `NATIVE claim 0 1`. Each request already contains the exact
   checked bodies. Preserve its JSON values, including SQL and workspace IDs. Send each
   batch once.
2. Save the native result JSON (or its MCP envelope) with
   `NATIVE capture --batch <index> --input "<saved-result-file>"` or pipe the JSON to the
   same command without `--input`. The helper records each returned slot. A reserved batch
   with a missing, truncated or refused result is uncertain. Check live state before any
   new write. Send the helper's `lookup_batches` to recover identities, verify their exact
   releases, and never replay the original batch. A conflict is held for review. A proven
   absent failed item can be considered only in a new checked and approved plan.
For fewer local calls, save several replies together with `NATIVE advance --input "<file>"`.
Pass `{"results":[{"batch":0,"reply":...},{"request":...,"reply":...}]}` using the actual
batch indices, read requests and replies. The helper saves them and returns the next phase.

3. Run `NATIVE status` after the create phase if you used individual captures. Send
   `verification_batches` through
   `mcp__call_read_tools`. To record a read, pass a JSON object with the exact `request`
   (its `name` and native `arguments`) and `reply` to `NATIVE capture`. For a read batch
   the request name is `mcp__call_read_tools` and its arguments hold `calls`. A failed or
   truncated slot leaves only that check outstanding. Retry the missing reads in smaller
   batches or use the single descriptors in `readback_calls`, `lookup_calls`, and
   `link_readback_calls`. A create response alone is not proof of release.
4. Follow the link procedure below. The initial verification batches also read existing
   relationships. Send the helper's `link_calls` after those reads. Use
   `link_readback_batches` to verify the links after writing. If an initial relationship
   read was incomplete, finish that read before writing its links. When resuming after an
   uncertain link write, read the relationships again before considering further writes.
   The helper holds edges that conflict with existing attributes. Follow the separate
   `depends_on` procedure below for derived edges.
5. Run `NATIVE status --finalize` when the helper shows `ready` or `partial`. It records
   only complete release and relationship proof. Report held items and links for a
   partial result. The finalizer is safe to rerun and performs no Trident writes.

When your host can compose tool calls, pass the prepared JSON and tool replies directly
between the shell and MCP inside the host's tool composer. In Codex, use one
`functions.exec` call per phase, awaiting each create and recording its result before the
next create. Send independent read batches concurrently in the same phase. Return compact
counts, not the full result bodies. Treat every shell argument as literal data and use safe
shell quoting. Use the normal host approval for every tool call. In Claude Code, use the
ordinary MCP write/read runner. Save replies together in a private JSON file with the
host's file tool, then pass it to `NATIVE advance` to obtain the next phase. Use a separate
input per read batch when replies are large. Keep each input under 256 KiB.

- **Create:** use the prepared batches above. Each batch is one kind and at most 25 items.
  The helper also limits the request bytes and keeps each definition whole.
- **Revise:** append a new version to the matched artifact (`match_id`), as
  [appending a revision](#appending-a-revision) below says. Keep its `name` and `display_name`.
  The checked body already carries `definition.version` one above the reviewed live version.
  Do not change it after plan approval.
- **Reuse:** nothing to write. Record the live id.
- A `409` on create means the name is taken. Report it and hold the item: "<name> wasn't
  saved, because <workspace> already has one with that name. I held it back rather than rename
  it." Never rename to get around it.

Never rename. Archive only to finish a requested move ([6. Move](#6-move-to-another-workspace)).

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
authenticated Atlan Trident release client when publication is authorized. Discover that capability
and never invent an MCP release tool. If none exists, leave publication pending and say "They're
saved in Atlan Trident but not yet released, so your agent can't answer from them yet."

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
   still missing, hold it and report it. Once the derived edge exists, restate it as a link in
   step 4's `registry__relate_artifacts` call, passing only `attributes` (`why`, `relation`,
   `role`, `confidence`): the gateway merges them into the derived edge and keeps its marker.
4. For `source_of` and `uses_metric`, the saved query is the `artifact_id`. Read its existing
   links first. A link that matches on source id, target id and `rel_type` is reused unchanged;
   one whose metadata conflicts is held for review. Write every missing link, with the
   restated `depends_on` edges, in one `registry__relate_artifacts` call (up to 100 `links` a
   call), each passing `artifact_id`, `subject_id`, `rel_type`, and its `attributes` (`why`,
   `confidence`). Its result has one slot per link in order; resend only the failed links once
   their cause is fixed. Never send one `registry__relate_artifact` call per link.
5. Read each one back, and record which links were created, reused, restated and held.

Never derive links from other workspace artifacts, and never write workspace-wide.

## 5. Verify

Count by kind in one call: `registry__get_artifact_counts` with the `workspace_id` and
`group_by` `kind` (on a gateway without it, `registry__list_artifacts` with `limit` 1 per kind
and read `page.total`). Each kind's count must equal the preflight count plus the creates the results
report; a difference is an item to find by name before anything is resent. Then `registry__search_artifacts` for one sample
question with kind `sql_query`. It should come back near the top. Report the counts at the
workflow's STOP gate.

## 6. Move to another workspace

`registry__move_artifact` refuses metrics and saved queries: their links must stay in one
workspace. When the person asks to move saved ones, copy them and archive each original in the
same run once its copy is in place. Never leave an original live for a later cleanup when its
copy is ready.

1. Before any write, read `registry__list_workspaces` on this connection. When the old
   workspace shows `can_manage: false`, archiving will be refused: name who can and stop.
2. Read each original's head with `registry__get_artifact` and its links with
   `registry__get_related_artifacts`. Hold an original whose head no longer matches its checked
   body, that any artifact outside the moved set links to or loads, or that links to a metric
   or saved query outside it. A held original stays in the old workspace, so repeat these
   checks until none more is held: also hold every original a held one links to or loads, and
   every original that links to a held one. Name each held original with the reason.
3. Run this flow again from **1. Preview** for the target workspace, through the same
   connection, for the originals not held. The preview adds every original to archive, by id:
   "Once each copy is in place, I'll archive its original in <old workspace>: <name> (<id>)."
   Leave out the line about choosing the workspace now: the archive list takes its place. One
   approval covers the copies and that list.
4. Create, link and verify in the target as above.
5. Archive saved queries first, then metrics, each metric before any metric it depends on, so
   stopping at any point leaves no live original linked to an archived one. Just before each
   archive, read the original again as in step 2 and hold it if its head changed or it gained a
   link from outside the set. Archive an original with `registry__archive_artifact` (run with
   `mcp__call_tool`) only when it is on the approved list, its copy was created or reused, the
   copy's release reads back, and every approved link of the copy exists. Under the `manual`
   release policy the copy has no release yet: keep the original live and say "<name> stays in
   <old workspace> until its copy is released in <workspace>." If an archive is refused or its
   confirmation declined, stop archiving and keep the rest live. When the person later says the
   copies are released, read their releases back and run this step again against the same
   approved list.
6. Report the new ids, the archived originals, and every original still live with its reason.
