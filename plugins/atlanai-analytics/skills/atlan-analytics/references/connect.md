# Find the warehouse query tool

Every `atlan-analytics*` skill reads the warehouse through one read-only SQL tool. This skill
finds that tool, proves it works and records it in state. Later runs reuse the record and only
re-check it.

Connecting is always the person's own sign-in. Never ask for, print or store a warehouse
password, key or token. The statements for each warehouse are in
[warehouses](warehouses.md); read only the section for the warehouse in question.

## 0. Reuse what state holds

For a selected local project setup run, inspect its `run.json` through `project.py status`.
If it holds `execution.kind: sqlite` or `duckdb`, use [project setup](project.md#2-bind-data-once) and its
recorded interpreter. The local executor is a connection and takes precedence over warehouse
discovery. Rebind if its schema changed; never silently select another database or server.

If `state.json` has `warehouse.tool`, run that warehouse's check statement once. If it works,
show `warehouse ok (<kind> via <via>)` and return. If it fails, continue at step 1.

A setup run does not require DuckDB. `PROJECT answer` reads this same warehouse record and
returns a validated plan with `execute_in_host`. Use its SQL and separate native bindings
with the recorded read-only tool; report results only after that call succeeds. A report
export's original connector does not override the data source the person selected.

## 1. Look through Atlan first

The Atlan MCP server fronts the MCP servers the organization registered, so a Snowflake or
Databricks server an admin added is reachable through the same connection, signed in as the
person.

1. Search for a SQL tool. In search mode, call `mcp__search_tools` with the query "run a
   read-only SQL query on the data warehouse (Snowflake, Databricks)". In full mode, read the
   listed tools.
2. A candidate runs one SQL statement and returns rows. Recognise the warehouse from the tool's
   `_meta`:
   - `com.atlan/server-domain` `snowflakecomputing.com` is Snowflake;
   - `databricks.com` or `azuredatabricks.net` is Databricks;
   - otherwise use the server and tool names, and ask the person when it is still unclear.
3. Prefer a server whose `com.atlan/server-source` is `atlan_catalog` (installed from Atlan's
   catalog) over `custom`. When several warehouses match, ask which one the agent should read.
4. Skip tools that create, write or call procedures. Record only the query tool.
5. A matching server listed under `connect` is registered but not signed in for this person.
   Ask them to connect it with `mcp__connect_server`, which gives them a sign-in link to open,
   then search again. A call that comes back with a sign-in link means the same thing: show the
   link and wait.

If a tool is found, go to step 5.

## 2. The harness's own connectors

Look for a SQL tool connected to this harness directly: in Claude Code, ToolSearch
`snowflake sql query` and `databricks sql`; elsewhere, the listed MCP tools. The candidate
rules in step 1 apply. If only sign-in or connect tools come back, the connector is not signed
in: ask the person to sign in, then search again.

If a tool is found, go to step 5.

## 3. A vendor CLI, where there is a shell

Only when this harness runs shell commands (Claude Code, Codex, Cursor's agent, VS Code agent
mode) and the CLI is already installed and configured: `snow --version` with a connection in
`snow connection list`, or `databricks --version` with a profile in
`databricks auth profiles`. Never install a CLI, and never open its config files: they can
hold secrets. Ask which connection or profile to use when there are several.

Run the check statement through it ([through a CLI](warehouses/cli.md))
and show the person the role it ran as. A CLI does not enforce read-only access, so the
connection must use a read-only role: ask the person to confirm it, and if they can't, don't
use the CLI. The read-only rules apply to every statement you send.

If it works, go to step 5 with `via` `cli`.

## 4. Ask for it to be added

No tool in steps 1–3 means the organization has not added one for this person.
Snowflake, Databricks and ClickHouse are in the Atlan Store's MCP catalog. An Atlan admin
installs the listing in the Store with the warehouse's host (for Snowflake, also the database,
schema and MCP server name), then each person connects their own account. You can't install it
from here.

Ask:
- `<slug> · warehouse · how to reach the warehouse`
- **Ask an Atlan admin to add <warehouse> from the Store (Recommended)**: everyone on the team
  reaches it through Atlan with their own sign-in. Explain the missing connection and wait until it is added.
- **Add one to this tool myself**: give the steps for this harness from
  [adding a server here](warehouses/add-server.md), then name
  the missing connection and ask the person to run the skill again once it is connected.

In metrics mode, add **No warehouse: use a declared model, a BI file or an export** and return
without a tool when it is chosen.

## 5. Check and record

1. Run the warehouse's check statement once. The read-only rules apply.
2. Record in `state.json`:

   ```json
   "warehouse": {
     "kind": "snowflake | databricks | other",
     "dialect": "snowflake | databricks | <dialect>",
     "via": "atlan | host | cli",
     "tool": "<the exact tool name, or snow | databricks>",
     "server": "<the Atlan server name, when via is atlan>",
     "runner": "<the runner its run_with names, when via is atlan>",
     "profile": "<the CLI connection or profile name, when via is cli>",
     "bulk": {"via": "cli | tool | file", "profile": "<when via is cli>"}
   }
   ```

   Through Atlan in search mode, `tool` is the name you pass to a runner; record the runner its
   `run_with` names as `runner` beside it (`mcp__call_read_tool` for a read-only query tool). Through a
   harness connector it is the full name the harness lists, with its prefix.
3. Show `warehouse ok (<kind> via <via>)`.

## 6. Bulk reads

The query-history pull can return 5,000 rows of SQL text, which should not pass through this
conversation when it can be avoided. Record `warehouse.bulk`, in this order:
1. **`cli`**, when step 3's conditions hold (a shell, an installed and configured CLI, a
   read-only role), even if small reads go through MCP: the CLI writes the rows straight to a
   file ([through a CLI](warehouses/cli.md)).
2. **`tool`**: the query tool above, paged 5,000 rows at a time. Where the host saves a large
   tool result to a file and names its path, `CONTEXT querylog` reads that file, so the rows are
   never copied out by hand.
3. **`file`**: an export someone with access makes.

The CLI is never required: with no shell, `tool` or `file` covers every harness.

From here on, the skills call it "the warehouse query tool recorded in state". Write SQL in the
recorded dialect, using the statements in [warehouses](warehouses.md).

End with the useful result or one recovery action in natural language.
