# Setup stages 1 and 2: Inspect and Scope

`PROJECT` is the helper named in [setup](../../analytics-build/references/setup.md). Read this file on entering Inspect.

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
- **A BI service link whose model holds the measures** (a Power BI report, a Tableau
  workbook): the measure formulas are in the model, not on the page. A page read or a
  metadata query (`INFO.VIEW.MEASURES`) returns names without expressions for most roles.
  Get the definition files, in this order:
  1. the engine's own reader for that service (`powerbi`, `tableau` features), signed in as
     the person through its device-code or browser route, as
     [Power BI service inputs](../../metrics-generation/modules/metrics/engine/docs/guides/power-bi-service-inputs.md) says;
  2. the service's definition export through the person's signed-in session (Power BI's
     item definition, TMDL format), saved as a file into the run's `sources/` folder by one
     announced download, then `ENGINE stage-input`;
  3. a supported export the person downloads (`.pbix`, `.pbip`, `.twbx`).

  Never carry a definition through chat or page text: page readers cap output and collapse
  whitespace, so a copied model differs from the original. Check any staged file against the
  service's copy (size or hash) before surveying it.

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

Settle whether each source can be built from before returning, as the skill's `readiness`
section defines it: the tables a report reads must resolve in a reachable warehouse or a bound
local database. Return that with the inventory, so no use-case question is asked about a
source that cannot be built.

Before authoring, give a short business summary: observed source types and subject, what
the project appears to be about, and two or three analyses those sources could support.
Explain how the sources complement each other; counts alone do not show understanding.
Keep this summary with the first native question, not only in collapsible commentary.
Distinguish sample, available, needs review, held and scan-limited inputs; keep filenames
and diagnostics in details. Models provide declared definitions; relevant query
history adds actual query patterns, dimensions, joins and missing measures. A data extract is
an executor/schema source, not a replacement for available history. Reuse already released
workspace context early when the destination is known.

Refresh [the checklist](../../analytics-build/references/setup/checklist.md) so the survey step shows its source count before the confirmation.
The caller owns the conversation: `analytics-build` asks its opening questions before any
data is read, and asks which use cases to build before authoring. Use the domain the caller
passed to select relevant source coverage.

When the survey exposes a declared measure relevant to that focus, show it after the intent
checkpoint and before runtime installation or generation: its actual name, calculation, source and known population. Label it a discovered
definition, with SQL not generated and checks not run where appropriate. Missing lineage is
an explicit limit, not a guessed table mapping. This first look does not certify a candidate,
replace the engine's authored gates or satisfy the later checked publication preview.

## 2. Scope the report and evidence

**Order the sources first (`plan`).** Return the sources to build from as one short list, in
this order, each with what it adds and its scope:

| Order | Source | What it adds |
| --- | --- | --- |
| 1 | BI reports and models | The rules the team already agreed: measures, filters, joins |
| 2 | Query history | What people actually ask: the cuts, dimensions and measures the reports miss |
| 3 | Table definitions | Columns and types, so every rule can run and be checked |

Leave out a level that has no buildable source, and say so in one clause. The caller shows
this list and decides whether to ask about it. A report is scoped whole, with
`pages: ["all"]`, unless the person narrowed it in their own words. Query history is scoped
to the tables the report's measures are computed on, never the whole warehouse; leave out a
shared lookup table (accounts, users, dates) when it alone would match most of the history.

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
with [source readers](../../metrics-generation/references/metrics/sources.md), retaining held coverage instead of inventing a parser.
