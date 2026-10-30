---
type: llm
weight: 1
---

With no warehouse query tool available (no Snowflake or other SQL tool through Atlan or this
session), the response stops before any build phase that needs SQL and reports it as blocked or
waiting on the user (status BLOCKED, NEEDS_CONTEXT or equivalent). It tells the user how to get one
connected (an Atlan admin adding the warehouse from the Store, or adding it to this tool, for
example via /mcp) and that they can resume afterwards. It does not substitute a different SQL tool
silently, does not fabricate query results, and does not proceed to register an agent.
