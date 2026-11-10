# Metrics: build the metric layer

Goal: `metric` and `sql_query` artifacts in a workspace, made from definitions people already
trust, each one checked against the registry's contracts and previewed before it is written.
Two entries use this file:
- **metrics mode** (`analytics-metrics`, or the `metrics` argument of `analytics-build`, which
  Metrics Studio sends) starts at section 0 and stops at section 6;
- **project setup** arrives after its combined repo survey. Preserve that route's state,
  use both model and history evidence, and return to its publication receipts and questions.

The plugin owns the interview and publication. Every generation run uses the isolated metrics
module; the standard-library helpers retain validation and publication only. Below:
- `CONTEXT` means `python3 "<build-dir>/scripts/context_contracts.py"`. `<build-dir>` is the
  `analytics-build` skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/analytics-build` in Claude
  Code), which holds the shared helpers; `<metrics-dir>` is this skill's directory beside it.
- `<context-dir>` is `.atlan-metrics/<slug>/context`.

`CONTEXT` prints one JSON object. It exits 2 when BLOCKED, and 1 with `"status": "REFUSED"` when
a check refuses an item.

Setup callers follow [shared setup](../../analytics-build/references/setup.md) for ordering. They skip section 0's destination
question when the workspace is unknown. Use provisional `PROJECT check` and `PROJECT preview`
locally, then fetch the live schema and existing definitions and run `CONTEXT check` after choosing
the destination. Do not report create/revise/reuse counts before that reconciliation.
Explicit metrics mode retains its entry contract below.

## 0. Metrics-mode entry

1. Reuse the requested destination and publication intent. Local draft requests need no
   publication workspace. For Atlan saves, open the Registry session and verify the selected
   workspace. If the destination is unresolved, list accessible workspaces and ask once with
   Keep local and those existing workspaces as the choices. Resolve an explicit personal-workspace
   selection directly. Never choose the first accessible workspace silently or attempt workspace
   creation through API/CLI. With no accessible workspace, keep drafts and explain the access
   needed. An explicit new-workspace request gets the UI/admin route, followed by a list refresh.
2. Keep state in `.atlan-metrics/<slug>/state.json`, with `mode: "metrics"`,
   `ids.workspace_id` and `session_id` when connected, and set `phase` to `context`. Resume only a metrics
   state for this workspace and scope; when several match, ask which. Never read or resume
   `.atlan-analytics/*/state.json`, and do not call `EVAL init`. Keep source files and authored
   answers under the run directory and out of commits.
3. Reuse the supplied business area and scope. Derive a bounded table set from the selected
   BI lineage or declared model and write `tables.txt`. Ask only when ambiguity affects the
   source or table boundary. Do not require a second confirmation of source-derived scope or
   ask for an agent name, audience or eval questions.

## 1. Pick the sources

Local drafts need no publication workspace. When a workspace is already known, read its existing `metric` and `sql_query` artifacts first
(`registry__list_artifacts`). When they already cover the request, say so and stop rather than
create duplicates. In project setup, reconcile every scoped source first, then carry reused
released artifacts through receipts and suggestions; existing context is not a reason to end
the user's setup before the question handoff.

Reuse the source the person supplied. When the source is unresolved, find what is in reach
within the selected scope using [sources](metrics/sources.md). Combine selected evidence in
the order the conventions' **Source of truth** sets: a declared model's metric is taken
as declared, and query history adds what the models miss.

| source | in reach when | becomes |
|---|---|---|
| A declared semantic model: dbt, LookML or Cube in this repository; Snowflake semantic views; Databricks metric views | its files are here, or the warehouse lists one over the scoped tables | one metric per declared measure, as declared |
| A Power BI or Tableau model | the person has the file or extract | declared measures, translated to SQL and tested |
| A completed current engine run | its pinned runtime and run directory are available | `ENGINE export`, then section 4 |
| Query history | a warehouse query tool, a vendor CLI, or an export file | mined metrics and saved queries |

Ask only if the source choice is unresolved, with the options actually in reach. A supplied
BI file, asset URL or query-history export already settles this decision:
- `<slug> · context · where the definitions come from`
- **Declared models plus query history (Recommended when both are in reach)**: the models give
  the definitions, and history shows what is used and what the models miss.
- **Query history (Recommended when no model is in reach)**: what analysts actually ran against
  the scoped tables.
- **Only the declared model**: no warehouse history needed.
- **Resume a current engine run**: its pinned runtime and source configuration.

Skip this choice when the request already selected a source, including `query-history`, or
project setup has surveyed the repository. That survey selects both model and history when usable.
Use a supplied query export before asking for warehouse history access. Discover a data
connection only when needed for checks or fresh answers, and explain that purpose first.

If the person offers a benchmark or eval file here, keep it out of this workflow. Eval
answers never become context.

## 2. Generate through the main metrics engine

Every generation route follows [the metrics module](metrics-engine.md): local files, demos,
BI models and query history. There is no separate shape miner or by-hand card
pipeline. `PROJECT history` only captures and filters history for the engine's common input.

Answer the engine gates for the complete selected collection and continue `ENGINE advance`
in the same run until validation finishes. Keep progress visible, then review the accepted
collection and its checks. Do not add a mid-build customer decision between cards.

## 3. Export accepted context

Complete every engine gate, then `ENGINE export` into a fresh context destination. Preserve
all five exported files and their source/eligibility receipts. See [contracts](metrics/contracts.md)
for the Registry wire format; it is not a separate generation procedure. Unsupported sources
remain held until a faithful input reader is available. Existing legacy drafts can be read
and reconciled, but generation or changes require a new engine run from their source evidence.

## 4. Check

1. Write the workspace's live artifacts to `<context-dir>/existing.json`, one
   `{id, kind, name, display_name, source_id, definition}` per artifact. Read definitions with
   `registry__get_artifact` for the metrics whose tables overlap this run's.
2. Run `CONTEXT check --dir <context-dir>`. It checks every body against the registry's contract
   and the card rules. It refuses duplicates, and matches each metric to a live one that
   measures the same thing: the same source, formula and mandatory filters, literals included,
   so `STATUS = 'paid'` never matches `STATUS = 'refunded'`. It then writes `plan.json`, which
   marks each item `create`, `revise` or `reuse` and lists every refusal by item and field.
   It also edits `metrics.json` in two ways: it fills a missing `source_id`, and a metric that
   matches one live metric takes that metric's `source_id`, `name` and `display_name`, since a
   name is frozen at create. It changes nothing else there. A
   warning that a revision "changes what live … measures" means its population changes: show
   the person both definitions in the preview before it is sent.
3. On REFUSED, fix exactly the named fields of the refused items and run it again. Never touch
   items that passed. After three rounds, hold what is still refused and report it.
4. **Test against the warehouse** when a query tool is recorded. For each metric, run one read
   with the parameters at their defaults:
   `SELECT <formula> AS value FROM <source.table or source.sql> WHERE <mandatory filters>`.
   Record the outcome as `extensions.atlan_analytics.check`: `ok`, `failed` with the
   warehouse's message, or `untested`. Fix a failed metric or hold it. Never report a metric as
   tested when it was not.

## 5. Publish

Read [publish](metrics/publish.md): show the checked plan, carry forward existing scoped
authorization, then make the writes through the registry's MCP tools only.

## 6. STOP gate

Report:
- the counts of new, revised and reused metrics, saved queries and links, and the workspace;
- the published query revisions, and any left unreleased by workspace policy or a draft choice;
- how many metrics were tested against the warehouse and how many are only parsed;
- what was held, and why.

If publication was declined, say the drafts remain in `<context-dir>`. Record the counts and the
sources used in state as `context_run`.

- **Metrics mode:** set `phase` to `complete`, return the Metric Studio link if one was
  supplied, and stop. 

