---
name: analytics-readiness
description: "Atlan Analytics: check you are ready to build and what to fix first, or see what a warehouse, folder or dashboard holds. Use when asked for a readiness check, what data exists, which tables to use, or if a source works. Not for building."
---

# Readiness check and source survey

It reads, it never writes to Atlan, it installs nothing and it authors no metric or SQL. Who
started it picks the flow:

| Started by | Flow | Follow |
| --- | --- | --- |
| A person asking whether they are ready to build: "run the readiness check", "am I ready to start?" | **Readiness check**: about seven questions, one read-only check per route after a yes, then a report of what to fix first | [Readiness check](references/readiness.md) |
| A person asking what their sources hold: "what do these dashboards cover?", "which tables should I use?", "can I use them?" | **Survey**: asks only for a missing source, shows what it found, and stops | [Survey](references/survey.md) |
| A skill naming a step below and its run: `analytics-build`, `metrics-generation`, `analytics-demo` or `analytics-ask` | **A step**: asks the person nothing, writes into the caller's run, and returns | [Survey](references/survey.md), then the step's procedure |

A question about named sources (a folder, these dashboards, these tables) is the survey, even
when it asks whether they can be used. A question about the person's whole setup before a build
is the readiness check. The readiness check uses only its own file,
[warehouse checks](references/warehouse-checks.md) and `scripts/readiness.py`: no step below and
no build helper, so it runs on any Python 3. The steps share that script for the warehouse CLI
listing and the dashboard-file check, and the build reads the readiness result through it.

## Steps

| Step | Does | Procedure |
| --- | --- | --- |
| `connections` | Lists the warehouse connections already configured on this machine, and whether each answers | [Connections, discovery and access](references/discovery.md) |
| `discover` | On a chosen warehouse, for a domain or with none: finds the schemas in use and the tables that matter, reads their columns and returns use cases, each with its tables | [Connections, discovery and access](references/discovery.md) |
| `access` | For sources the person named (a dashboard, a list of tables): whether each opens and can be read, and exactly what cannot | [Connections, discovery and access](references/discovery.md) |
| `inventory` | Reads every supplied source in full (models, dashboards, query history, SQL, local data) and says what they cover and could answer | [Survey](references/survey.md#inventory), then [inspect and scope](references/inspect-scope.md) section 1 |
| `plan` | Orders the sources to build from: governed metric catalogues and BI reports or models, then query history, then table definitions | [Inspect and scope](references/inspect-scope.md), section 2 |
| `scope` | Selects pages, measures and query-history tables | [Inspect and scope](references/inspect-scope.md), section 2 |
| `candidates` | Lists candidate metric names and the questions they would answer, ranked by relevance to a stated focus or problem. Names only | [Survey](references/survey.md#candidates) |
| `buildable` | Says whether work can proceed: source readable, data executable, Atlan reachable, published context present | [Survey](references/survey.md#buildable) |
| `connect` | Finds and checks the warehouse query tool | [Warehouse discovery](references/connect.md) |

`connections` and `discover` come first when a warehouse is the starting point. Run
`inventory` on a supplied file or folder before `plan`, `scope` and `candidates`; `buildable` and
`connect` can run alone. Query history follows
[query history](references/warehouses/query-history.md); a warehouse's dialect and statements
are in [warehouses](references/warehouses.md), only the section for that warehouse. Reuse a
supplied source and the selected run; never resume another project silently.
