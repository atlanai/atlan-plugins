# Query history

Try each source in order and use the first one that answers. Every statement keeps 90 days (or
less, where the source holds less) of successful SELECTs that touch the scoped tables, grouped
per distinct text, at most 5,000 rows, in pages. Never print the rows into the conversation or
copy them out by hand: turn each saved page into `querylog.jsonl` with `CONTEXT querylog`, as
section 2 of the metrics workflow (`metrics.md`) says. It keeps `{sql, exec, users}` and never
writes a user name.

## Size the pull before fetching it

Generated SQL (a BI tool's compiled query, a dbt model, an export job) can run to tens of
kilobytes a statement, and a chat SQL tool fails or drops the connection on a result of tens of
megabytes. Before the pull, run one counting statement over the same filter: distinct texts,
runs and total characters, grouped by statement length (under 2,000, 2,000 to 10,000, 10,000 to
50,000, over 50,000 characters).

- When the total is a few megabytes or less, pull it in pages as below.
- Otherwise page by length band, each band its own statement with a `length(<text>)` bound, so
  no page exceeds a few megabytes. Leave out the longest band when it holds few runs, and pass
  the same bound to `CONTEXT querylog --max-statement-chars <n>`, with the counting statement's
  totals for the band you never fetched as `--left-out-at-source <texts> <runs>`. Both land in
  `querylog.jsonl.coverage.json`.
- Say what was left out with its counts ("300 of 2,000 texts, 900 runs, over 10,000
  characters"), and keep that line in the survey and the collection review.

## Snowflake

When using the metrics module with its `snowflake` feature and a configured direct connection,
prefer its native `history_source: account_usage` route. It needs no prepared shape table and
uses the existing common plug contract. See [the module procedure](../../../metrics-generation/references/metrics-engine.md).
The numbered alternatives below apply when capturing through chat SQL tools or when the
person selected an existing shape view.

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

## Databricks

With the direct connector, use the maintained metrics module's `databricks` feature. Supply
the selected numeric workspace ID, bounded tables and absolute dates. The native source is
`system.query.history`, with explicit masking and namespace-context checks. See
[the module procedure](../../../metrics-generation/references/metrics-engine.md). The alternatives below are capture routes for
existing authorized tools; keep their scope within the selected workspace.

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

## Another warehouse

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
