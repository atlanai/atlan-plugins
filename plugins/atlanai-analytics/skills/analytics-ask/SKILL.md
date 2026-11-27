---
name: analytics-ask
description: "Atlan Analytics: answer data questions from metrics and saved queries in Atlan Trident, or a setup kept on this machine. Use for totals, rates, trends, breakdowns and follow-ups. Not for setup or writing metrics (analytics-build, metrics-generation)."
---

# Answer a data question from managed context

Read [shared conventions](../analytics-build/references/conventions.md) first; its **Source of
truth**, read-only SQL and safety rules apply. Answer here without a new project,
eval, agent or Atlan Trident write. It needs no subagent. Between calls, write nothing of your own: no working notes, no line about a card before or after it,
no file path.

If a setup run was selected, or this folder holds one kept local, follow
[setup retrieval](references/retrieval.md) and return. Otherwise continue
below using existing Atlan context. Ordinary questions need no local run, publication
receipt or DuckDB. Never search the whole machine or substitute drafts for released context.

## 1. Frame the question

From the question, write down the measure, the time window, the population and the grain, and
whether it asks for **one value** (a lookup) or **an explanation** (a trend, a comparison, a
driver of a change). Ask only when a missing piece would change the answer, and then once;
otherwise take the narrowest reasonable reading and say "I read this as <reading>. Tell me if
you meant something else."

## 2. Find the definitions

1. Open a session as **Atlan Trident calls** in the conventions describes, with the question
   as the `objective`. Its first call is step 2's search; send nothing else until it returns.
2. Search released metrics in every workspace at once: call
   `extension__metric__search_metrics` with the measure and its business nouns as `query`,
   `semantic: true`, `include_definition: true`, `definition_view: "compact"`,
   `definition_limit: 1` (2 when the top two names both fit the measure) and no
   `workspace_id`. Each item names its workspace and release receipt; the first carries its
   definition, cut to the calculation. If the call refuses
   `definition_view` or `semantic` by name, drop that argument for the session. Semantic search can drop a released metric whose name the query lacks: an
   empty result proves nothing.

   **A hit is a candidate, not an answer.** The right metric can rank low. Keep the hit
   whose name or formula measures what the question asks (revenue is a sum of money; a
   count of crew is not revenue), allowing the synonyms its description lists. Sharing a
   dimension word such as "region" is not a match. Only when no hit of either search below
   measures it is the answer "No metric in Atlan Trident covers <measure>": go to **Nothing found**, naming
   near misses: "Close, but measuring something else: <names>."
3. Send these together after the search, as one `mcp__call_read_tools` call when listed:
   - **The keyword search, when no semantic hit measures it**: the same search with
     `semantic: false`. Keep `include_definition`: without it, unreleased heads return.
     Once the session is open, send both searches together.
   - **The kept hit's definition, when it is not the first**: `extension__metric__search_metrics`
     with its exact `name`, the `workspace_id` that hit carries (required with a name),
     `include_definition: true` and `definition_view: "compact"`. Never answer from the
     definition attached to another hit.
   - **The warehouse**, unless this conversation already holds it: step 3 below.
   - **Saved queries**, only when no semantic hit measures it or the person asks for one:
     `extension__metric__search_queries` with `query`, `limit: 5`, `include_sql: false` and the
     metric's `workspace_id` when you have one. Then fetch the SQL of the one query you will use
     by `name` and its `workspace_id`. Without the metric tools, use `registry__search_artifacts`
     with kind `metric` or `sql_query` and read the hit at a release, as step 4 says.

   An older metric app refuses a search with no `workspace_id`: repeat it in the workspace of
   the best `registry__search_artifacts` hit (kind `metric`, the measure as `query`) or the
   first of `registry__get_artifact_counts` (kind `metric`, `group_by` `workspace_id`), never
   workspace by workspace.
4. Use only released revisions: a rejected query is skipped, an unreviewed one is a caveat, and
   a usage note or rule is followed. Read a metric or query that came without its release with
   `registry__get_artifact` and `channel: "latest"`; a missing or withdrawn release never falls
   back to a head. Keep each returned id, `version_id`, `release_id` and `channel`.

