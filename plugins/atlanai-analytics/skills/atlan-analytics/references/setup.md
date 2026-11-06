# Prepare existing context for questions

Use seven stages: **Inspect, Scope, Build, Check, Preview, Publish, Ready to ask**.
The result is released metrics and saved SQL in Trident, used by this chat through MCP.
An input folder is ordinary customer context. Never depend on a particular repository,
sample schema or database name. Explicit agent and evaluation modes remain separate.

`PROJECT` means `python3 "<skill-dir>/scripts/project.py" --run-dir "<run-dir>"`.
Use an existing approved Python runtime; DuckDB execution needs one with DuckDB installed.
The [local procedures](project.md) describe binding, preparation and complete release readbacks.

## 1. Inspect what is available

Reuse the supplied source and the selected run. Without a run, use the helper's `location`
command with `--workspace "<working-directory>"`. An explicit `--run-dir` wins. A configured,
permitted cache can be supplied as `--permitted-cache`. Only when the host already permits it,
`--home-permitted` selects `~/.atlan/analytics/runs/<opaque-id>/`. Otherwise the default is a
private `.atlan-metrics/<opaque-id>/` in the current workspace. The resolver creates nothing.
Preserve existing run locations; do not change host permissions or read credential config.

- **Repository link:** reuse a matching local checkout, or clone the exact requested repo into
  an unused working directory using existing Git authentication. Do not execute its setup scripts.
- **Folder or supported file:** run `PROJECT survey "<absolute-path>"`. Read supplied files in
  place. The survey includes model files, HTML dashboards, query history, SQL and local data.
- **Report/web link:** inspect lightweight metadata through the existing authorized host tools.
  A visible chart is evidence, not its missing SQL. Obtain a supported export when necessary;
  if definitions are unavailable, hold that coverage with one specific next step.

For downloads, announce the necessary acquisition once. A request to use the source covers
necessary acquisition within its scope; host/provider approval still applies. Do not add a
second plugin permission question. If a page-only request needs a broader full-model download,
explain that difference and resolve the missing scope authorization first. Never export more
data simply to obtain page names. If decoding requires a full authorized export, acquire it
once, list pages, then select scope before expensive authoring or checks.

`PROJECT acquisition "<request.json>"` takes `url`, `revision` and `access_scope`. It checks
the local receipt and file hash. `reused` supplies the matching path; `needs_download` names a
run-local acquisitions directory. Use existing authorized host tools to download there, then
repeat with `file` to record the receipt. No helper fetches a URL or stores signed URLs or
credentials. Without a trustworthy revision/access scope, do not claim cross-session cache
validity. Local files are never copied merely to use this cache. Keep run files private and
clean up only explicitly selected owned outputs, never source files.

Show the inventory and the combination you will use before authoring. Distinguish available,
needs review, held and scan-limited inputs. Models provide declared definitions; relevant query
history adds actual query patterns, dimensions, joins and missing measures. A data extract is
an executor/schema source, not a replacement for available history. Reuse already released
workspace context early when the destination is known.

## 2. Scope the report and evidence

For parsed Power BI inventories, run `PROJECT scope "<selection.json>"` with `source_id` and
optional `pages` (exact IDs or unique labels). `pages: ["all"]` selects ordinary visible pages;
repeat for each selected report in a folder; scopes are retained by source ID.
hidden, tooltip and drill-through pages are listed separately and can be selected explicitly.
For one page, state the selection without a picker. Honor a named page or page-specific URL.
Otherwise state the All default and allow a narrower selection before authoring. Do not ask
the person to re-select a page already supplied. BIM/model-only exports have measure scope.

The helper retains report/page/visual contexts separately, deduplicates base measures and
includes referenced supporting measures. It holds unresolved references; never expand to the
entire model to hide a missing reference. Confirm implicit aggregations, dynamic field
parameters and unsupported layouts through the source reader. All does not merge different
pages' filters into one metric. Preserve each scoped variant as context or a saved query.

Map selected semantic tables to evidenced physical relations. Supply `history_tables` in
the scope request when narrowing history; the helper refuses unscoped mining after an explicit
page selection. Review broader joins separately. For other formats use the same scope rules
with [source readers](metrics/sources.md), retaining held coverage instead of inventing a parser.

## 3. Build candidates

