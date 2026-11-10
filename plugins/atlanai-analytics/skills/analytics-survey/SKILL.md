---
name: analytics-survey
description: Survey a folder, dashboard link, BI export or warehouse and explain what it covers: dashboards, measures, query history and data. Use when asked what sources hold or which tables or pages to use. Not for building metrics (analytics-metrics).
---

# Survey analytics sources

Find what a source holds and what it could help analyse, then scope it. The result is a
saved setup run with an inspected inventory and, when asked, a selected scope. This skill
writes nothing to Atlan and generates no metric.

Read [shared conventions](../analytics-build/references/conventions.md) first: privacy,
read-only SQL and status reporting apply. `PROJECT` means
`python3 "<build-dir>/scripts/project.py" --run-dir "<run-dir>"`, where `<build-dir>` is the
`analytics-build` skill's directory beside this one (`${CLAUDE_PLUGIN_ROOT}/skills/analytics-build`
in Claude Code). Pass separate argv entries; zsh does not split a scalar holding a command.

## Routes

| Request | Follow |
| --- | --- |
| A folder, file, repository link or dashboard link | [Inspect and scope](references/inspect-scope.md), section 1 |
| Which pages, measures or history tables to use | [Inspect and scope](references/inspect-scope.md), section 2 |
| Find or check the warehouse query tool, or the argument `connect` | [Warehouse discovery](references/connect.md) |
| Read the warehouse's query history | [Query history](references/warehouses/query-history.md) |
| A warehouse's SQL dialect or statements | [Warehouses](references/warehouses.md), only the section for that warehouse |

Use the selected project's state when one exists; never resume another project silently.
Reuse a supplied source and an existing run; do not ask for either again.

## Survey

1. Resolve the run with `PROJECT location --workspace "<working-directory>"`, unless a run
   directory was supplied. The resolver creates nothing.
2. Inspect: `PROJECT survey "<absolute-path>"` for a folder or file, a clone for a repository
   link (never run its setup scripts), and the authorized host tools for a report link.
3. Explain the result in two or three sentences: the source types and their subject, how they
   complement each other, and two or three analyses they could support. Distinguish sample,
   available, needs review, held and scan-limited inputs. Keep file names and diagnostics in
   details, shown when asked.

Counts alone do not show understanding. Use the inspector's actual count and noun: 110 history
records are not 110 metrics. Never invent tables, measures, user behaviour or findings.

## Stopping point

A survey-only request ends with that explanation and the one next step: generate metrics from
these sources (the `analytics-metrics` skill), or the guided setup (`analytics-build`). Inside
the guided flow, this procedure is step 1, and section 2 of
[inspect and scope](references/inspect-scope.md) is step 3; return to that flow after each.
