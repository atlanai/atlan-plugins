---
name: metrics-generation
description: "Atlan Analytics: generate, check, edit or save metrics and saved SQL from dashboards, BI exports, models or query history. Use when asked to build, mine, refresh, fix, propose or publish a metric. Not for answering a data question (analytics-ask)."
---

# Generate, check, edit and save metrics

A capability. It turns surveyed sources into `metric` and `sql_query` definitions, checks
them, changes them, and saves them locally or to Atlan Trident. The engine lives in this
skill's `modules/metrics/`; the plugin drives the conversation and Registry publication. A
person can call it directly; `analytics-demo`, `analytics-build` and `analytics-ask` call it.

Read [shared conventions](../analytics-build/references/conventions.md) first: task
authorization, privacy, read-only SQL and status reporting apply.

Paths:
- `<metrics-dir>` is this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/metrics-generation`
  in Claude Code). `ENGINE` means `python3 "<metrics-dir>/scripts/analytics_agent.py" metrics
  --runtime <runtime>`.
- `<build-dir>` is the `analytics-build` skill's directory beside it. It holds the shared
  helpers: `PROJECT` is `<build-dir>/scripts/project.py` and `CONTEXT` is
  `<build-dir>/scripts/context_contracts.py`.

This skill needs the complete Analytics plugin, including its sibling `analytics-build`
skill. A standalone download that misses either cannot initialize the engine; refresh the
plugin when the dispatcher reports missing module files.

## Who is calling

- **A flow called it** (it named the operation, the run and the selection): ask the person
  nothing and show no moment. Do the operation, record the result in the caller's run, and
  return. The caller owns intent, the visible review and the destination question.
- **A person called it**: ask only for what the operation cannot proceed without, once: the
  source for `generate`, the metric for `edit`, the destination for `save`. Then show the
  definition and its check status and stop.

Every operation needs a survey. With none in the run, run `source-survey` `inventory` first.

## Operations

| Operation or argument | Does | Procedure |
| --- | --- | --- |
| `generate`, or `metrics` with a scope | Authors the selected metrics and what they depend on, with every surveyed source as context | [The metrics workflow](references/metrics.md) and [the metrics module](references/metrics-engine.md) |
| `check` | Runs each definition on the data and records what passed, what is untested and what is held | [Build and check](../analytics-build/references/setup/build-check.md), section 4 |
| `edit` | Changes one definition's rule, re-checks it and returns the difference | Below |
| `save` | Keeps definitions on this machine or publishes them to a workspace | [Publication](references/metrics/publish.md), [contracts](references/metrics/contracts.md) |
| `dashboard` | A Power BI or Tableau dashboard to metrics | [Dashboard through the metrics module](references/dashboard-agent.md); a saved dashboard-helper job uses [legacy readback](references/dashboard.md) |
| `propose` | A metric gap or correction from this conversation, for its owners | [The proposal workflow](references/propose.md); no metric save |

### `generate`: scan everything, then build

Read every surveyed source before authoring any metric. The engine sees the whole survey
even when the selection is two names, so a source read later cannot contradict a formula
written earlier. Author the selected names and their dependencies, through the complete
collection's gates in one run; do not author the rest of the survey unasked.

All sources use the one engine: demos, local files, dashboards and query history. Models and
BI files need no warehouse; query-log filtering and `CONTEXT` are permitted. Do not create a
second authoring path because an executor is missing; mark SQL untested and keep the work.
Effective compiler-review failures become structured publication holds; never bypass them.

### `edit`: change a rule

| The definition is | Do |
| --- | --- |
| In this run, not yet published | Change it through the engine, run `check`, and return the old rule, the new rule and both check results |
| Published, and the person owns the run that made it | The same, then `save` as a revision of the same metric, never a new one |
| Published by someone else, or the request came from an answer | `propose`: a proposal for its owners. It never saves |

Keep the metric's identity through an edit. If reconciliation changes a definition or a
count, say which and show its check status again ([reconciliation](references/metrics/reconciliation.md)).

### `save`: local or Trident

- **Keep on this machine:** nothing leaves the run directory and no sign-in starts.
- **Save to Atlan Trident:** show the checked plan with its counts (to publish, held, kept
  local) and publish only after the person approves that concrete plan. An approval of one
  plan does not cover a changed one. Respect explicit draft requests.

Registry writes use the Atlan MCP connection with the person's sign-in. If registry tools are
missing or sign-in is required, follow
[connection recovery](../analytics-build/references/auth-recovery.md) and resume the save.

## What comes back

Return to the caller, or show the person, for each definition: its name, its rule in plain
words, its population, its source, and its check status. Keep execution, source review, holds
and release status as separate facts. Total definitions are metrics plus queries. SQL is shown
only when asked. Inside a guided flow, return to that flow after the checks. Metrics-only work
ends after the publication summary.

Never expose credentials or identifying query literals. Keep eval answers and gold outside
all metric-generation context.

Before sending: the first sentence is the result; one next step, not a menu; no apology, no
closing offer; commas and full stops, no em dashes or exclamation marks.
