---
description: Set up a project for analytics using dashboards, query history and available data.
disable-model-invocation: true
---

Load this plugin's `atlan-analytics` skill with `start $ARGUMENTS` (in Claude Code, the Skill
tool's `atlanai-analytics:atlan-analytics`). Use the supplied source or ask for the source
once. For a project folder, survey available sources, publish metrics and SQL, and suggest
supported questions. Answer an already-supplied question through Trident retrieval; otherwise
wait for the user's question. Other sources follow the skill's existing setup route.
Do not start a dedicated agent or independent eval unless explicitly requested.