Use [local source procedures](project.md#2-bind-data-once) to select an executor independently
of the input: existing DuckDB, approved CSV preparation, or an existing read-only warehouse
through [connect](connect.md). No data connection is needed to preview definitions. Without
execution access, mark SQL untested and preserve source work.

Run `PROJECT history` for available history, then use [metrics authoring](metrics/authoring.md).
Keep `context/metrics.json`, `queries.json` and `links.json` in existing formats. Include formula,
grain, units, dimensions, mandatory filters, time semantics and provenance. Reconcile declared
and history evidence; detail SQL remains a saved query. Unsupported formulas remain held.
Write coherent candidate batches atomically and show a first draft preview as soon as useful.

## 4. Check the actual revisions

Run `PROJECT check` for local structural validation. It requires no workspace and reports
`schema_checked`, not certification or execution. Use `PROJECT check-query "<request.json>"`
with a candidate `name` and separate `parameters` to execute exact DuckDB saved SQL. Checks
for metrics use `PROJECT check-metric` with `name`, `example_index` (zero-based) and parameters.
It executes that exact authored SQL example, labels the result as example execution, and does
not claim the example proves formula equivalence or dashboard parity. Review those separately.
Both types of checks
are bound to definition hashes. For warehouses use the existing read-only tool and retain
actual revision-bound check evidence. Test representative parameters, excluded populations
and empty denominators. Never treat one parameter sample as exhaustive coverage.
Failed items remain held while usable items continue. Source/scope changes archive previous
candidate files and clear publication receipts; re-author/reconcile before continuing.

## 5. Show the metrics before asking where to publish

Automatically show a compact real preview with `PROJECT preview --limit 1`, plus a history
query when available using `--kind sql_query`. Say that candidates are provisional.
Offer Publish, Show five, Show all and Keep local, using native choices when available.
If publication was requested already, continue toward the destination and final plan without
another intent question. Available preview actions still work immediately.

**Show five:** run `PROJECT preview --limit 5 --format markdown` and display its text in the
same response. **Show all:** use `--all`. Neither action selects a workspace, writes to Trident,
reruns extraction or restarts the workflow. Fewer/zero samples have explicit text outcomes.
**Keep local:** preserve candidates and stop with an unshared status. Do not call them Trident-ready.
Draft question ideas must say that retrieval-based answers require publication first.

## 6. Choose the destination, reconcile and publish

Reuse an explicit destination or ask once by accessible workspace name. Explain it as where
the team finds and reuses definitions. Fetch live kind schemas and existing definitions, then
run `MINE check` with that destination. Only now show actual create/revise/reuse/held counts.
If reconciliation changes a candidate, show the changed preview. Follow [publication](metrics/publish.md)
for the concrete plan, missing authorization, idempotency, release policy and uncertain-write
recovery. Never retry an uncertain remote mutation blindly. Carry approved writes through
release and full MCP readback. Record receipts with `PROJECT publication` as the local
procedures describe. A draft or local plan is not publication.

## 7. Ready to ask here

Show actual released metric/query counts and verified artifact links, then three to five
supported questions (fewer if needed), including history-derived questions where useful.
Preserve data-connection and unmeasured-parity limitations. Do not invent a time basis or
dimension. Continue to a question already supplied; otherwise wait for the user's selection.
Use [retrieval](retrieval.md) for fresh Trident context and fresh execution on every question.

## Progress, workers and presentation

Use `PROJECT progress --stage <stage> --status <status>` at parent-owned stage transitions.
`PROJECT progress` reads the latest state. Inspect/check operations also checkpoint their
own counts. Statuses are pending, running, waiting-for-user, complete, complete-with-held-items,
failed or canceled. Progress contains real counts, seven named steps and steps remaining,
never a guessed percentage or ETA. Model metrics and saved queries have separate counts.

Run deterministic scanning, decoding, mining, checking and rendering through the local
helpers. Use host background task IDs/output files when available, otherwise bounded foreground
batches. A survey returning `continue` resumes with the same command and cached inventories.
The parent retains semantic decisions, permission UI and authenticated MCP/warehouse calls.
Serialize state mutation and publishing. Do not create a daemon, hidden model calls, hooks or
another MCP server. Cancellation preserves checkpoints; uncertain remote writes need readback.

Normal structured helper results over 4 KiB become private result files. Read relevant fields
from them instead of dumping logs. `--full` is for targeted diagnosis, not normal chat.
At phase changes, first previews and useful count changes, show concise progress. For long
work, consume `progress.json` and provide a truthful heartbeat every 30 to 60 seconds.
Keep diagnostics private and give one recovery step on failure.

When the host has a panel API, open one surface and update it. `PROJECT preview --format html`
creates an escaped local snapshot with working details controls and a refresh link. It is a
snapshot, not a live service; regenerate before refreshing. Keep publication decisions in chat.
CLI users always get text and samples, with native task tools only if available. If the rich
renderer fails, display the Markdown result; optional UI never blocks the workflow.
