# Setup stages 3 and 4: Build and Check

`PROJECT` is the helper named in [setup](../setup.md). Read this file on entering Build.

## 3. Build candidates

On fresh setup, complete [business focus and purpose](intent.md) before authoring. Reuse saved
answers on resume. Select the first metric and the smallest useful source scope from those
answers. A specific business question prioritizes its required metric/query and first supported
answer; broader team preparation follows only within the requested scope. Existing publication,
retrieval and execution requirements still apply.

After intent, check connection readiness once when the requested outcome includes team reuse
or saving in Atlan. Use a current-host `registry__list_workspaces` read and record success with
`PROJECT auth` `record_read` as [connection recovery](../auth-recovery.md) describes. A failed
read starts that host's recovery while independent local work continues. Do not mark the
workspace-listing task done until a read succeeds, and do not retry it at every authoring gate.
Local exploration and understanding-only requests do not need this sign-in step.

Use [local source procedures](../project.md#2-bind-data-once) to select an executor independently
of the input: an existing SQLite or DuckDB file, CSV preparation, or an existing read-only warehouse
through [connect](../connect.md). No data connection is needed to preview definitions. Without
execution access, mark SQL untested and preserve source work.
For a rerun with a selected existing destination, inspect its saved receipt/binding and fetch
fresh relevant released definitions before choosing a different executor. Follow
[released execution](../project.md#reuse-a-released-local-executor). The released SQL dialect
and catalog decide the local binding; keep that SQL and its definitions unchanged. Use an
existing approved matching runtime. A missing dependency needs authorization, not a choice
between overwriting releases and changing engines.

For a fresh local setup without relevant existing releases, prepare CSVs into a new `.sqlite`
database. It needs only the standard library, so do not offer or install DuckDB for that case.
Author in the selected binding's dialect and native named parameters. Translate unrelated
history SQL before checking it, and keep the original in the shape's provenance.

When every selected input is a local file (BI files, CSVs, query-history files) and no
warehouse is selected, author with [metrics authoring](../metrics/authoring.md) over this run's
`PROJECT history` shapes, and check with `PROJECT check-query` and `check-metric` against the
selected local binding. Do not initialize the metrics module runtime for this case; it installs
packages.

Before authoring the full local collection, save one relevant candidate and its supporting
query, select it with `PROJECT journey select`, and run its exact local check. Show the first
result with its scope and check limitations, then expand the collection. Keep that first
candidate when appending later ones. If execution is unavailable, show the source-backed
draft and name the missing check; do not claim a checked result or stall all source work.

Otherwise, for new supported metrics, use [the metrics module](../metrics-engine.md), preserving this setup
run, scope and preview/publication order. Reuse the interview decisions; do not run another.
Supported BI definitions and captured history enter through its input plugs and authored gates.
Power BI templates (`.pbit`) now use the model and report together. Do not replace a supported
template with its BIM sibling as a routine workaround. Query-history admission uses the same
identifying-literal filter as local setup, before any authoring prompt; report exclusions as
counts and reasons without reproducing the withheld values.
For a configured direct Snowflake connection, select native Account Usage history; no shape
table preparation is required. `PROJECT history` remains an optional capture path for local or
chat-tool evidence, which must enter the module's common inputs before metric generation.

Export the module's checked metrics, queries, links and structured publication holds into this
run's context. Carry its source receipts through reconciliation and publication; source/scope
changes require a new engine run, never edited pinned inputs. Existing legacy authored contexts
and explicitly requested by-hand drafts use [metrics authoring](../metrics/authoring.md).
Keep `context/metrics.json`, `queries.json` and `links.json` in existing formats. Include formula,
grain, units, dimensions, mandatory filters, time semantics and provenance. Reconcile declared
and history evidence; detail SQL remains a saved query. Unsupported formulas remain held.
Write coherent candidate batches atomically and show one real draft as soon as useful.
After an engine gate or phase transition, run `PROJECT experience --format chat --engine-dir
<within-run-engine>` and show its factual change or sample. This reads provisional evidence;
it does not export, change eligibility or publish. Keep Build as the stage while work remains.
Use `--detail` when the user wants that sample's SQL, source and check evidence. The first
useful preview must not wait for every check or for the engine to finish.

## 4. Check the actual revisions

Run `PROJECT check` for local structural validation. It requires no workspace and reports
`schema_checked`, not certification or execution. Use `PROJECT check-query "<request.json>"`
with a candidate `name` and separate `parameters` to execute exact saved SQL on the bound SQLite or DuckDB file. Checks
for metrics use `PROJECT check-metric` with `name`, `example_index` (zero-based) and parameters.
It executes that exact authored SQL example, labels the result as example execution, and does
not claim the example proves formula equivalence or dashboard parity. Review those separately.
Both types of checks
are bound to definition hashes. For warehouses use the existing read-only tool and retain
actual revision-bound check evidence. Test representative parameters, excluded populations
and empty denominators. Never treat one parameter sample as exhaustive coverage.
When showing an early local answer or explaining excluded rows, apply
[explanation checks](../answer-checks.md). Account for every formula component, including
revenue on rows with no costs; distinguish an unchanged top result from an unchanged total.
Failed items remain held while usable items continue. Source/scope changes archive previous
candidate files and clear publication receipts; re-author/reconcile before continuing.

Save progress and candidate evidence at meaningful phase changes, then refresh
[the checklist](checklist.md) from `PROJECT checklist`.
