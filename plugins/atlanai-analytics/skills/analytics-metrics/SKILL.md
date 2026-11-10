---
name: analytics-metrics
description: "Atlan Analytics Metrics: generate, check, edit or save metric definitions and saved SQL. Use when asked to create, revise, fix, propose or publish a metric or saved query. Not for answering data questions (analytics-ask)."
---

# Generate, check, edit and save metrics

A capability. It turns surveyed sources into metric and saved-query definitions, checks
them, changes them, and saves them locally or to Atlan Trident. A person can call it
directly; `analytics-demo`, `analytics-build` and `analytics-ask` call it as a step.

Read [shared conventions](../analytics-build/references/conventions.md) first. `PROJECT` is
the helper named in [setup](../analytics-build/references/setup.md); the engine, helpers and
procedures live in the `analytics-build` skill, which holds the shared runtime.

## Who is calling

- **A flow called it** (it named the operation, the run and the selection): ask the person
  nothing and show no moment. Do the operation, record the result in the caller's run, and
  return. The caller owns intent, the visible preview and the destination question.
- **A person called it**: ask only for what the operation cannot proceed without, once: the
  source for `generate`, the metric for `edit`, the destination for `save`. Then show the
  definition and its check status and stop.

Every operation needs a survey. With none in the run, run `analytics-survey` `inventory`
first.

## Operations

| Operation | Does | Procedure |
| --- | --- | --- |
| `generate` | Authors the selected metrics and the ones they depend on, with every surveyed source as context. | [The metrics engine](../analytics-build/references/metrics-engine.md), then [build](../analytics-build/references/setup/build-check.md) section 3 |
| `check` | Runs each definition on the data and records what passed, what is untested and what is held. | [Check](../analytics-build/references/setup/build-check.md), section 4 |
| `edit` | Changes one definition's rule, re-checks it and returns the difference. | Below |
| `save` | Keeps definitions on this machine or publishes them to a workspace. | [Preview and publish](../analytics-build/references/setup/preview-publish.md) |

### `generate`: scan everything, then build

Read every surveyed source before authoring any metric. The engine sees the whole survey
even when the selection is two names, so a source read later cannot contradict a formula
written earlier. Author the selected names and their dependencies only; do not author the
rest of the survey unasked.

All sources use the one engine: demos, local files, dashboards and query history. Do not
create a second authoring path because an executor is missing; mark SQL untested and keep
the work. For a Power BI or Tableau source, follow
[dashboard through the metrics module](../analytics-build/references/dashboard-agent.md).

### `edit`: change a rule

| The definition is | Do |
| --- | --- |
| In this run, not yet published | Change it through the engine, run `check`, and return the old rule, the new rule and both check results. |
| Published, and the person owns the run that made it | The same, then `save` as a revision of the same metric, never a new one. |
| Published by someone else, or the request came from an answer | [Propose](../analytics-build/references/propose.md): a proposal for its owners. It never saves. |

Keep the metric's identity through an edit. If reconciliation changes a definition or a
count, say which and show its check status again
([reconciliation](../analytics-build/references/metrics/reconciliation.md)).

### `save`: local or Trident

- **Keep on this machine:** nothing leaves the run directory and no sign-in starts.
- **Save to Atlan Trident:** show the checked plan with its counts (to publish, held, kept
  local) and publish only after the person approves that concrete plan. An approval of one
  plan does not cover a changed one. Published links follow
  [definition links](../analytics-build/references/definition-links.md).

Registry writes use the Atlan MCP connection with the person's sign-in. If registry tools
are missing or sign-in is required, follow
[connection recovery](../analytics-build/references/auth-recovery.md) and resume the save.

## What comes back

Return to the caller, or show the person, for each definition: its name, its rule in plain
words, its population, its source, and its check status. Keep execution, source review,
holds and release status as separate facts. Total definitions are metrics plus queries. SQL
is shown only when asked.

Never expose credentials or identifying query literals.

Before sending: the first sentence is the result; one next step, not a menu; no apology, no
closing offer; commas and full stops, no em dashes or exclamation marks.
