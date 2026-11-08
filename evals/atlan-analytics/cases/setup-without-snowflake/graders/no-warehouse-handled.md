---
type: llm
weight: 1
---

With no warehouse query tool available, the response follows the setup route rather than a
full-agent build: it does not refuse or stop only because no warehouse is connected, asks for
the source it needs (models, dashboards, query history or a data extract) or ends with
NEEDS_CONTEXT naming it, and offers a separately deployed agent in at most one line. It does not
substitute a different SQL tool silently, fabricate query results or register an agent.
