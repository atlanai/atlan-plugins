---
type: llm
weight: 2
---

The response loads the analytics-build router skill and treats "build an analytics agent" as a
setup run: it asks for or starts surveying the support-ticket context (models, dashboards,
saved SQL, query history or a data extract), or stops with NEEDS_CONTEXT naming the one input it
needs. It offers a separately deployed agent with an eval set in one line, without starting
that build. It must NOT invent workspace ids, agent ids, table names or results, and must not
run SQL, publish or register an agent.
