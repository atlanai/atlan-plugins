# Offer the use cases, each with its tables

Read in the "Let discovery decide" branch, after discovery. The other two branches do not
use this file: the person already named the dashboard or the tables, so they go from the
access check straight to the confirmation in [the interview](intent.md). Demos skip it too,
and so does a resume with a saved selection.

Discovery ends in a short menu of use cases, never a single suggestion. The person picks
which to build.

## 1. Get the use cases

`source-survey` `discover` returns two to four use cases, each with the tables that power
it. With a BI source, `source-survey` `candidates` adds the metric names the report holds.
Neither generates SQL or runs a check on a metric.

- With no subject named: the use cases are the most used subjects in the warehouse, most
  used first.
- With a subject typed in card 1: the distinct things that subject's tables can answer (for
  sales: pipeline and win rate, rep activity, account coverage).
- A dashboard discovery found in the working folder, or a set of metrics already in Atlan,
  is named on the use case it belongs to: "Tables: … Dashboard: pipeline-review.pbix".

## 2. One card, kept short

The question's own text is one line of what was found and where the usage came from:

"Found: Snowflake. Most used across your account in the last 7 days."

Then "Which should I build?", one option per use case, with multiple selection where the
host allows it:

| Part | What goes there |
| --- | --- |
| Label | The use case, two to four words: "Sales pipeline" |
| Description | One line of what it answers, then its tables: "Pipeline by stage, win rate and slipped deals. Tables: FCT_OPPORTUNITY, DIM_ACCOUNTS, DIM_REPS_CURRENT" |

Rules for the card:

- The use case line is at most twelve words and says what the person could ask, not what
  the tables are.
- Two to five tables per use case, by table name only. Say the schema once in the found line
  when every table shares it; otherwise prefix only the tables that differ.
- Mark the most used use case recommended. Order the rest by use.
- Nothing else in the card: no row counts, query counts, column names, join keys,
  exclusions, snapshot notes or sample questions. Those are details.

Where the host has no question tool, print the same thing as a short list, one line per use
case with its tables under it, and end the turn.

The person can pick one, several, or type a change ("add the snapshot table", "only
pipeline"). A table they add is checked against the warehouse before it is accepted. A table
or measure the sources do not show is never invented, and nothing is described as built or
checked.

**Details on request.** What discovery worked out about the chosen tables (how they link,
what will be left out, how much each is used) is kept in the run and shown only when the
person asks, or in one sentence after they choose: "Building sales pipeline from 3 tables.
I'll leave out the test accounts on the exclusion list."

For an understanding-only request, the menu is the result; no build follows unless asked.

## 3. Then clarify and confirm

With the choice made, ask [the interview's clarification](intent.md) only for what discovery
could not settle about the chosen use cases, then its confirmation. Record the selection in
the run. After the yes, enter [Build](build-check.md): call `metrics-generation` `generate`
with the tables of every chosen use case and the whole of any dashboard named on them. A
report is built whole, every page and measure, and query history is scoped to the chosen
tables. The collection review's representative metric is the one closest to the first chosen
use case.

When discovery finds nothing to build from, do not ask this question. Say the one thing that
is missing and offer `analytics-demo`.
