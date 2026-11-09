# Setup stages 3 and 4: Build and Check

`PROJECT` is the helper named in [setup](../setup.md). Read this file on entering Build.

## 3. Build candidates

Use [local source procedures](../project.md#2-bind-data-once) to select an executor independently
of the input: an existing SQLite or DuckDB file, CSV preparation, or an existing read-only warehouse
through [connect](../connect.md). No data connection is needed to preview definitions. Without
execution access, mark SQL untested and preserve source work.
Prepare CSVs into a new `.sqlite` database. It needs only the standard library, so do not
check for, offer or install DuckDB or any other package for this step. Author SQL in the
`sqlite` dialect with `:name` parameters. Translate history SQL written for another engine
before checking it, and keep the original in the shape's provenance.

When every selected input is a local file (BI files, CSVs, query-history files) and no
warehouse is selected, author with [metrics authoring](../metrics/authoring.md) over this run's
`PROJECT history` shapes, and check with `PROJECT check-query` and `check-metric` against the
SQLite binding. Do not initialize the metrics module runtime for this case; it installs
packages.

Otherwise, for new supported metrics, use [the metrics module](../metrics-engine.md), preserving this setup
run, scope and preview/publication order. Reuse the interview decisions; do not run another.
Supported BI definitions and captured history enter through its input plugs and authored gates.
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
Write coherent candidate batches atomically and show a first draft preview as soon as useful.
Run `PROJECT preview --limit 5 --format chat` and put its actual content in chat before long
SQL checks. Keep the current Build stage; a draft preview is useful before Check completes.

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
Failed items remain held while usable items continue. Source/scope changes archive previous
candidate files and clear publication receipts; re-author/reconcile before continuing.
