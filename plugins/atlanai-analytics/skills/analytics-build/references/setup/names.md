# Suggest the tables, then confirm

Read after discovery, before runtime setup or generation. Demos skip this file: their source
choice selects what to build. A resume with a saved selection skips it too.

Discovery ends in a suggestion, and the person confirms it once. This is the only question
between the opening interview and metric generation.

## 1. Get the suggestion

`source-survey` `discover` returns the tables to build from, the reasons, and what it worked
out about them. With a BI source, `source-survey` `candidates` adds the metric names the
report holds. Neither generates SQL or runs a check on a metric.

## 2. One message, as the question's own text

1. **What was found**, one line of products and counts: "Found: Snowflake (3 schemas in
   use), 1 Power BI report, 30 days of query history."
2. **The suggested tables**, at most eight, as a table:

   | Table | What it holds | Why |
   | --- | --- | --- |
   | The table's name | One plain line | How it was picked: "read by the report", "queried 1,240 times by 14 people in 30 days", "holds the deal stage history" |

3. **What discovery worked out**, two or three short lines, stated as what will be done:
   the main things counted and how they link ("accounts link to opportunities on
   ACCOUNT_ID"), and what will be left out ("test and internal accounts, flagged IS_TEST and
   IS_INTERNAL").
4. **With a BI source**, its metric names by page with a count, and two or three questions
   the context will answer. Without one, two or three questions the query history shows
   people asking.
5. **The question**: "Build from these tables?"

| Option | Description |
| --- | --- |
| Build from these (recommended) | Metrics from the report first, then query history on these tables, checked on the warehouse. About how long |
| Change the tables | Say which to add or drop |

Names only: a table or measure the sources do not show is never invented, and nothing is
described as built or checked. A table the person adds is checked against the warehouse
before it is accepted.

For an understanding-only request, the suggestion is the result; no build follows unless
asked.

## 3. Build from the confirmed tables

Record the selection in the run, then enter [Build](build-check.md). Call
`metrics-generation` `generate` with the confirmed tables and the whole of any BI source: a
report is built whole, every page and measure, and query history is scoped to the confirmed
tables. The collection review's representative metric is the one closest to the chosen
domain.

When discovery finds nothing to build from, do not ask this question. Say the one thing that
is missing and offer `analytics-demo`.
