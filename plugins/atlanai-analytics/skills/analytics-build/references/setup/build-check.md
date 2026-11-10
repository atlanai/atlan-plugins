# Setup stages 3 and 4: Build and Check

`PROJECT` is the helper named in [setup](../setup.md). Read this file on entering Build.

## 3. Build candidates

On fresh setup, complete [business focus and purpose](intent.md) before authoring. Reuse saved
answers on resume. Select the smallest useful source scope from those
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
through [connect](../../../analytics-survey/references/connect.md). No data connection is needed to preview definitions. Without
execution access, mark SQL untested and preserve source work.
All sources, including the demos and local-only files, use [the metrics engine](../../../analytics-metrics/references/metrics-engine.md).
For CSVs, initialize or reuse its pinned runtime, then use that runtime's Python with
`PROJECT prepare` to create a new `.duckdb` database. Configure `local-files` with the selected
BI definitions, cleaned history and this database. Respect Dashboard only versus history scope.
Existing SQLite inputs remain supported by the local checking helper; when the engine has no
compatible SQL-check plug, retain parse-only evidence and run exact local checks separately.
Do not create a second authoring path because an executor is unavailable.

Reuse the existing survey, scope, business focus and interview answers. Prepare one engine
run under this setup run. Advance through its maintained gates for the complete selected
collection. At X3, a demo first shows its
[metrics found](../../../analytics-demo/references/metrics-found.md) list; own data shows
nothing extra there, because its names were committed in step 2. Then author all scoped
cards using the returned instructions and continue through the remaining gates. Gate answers
are the assistant's work, not customer choices. Keep factual progress visible while
generation and checks run. Do not stop for a single-card preview or request permission to
generate the remaining already-selected context.

After completion, export into a fresh `engine-export/` and install the unchanged metric,
query, link, source-receipt and publication-hold files into this run's context as the engine
procedure describes. Source/scope changes require a new engine run. Unsupported items remain
held. No by-hand, legacy-shape or demo-only fallback is available.
After export, select a relevant completed candidate with `PROJECT journey select
--focus <exact-identity>` so review and ready moments reflect the business focus.

After a gate or phase transition, run `PROJECT experience --format chat --engine-dir
<within-run-engine>` and show factual progress. Review the collection after its checks, using
[the presentation flow](presentation.md). Keep Build as the stage while work remains.

## 4. Check the actual revisions

Run `PROJECT check` for local structural validation. It requires no workspace and reports
`schema_checked`, not certification or execution. Use `PROJECT check-query "<request.json>"`
with a candidate `name` and separate `parameters` to execute exact saved SQL on the bound SQLite or DuckDB file. Checks
for metrics use `PROJECT check-metric` with `name`, `example_index` (zero-based) and parameters.
It executes that exact authored SQL example and the calculation composed from the card's
formula, source and mandatory filters, including an empty-population probe. Both must run;
fix invalid column scope or bindings on the card and rerun. The local helper batches these statements in one bounded worker, retaining each statement's
read-only validation, table scope, parameter bindings, schema check and separate result.
The evidence records example and composition separately. Neither establishes source equivalence or dashboard parity; compare
the source calculation and relevant edge cases separately.
Both types of checks
are bound to definition hashes. For warehouses use the existing read-only tool and retain
actual revision-bound check evidence. Test representative parameters, excluded populations
and empty denominators. Never treat one parameter sample as exhaustive coverage.
When showing an early local answer or explaining excluded rows, apply
[explanation checks](../../../analytics-ask/references/answer-checks.md). Account for every formula component, including
revenue on rows with no costs; distinguish an unchanged top result from an unchanged total.
Failed items remain held while usable items continue. Source/scope changes archive previous
candidate files and clear publication receipts; re-author/reconcile before continuing.

The engine's X3 whole-card review and later consistency checks own semantic review for the
full collection. Repair source bindings, descriptions, grain or dimensions in that same run;
then revalidate, export and rerun checks on changed revisions. SQL execution alone is not
semantic review or dashboard parity.

Save progress and candidate evidence at meaningful phase changes, then refresh
[the checklist](checklist.md) from `PROJECT checklist`.
