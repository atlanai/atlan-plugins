---
name: atlan-analytics-ask
description: "Answer a data question now from the organization's published metrics and saved queries in Atlan, run against the warehouse: a single number, a trend, a segment comparison or what drove a change. Says which definition it used, labels an answer no published metric covers, and offers to propose the missing or corrected metric to its owners. Use for questions such as “what was revenue last quarter by region” or “why did weekly active users drop”, when no talk-to-data agent should be built."
---

# Answer a data question from governed context

Read [shared conventions](../atlan-analytics/references/conventions.md) first; its **Source of
truth**, read-only SQL and safety rules apply. This skill answers in this conversation. It
creates no project, eval, agent or registry write, and needs no subagents, so it works in any
harness.

## 1. Frame the question

From the question, write down the measure, the time window, the population and the grain, and
whether it asks for **one value** (a lookup) or **an explanation** (a trend, a comparison, a
driver of a change). Ask only when a missing piece would change the answer, and then once;
otherwise take the narrowest reasonable reading and say so in the answer.

## 2. Find the definitions

1. Open a registry session as **Registry calls** in the conventions describes, with the question
   as the `objective`.
2. Pick the workspace. Call `registry__list_workspaces`; with one, use it. With several, search
   each for the measure (step 3) and ask which one, recommending the one whose metrics match.
3. Search the workspace for the question's key terms: the measure, its business nouns and the
   likely metric names. Use `extension__metric__search_metrics` with `workspace_id`, `query` and
   `include_definition: true`, and `extension__metric__search_queries` with `workspace_id` and
   `query`, when they are listed. Without `include_definition`, the metric search returns heads,
   which may be unreleased drafts. With it, the search returns released revisions with their
   receipts and definitions. The query search returns only released revisions. An empty
   published result is the answer "no published metric"; never fall back to the head search
   for a definition. When those tools aren't listed, use `registry__search_artifacts` with kind
   `metric`, then `sql_query`, and read each hit at a release as step 4 says.
4. Read the one or two most relevant results exactly as step 1 and the **Rules** of
   [the agent template](../atlan-analytics/references/eval/agent-template.md) say: only a
   released revision counts, a rejected query is skipped, an unreviewed one is a caveat, and a
   usage note or rule skill is followed. Keep each returned `version_id`, `release_id` and
   `channel`.

5. When the question's business terms need resolving (which table holds a customer, how orders
   join to accounts) and the Ontology app is installed, use the `atlan-ontology` skill's
   context mode for entity names and join paths. A released metric still decides the formula,
   population and filters.

Nothing found is an answer too: say the workspace has no published definition for the measure,
then continue with your own SQL and its assumptions spelled out (step 5 labels it).

## 3. Reach the data

Find the warehouse query tool with [warehouse discovery](../atlan-analytics/references/connect.md) (`atlan-analytics connect`). With no project, hold
its record in this conversation instead of a state file.

No warehouse is a fallback, not a stop. Ask for the smallest thing that answers the question:
- **Paste or attach the result** of a query, or a CSV export;
- **Run the SQL yourself**: write the statement from the definition for the person to run, then
  answer from what they paste back.

The published definition still decides the formula and filters, whatever the data source.

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
- **Source**: the metric or saved query used, by name, with its release; or **Not governed:
  no published metric covers this**, followed by the formula and filters you assumed;
- **Caveats**: an unreviewed query, a partial window, an assumption about the question;
- the SQL, only when the person asks to see it.

When a conflict between sources shaped the answer, say which one answered and why, as the
conventions' **Source of truth** says.

## 6. Close the gap

Offer the propose flow once, when either happened:
- the answer is **Not governed**: offer to propose a new metric to the workspace's owners;
- the person **corrects** a published metric's number or definition: offer to propose the fix.

On a yes, follow [propose mode](../atlan-analytics/references/propose.md) from its step 1. It
previews before writing and never changes a metric itself. Never propose without asking.

When the person wants the answer kept or shared as a document, chart or link, the core
plugin's `atlan-record-outputs` skill records it as an Atlan output; name the metric it used.

End with one status line from the conventions.