**Nothing found** is an answer too: write your own SQL, and step 5's **Based on** says so.

## 3. Reach the data

For a project run on this machine (SQLite or DuckDB), run each query with
`PROJECT execute` as [project setup](../analytics-build/references/project.md) describes,
never by opening its database file.
This is an available data connection; do not ask for Snowflake or a new MCP server. Keep
the binding across turns; use fresh released definitions and query results.

Find the warehouse query tool with steps 0 to 2 of
[connect](../analytics-readiness/references/connect.md): a record this conversation
or a project holds, then a SQL tool behind Atlan (`mcp__search_tools`), then the harness's own
connectors. Hold what you find, or that there is none, for the conversation.

With no warehouse tool, do not stop or run the rest of discovery for a one-off
question. Open with "I can't reach your warehouse through Atlan. An Atlan admin can add it
from the Atlan Store." Then offer the smallest thing that answers it:
- **Run the SQL yourself**: "I'll write it from the definition for you to run." Use its
  dialect ([warehouses](../analytics-readiness/references/warehouses.md));
- **Paste a result**: "Paste a query result here, or attach a CSV export."

End with "Next: paste the result, and I'll answer from it." The published definition still
decides the formula and filters, whatever the data source.

A released dashboard metric may carry
`extensions.atlan_analytics.dashboard_validation.parity: unmeasured`: answer fresh from the
warehouse anyway, keep the metric's scope and give its **Good to know** line once. Never call
the result dashboard-validated or reuse the report's cutoff as the warehouse's. A recorded
failed check needs correction.

## 4. Query and check

Apply [explanation checks](references/answer-checks.md) to numerical claims.

1. Write one SELECT or WITH statement that keeps the definition's formula, population, mandatory
   filters and grain, adding only the filters the question needs. Check column names against
   the definition or the table's columns; never guess one.
2. Run it with the warehouse query tool, with a LIMIT on anything returning rows. Show the
   answer card first, the `show_first` of: in a setup or demo run, the answering `PROJECT execute`
   with `"answer": {"question": "<question>", "metrics": ["<each used>"]}` (a warehouse plan's
   `next` names the follow-up); with no run, `python3 "<analytics-build dir>/scripts/project.py"
   visual answer --input -` with those and the result's `columns` and `rows`.
3. Before answering, check the result:
   - the window is complete, or the answer says it is partial (this week so far);
   - a rate has a non-zero denominator, and segment shares add up to the total;
   - a join did not multiply rows (compare the counts when in doubt);
   - the number is plausible against the definition (no all-NULL or zero where a count is
     expected). If not, re-check the filters before answering.
4. For an explanation, break the change down by the definition's dimensions, one at a time,
   and report the largest contributors and their shares. Say "associated with",
   not "caused", unless an experiment shows it.

## 5. Answer

Lead with the answer in one or two sentences: the number, its definition and window ("CSAT,
good ÷ answered ratings, Q2 2026: 94.1%"). Then, in the layout of
[definition links](references/definition-links.md):
- **Based on**: always, one line per metric or saved query used, linked to its page in Atlan Trident,
  never a bare id in prose or caveats. Resolve the organization automatically through the
  authenticated self-profile; never ask for its slug or URL. With no metric: "My own SQL. No
  metric in Atlan covers <measure>. I assumed <formula and filters>.";
- **Good to know**: an unreviewed query, a partial window, an assumption about the question, or
  which source answered when two conflicted, and why (the conventions' **Source of truth**);
- the SQL, only when the person asks to see it.

## 6. Close the gap

For missing definitions, corrections or a request to save/share the answer, follow
[answer follow-ups](references/ask-followups.md). For a dashboard from the answers, a
question no published metric covers, or a wrong rule, follow [next steps](references/next-steps.md).

End with one `Next:` line: one a step above names, else "Next: ask a follow-up, or ask to
see the SQL." A setup run ends as [retrieval](references/retrieval.md) says.
