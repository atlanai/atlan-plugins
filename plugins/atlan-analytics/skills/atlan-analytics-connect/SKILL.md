---
name: atlan-analytics-connect
description: "Find, check and record the read-only SQL tool a talk-to-data run reads the warehouse with: Snowflake, Databricks or another warehouse, reached through the Atlan MCP server or the harness's own connector. Asks the person to connect it when it is registered but not signed in, and says how to add it when it is missing. The atlan-analytics skills run it before any warehouse read."
---

# Find the warehouse query tool

Every `atlan-analytics*` skill reads the warehouse through one read-only SQL tool. This skill
finds that tool, proves it works and records it in state. Later runs reuse the record and only
re-check it.

Connecting is always the person's own sign-in. Never ask for, print or store a warehouse
password, key or token. The statements for each warehouse are in
[warehouses](references/warehouses.md).

## 0. Reuse what state holds

If `state.json` has `warehouse.tool`, run that warehouse's check statement once. If it works,
show `warehouse ok (<kind> via <via>)` and return. If it fails, continue at step 1.

## 1. Look through Atlan first

The Atlan MCP server fronts the MCP servers the organization registered, so a Snowflake or
Databricks server an admin added is reachable through the same connection, signed in as the
person.

1. Search for a SQL tool. In search mode, call `mcp__search_tools` with the query "run a
   read-only SQL query on the data warehouse (Snowflake, Databricks)". In full mode, read the
   listed tools.
2. A candidate runs one SQL statement and returns rows. Recognise the warehouse from the tool's
   `_meta`:
   - `com.atlan/server-host` ending in `snowflakecomputing.com` is Snowflake;
   - one ending in `cloud.databricks.com`, `azuredatabricks.net` or `gcp.databricks.com` is
     Databricks;
   - otherwise use the server and tool names, and ask the person when it is still unclear.
3. Prefer a server whose `com.atlan/server-source` is `atlan_catalog` (installed from Atlan's
   catalog) over `custom`. When several warehouses match, ask which one the agent should read.
4. Skip tools that create, write or call procedures. Record only the query tool.
5. A matching server listed under `connect` is registered but not signed in for this person.
   Ask them to connect it with `mcp__connect_server`, which gives them a sign-in link to open,
   then search again. A call that comes back with a sign-in link means the same thing: show the
   link and wait.

If a tool is found, go to step 4.

## 2. Ask for it to be added

No warehouse server in the search means the organization has not added one for this person.
Snowflake, Databricks and ClickHouse are in the Atlan Store's MCP catalog. An Atlan admin
installs the listing in the Store with the warehouse's host (for Snowflake, also the database,
schema and MCP server name), then each person connects their own account. You can't install it
from here.

Ask:
- `<slug> · warehouse · how to reach the warehouse`
- **Ask an Atlan admin to add <warehouse> from the Store (Recommended)**: everyone on the team
  reaches it through Atlan with their own sign-in. Stop with NEEDS_CONTEXT until it is added.
- **Use a connector this tool already has**: step 3.
- **Add one to this tool myself**: give the steps for this harness from
  [warehouses](references/warehouses.md) ("Adding a server here"), then stop with
  NEEDS_CONTEXT and ask the person to run the skill again once it is connected.

In metrics mode, add **No warehouse: use an export file or BI model** and return without a
tool when it is chosen.

## 3. The harness's own connectors

Look for a SQL tool connected to this harness directly: in Claude Code, ToolSearch
`snowflake sql query` and `databricks sql`; elsewhere, the listed MCP tools. The candidate
rules in step 1 apply. If only sign-in or connect tools come back, the connector is not signed
in: ask the person to sign in, then search again.

## 4. Check and record

1. Run the warehouse's check statement once. The read-only rules apply.
2. Record in `state.json`:

   ```json
   "warehouse": {
     "kind": "snowflake | databricks | other",
     "dialect": "snowflake | databricks | <dialect>",
     "via": "atlan | host",
     "tool": "<the exact tool name>",
     "server": "<the Atlan server name, when via is atlan>"
   }
   ```

   Through Atlan in search mode, `tool` is the name you pass to `mcp__call_tool`. Otherwise it
   is the full name the harness lists, with its prefix.
3. Show `warehouse ok (<kind> via <via>)`.

From here on, the skills call it "the warehouse query tool recorded in state". Write SQL in the
recorded dialect, using the statements in [warehouses](references/warehouses.md).

End with one status line.
