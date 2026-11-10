---
name: analytics-survey
description: "Atlan Analytics Survey: inventory a source and check it is ready, with what it covers and candidate metric names. Use when asked what a dashboard, folder, BI export or warehouse holds or if it is usable. Not for building metrics (analytics-metrics)."
---

# Survey a source and check it is ready

A capability. It reads, it never writes to Atlan, and it authors no metric or SQL. A person
can call it directly; `analytics-demo`, `analytics-build` and `analytics-ask` call it as a
step.

Read [shared conventions](../analytics-build/references/conventions.md) first. `PROJECT` is
the helper named in [setup](../analytics-build/references/setup.md); the procedures below
live in the `analytics-build` skill, which holds the shared runtime.

## Who is calling

- **A flow called it** (Demo, Build or Ask named the operation and the run): ask the person
  nothing. Use the inputs given, write the result into the caller's run, and return to the
  caller. The caller decides what the person sees.
- **A person called it**: ask only for a missing source, once. Show the result as below and
  stop. Do not start generation, a destination question or a publication pitch. If they then
  want metrics from it, hand to `analytics-build` with the survey already done.

## Operations

| Operation | Does | Procedure |
| --- | --- | --- |
| `inventory` | Reads every supplied source in full: models, dashboards, query history, SQL and local data. Says what they cover and what they could answer. | [Inspect](../analytics-build/references/setup/inspect-scope.md), section 1 |
| `candidates` | Lists candidate metric names from the inventory, ranked by relevance to a stated focus or problem. Names only. | Below |
| `readiness` | Says whether work can proceed: source readable, data executable, Atlan reachable, published context present. | Below |

Run `inventory` before `candidates`. `readiness` can run alone.

### `candidates`: names before metrics

The point is relevance before accuracy: the person commits to names, so a wrong focus costs
one question and not a generation run.

1. Take names from the inventory only: declared measures in models and dashboards, and
   measures that recur in query history. Never invent a name the sources do not show.
2. Rank by relevance to the focus or problem the caller passed. With none, rank by how many
   sources show the measure.
3. Return at most ten, each with its name, one line of plain meaning, and where it came from
   (the dashboard tile, the model measure, or the count of past queries).
4. Label every one as discovered, with SQL not generated and checks not run. Do not preview
   SQL, run the engine or install its runtime for this operation.

### `readiness`: one of three answers

| Answer | Means | Next step to name |
| --- | --- | --- |
| `ready` | Sources are readable and, for answers, released context or an executor exists. | None |
| `partial` | Some coverage is held: a missing export, an untested executor, a sign-in not yet done. | The one thing that unblocks the held part |
| `none` | No readable source and no published context. | Offer `analytics-demo` |

Check only what the caller's goal needs:

- **Source access:** the inventory's own result. A visible chart without its definition is
  held coverage, not a failure.
- **Data execution:** follow [warehouse discovery](../analytics-build/references/connect.md)
  only when the goal needs live numbers. Previewing definitions needs no connection.
- **Atlan access:** only when the goal is saving or answering from published context. A
  failed registry read follows [connection recovery](../analytics-build/references/auth-recovery.md).
  Workspace questions follow [workspace help](../analytics-build/references/setup/workspace-access.md).
- **Published context:** for `analytics-ask`, whether released metrics cover the question's
  measure. That is the search `analytics-ask` already runs; do not repeat it here.

## What comes back

The saved run holds the survey; `PROJECT journey status` and `PROJECT checklist` read it.
Return to the caller, or show the person:

- the source types found and their subject, in two or three sentences;
- two or three analyses the sources could support, phrased as things to learn;
- for `candidates`, the ranked names;
- for `readiness`, the answer and its one next step.

Keep filenames, counts and diagnostics in details. Distinguish sample, available, needs
review, held and scan-limited inputs.

Before sending: the first sentence is the result; one next step, not a menu; no apology, no
closing offer; commas and full stops, no em dashes or exclamation marks.
