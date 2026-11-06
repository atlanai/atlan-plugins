---
name: atlan-analytics-ask
description: Answer data questions in this chat using published Trident metrics and saved SQL, including a prepared local project and DuckDB. Use for follow-up questions after Analytics setup or questions about totals, rates, trends and breakdowns. Not for repo setup, metric authoring or a separate agent build (atlan-analytics).
---

# Answer a data question from governed context

Read [shared conventions](../atlan-analytics/references/conventions.md) first; its **Source of
truth**, read-only SQL and safety rules apply. This skill answers in this conversation. It
creates no project, eval, agent or registry write, and needs no subagents, so it works in any
harness.

Reuse the selected project run, or find this project's `.atlan-metrics/*/run.json`; ask if
ambiguous. Follow [project retrieval](../atlan-analytics/references/project.md#6-retrieval-contract-for-every-question)
and return. It replaces the generic discovery and fallback below with fresh MCP releases,
receipt checks and the recorded data binding. Local drafts are never the definition source.

## 1. Frame the question

From the question, write down the measure, the time window, the population and the grain, and
whether it asks for **one value** (a lookup) or **an explanation** (a trend, a comparison, a
driver of a change). Ask only when a missing piece would change the answer, and then once;
otherwise take the narrowest reasonable reading and say so in the answer.

## 2. Find the definitions

1. Open a registry session as **Registry calls** in the conventions describes, with the question
   as the `objective`. Its first call is step 2's search; send nothing else until it returns the
   session id.
2. Search released metrics in every workspace at once: call
   `extension__metric__search_metrics` with the measure and its business nouns as `query`,
   `semantic: true`, `include_definition: true`, `definition_view: "compact"`,
   `definition_limit: 1` (2 when the top two names both fit the measure) and no
   `workspace_id`. Each item names its workspace and carries its release receipt; the first
   carries its definition, which compact cuts to the calculation. If the call refuses
   `definition_view` or `semantic` by name, send it again without that argument for the rest of
   the session. An empty result is the answer "no published metric": go to **Nothing found**,
   and never fall back to a search without `include_definition`, which returns unreleased heads.

   **A hit is a candidate, not an answer.** Search ranks by wording, so it always returns
   something, and the right metric can rank low: "on-time departure rate" has put `otp_d15`
   eighth, under a turnaround metric. Read every hit's name and display name, not only the
   first, and keep the one whose name or formula measures what the question asks (revenue is a
   sum of money; a count of crew is not revenue), allowing the synonyms its description lists.
   Sharing a dimension word such as "region" is not a match. When no hit measures it, the answer
   is "no published metric"; name the near misses you set aside in one line of the answer.
3. Send these together, in the turn after the search:
   - **The kept hit's definition, when it is not the first**: `extension__metric__search_metrics`
     with its exact `name`, the `workspace_id` that hit carries (an exact name is refused
     without one), `include_definition: true` and `definition_view: "compact"`. Never answer
     from the definition attached to another hit.
   - **The warehouse**, unless this conversation already holds it: step 3 below.
   - **Saved queries**, only when no released metric answers or the person asks for one:
     `extension__metric__search_queries` with `query`, `limit: 5`, `include_sql: false` and the
     metric's `workspace_id` when you have one. Then fetch the SQL of the one query you will use
     by `name` and its `workspace_id`. Without the metric tools, use `registry__search_artifacts`
     with kind `metric` or `sql_query` and read the hit at a release, as step 4 says.

   An older metric app refuses a search with no `workspace_id`: take the workspace of the best
   `registry__search_artifacts` hit (kind `metric`, the measure as `query`), or the first of
   `registry__get_artifact_counts` (kind `metric`, `group_by` `workspace_id`), and repeat the
   search there. Never search workspaces one by one.
4. Use only released revisions: a rejected query is skipped, an unreviewed one is a caveat, and
   a usage note or rule is followed. Read a metric or query that came without its release with
   `registry__get_artifact` and `channel: "latest"`; a missing or withdrawn release never falls
   back to a head. Keep each returned id, `version_id`, `release_id` and `channel`.
5. When the question's business terms need resolving (which table holds a customer, how orders
   join to accounts) and the Ontology app is installed, use the `atlan-ontology` skill's
   context mode for entity names and join paths. A released metric still decides the formula,
   population and filters.

