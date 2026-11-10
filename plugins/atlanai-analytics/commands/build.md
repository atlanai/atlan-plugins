---
description: Turn your dashboards, BI exports or query history into metrics you can ask questions of.
disable-model-invocation: true
---

Load this plugin's `analytics-build` skill with `start $ARGUMENTS` (in Claude Code, the Skill
tool's `atlanai-analytics:analytics-build`). With a supplied source or business problem,
survey every source, let the person pick from the candidate metric names, build and check
the selection, show the first metric, publish after the person approves the plan, and
suggest supported questions. With no source, problem or saved run, ask for a dashboard
link, local folder or BI export, and offer the demo as the alternative. Do not start a
dedicated agent or independent eval unless explicitly requested.
