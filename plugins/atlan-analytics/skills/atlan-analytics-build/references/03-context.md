# Phase 3 — governed context: metrics and saved queries

Goal: the definitions the agent will look up before it writes SQL, published to the project
workspace:
- `metric` artifacts: formula, population, filters, dimensions
- `sql_query` artifacts: a question paired with trusted SQL

This is the longest phase. With the metrics-agent it is typically 30–60 minutes, mostly
background and parallel workers.

## Metrics-only entry

Only enter here when the router selected `metrics`. A full agent build starts at section 1.
The outcome is metrics and saved queries in the selected workspace, not a new analytics agent.

1. Open a registry session as **Registry calls** in the conventions describes, then call
   `registry__list_workspaces`. Keep the session id for registry calls. Confirm access to the workspace supplied by Metric
   Studio. If its scope is `all` or none was supplied, ask which accessible workspace to use.
   Do not choose a workspace silently or require an empty one.
2. Use `.atlan-metrics/<slug>/state.json`, with `mode: "metrics"`, `ids.workspace_id` and
   `session_id`. Resume only a metrics state for this workspace and the scope the user chose;
   when several match, ask which. Never read or resume `.atlan-analytics/*/state.json` here.
   Set `phase` to `context`. Keep source files and authored answers under this run directory;
   do not commit them. Do not call `EVAL init`.
3. Ask only for what is missing: the business area, the source (section 1), the SQL dialect,
   and a bounded set of tables. Use supplied scope and BI lineage to propose a small table
   set; confirm it and write `tables.txt`. No agent name, audience interview or eval questions.
   For a direct warehouse read, find and verify the read-only warehouse query tool with the
   `atlan-analytics-connect` skill when needed. An export file or BI model does not require a Snowflake connection
   or any other warehouse connection.
4. Read the workspace's existing `metric` and `sql_query` artifacts. Reuse matching definitions
   and source identities. Show proposed additions and changes separately; preserve unrelated
   artifacts and human edits. When definitions already cover the request, explain that and
   stop rather than creating duplicates.
5. Run sections 1–4 below with the chosen inputs. Here `<context-dir>` means
   `.atlan-metrics/<slug>/context`; the state file stays at the parent. Ask about a source only
   if it is still unknown. Use the metrics-agent's staged pipeline and its authored gates;
   never run its `scripts/run.py --push` wrapper, tracing/agent setup, or key rotation.
6. After the review or approved publication, set `phase` to `complete` and stop. Report new,
   updated and reused metrics and saved queries, and return the Metric Studio link if supplied.
   Do not advance to `eval_set`, register an agent, deploy, run a smoke test or run an eval.

For a full agent build, `<context-dir>` means `.atlan-analytics/<slug>/context` and the existing
project state still applies. Both paths use the same source, authoring and publish steps below.

## 1. Pick the source

If the source is not yet known, ask whether a Power BI or Tableau model covers the scoped
tables. Use the formats supported by the metrics-agent checkout: a Power BI model extract
or a Tableau workbook/data-source file. A BI model holds the definitions people already read
on dashboards, and the metrics-agent merges it with query history. It reads only Power BI and
Tableau; definitions from other BI tools come in by hand (section 5).

Then ask:
- `<slug> · context · where the trusted SQL comes from`
- **Query history plus the BI model (Recommended when a model exists)**: both lanes, merged
- **The warehouse's own query history (Recommended otherwise)**: what analysts actually ran
  against the scoped tables
- **The Atlan context app in Snowflake** when the account has it installed: the
  `snowflake-context-source` skill reads the app's query shapes with the analyst's own token
  and comes back here at section 2, step 3
- **A query-history export file** the user already has (JSONL or CSV of SQL + counts)
- **None: we write a few definitions together** (section 5)

If the user offers a benchmark or eval file here, keep it out of this workflow. In a full
agent build it can be used in phase 4. Eval answers must never become context.

## 2. Pull and clean the history

For a supplied export, start at step 2. For a BI-only source, skip this section.

