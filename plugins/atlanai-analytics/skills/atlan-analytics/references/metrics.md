# Metrics: build the metric layer

Goal: `metric` and `sql_query` artifacts in a workspace, made from definitions people already
trust, each one checked against the registry's contracts and previewed before it is written.
Three entries use this file:
- **metrics mode** (`/atlanai:atlan-analytics metrics` from the public Atlan marketplace,
  or `/atlanai-analytics:atlan-analytics metrics` from the separate add-on) starts at section 0
  and stops at section 6;
- a **full agent build** reaches it from phase 3 and starts at section 1;
- **project setup** arrives after its combined repo survey. Preserve that route's state,
  use both model and history evidence, and return to its publication receipts and questions.

The plugin owns the interview and publication. New supported runs use the isolated metrics
module; legacy authored runs use the standard-library helpers. Below:
- `MINE` means `python3 "<skill-dir>/scripts/metrics_mine.py"`. `<skill-dir>` is this skill's
  directory: `${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics` in Claude Code, and otherwise the
  directory that holds this file's `references/`.
- `<context-dir>` is `.atlan-metrics/<slug>/context` in metrics mode, and
  `.atlan-analytics/<slug>/context` in a build.

`MINE` prints one JSON object. It exits 2 when BLOCKED, and 1 with `"status": "REFUSED"` when
a check refuses an item.

Setup callers follow [shared setup](setup.md) for ordering. They skip section 0's destination
question when the workspace is unknown. Use provisional `PROJECT check` and `PROJECT preview`
locally, then fetch the live schema and existing definitions and run `MINE check` after choosing
the destination. Do not report create/revise/reuse counts before that reconciliation.
Explicit metrics mode and full builds retain their entry contracts below.

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

A full build already has its workspace, `tables.txt` and state from phases 1 and 2.

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
| A finished metrics-agent run | the person has its run directory | `MINE import-run`, then section 4 |
| Query history | a warehouse query tool, a vendor CLI, or an export file | mined metrics and saved queries |
| Definitions written together | always | five to ten hand-drafted metrics |

Ask only if the source choice is unresolved, with the options actually in reach. A supplied
BI file, asset URL or query-history export already settles this decision:
- `<slug> · context · where the definitions come from`
- **Declared models plus query history (Recommended when both are in reach)**: the models give
  the definitions, and history shows what is used and what the models miss.
- **Query history (Recommended when no model is in reach)**: what analysts actually ran against
  the scoped tables.
- **Only the declared model**: no warehouse history needed.
- **Import a finished metrics-agent run**: the run directory's `registry_payloads/`.
- **We write a few definitions together**: section 3, by hand.

Skip this choice when the request already selected a source, including `query-history`, or
project setup has surveyed the repository. That survey selects both model and history when usable.
Use a supplied query export before asking for warehouse history access. Discover a data
connection only when needed for checks or fresh answers, and explain that purpose first.

If the person offers a benchmark or eval file here, keep it out of this workflow. In a full
build it is used in phase 4. Eval answers never become context.

## Use the metrics-agent module for new supported runs

For new query-history, Power BI, Tableau and supported semantic-model sources, follow
[the metrics module](metrics-engine.md) instead of sections 2 and 3 below. It runs the maintained
metrics implementation and returns the same context files. Continue at section 4
for the existing Registry check and publication flow. The module owns generation only.

A finished metrics-agent run can still use `MINE import-run` when it already has Registry
payloads; a completed embedded core run uses `ENGINE export` without a Registry package.
For an unsupported declared model or an explicitly chosen by-hand draft, use sections 2 and 3
as a named legacy authoring path and disclose its evidence level. Never switch to that path to
hide a compiler refusal. Existing in-flight legacy projects resume their original procedure.

## 2. Query history into shapes

Skip this section when no history source was chosen.

1. Pull 90 days of successful SELECTs that touch the scoped tables, grouped per distinct text
   with run and user counts, at most 5,000 rows, by the bulk path `warehouse.bulk` names.
   [Query history](warehouses/query-history.md) holds the statement for each warehouse and the
   fallbacks when access is denied.
2. Turn the pull into `<context-dir>/querylog.jsonl` without the rows passing back through this
   conversation: `MINE querylog <saved pull> <context-dir>/querylog.jsonl`, adding `--append`
   for each later page. The saved pull is the CLI's output file, the export, or the file the host
   saved a large tool result to. It reads all of these, keeps `{sql, exec, users}`, counts users
   without writing their names, and prints only counts. Never retype rows into a file yourself:
   when a page came back inline and the host saved no file, pull smaller pages rather than
   copying it out. Record the window's absolute start and end dates in state.
3. Drop identifying literals:
   `EVAL filter-querylog <context-dir>/querylog.jsonl <context-dir>/querylog.clean.jsonl`.
   Report the counts by reason in one line.
4. Group and rank:
   `MINE shapes <context-dir>/querylog.clean.jsonl <context-dir>/shapes.jsonl --tables tables.txt`.
   Each shape is one statement with its dates and numbers folded away. Quoted values that pick a
   population stay: `status = 'paid'` and `status = 'refunded'` are two shapes. Shapes that differ
   only in those values share a `family`, and each lists its values under `predicates`. A shape
   carries the most-run text, its run and user counts, and hints: tables, aggregates, group-bys,
   filters. Report the shape and family counts in one line.
5. If a database name in the SQL is a mirror of the real one (a copy or a benchmark prefix),
   normalise it to the real name and say so.

## 3. Author

Read [authoring](metrics/authoring.md) and follow its four passes:
1. describe and propose, per shape;
2. merge the proposals into metrics;
3. write the metric and saved-query bodies;
4. relate them.

Declared metrics skip the first pass. By-hand metrics start at the third pass.

Split big passes into chunks. Where this harness has subagents (Claude Code), give each chunk
to a worker, a fresh subagent from the Agent tool, with the authoring rules. Elsewhere, work through the chunks in turn. Workers
write their answer files only. You run every `MINE` command and every registry call. Workers
never read eval files.

The passes leave three files in `<context-dir>`: `metrics.json`, `queries.json` and
`links.json`. For a by-hand definition, test its SQL with a `LIMIT 50` when a warehouse is
connected, and otherwise mark it untested.

## 4. Check

1. Write the workspace's live artifacts to `<context-dir>/existing.json`, one
   `{id, kind, name, display_name, source_id, definition}` per artifact. Read definitions with
   `registry__get_artifact` for the metrics whose tables overlap this run's.
2. Run `MINE check --dir <context-dir>`. It checks every body against the registry's contract
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
  supplied, and stop. Do not move on to `eval_set`, register an agent, deploy, smoke test or
  run an eval.
- **Full agent build:** set `phase` to `eval_set` and wait at the phase gate.
