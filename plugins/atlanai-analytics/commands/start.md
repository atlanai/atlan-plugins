---
description: Turn dashboards and query history into context you can ask questions of, on demo data or your own.
disable-model-invocation: true
---

Load this plugin's `atlan-analytics` skill with `start $ARGUMENTS` (in Claude Code, the Skill
tool's `atlanai-analytics:atlan-analytics`). With no source, question or saved run, first ask
whether to try the demo or use the person's own data, as the skill's start choice describes.
With a supplied source, or after "Use my own data", survey available sources, publish metrics
and SQL, and suggest supported questions. Answer an already-supplied question through Atlan
retrieval; otherwise wait for the user's question. Other sources follow the skill's existing
setup route. Do not start a dedicated agent or independent eval unless explicitly requested.