1. With the warehouse query tool recorded in state, read the last 90 days of successful SELECTs
   that touch the scoped tables. Aggregate per distinct text, in pages of at most 5,000 rows.
   On Snowflake:

   ```sql
   select query_text as sql, count(*) as exec, count(distinct user_name) as users
   from snowflake.account_usage.query_history
   where execution_status = 'SUCCESS' and query_type = 'SELECT'
     and start_time >= dateadd('day', -90, current_timestamp())
     and (query_text ilike '%<TABLE_1>%' or query_text ilike '%<TABLE_2>%' …)
   group by 1 order by 2 desc limit 5000
   ```

   On Databricks:

   ```sql
   select statement_text as sql, count(*) as exec, count(distinct executed_by_user_id) as users
   from system.query.history
   where execution_status = 'FINISHED' and statement_type = 'SELECT'
     and start_time >= current_timestamp() - interval 90 days
     and (statement_text ilike '%<table_1>%' or statement_text ilike '%<table_2>%' …)
   group by 1 order by 2 desc limit 5000
   ```

   Record the window's absolute start and end dates for reproducibility. For another SQL
   dialect, use its documented read-only history source rather than these statements.
   If history access is denied, ask for an export file instead.
2. Write the rows to `<context-dir>/querylog.jsonl`, one `{sql, exec, users}` per line.
   Never include user names.
