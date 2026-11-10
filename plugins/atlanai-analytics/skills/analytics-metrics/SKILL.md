---
name: analytics-metrics
description: Generate checked metrics and saved queries from dashboards, BI exports, models or query history, and publish them to Atlan. Use when asked to build, mine, refresh or fix metrics. Not for answering a data question (analytics-ask).
---

# Generate metrics with the metrics module

Turn trusted definitions (Power BI and Tableau dashboards, declared models, a warehouse's
query history) into `metric` and `sql_query` artifacts, each checked against the registry's
contracts and previewed before it is written. The engine that does the work lives in this
skill's `modules/metrics/`; the plugin drives the conversation and Registry publication.

Read [shared conventions](../analytics-build/references/conventions.md) first: task
authorization, privacy, read-only SQL and status reporting apply.

Paths:
- `<metrics-dir>` is this skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/analytics-metrics`
  in Claude Code). `ENGINE` means `python3 "<metrics-dir>/scripts/analytics_agent.py" metrics
  --runtime <runtime>`.
- `<build-dir>` is the `analytics-build` skill's directory beside it. It holds the shared
  helpers: `PROJECT` is `<build-dir>/scripts/project.py` and `CONTEXT` is
  `<build-dir>/scripts/context_contracts.py`.

This skill needs the complete Analytics plugin, including its sibling `analytics-build`
skill. A standalone download that misses either cannot initialize the engine; refresh the
plugin when the dispatcher reports missing module files.

## Dispatch the argument first

| Argument or request | Follow, then return |
| --- | --- |
| None, or `metrics` with a scope: build or refresh metrics | [The metrics workflow](references/metrics.md), with `.atlan-metrics/<slug>/` |
| `dashboard`: a Power BI or Tableau dashboard to metrics | [Dashboard through the metrics module](references/dashboard-agent.md) |
| A saved dashboard-helper job | [Legacy readback](references/dashboard.md), preserving capture and checks; new generation uses a new engine run |
| `propose`: a metric gap or correction from this conversation | [The proposal workflow](references/propose.md); preview a proposal for its owners, no metric save |
| Explain how the engine works | [The metrics module](references/metrics-engine.md) |

Every new generation uses [the metrics module](references/metrics-engine.md): the first
metric, the complete collection, demos and local-only setup. Dispatch before mode-specific
conventions or preflight. Models and BI files need no warehouse; query-log filtering and
`CONTEXT` are permitted. No full-agent preflight, eval, registration or deployment runs here;
building an agent later needs an explicit new request to `analytics-build build`.

## The metrics flow

1. **Sources and scope.** Reuse the source and scope the person or the calling flow already
   chose. Otherwise read [sources](references/metrics/sources.md) and, for a folder or link,
   the `analytics-survey` skill's procedure. Ask only for missing decisions that change
   meaning or execution; never ask for credentials.
2. **Generate.** Prepare one engine run and answer its gates, as
   [the metrics module](references/metrics-engine.md) sections 1 to 3 describe.
3. **First metric.** For own data, show [the first metric](references/first-metric.md) at the
   X3 checkpoint, before building the rest. A demo shows its metric names instead, as the
   `analytics-demo` skill describes.
4. **Build the rest and check.** Finish the same run, export, then `CONTEXT check` against
   the live schema and existing definitions once a destination is chosen.
5. **Preview and publish.** Show the checked plan and publish only after the person approves
   it, through [publication](references/metrics/publish.md) and its
   [contracts](references/metrics/contracts.md). Reconcile changed definitions with
   [reconciliation](references/metrics/reconciliation.md). Respect explicit draft requests.

Use inline cards only with installed tools; never register another MCP server. Keep progress
in native chat. Effective compiler-review failures become structured publication holds; never
bypass them, and say what was held and why.

Inside a guided flow (`analytics-build` step 4, `analytics-demo` step 3), return to that flow
after the checks, so its save, question and dashboard steps follow. Metrics-only work ends
after the publication summary.

Never expose credentials or identifying query literals. Keep eval answers and gold outside
all metric-generation context.
