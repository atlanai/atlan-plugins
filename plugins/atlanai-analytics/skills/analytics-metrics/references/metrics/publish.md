# Publish within the selected task scope

When only explaining the next publication step, use one short paragraph: counts, selected
workspace, scoped authorization, then publication and readbacks under the release policy.
Omit helper command blocks unless commands were requested. Do not turn the explanation into
another approval request.

For an explanation of an explicit pre-dispatch helper refusal, explain the result and next action.
Do not enumerate argument names or commands unless asked. For example: "Nothing was created.
I will use the same connection with native arguments, dropping local fields instead of retrying
the rejected descriptor. The checked definitions, workspace and receipt checks stay in place.
Automatic recording is unavailable on this route; no alias setting or account change is needed."

Every write goes through the registry's MCP tools with the person's own sign-in. Nothing here
calls the registry's REST API, holds a token or runs a push script. Publish only what
`plan.json` marks `ok`. The checker enforces `publication-holds.json` for engine exports.
Preserve the hold manifest and source receipt; never reintroduce held bodies or links, infer
status from prose, or delete evidence to make a plan pass. Repair through the engine gates
and export a new context. A held-only export stops with its reasons and sends no write.

## Contract preflight

Follow the [publication contract](../../modules/metrics/contracts/README.md) for every
input route, including local files and demos through the same engine. Read
`registry__get_kind_schema` once per kind in this publication session and compare its
`properties.definition` with the checked-in metric and saved-query schemas. Reuse those
schema reads throughout the session. Hold affected writes on a mismatch and report the field;
never send an unsupported shape or bypass validation. Run `CONTEXT check` again on the exact
bodies after any repair. Keep the existing publication authorization and transport.

## 1. Preview

Before previewing the plan, follow [existing-work reconciliation](reconciliation.md). Read
its review requests and preserve coverage limitations in the handoff, including when the
final plan reuses every artifact. A reusable total does not imply every proposed cut is covered.

For setup runs, first show the full first-metric block from
[the presentation flow](../../../analytics-build/references/setup/presentation.md) if it has not been displayed. A file link, tool result or counts alone
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

- `continue`: the user approved this exact plan. State the counts and target, then write.
- `deferred`: the person requested drafts or a preview. Save the preview and stop.
- `needs_context`: resolve the named missing target, changed scope or unauthorized revision
  once, then record the decision.
- `needs_confirmation`: show the counts, target and every revision, ask once, and record the
  plan ID in `approved_plans` only on a yes. A request to publish is not approval of a plan
  the user has not seen.

A checked plan is not authorization by itself. The source and workspace must match the trusted
conversation record. If the person drops items, rerun `CONTEXT check` and keep the new plan within
that scope. Existing producer logic, batching, ETags, idempotency, retries and release readbacks
below are unchanged. Never ask again for file reads, or because the publication phase started.

**Show me 5 sample cards first** has an immediate action: run
`python3 "<build-dir>/scripts/project.py" --run-dir "<run-dir>" preview --limit 5 --format markdown`
and display its returned text in this response. Use `--kind sql_query` for saved queries.
If fewer than five exist, show all; if zero, show the reason. Do not publish, restart extraction
or ask for a workspace to show samples. Optional rich cards must fall back to this text.
**Show all** uses `--all`. **Not yet / Keep local** preserves the run with an unshared status.


## 2. Metrics

### Load create batches from local files

Prepare guarded local batches before copying definitions or SQL into tool arguments. Use the
current host's discovered Atlan runner, whatever its server is named. A capability notice
is useful evidence, but a missing notice does not prove that a hook is unavailable:

```bash
python3 "<build-dir>/scripts/publication_batches.py" --record-receipts --context "<context-dir>" --authorization "<project>/authorization.json" --workspace "<workspace-id>" --source "<original-asset-identity>"
```

Repeat `--source` for every selected source. The helper checks the existing task authorization,
publication holds and checked plan, and writes immutable, digest-named JSON files inside the
context. It returns small call descriptors, source identities and counts, never definition
bodies. Only admitted `create` actions are batched, separated by kind, at most 25 items and
240 KiB per file. No SQL is truncated. Revisions, reuse and links keep the procedures below.

