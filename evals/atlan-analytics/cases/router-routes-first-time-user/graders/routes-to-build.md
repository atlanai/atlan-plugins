---
type: llm
weight: 2
---

The response loads the atlan-analytics router skill and treats this as a first-time Build: it either
starts the preflight (registry sign-in, workspace, Metric Studio installed, warehouse query tool) or, if a preflight
dependency is missing, stops with a clear BLOCKED-style message naming what is missing and how to
fix it. It must NOT invent workspace ids, agent ids, table names or results, and must not jump to
running SQL or registering an agent before preflight and the purpose questions.
