---
name: atlan-analytics
description: Answer a data question from published metrics, set up metrics and saved queries, build a talk-to-data agent, evaluate or improve it with isolated held-out evaluation, connect a warehouse, or propose a metric from a conversation.
---

# Metrics and talk-to-data agents

Read [shared conventions](references/conventions.md) first. They define audit sessions,
publication approval, privacy and status reporting.

## 0. Dispatch the requested mode first

If the first argument is exactly `metrics`, enter metrics-only mode. Treat the remaining
arguments as scope. Read [the context workflow](references/build/03-context.md) and start
at **Metrics-only entry**. It checks Registry access, source and table scope before previewing
publication. Use `.atlan-metrics/<slug>/`, never `.atlan-analytics/` state.

**Return after that branch. Do not continue below:** no full-agent preflight, `EVAL init`,
agent, eval dataset, experiment, deployment, smoke test or baseline. A file source does
not require a warehouse connection. Starting an agent build later needs an explicit new request.

If the first argument is exactly `propose`, read [the proposal workflow](references/propose.md),
preview a proposal for its owners, and return: no metric save, full-agent preflight or eval.

If the first argument is exactly `ask`, or there is no mode and the request is a data question
(a number, a trend, why something changed) rather than metrics or agent work, follow the
`atlan-analytics-ask` skill (`skills/atlan-analytics-ask/SKILL.md`). It answers in the
conversation from published definitions, then returns: no full-agent preflight, project, eval
or agent.

If the first argument is exactly `ontology`, or the request is about business objects, their
relationships, governed policies or modelling the business, follow the `atlan-ontology` skill
(`skills/atlan-ontology/SKILL.md`), then return.

If it is `connect`, follow [warehouse discovery](references/connect.md), then return.
Use the selected project’s state when one exists; never resume another project silently.

The remaining sections apply only to full-agent modes.

## 1. Preflight and resume

Follow [full-agent preflight and saved project routing](references/full-agent.md).
Use `.atlan-analytics/<slug>/` and the saved phase; an explicit mode wins over it.

| Mode | Procedure |
| --- | --- |
| `build` | [Build](references/build.md), purpose through baseline |
| `eval` | [Evaluate](references/eval.md), isolated cases and locked publishing |
| `improve` | [Improve](references/improve.md), adopt, plan and train-only iteration |

The single helper is `scripts/analytics_eval.py`. Local grading uses the standard library;
publishing uses the locked `scripts/pyproject.toml` and `scripts/uv.lock` and requires private
GitHub contents-read access to `an internal repository` through normal credential helpers.

## MCP resources

With an installed plugin, paths resolve under `${CLAUDE_PLUGIN_ROOT}/skills/atlan-analytics/`.
With MCP only, read this skill at `atlan://defaults/skills/atlan-analytics/SKILL.md`;
read a cited reference at the same base plus its relative path. Fetch supporting references
and scripts through MCP resources when needed, preserving their relative directory layout
locally before running a script. MCP does not expand plugin-local includes.

Never expose credentials or identifying query literals. Gold SQL and results stay local and
out of agent context; the failure analyst sees train traces only. Keep the two specialist
agents, `atlan-analytics-grader` and `atlan-analytics-failure-analyst`, isolated as documented.