Send each returned descriptor's `name` and `arguments` through the Atlan write runner, adding
the existing session and rationale beside them. Preserve `items: []`, `items_from`,
`items_sha256`, `kind` and the selected `workspace_id` exactly. The hook verifies the digest,
path, size, text and workspace, then replaces those local fields with the file's `items`.
It uses the same Atlan call and sign-in; it never runs a REST publisher.

Without the hook, the empty items list is refused before any create. If `invalid_arguments`
explicitly rejects `items_from` as undeclared, no create was dispatched: treat the file helper
as unavailable for that connection. Do not send the same descriptor again or add inline items
beside `items_from`. Remove `items_from`, `items_sha256`, `workspace_id` and `receipt_dir` from
the top-level remote arguments and use the normal native bulk procedure below, retaining
`workspace_id` inside every item. Stop expecting automatic receipts on that unsupported route. A timeout, lost reply, partial outcome or local receipt
failure is not this refusal: reconcile it before any further write. Do not repeatedly probe
an unavailable helper. A changed file requires preparation and review
again, not editing its digest by hand. Keep the reviewed context fixed while sending batches.

Read every returned slot by index against the descriptor's `source_ids`. Record successes
before addressing failures. Never resend a complete partially successful batch. If the reply
is lost, reconcile each source identity and exact definition/version before retrying anything;
a repeated create can append a version. A confirmed failed subset needs its own checked,
authorized plan and batch, excluding successful and uncertain items. Stop on an unexpected
revision and reconcile it. Keep all normal release, link and complete readback checks.

### Record native receipts without copying them through the model

The preparation command requests deterministic recording with `--record-receipts`. After
an actual call reports `Atlan receipt saved`, use its returned `receipt_dir` for this run's checked plan.
An installed-hook notice alone does not establish that this host can rewrite inputs or expose
complete outputs. On an unsupported route, use native MCP arguments and the normal receipt
procedure below; never claim that automatic recording ran.
The create descriptors already carry this local field. Keep a returned relative path relative
to the same working directory. Add the same field to each
`registry__get_artifact` read with `channel: latest` and each relationship verification read
with `registry__get_related_artifacts`. The hook strips the field before dispatch and saves
the native response locally. Other hosts keep the existing manual recording procedure.

After the creates, inspect the captured state using counts and call descriptors only:

```bash
python3 "<build-dir>/scripts/publication_receipts.py" --run-dir "<run-dir>" --receipts "<receipt-dir>" --workspace "<workspace-id>"
```

Send the returned `readback_calls` through the read runner. Continue the normal approved link
procedure, then send `link_readback_calls` to capture its proofs. Do not copy raw tool
responses into Write calls or Python literals. Do not read the full receipt files into the
conversation merely to assemble them. The helper compares complete released definitions,
SQL, identity and planned links with the checked bodies. A create response is not a release
readback. A failed, missing, changed or conflicting receipt stays pending.

Once the status is `ready`, repeat that command with `--finalize`. It assembles the native
readbacks and invokes the existing publication validator and compact durable run receipts.
Re-running it is safe and makes no Registry writes. The normal final counts/search checks
remain. If local recording fails after a tool runs, preserve the result and reconcile live
state; never repeat a write to repair a receipt. Missing readbacks can be read again safely.

For an interrupted run, resume with the same receipt directory and run the helper first;
complete only its missing reads and normal checked link work. Conflicting revisions require
reconciliation and a fresh receipt set. A new run still reads live Registry inventory and
runs the existing create/revise/reuse checker. Local receipts never replace that discovery
or the fresh released-context retrieval required to answer questions. Each receipt set is
bounded to 128 immutable files, with at most 256 KiB per native response file.

- **Create:** `registry__create_artifacts` with kind `metric`, each body exactly as checked, in
  calls of at most 100 items and about 256 KB of JSON (the core plugin's **Batches** rule in
  `calling-tools.md`): about 25 full metric definitions to a call. Save each returned id against
  its `source_id` in `<context-dir>/published.json`. Reuse the exact checked bodies; do not
  regenerate metric JSON while constructing each call. Use the supported file-backed hook
  above when available, with inline bulk calls as the fallback. Do not invent a REST route.
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
