# Warehouse statements

The SQL each skill sends, per warehouse. Every statement is read-only: a single SELECT, WITH,
SHOW or DESCRIBE, with a LIMIT on anything that returns rows. Replace `<…>` slots before
running.

Read only the section for the recorded warehouse. Three topics live apart, because most runs
never need them: [query history](warehouses/query-history.md) (metrics mode's pull, its
fallbacks and the view an admin can create), [through a CLI](warehouses/cli.md) and
[adding a server here](warehouses/add-server.md).

## Snowflake

- **Check:** `select current_role(), current_warehouse()`
- **Find tables:** `show terse tables in schema <db>.<schema>`, or search
  `<db>.information_schema.tables` by name for the domain's nouns.
- **Columns:** `select column_name, data_type, comment from <db>.information_schema.columns
  where table_schema = '<SCHEMA>' and table_name = '<TABLE>'`
- **Query history:** [query history](warehouses/query-history.md), only when metrics mode pulls it.
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
- **Query history:** [query history](warehouses/query-history.md), only when metrics mode pulls it.
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
