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
- **Query history:** see **Query history** below.
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
- **Query history:** see **Query history** below.
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

## Query history

Try each source in order and use the first one that answers. Every statement keeps 90 days (or
less, where the source holds less) of successful SELECTs that touch the scoped tables, grouped
per distinct text, at most 5,000 rows, in pages. Write the rows as `{sql, exec, users}` and
never keep user names.

### Snowflake

1. **A query-shape view an admin created once** (see **One-time setup** below). Look for it
   with `show views like 'QUERY_SHAPES' in account`. It needs no grant beyond SELECT on the
   view:

   ```sql
   select sql, exec, users from <db>.atlan.query_shapes
   where sql ilike '%<TABLE_1>%' or sql ilike '%<TABLE_2>%'
   order by exec desc limit 5000
   ```
2. **`ACCOUNT_USAGE`**, when the role has the `SNOWFLAKE.GOVERNANCE_VIEWER` database role (or
   imported privileges on the `SNOWFLAKE` database):

   ```sql
   select query_text as sql, count(*) as exec, count(distinct user_name) as users
   from snowflake.account_usage.query_history
   where execution_status = 'SUCCESS' and query_type = 'SELECT'
     and start_time >= dateadd('day', -90, current_timestamp())
     and (query_text ilike '%<TABLE_1>%' or query_text ilike '%<TABLE_2>%')
   group by 1 order by 2 desc limit 5000
   ```
3. **`INFORMATION_SCHEMA`**, with no grant at all. It returns the person's own queries (and
   others' on warehouses their role can monitor) from the last 7 days only. Its row limit is
   applied before the filter, so it is thin. Use it to start, and say how thin it is:

   ```sql
   select query_text as sql, count(*) as exec, count(distinct user_name) as users
   from table(<db>.information_schema.query_history(
          end_time_range_start => dateadd('day', -7, current_timestamp()), result_limit => 10000))
   where execution_status = 'SUCCESS' and query_type = 'SELECT'
     and (query_text ilike '%<TABLE_1>%' or query_text ilike '%<TABLE_2>%')
   group by 1 order by 2 desc limit 5000
   ```
4. **An export file** from someone with access.

### Databricks

1. **A query-shape view an admin created once** (see **One-time setup**), read like Snowflake's.
2. **`system.query.history`**, when the person can read system tables:

   ```sql
   select statement_text as sql, count(*) as exec, count(distinct executed_by_user_id) as users
   from system.query.history
   where execution_status = 'FINISHED' and statement_type = 'SELECT'
     and start_time >= current_timestamp() - interval 90 days
     and (statement_text ilike '%<table_1>%' or statement_text ilike '%<table_2>%')
   group by 1 order by 2 desc limit 5000
   ```
3. **The Query History API**, through the CLI where there is a shell:
   `databricks query-history list --max-results 1000 -o json`, paged with its page token. It
   returns the queries the person can see in the Query History page: their own, and those on
   warehouses they can view. Group the statement texts yourself. Flags vary by CLI version, so
   check `--help`.
4. **An export file** from someone with access.

### Another warehouse

Its own documented read-only query history, grouped the same way. With none, ask for an export.

## One-time setup an admin can run

Most friction is the history grant. Rather than giving every analyst a governance role, an
admin can create one view, once, that groups statements without user names. Analysts then need
only SELECT on that view. Offer this script to the person when the history pull is refused. The
admin reviews and runs it: you never run it. Narrow the `where` to the databases the team works
in, because the view shows the text of every query it covers.

**Snowflake**, run by a role that can read `SNOWFLAKE.ACCOUNT_USAGE`:

```sql
create schema if not exists <db>.atlan;
create or replace secure view <db>.atlan.query_shapes as
select query_text as sql, count(*) as exec, count(distinct user_name) as users,
       max(start_time) as last_run
from snowflake.account_usage.query_history
where execution_status = 'SUCCESS' and query_type = 'SELECT'
  and start_time >= dateadd('day', -90, current_timestamp())
  and database_name in ('<DB>')
group by query_text;
grant usage on schema <db>.atlan to role <analyst_role>;
grant select on view <db>.atlan.query_shapes to role <analyst_role>;
```

**Databricks**, run by someone who can read `system.query.history`:

```sql
create schema if not exists <catalog>.atlan;
create or replace view <catalog>.atlan.query_shapes as
select statement_text as sql, count(*) as exec, count(distinct executed_by_user_id) as users,
       max(start_time) as last_run
from system.query.history
where execution_status = 'FINISHED' and statement_type = 'SELECT'
  and start_time >= current_timestamp() - interval 90 days
group by statement_text;
grant use schema on schema <catalog>.atlan to `<analyst_group>`;
grant select on view <catalog>.atlan.query_shapes to `<analyst_group>`;
```

## Through a CLI

Only where step 3 of the skill allowed it, with the connection or profile recorded in
`warehouse.profile`. Send the same read-only statements as above.

- **Snowflake:** `snow sql -c <connection> --format json -q "<statement>"`. The check is
  `select current_role(), current_warehouse()`.
- **Databricks:** the Statement Execution API through the CLI:
  `databricks api post /api/2.0/sql/statements -p <profile> --json '{"warehouse_id": "<id>",
  "statement": "<statement>", "wait_timeout": "50s", "format": "JSON_ARRAY", "disposition":
  "INLINE"}'`. List warehouse ids with `databricks warehouses list -p <profile>`. Poll a
  `PENDING` statement with `databricks api get /api/2.0/sql/statements/<statement_id>`.

For the history pull, redirect the output to `<context-dir>/querylog.raw.json`. Convert it to
`querylog.jsonl` with a short script, without printing the rows into this conversation.

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
