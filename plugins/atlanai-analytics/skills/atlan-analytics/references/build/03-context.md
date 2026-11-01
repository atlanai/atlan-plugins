# Phase 3: governed context: metrics and saved queries

Goal: the definitions the agent will look up before it writes SQL, published to the project
workspace:
- `metric` artifacts: formula, population, filters, dimensions
- `sql_query` artifacts: a question paired with trusted SQL

Follow [the metrics workflow](../metrics.md) from section 1, with `<context-dir>` set to
`.atlan-analytics/<slug>/context`. The project's state, workspace and `tables.txt` from phases
1 and 2 already apply. Its STOP gate sets `phase` to `eval_set`.

This is the longest phase: 20–60 minutes, depending on how many shapes the query history holds
and how many sources are in reach.

The eval set is not context. Questions and gold SQL written in phase 4 never feed this phase.
If the user offers a benchmark file here, park it for phase 4.