3. Clean them:
   `EVAL filter-querylog <context-dir>/querylog.jsonl <context-dir>/querylog.clean.jsonl`.
   Report the counts by reason in one line ("dropped 1,059 of 5,324: 412 person ids, 316
   names …").
4. If a database name in the SQL is a mirror of the real one (a copy or a benchmark prefix),
   normalise it to the real name and say so.

## 3. Run the metrics-agent (preferred)

The metrics-agent is open source at `https://github.com/atlanhq/metrics-agent`. Get a checkout:

1. If the user names an existing checkout, use it. Otherwise look for `~/metrics-agent`.
2. If there is none, clone it: `git clone --depth 1 https://github.com/atlanhq/metrics-agent ~/metrics-agent`.
   This downloads code you will then run, so it is outward-facing: show the command and ask
   first.
3. Record the checkout path and its `git rev-parse --short HEAD` in `state.json` as
   `context_tool`, so the run says which version produced its context.
4. If it has no `.venv`, follow its `README.md` Quickstart and use its declared dependencies.

Read its `README.md` and `.claude/skills/run-pipeline/SKILL.md` for the checked-out version's
stage arguments and gate formats. Follow the **stage-by-stage** pipeline, with these limits:
- Write a small non-secret env file yourself, with the registry base URL, the workspace id and
  `VQA_TRACE_OFF=1`.
- Mint the registry token per command from the `atlanai` CLI.
- Run each stage in its own Bash call.
- Do not invoke `scripts/run.py --push`, `run_pipeline.sh --push` or `t0_agent.py`. Their
  identity/tracing setup is not needed to create metrics and can create agents or rotate keys.
  Do not create an agent, experiment or eval dataset as part of this context step.

1. `s0_inputs.py`: `--scope <slug> --tables-file tables.txt --witness-mode parse
   --dialect <chosen dialect>`, plus the registry target: `--publish-target
   registry --registry-base-url <base URL> --registry-workspace-id <workspace id>`. With a BI
   model, pass `--bi <file or extract directory> --bi-vendor powerbi|tableau --bi-source file`.
   With history, pass `--querylog <clean> --querylog-source file` and its frozen window. Restate
   the actual profile: `bi-only`, `history-only` or `both`. Parse-only means the SQL is not run.
2. With a BI model, run the BI lane: `a1_parse_bi.py`, `a2_question_units.py`,
   `a3_translate.py`, optionally the `a3b_review` gate, `a4_witness_bi.py`, then
   `c1_normalise.py`, `c2_merge.py`, the `c3_questions` gate, `c4_gates.py`, `c5_emit.py`.
3. With history, run `h0_corpus.py`, `h1_shapes.py`, `h2_measures.py`, `h5_verified.py`. Then run
   `m1_merge.py --pre-describe`, which merges the BI and history lanes.
4. Re-check `verified_queries.json` for identifying literals in the SQL and in `observed_values`:
   names, emails, IDs, customer names. If any turn up, tighten the filter and start a new run
   directory. Never patch a layer in place.
5. For each authored gate:
   - run `--emit-prompt`;
   - split the bundle into chunks and give each chunk to a fork worker with the gate's skill
     from the metrics-agent's `.claude/skills/`;
   - each worker writes `answers/<gate>/part-N.json` and self-checks against the stage's rules;
   - ingest all parts at once with `--from '<dir>/part-*.json'`.

   Workers never read eval files.

   | gate | chunks | lessons |
   |---|---|---|
   | a3b review (BI, optional) | one bundle | Review the compiled SQL against its widget; answer with rule rows, never SQL. |
   | c3 questions (BI) | one bundle | One business question per BI candidate; state each restriction as a claim, never SQL. |
   | m2 describe | ~90 statements each | Dedupe `name` across chunks before ingest. Rename collisions by what differs (date field, scale, denominator). |
   | x1 propose | ~90 statements each | Normalise every `entity` to the statement's fully qualified table before x2, or one table splits into several groups. |
   | x2 merge | one worker per table family | Names must be unique across the whole layer, so put the subject in generic names. The same formula on the same table must merge. |
   | x3 cards | ~17 metrics each, balanced by size | x3 refuses a count whose name ends in "by X". Rename it at x2 and re-ingest x2; cards are keyed by name. |
   | x4 relate | ~22 tasks each | Grain variants are symmetric, so dedupe reversed pairs across chunks. |

   After m2, run `m1_merge.py` again without `--pre-describe` to apply the descriptions, then
   `d1_candidates.py`. Run `d4b_consistency.py` after x3. d4b must be clean; its advisory
   flags are fine.
6. Dry runs: `r1_envelopes.py`, `r1b_envelopes.py`, `r2_mirror.py` (must be clean), `r3b_push.py`,
   `r4_relate_push.py`.

## 4. Publish (preview → yes → write)

1. Check the workspace with `registry__list_artifacts` (kinds `metric`, `sql_query`). Match
   existing definitions and source identities before adding anything. Reuse matching artifacts;
   distinguish new definitions from proposed revisions, and preserve unrelated and human-edited
   artifacts. Do not require an empty workspace.
2. Show the plan and ask:
   Include the exact relationship list in the preview, with both endpoints, relation type and
   assessed metadata. Build it only from this run's `metric_relationships.json` `edges` and
   `metric_cards.json` `metric_refs_by_vq`. Save the approved list in the run directory;
   the approved count E covers only that list.

   - `<slug> · context · publish to <workspace>`
   - **Publish N metrics, E links and Q saved queries (Recommended)**
   - **Show me 5 sample cards first**
   - **Not yet**
3. **Metrics**: `r3b_push.py --push` as a background Bash call. Pass `--allow-new` only for
   new metrics included in the approved preview; otherwise
   append only the approved revisions. Verify the created counts. Never pass the archive or
   rename flags. Saved queries use the MCP path below; do not also run `r3_push.py`, which in
   current metrics-agent versions publishes those same saved queries.
4. **Saved queries**: call `registry__get_kind_schema` for `sql_query` once, then map each
   verified query to a body:
   - `name`: slug of the query name, unique
   - `display_name`: the question
   - `description`: the description
   - `definition`: `spec_version: "1.0-draft.1"`, `sql`, `question`, `dialect`,
     `review_status: "unreviewed"`, `usage {execution_count, user_count}`,
     `analysis {tables, filters, dimensions}`, a `usage_note` saying it was mined from history
     and never executed, and `extensions.metrics_agent.vq_id`
   - `source_id`: `<slug>/<vq_id>`

   Send new ones with `registry__create_artifacts` in chunks of about 25. For an existing
   source identity, reuse it unchanged or preview the revision and update the existing artifact
   through the registry's update tool; never create a duplicate. Save each returned id against
   its `vq_id` in `<context-dir>/published_queries.json`. These ordinary writes inherit Registry
   workspace/channel release policy: `auto` publishes each new revision, while `manual` leaves
   it unreleased. A deliberate draft can send
   `auto_release: false` where policy permits. A producer's `verified` claim does not decide
   publication, and this workflow does not backfill releases for untouched existing queries.
   Resolve and record the actual release id, channel and exact version id for published queries;
   never assume a successful create necessarily published. For manual-policy or draft writes,
   use Studio or an authenticated Registry release client when publication is authorized:
   `POST /registry/v1/artifacts/sql_query/{id}/releases` with exact `version_id` and `channel`.
   Discover the actual write capability; do not invent an MCP release tool. If that client is
   unavailable, leave publication pending and explain that agents cannot consume those drafts.
5. **Links**: Keep `r4_relate_push.py` in dry-run mode. Its write path derives links from every
   live metric in the workspace, including metrics outside this run's preview.
   After both kinds exist, publish only the approved relationship list through MCP:
   - Resolve each approved metric to its actual artifact id using `published_v2.json`, including
     its adopted-identity mapping. Resolve each approved `vq_id` through
     `<context-dir>/published_queries.json`. Confirm both endpoints exist in the selected workspace.
     If an identity is missing or ambiguous, hold that link and report it; do not guess by name
     or redirect a folded query to a different statement.
   - Read `registry__get_kind_schema` for each source kind and use only its declared relation
     types and target kinds. Report unsupported links without writing them.
   - For both edge types, read existing links first, including `depends_on` links already
     derived by the metric posthook. Match by source artifact id, target artifact id and `rel_type`.
     Reuse an existing link unchanged; never update it through MCP, which cannot preserve its top-level
     metadata. If the assessed metadata conflicts, hold that proposal for review without a write.
     Create only approved missing links; preserve unrelated links and all existing metadata.
   - For each approved metric edge, call `registry__relate_artifact` with the resolved
     `artifact_id`, `subject_id` and `rel_type`. Keep the original edge attributes, and preserve
     its assessed `label`, `role` and `confidence` as attributes: the MCP tool accepts no
     top-level fields for those values. Include that representation in the preview.
   - For each approved query-to-metric edge, use the saved query as `artifact_id`, the metric
     as `subject_id`, and `source_of` as `rel_type`.
   Verify each write by reading the relationship back, and record created, reused and
   held links in the run.
   Do not derive additional links from other workspace artifacts or use a workspace-wide push.
6. Verify: count by kind with `registry__list_artifacts`, then `registry__search_artifacts` one
   sample question with `kind` `sql_query`. It should come back near the top.

## 5. Fallback: definitions by hand (no metrics-agent, no history)

If no source is available, offer to draft a few definitions together. In metrics-only mode,
ask for one business question; do not start the full agent interview. For each question:
1. Draft the metric: formula, population, filters, grain.
2. If a read-only warehouse connector is available, test its SQL with a LIMIT of 50. Otherwise
   keep the draft unverified and say so; do not claim a number was checked.
3. Show the user the definition and SQL, with any check result.

On a yes:
- Call `registry__get_kind_schema` for `metric`.
- Create the metric with `registry__create_artifact`, and the trusted SQL as a `sql_query`
  linked `source_of` to it.

Five to ten well-defined metrics is a good first layer.

**STOP gate.** Report the actual counts of new, updated and reused metrics, saved queries and
links, plus the target workspace. Report confirmed published query revisions and queries left
unreleased under workspace policy or an explicit draft choice. Say whether SQL was only parsed
or checked against the warehouse; do not claim it was executed when it was not. If publication was declined, say the
drafts remain local. Record the counts and `context_run` in state.

- **Metrics-only mode:** set `phase` to `complete`, return the Metric Studio link and stop.
- **Full agent build only:** set `phase` to `eval_set` and wait at the phase gate.
