# Warehouse routes and checks

Read in step 3. The goal is one way to run SQL on the team's warehouse as this person. Check the
routes in this order, stop at the first that is verified, and record the others found as
`present`. When the person names their warehouse, go straight to its row; a route for a warehouse
the team does not use is noted, not checked. With several connections or profiles on one route,
ask which; never pick for the person.

| Order | Route | Check |
|---|---|---|
| 1 | A warehouse server in **your own tool list**: names with `snowflake`, `databricks`, `bigquery`, `redshift` or `postgres`, or Atlan's gateway fronting one | [Through a tool](#through-a-tool) |
| 2 | **Snowflake CLI** (`snow`), or Cortex Code (`cortex`), which shares its connections | `READY check snowflake --connection "<label>"` |
| 3 | **Databricks CLI** | `READY check databricks --profile "<label>"` |
| 4 | **BigQuery** (`gcloud`, `bq`) | `READY check bigquery --configuration "<label>"`, adding `--project <id>` when the configuration sets none |
| 5 | **Redshift** (`aws`) | `READY check redshift --profile "<label>" --database <db> --workgroup <name>`, or `--cluster <name>` for a provisioned cluster. Ask for the workgroup or cluster and the database: they are not secrets |
| 6 | **Postgres, ClickHouse, Microsoft Fabric or SQL Server, Trino, DuckDB** | [Other warehouses](#other-warehouses) |

`via` in a record is `atlan` (a tool behind Atlan's gateway), `host` (a tool connected to this
agent directly) or `cli` (rows 2 to 6).

## Through a tool

- **Behind Atlan:** `mcp__search_tools` with the query "run a read-only SQL query on the data
  warehouse". A candidate runs one SQL statement and returns rows; skip tools that create, write or
  call procedures. Its `_meta` `com.atlan/server-domain` names the warehouse:
  `snowflakecomputing.com` is Snowflake, `databricks.com` or `azuredatabricks.net` is Databricks;
  otherwise go by the server's and the tool's names. A matching server listed under `connect` is
  set up but not signed in: `mcp__connect_server` returns its sign-in link. Say "<server> is set up
  in Atlan, but you haven't signed in to it yet. Sign in with this link, then tell me when you're
  done: <link>", and record the route `present` until a check passes.
- **Connected to this agent:** a SQL tool in your own list. In Cortex Code, the session's own
  Snowflake connection is one. A server listed but not callable needs the person to sign in from
  their agent (Claude Code `/mcp`; Codex `codex mcp login <name>`; Cursor Settings, then MCP): record
  it `present`.

Run the warehouse's check statement once:

| Warehouse | Check statement | Administrator when |
|---|---|---|
| Snowflake | `select current_user(), current_role(), current_warehouse()` | The role is ACCOUNTADMIN, SECURITYADMIN, SYSADMIN, ORGADMIN or USERADMIN: recording the role is enough |
| Databricks | `select current_user(), current_catalog(), is_member('admins') as is_admin` | `is_admin` is true: add `--set admin=yes` |
| BigQuery | `select session_user()` | |
| Redshift | `select current_user` | |
| Postgres | `select current_user, current_setting('is_superuser')` | The second column is `on`: add `--set admin=yes` |
| Another | `select current_user`, or that engine's equivalent | |

Then `READY record warehouse --status verified --set kind=<kind> --set via=<atlan|host> --set
server="<server>" --set user=<user> --set role=<role>`. A refusal is `failed` with `--set
error="<first line>"`; the script strips hosts and links from it.

## Past queries

The same yes covers one more statement: whether the team's past queries can be read. Each returns a
count from at most one row, never a statement's text. Try everyone's first, then the person's own,
and stop at the first that answers:

| Warehouse | Everyone's | Their own |
|---|---|---|
| Snowflake | `select count(*) as n from (select 1 from snowflake.account_usage.query_history limit 1)` | `select count(*) as n from (select 1 from table(<database>.information_schema.query_history_by_user(result_limit => 1)))`, with a database they read |
| Databricks | `select count(*) as n from (select 1 from system.query.history limit 1)` | |
| BigQuery | ``select count(*) as n from (select 1 from `<project>`.`region-<region>`.INFORMATION_SCHEMA.JOBS limit 1)`` | The same with `JOBS_BY_USER` |
| Redshift | | `select count(*) from (select 1 from sys_query_history limit 1)` |
| Another | Not checked: record `not_checked` | |

Record `READY record history --status verified --set kind=<kind> --set scope=<everyone|your own>`,
or `--status failed --set kind=<kind> --set error="<first line>"` when neither answers.

## Through a CLI

Rows 2 to 5 run through the script: it runs one read-only statement with the person's own sign-in,
keeps names only, flags an administrator role and records the result itself, then checks past
queries the same way and records that too. A connection that
signs in through the browser opens a tab during its check. A sign-in that has to run on its own
(`databricks auth login`, `gcloud auth login`, `aws sso login`, `az login`) is the person's to run:
the check records the failure and the report gives them the command.

## Other warehouses

Run the one command for the person's warehouse after a yes, with the host, database and user they
give you. Those go into the command only, never into a record or the report. A command that asks
for a password is the person's to run in their own terminal: record the route `present`.

| Warehouse | Check command |
|---|---|
| Postgres, Aurora, Supabase, Neon, AlloyDB | `psql -w "host=<host> port=5432 dbname=<db> user=<user> sslmode=require" -Atc "select current_user, current_setting('is_superuser')"`. The password comes from their `~/.pgpass` or `PGPASSWORD`, never from you; `-w` stops it prompting. `on` in the second column is a superuser |
| ClickHouse | `clickhouse-client --host <host> --secure --user <user> --query "select currentUser()"` |
| Microsoft Fabric, SQL Server | `sqlcmd -S <server> -d <database> --authentication-method ActiveDirectoryAzCli -Q "select suser_sname()"`, after the person's own `az login` |
| Trino, Starburst | `trino --server https://<host> --user <user> --external-authentication --execute "select current_user"`, which opens a browser to sign in |
| DuckDB, MotherDuck | `duckdb <file> "select 1"`; MotherDuck `duckdb md: "select 1"` with `motherduck_token` set in their shell |

Record `READY record warehouse --status <verified|failed|present> --set kind=<postgres|clickhouse|
fabric|trino|duckdb> --set via=cli --set user=<user it returned>`, adding `--set admin=yes` for a
superuser. For anything else, record `--set kind=other` with how the person reaches it today as a
`note`: it is not a blocker.

## For your warehouse admin

When only the person's own past queries, or none, can be read, the report hands them this script for
their warehouse admin, who reviews and runs it: you never run it. It creates one view of past
queries without user names; the admin narrows the `where` to the databases the team works in.

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
