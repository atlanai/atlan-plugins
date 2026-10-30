---
description: Answer a data question from published metrics, bootstrap metrics and saved queries, build, evaluate and improve a talk-to-data agent, or propose a metric from this conversation, on the Atlan registry.
argument-hint: "[ask | metrics | build | improve | eval | propose] [question, scope or project]"
---

Use the included instructions and follow the router with these arguments: $ARGUMENTS

Shared conventions:
@${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/references/conventions.md

Router:
@${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/SKILL.md

Metrics workflow (only when selected by the router):
@${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/references/metrics.md

Do not invoke `atlan-analytics` through the Skill tool:
the command and skill share that name, so it can load this wrapper again.

Resolve the router's `references/` paths from
`${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/` and its `skills/` paths from
`${CLAUDE_PLUGIN_ROOT}/`.

With `ask`, or a data question and no mode, answer it in this conversation from the workspace's
published metrics and saved queries, run against the warehouse. It says which definition it used,
and offers `propose` when none covered the question. It builds no agent and runs no eval.

With `metrics`, run only the metrics workflow. It chooses the table scope, finds the sources
in reach (declared semantic models, BI files, query history, or a finished metrics-agent run),
reuses existing definitions, and previews metrics and saved queries before publishing. Stop
there. This mode does not build an analytics agent or create an eval, and it installs nothing.

With `propose`, turn a gap this conversation found into a proposal for the metric's owners in
Metric Studio: a new metric when an answer had no published metric, or a fix when you
corrected one. It previews the proposal before writing it and never changes a metric itself.

For `build`, `improve` or `eval`, it checks your registry sign-in and warehouse connection
(Snowflake, Databricks or another warehouse, through Atlan or this tool's own connectors),
resumes a project saved under
`.atlan-analytics/` in this directory if there is one, and otherwise asks whether to build a new
agent or improve an existing one. It asks one question at a time and stops at each phase so
you can review what it made.
