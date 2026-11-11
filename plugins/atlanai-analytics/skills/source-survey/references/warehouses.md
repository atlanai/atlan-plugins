# Warehouse statements

The SQL each skill sends, per warehouse. Every statement is read-only: a single SELECT, WITH,
SHOW or DESCRIBE, with a LIMIT on anything that returns rows. Replace `<…>` slots before
running.

Read only the section for the recorded warehouse, or for the dialect a metric names when none
is recorded. Three topics live apart, because most runs
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
- **Table usage**, for [discovery](discovery.md). Try in order; stop at the first that answers.
  1. Account-wide access, with the `GOVERNANCE_VIEWER` database role or imported privileges.
     It returns no rows on an edition that does not record access history: treat empty as
     refused and go to 2.

     ```sql
     select f.value:"objectName"::string as table_name,
            count(distinct ah.query_id) as queries, count(distinct ah.user_name) as people
     from snowflake.account_usage.access_history ah,
          lateral flatten(input => ah.base_objects_accessed) f
     where ah.query_start_time >= dateadd('day', -30, current_timestamp())
       and f.value:"objectDomain"::string in ('Table', 'View')
     group by 1 order by people desc, queries desc limit 50
     ```
  2. Account-wide queries. `<db>` is each database in use. The 2,000 most run query patterns
     are matched to table names on a word boundary, so `ACCOUNTS` does not count queries on
     `DIM_ACCOUNTS`. `people` is the most people on any one pattern, so say "at least":

     ```sql
     with q as (select any_value(upper(query_text)) as sql, count(*) as runs,
                       count(distinct user_name) as people
                from snowflake.account_usage.query_history
                where start_time >= dateadd('day', -30, current_timestamp())
                  and execution_status = 'SUCCESS' and query_type = 'SELECT'
                group by query_parameterized_hash order by runs desc limit 2000)
     select t.table_schema, t.table_name, sum(q.runs) as queries, max(q.people) as people
     from <db>.information_schema.tables t
     join q on regexp_instr(q.sql, '\\b' || t.table_name || '\\b') > 0
     where t.table_schema <> 'INFORMATION_SCHEMA'
     group by 1, 2 order by people desc, queries desc limit 50
     ```
  3. The person's own queries, with no grant at all. Just under seven days, the most the
     function allows, and only what they ran:

     ```sql
     with q as (select upper(query_text) as sql
                from table(<db>.information_schema.query_history_by_user(
                       end_time_range_start => dateadd('hour', -167, current_timestamp()),
                       result_limit => 10000))
                where execution_status = 'SUCCESS' and query_type = 'SELECT')
     select t.table_schema, t.table_name, count(*) as queries
     from <db>.information_schema.tables t
     join q on regexp_instr(q.sql, '\\b' || t.table_name || '\\b') > 0
     where t.table_schema <> 'INFORMATION_SCHEMA'
     group by 1, 2 order by queries desc limit 50
     ```
  4. None readable: `row_count` and `last_altered` from `<db>.information_schema.tables`.

  A bare table name that exists in more than one schema is counted for each: say so when the
  top tables share a name.
- **Table search**, always, beside the usage statement. Name matches come first:

  ```sql
  select table_schema, table_name, table_type, row_count, last_altered, left(comment, 120) as comment
  from <db>.information_schema.tables
  where table_schema <> 'INFORMATION_SCHEMA'
    and (table_name ilike any ('%<WORD_1>%', '%<WORD_2>%') or comment ilike any ('%<word_1>%', '%<word_2>%'))
  order by (table_name ilike any ('%<WORD_1>%', '%<WORD_2>%')) desc, row_count desc nulls last limit 50
  ```
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
- **Table usage**, for [discovery](discovery.md). Try in order; stop at the first that answers.
  1. Account-wide access, when the person can read system tables:

     ```sql
     select source_table_full_name as table_name,
            count(*) as reads, count(distinct created_by) as people
     from system.access.table_lineage
     where event_time >= current_timestamp() - interval 30 days
       and source_table_full_name is not null
     group by 1 order by people desc, reads desc limit 50
     ```
  2. Account-wide queries, when lineage is refused:

     ```sql
     with q as (select upper(statement_text) as sql, executed_by
                from system.query.history
                where start_time >= current_timestamp() - interval 30 days
                  and execution_status = 'FINISHED' and statement_type = 'SELECT')
     select t.table_schema, t.table_name, count(*) as queries, count(distinct q.executed_by) as people
     from <catalog>.information_schema.tables t
     join q on q.sql rlike concat('\\b', upper(t.table_name), '\\b')
     where t.table_schema <> 'information_schema'
     group by 1, 2 order by people desc, queries desc limit 50
     ```
  3. The person's own queries: the statement in 2 with `and executed_by = current_user()`
     where their own rows are readable, otherwise the Query History API through the CLI,
     which returns their own queries, matched to table names the same way.
  4. None readable: `show table extended` or the catalog's table metadata for size and
     last change.
- **Table search**, always, beside the usage statement:

  ```sql
  select table_schema, table_name, table_type, comment
  from <catalog>.information_schema.tables
  where table_schema <> 'information_schema'
    and (table_name ilike '%<word_1>%' or table_name ilike '%<word_2>%'
         or comment ilike '%<word_1>%' or comment ilike '%<word_2>%')
  limit 50
  ```
- **Names:** `catalog.schema.table`, lower case. Quote with `` `…` ``.
- **Server:** Databricks' managed SQL MCP service, at
  `https://<workspace-host>/ai-gateway/mcp-services/system.ai.dbsql` or the older
  `https://<workspace-host>/api/2.0/mcp/sql`. When the server offers a read-only variant of its
  execute tool, record that one. If a result comes back as a pending statement, poll it with the
  server's own status tool before reading rows.

## SQLite

- **Check:** `select sqlite_version()`
- **Find tables:** `select name, type from sqlite_master where type in ('table', 'view') order by name`
- **Columns:** `select name, type from pragma_table_info('<table>')`
- **Query history:** none; SQLite keeps no log of what ran. Use a declared model, a BI file or
  an export.
- **Names:** `table`, or `schema.table` for an attached database. Quote with `"…"`.
- **Dates:** stored as text or numbers. Truncate with `strftime('%Y-%m-01', <col>)` or
  `date(<col>, 'start of month')`, never `date_trunc`; shift with `date(<col>, '-7 days')`, never
  `interval`. Divide with `1.0 * a / b`, since integer division truncates a rate to 0.
- **Server:** no vendor-managed one. An Atlan admin registers the team's SQLite store as a
  custom MCP server whose tool runs one read-only statement.

## Another warehouse

Use `select 1` as the check, its `information_schema` for tables and columns, and its own
documented read-only query history. Record its name as `dialect`. If you can't find a
read-only history source, ask for an export file.
