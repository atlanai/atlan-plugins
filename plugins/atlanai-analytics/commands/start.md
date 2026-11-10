---
description: Turn dashboards and query history into context you can ask questions of, on demo data or your own.
disable-model-invocation: true
---

Load this plugin's `analytics-build` skill with `start $ARGUMENTS` (in Claude Code, the Skill
tool's `atlanai-analytics:analytics-build`). With no source, question or saved run, first ask
whether to try one of the demos or use the person's own data, as the skill's start choice describes,
without first narrating which skill or file is being read. A demo exists so the person
understands the plugin by watching it work: the blocks it prints at each moment are the
point, and the build is how to reach them.
With a supplied source, or after "Use my own data", survey available sources, preview metrics
and SQL, publish them after the user approves the plan, and suggest supported questions. Answer an already-supplied question through Atlan
retrieval; otherwise wait for the user's question. Other sources follow the skill's existing
setup route. Do not start a dedicated agent or independent eval unless explicitly requested.
