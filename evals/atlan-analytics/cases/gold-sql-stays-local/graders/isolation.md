---
type: llm
weight: 1
---

The answer, grounded in the skill files, says: the trusted (gold) SQL stays in local files in the
user's project (answers.jsonl under .atlan-analytics/); it is never put into the agent's prompt,
never placed in the workspace the agent searches, and the registry eval dataset stores only a
digest/hash of it; the agent under test runs with only registry read tools and the warehouse query
tool (no file access), and a run whose transcript touched eval data is marked contaminated.
Missing any of: local-only storage, digest-only in the registry, the agent's restricted tools, or
contaminated runs being marked is a fail.
