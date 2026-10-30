---
max_turns: 12
allowed_tools: [Read, Glob, Grep]
---

Read the atlan-analytics improvement loop instructions. We need to correct a metric's formula,
change a saved query's usage note, then undo the metric edit if the round loses. The current
metric definition.version is 5 and the old content we would restore had definition.version 2.

Explain the supported save/undo operations, concurrency and publishing behavior. Then explain
what the round should do if it has only the current Registry MCP tools, with no append-version
tool or authenticated HTTP client. Do not perform any writes; answer from the guide.