**Nothing found** is an answer too: say the workspace has no published definition for the measure,
then continue with your own SQL and its assumptions spelled out (step 5 labels it).

## 3. Reach the data

For a selected project run with `execution.kind: duckdb`, use its recorded Python runtime
and `project.py execute` as [project setup](../atlan-analytics/references/project.md) describes.
This is an available data connection; do not ask for Snowflake or a new MCP server. Keep
the binding across turns and use fresh released definitions and fresh query results.

Find the warehouse query tool with steps 0 to 2 of
[warehouse discovery](../atlan-analytics/references/connect.md): a record this conversation
or a project holds, then a SQL tool behind Atlan (`mcp__search_tools`), then the harness's own
connectors. Hold what you find, or that there is none, for the rest of the conversation.

With no warehouse tool, do not stop and do not run the rest of discovery for a one-off
question. Open the answer by saying no warehouse is reachable through Atlan, and that an Atlan
admin can add it from the Store. Then offer the smallest thing that answers the question:
- **Run the SQL yourself**: write the statement from the definition, in the dialect it names
  (that dialect's section of [warehouses](../atlan-analytics/references/warehouses.md)), for the
  person to run, and answer from what they paste back;
- **Paste or attach the result** of a query, or a CSV export.

End that turn with `NEEDS_CONTEXT`. The published definition still decides the formula and
filters, whatever the data source.

A released dashboard metric may carry
`extensions.atlan_analytics.dashboard_validation.parity: unmeasured`. Snapshot mismatch
or missing report references do not prevent a fresh warehouse answer. Preserve the metric
scope and state this limitation once; never call the result dashboard-validated or reuse
the report cutoff as the current warehouse cutoff. A recorded failed check needs correction.

## 4. Query and check

1. Write one SELECT or WITH statement that keeps the definition's formula, population, mandatory
   filters and grain, adding only the filters the question needs. Check column names against
   the definition or the table's columns; never guess one.
2. Run it with the warehouse query tool, with a LIMIT on anything returning rows.
3. Before answering, check the result:
   - the window is complete, or the answer says it is partial (this week so far);
   - a rate has a non-zero denominator, and segment shares add up to the total;
   - a join did not multiply rows: compare the count before and after when in doubt;
   - the number is plausible against the definition (no all-NULL or zero where a count is
     expected). If not, re-check the filters against the definition before answering.
4. For an explanation, break the change down along the definition's dimensions, one at a time,
   and report the largest contributors with their share of the change. Say "associated with",
   not "caused", unless an experiment shows it.

## 5. Answer

Lead with the answer in one or two sentences: the number, its definition and window ("CSAT,
good ÷ answered ratings, Q2 2026: 94.1%"). Then, briefly:
- **Source**: each metric or saved query used, by name, with its release, then its id and
  release id in code (`metric_… · rel_…`, `sql_query_… · rel_…`), so Atlan can count the
  answer toward it; or **Not governed: no published metric covers this**, followed by the
  formula and filters you assumed. Write the ids as they are, without explaining them;
- **Caveats**: an unreviewed query, a partial window, an assumption about the question;
- the SQL, only when the person asks to see it.

When a conflict between sources shaped the answer, say which one answered and why, as the
conventions' **Source of truth** says.

## 6. Close the gap

For missing definitions, corrections or a request to save/share the answer, follow
[answer follow-ups](../atlan-analytics/references/ask-followups.md).

End with one status line from the conventions.
