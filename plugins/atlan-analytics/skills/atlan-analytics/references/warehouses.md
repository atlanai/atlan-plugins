# Warehouse statements

The SQL each skill sends, per warehouse. Every statement is read-only: a single SELECT, WITH,
SHOW or DESCRIBE, with a LIMIT on anything that returns rows. Replace `<…>` slots before
running.

## Snowflake

- **Check:** `select current_role(), current_warehouse()`
- **Find tables:** `show terse tables in schema <db>.<schema>`, or search
  `<db>.information_schema.tables` by name for the domain's nouns.
- **Columns:** `select column_name, data_type, comment from <db>.information_schema.columns
  where table_schema = '<SCHEMA>' and table_name = '<TABLE>'`
- **Query history:** `snowflake.account_usage.query_history`, filtered to
  `execution_status = 'SUCCESS'` and `query_type = 'SELECT'`, reading `query_text`.
- **Names:** `DB.SCHEMA.TABLE`. Unquoted identifiers are upper-cased; quote with `"…"` to keep
  case.
- **Server:** Snowflake's managed MCP server exposes SQL only when its specification includes a
  `SYSTEM_EXECUTE_SQL` tool. Its URL is
  `https://<account>.snowflakecomputing.com/api/v2/databases/<db>/schemas/<schema>/mcp-servers/<server>`.

## Databricks

- **Check:** `select current_catalog(), current_schema()`
- **Find tables:** `show tables in <catalog>.<schema>`, or search
  `<catalog>.information_schema.tables` by name for the domain's nouns.
- **Columns:** `select column_name, data_type, comment from <catalog>.information_schema.columns
  where table_schema = '<schema>' and table_name = '<table>'`
- **Query history:** `system.query.history`, filtered to `execution_status = 'FINISHED'` and
  `statement_type = 'SELECT'`, reading `statement_text`. It needs access to system tables;
  without it, ask for an export file.
- **Names:** `catalog.schema.table`, lower case. Quote with `` `…` ``.
- **Server:** Databricks' managed SQL MCP service, at
  `https://<workspace-host>/ai-gateway/mcp-services/system.ai.dbsql` or the older
  `https://<workspace-host>/api/2.0/mcp/sql`. When the server offers a read-only variant of its
  execute tool, record that one. If a result comes back as a pending statement, poll it with the
  server's own status tool before reading rows.

## Another warehouse

Use `select 1` as the check, its `information_schema` for tables and columns, and its own
documented read-only query history. Record its name as `dialect`. If you can't find a
read-only history source, ask for an export file.

## Adding a server here

Recommend the Atlan route first: an admin installs the warehouse from the Atlan Store once, and
everyone reaches it through Atlan with their own sign-in. When the person wants it in this tool
only, give the steps for the harness you are running in:

- **Claude Code:** `claude mcp add --transport http <name> <url>`, then `/mcp` to sign in.
- **Claude desktop or claude.ai:** Settings, Connectors, add a custom connector with the URL.
- **Codex:** `codex mcp add <name> --url <url>`, or a `[mcp_servers.<name>]` table with `url` in
  `~/.codex/config.toml`, then `codex mcp login <name>`.
- **Cursor:** add `"<name>": {"url": "<url>"}` under `mcpServers` in `.cursor/mcp.json`.

The warehouse's own OAuth client must allow the harness; its admin configures that. Never put a
password or token in these files.
