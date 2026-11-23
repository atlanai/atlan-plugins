---
name: source-survey
description: "Atlan Analytics: see what a warehouse, folder or dashboard holds: connections, key tables, what a source covers, metric names. Use when asked what data exists, which tables to use, or if a source works. Not for building metrics (metrics-generation)."
---

# Survey a source and check it is ready

A capability. It reads, it never writes to Atlan, and it authors no metric or SQL. A person
can call it directly; `analytics-demo`, `analytics-build` and `analytics-ask` call it as a step.

Read [shared conventions](../analytics-build/references/conventions.md) first: privacy,
read-only SQL and status reporting apply. `PROJECT` means
`python3 "<build-dir>/scripts/project.py" --run-dir "<run-dir>"`, where `<build-dir>` is the
`analytics-build` skill's directory beside this one (`${CLAUDE_PLUGIN_ROOT}/skills/analytics-build`
in Claude Code). Pass separate argv entries; zsh does not split a scalar holding a command.

## Who is calling

- **A flow called it** (demo, build or ask named the operation and the run): ask the person
  nothing. Use the inputs given, write the result into the caller's run, and return. The
  caller decides what the person sees.
- **A person called it**: ask only for a missing source, once: "Which warehouse, folder or
  dashboard should I look at?" Show the result as below and
  stop: no generation, destination question or publication pitch. If they then want metrics,
  hand to `analytics-build` with the survey already done.

## Operations

| Operation | Does | Procedure |
| --- | --- | --- |
| `connections` | Lists the warehouse connections already configured on this machine, and whether each answers | [Connections, discovery and access](references/discovery.md) |
| `discover` | On a chosen warehouse, for a domain or with none: finds the schemas in use and the tables that matter, reads their columns and returns use cases, each with its tables | [Connections, discovery and access](references/discovery.md) |
| `access` | For sources the person named (a dashboard, a list of tables): whether each opens and can be read, and exactly what cannot | [Connections, discovery and access](references/discovery.md) |
| `inventory` | Reads every supplied source in full (models, dashboards, query history, SQL, local data) and says what they cover and could answer | [Inspect and scope](references/inspect-scope.md), section 1 |
| `plan` | Orders the sources to build from: BI reports and models, then query history, then table definitions | [Inspect and scope](references/inspect-scope.md), section 2 |
| `scope` | Selects pages, measures and query-history tables | [Inspect and scope](references/inspect-scope.md), section 2 |
| `candidates` | Lists candidate metric names and the questions they would answer, ranked by relevance to a stated focus or problem. Names only | Below |
| `readiness` | Says whether work can proceed: source readable, data executable, Atlan reachable, published context present | Below |
| `connect` | Finds and checks the warehouse query tool | [Warehouse discovery](references/connect.md) |

`connections` and `discover` come first when a warehouse is the starting point. Run
`inventory` on a supplied file or folder before `plan`, `scope` and `candidates`; `readiness` and
`connect` can run alone. Query history follows
[query history](references/warehouses/query-history.md); a warehouse's dialect and statements
are in [warehouses](references/warehouses.md), only the section for that warehouse. Reuse a
supplied source and the selected run; never resume another project silently.

### `inventory`

1. Resolve the run with `PROJECT location --workspace "<working-directory>"`, unless a run
   directory was supplied. The resolver creates nothing.
2. Inspect: `PROJECT survey "<absolute-path>"` for a folder or file, a clone for a repository
   link (never run its setup scripts), and the authorized host tools for a report link.

### `candidates`: names and questions before metrics

The person commits to names, so a wrong focus costs one question and not a generation run.

1. Take names from the inventory only: declared measures in models and dashboards, and
   measures that recur in query history. Never invent a name the sources do not show.
2. Rank by relevance to the focus or problem the caller passed. With none, rank by how many
   sources show the measure.
3. Return at most ten, each with its name, one line of plain meaning, and where it came from
   ("From the tile <tile>", "From the model measure <measure>" or "Found in <N> past queries",
   with "1 past query" for one). When the caller source is a report, cover every page: group
   them by page with a count per page ("<page>: <N> metrics", with "1 metric" for one), the
   focus's page first, and do not cut the report to ten.
4. Add two or three questions the names would answer, phrased as things to learn and worded
   from the sources. No period, cause or number the sources do not hold.
5. Label every one "Found in your sources. No SQL written or tested yet." Do not preview
   SQL, run the engine or install its runtime for this operation.

### `readiness`: one of three answers

| Answer | Means | What the person sees |
| --- | --- | --- |
| `ready` | Sources are readable and, for answers, released context or an executor exists | "Ready" |
| `partial` | Some coverage is held: a missing export, an untested executor, a sign-in not yet done | "Partly ready: <what's missing>. Next: <the one step that unblocks it>." |
| `none` | No readable source and no published context | "Not ready: I can't read any of these sources, and Atlan has no metrics for them. Next: try the demo, which shows how this works on made-up data." (`analytics-demo`) |

Return the value (`ready`, `partial` or `none`) to a caller. Show the person only its wording.

Check only what the caller's goal needs: source access from the inventory; data execution
through `connect` only when live numbers are needed; Atlan access only for saving or
answering, with a failed read following
[connection recovery](../analytics-build/references/auth-recovery.md).

**Buildable is part of readiness, and it is settled before any question.** A report can be
built from only when the tables it reads exist somewhere a rule can run: a reachable
warehouse, or a bound local database. A report whose data is packed inside the file (an
extract or an embedded sheet) with no table behind it is `none` for building: say "This
dashboard's data is inside the file, so I can't build from it. Name the warehouse table
that holds the same data." A caller never
asks a use-case question about a source that cannot be built.

## What comes back

The saved run holds the survey; `PROJECT journey status` and `PROJECT checklist` read it.
Return to the caller, or show the person:

- the source types found and their subject, in two or three sentences, and how they
  complement each other;
- two or three analyses the sources could support, phrased as things to learn;
- for `connections`, the list; for `discover`, one line of what was found, then two to
  four use cases, each with one line of what it answers and the tables that power it;
- for `candidates`, the ranked names and questions; for `readiness`, the value to a caller and its
  wording to the person.

Counts alone do not show understanding. Use the inspector's actual count and noun: 110 past
queries are not 110 metrics. Keep file names and diagnostics in details. Distinguish inputs as sample
data, ready to use, needs your review, held back or only partly read. Never invent tables, measures, user
behaviour or findings.

Before sending: the first sentence is the result; one next step, not a menu; no apology, no
closing offer; commas and full stops, no em dashes or exclamation marks.
