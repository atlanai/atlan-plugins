---
type: llm
weight: 2
---

The response loads the analytics-build skill and treats "build an analytics agent" as a
setup run: it asks for or starts surveying the support-ticket context (models, dashboards,
saved SQL, query history or a data extract), or stops with NEEDS_CONTEXT naming the one input it
needs. This chat is the agent: it does not start an eval set, registration or deployment,
and promises no separately deployed agent. It must NOT invent workspace ids, agent ids, table names or results, and must not
run SQL, publish or register an agent.
