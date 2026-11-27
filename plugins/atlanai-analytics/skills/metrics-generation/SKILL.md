---
name: metrics-generation
description: "Atlan Analytics: build, test, change or save metrics and saved queries from dashboards, BI files, models or past queries (query history). Use when asked to refresh, fix, propose or publish a metric. Not for answering a data question (analytics-ask)."
---

# Generate, check, edit and save metrics

A capability. It turns surveyed sources into `metric` and `sql_query` definitions, checks
them, changes them, and saves them on this machine or to Atlan Trident. The engine lives in this
skill's `modules/metrics/`; the plugin drives the conversation and Atlan Trident publication. A
person can call it directly; `analytics-demo`, `analytics-build` and `analytics-ask` call it.

Read [shared conventions](../analytics-build/references/conventions.md) first: task
authorization, privacy, read-only SQL and status reporting apply.

A helper or `ENGINE` result with `show_first` carries a chip or card the build has reached: show it
before any other words or call, and do what its `side view` line says. It is the update on that
step: add no words of your own about it. Run each helper command on its own and read all it
returns: a script or filter of yours drops its chip. Between calls, write nothing of your own: no working notes, no line about a card before or after it, and never a file path or a step's, gate's or helper's own name.

Paths:
- `<metrics-dir>` is this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/metrics-generation`
  in Claude Code). `ENGINE` means `python3 "<metrics-dir>/scripts/analytics_agent.py" metrics
  --runtime <runtime>`.
- `<build-dir>` is the `analytics-build` skill's directory beside it. It holds the shared
  helpers: `PROJECT` is `<build-dir>/scripts/project.py` and `CONTEXT` is
  `<build-dir>/scripts/context_contracts.py`. `EDIT` is
  `python3 "<build-dir>/../analytics-demo/scripts/edit_metric.py" --run-dir "<run>"`.

This skill needs the complete Analytics plugin, including its sibling `analytics-build`
skill. A standalone download that misses either cannot initialize the engine; refresh the
plugin when the dispatcher reports missing module files.

## Who is calling

- **A flow called it** (it named the operation, the run and the selection): ask the person
  nothing and show no moment. Do the operation, record the result in the caller's run, and
  return. The caller owns intent, the visible review and the destination question.
- **A person called it**: ask only for what the operation cannot proceed without, once: the
  source for `generate` ("Which dashboard, file or past queries should I build from?"), the
  metric for `edit` ("Which metric should I change?"), the destination for `save` ("Keep these
  on this machine, or save them to Atlan Trident?"). Then show the definition and its check status
  and stop.

Every operation needs a survey. With none in the run, run `analytics-readiness` `inventory` first.

## Operations

| Operation or argument | Does | Procedure |
| --- | --- | --- |
| `generate`, or `metrics` with a scope | Authors the selected metrics and what they depend on, with every surveyed source as context | [The metrics workflow](references/metrics.md) and [the metrics module](references/metrics-engine.md) |
| `check` | Runs each definition on the data and records what passed, what is untested and what is held | [Build and check](../analytics-build/references/setup/build-check.md), section 4 |
| `edit` | Changes one definition's rule, re-checks it and returns the difference | Below |
| `save` | Keeps definitions on this machine or publishes them to a workspace, or moves saved ones to another workspace | [Publication](references/metrics/publish.md), [contracts](references/metrics/contracts.md); a move is [publication step 6](references/metrics/publish.md#6-move-to-another-workspace) |
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
| In this run, not yet published | Write `{"name", "changes": [{"path", "value"}], "before", "after"}` (each rule in plain words; a `path` is a field of the metric's or saved query's `definition` in the run's `context/metrics.json` or `context/queries.json`, such as `formula`, `source.sql`, `mandatory_filters` or a query's `sql`, or its `description`) to a file outside the run and run `EDIT <that file>`: it changes the definition, re-checks it and hands over the edit card in `show_first`, which says the change. On a warehouse it returns `run_on_warehouse` with `sql`: run that with the warehouse tool, then `EDIT` a file holding the metric's `name` and `result` (the rows, or `{"error": ...}`), and the card comes then. Without a card, return "Changed <metric>." then "Before: <old rule> · <test result>" and "After: <new rule> · <test result>" |
| Published, and the person owns the run that made it | The same, then one native question, "Save this change to Atlan Trident too?" (Save it (Recommended), Keep it on this machine), its text holding the edit card's lines in a terminal; on yes, `save` as a revision of the same metric, never a new one |
| Published by someone else, or the request came from an answer | `propose`: a proposal for its owners. It never saves |

Keep the metric's identity through an edit. If reconciliation changes a definition or a
count, say which and show its check status again: "<workspace> already had <metric>, so I'm
using that definition. Its test: <test result>." ([reconciliation](references/metrics/reconciliation.md)).

### `save`: this machine or Atlan

- **Keep it on this machine:** nothing leaves this machine, and you don't sign in.
- **Save to Atlan Trident:** show the checked plan with its counts (to publish, held, kept
  local) and publish only after the person approves that concrete plan. An approval of one
  plan does not cover a changed one. Respect explicit draft requests.

Trident writes use the Atlan MCP connection with the person's sign-in. If Trident tools are
missing or sign-in is required, follow
[connection recovery](../analytics-build/references/auth-recovery.md) and resume the save.

## What comes back

Return to the caller, or show the person, for each definition this request made or changed:
"<name>: <rule in plain words>. Counts <population>. From <source>. <Tested / Not tested / Held
back, because <reason>>." A request about one metric returns that one; the side page lists the
rest. Keep execution, source review, holds and release status as separate facts. Total
definitions are metrics plus saved queries. SQL and file paths are shown
only when asked. Inside a guided flow, return to that flow after the checks. Metrics-only work
ends after the publication summary.

Never expose credentials or identifying query literals. Keep eval answers and gold outside
all metric-generation context.

Before sending: the first sentence is the result; one next step, not a menu; no apology, no
closing offer; commas and full stops, no em dashes or exclamation marks.
