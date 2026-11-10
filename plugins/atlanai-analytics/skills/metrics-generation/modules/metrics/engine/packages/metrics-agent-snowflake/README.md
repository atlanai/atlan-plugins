# metrics-agent-snowflake

The Snowflake plug for [metrics engine](../../README.md). With this package installed, a run can
check its SQL on your own read-only Snowflake role, read column metadata and query history from
your account, and read Snowflake semantic views as its BI model.

## What it adds

| Name | Kind | Custody | What it does |
|---|---|---|---|
| `snowflake-direct` | input plug | `customer` | SQL checks on a read-only connection; column names, types and comments from each database's `INFORMATION_SCHEMA`; bounded native query history or an existing shape table; semantic views read as their DDL |
| `snowflake-semantic-view` | BI reader | : | turns a semantic view's DDL or YAML into the core's common BI format, with the metrics, dimensions and relationships the view declares |

Custody `customer`: the plug reads your own account on your own grant, so a `standalone` run may
use it.

**Query history** defaults to a direct read of
`SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` when no `shape_table` is set. Only successful SELECTs
in the bounded window are aggregated, by exact SQL plus session database and schema. Counts
include executions and distinct users; user names never leave Snowflake. Each group carries
one representative query ID, rather than a complete execution ID list or first/last times.
SQL text keeps its original formatting. The core performs the exact table-lineage scope check.

The default window is the previous 90 days, with a 365 day maximum and at most 5,000 SQL/context
groups, controlled by this package's `snowflake_history` policy. Set absolute `window.start`
and `window.end` in `run.yml` to pin the extraction interval. Dates mean midnight UTC and the
end is exclusive. Too many groups blocks the read before any partial result is admitted;
narrow the window or table scope. No history view or table is created.

The role needs read access to the Account Usage view. An access refusal identifies the missing
read; select an existing shape table or local export when that grant is unavailable. There is
no automatic switch to a different history source. Account Usage can lag by up to 45 minutes
and some short hybrid-table queries are absent, as described in
[Snowflake's QUERY_HISTORY documentation](https://docs.snowflake.com/en/sql-reference/account-usage/query_history).

An existing `shape_table` preserves the prior route: one row per distinct statement with
`sql`, `exec`, `users` and optionally `query_id`. Its supplied counts and time coverage are
used as-is; the native window and overflow checks do not apply to a prepared shape table.

## Install

From a clone of the repository, after the core (packages are not published to a package index):

```bash
pip install -e .
pip install -e packages/metrics-agent-snowflake
metrics-agent plugs
```

## Environment variables

One variable, `METRICS_AGENT_WAREHOUSE_DSN`: the connection settings as `k=v;k=v` (account,
user, role, warehouse, authenticator, private_key_file, …), passed to the Snowflake connector.
In a `.env` file, single-quote the whole value. Use a read-only role: the agent refuses any
statement that writes before it is sent, but the role is the first lock. The getting-started
guide, section C, shows a key-pair example.

## Settings in `run.yml`

| Setting | Meaning |
|---|---|
| `history_source` | `account_usage` or `shape_table`; inferred from whether `shape_table` is set |
| `shape_table` | existing aggregated table, `DB.SCHEMA.TABLE`; omit for native Account Usage |
| `catalog` | the database the SQL check's session uses, so a statement naming `SCHEMA.TABLE` resolves. `setup --check` fails, naming this setting, when the session has none |
| `witness_mode` | for the SQL check: `explain` (plans only, reads nothing) or `execute` (runs read-only with a row limit) |
| `semantic_views` | semantic views to read, `DB.SCHEMA.VIEW` (fetched at the start of the run) |
| `bi` | a semantic view's DDL or YAML already on disk, instead of `semantic_views` |

The policy defaults this package adds (`semantic_view.*`) are in
[`policy.yml`](src/metrics_agent_snowflake/policy.yml); a section of the same name in your own
policy file wins.

```yaml
version: 1
scope: example-sales
dialect: snowflake
mode: standalone
inputs:
  history: [snowflake-direct]
  schema: [snowflake-direct]
  bi: [snowflake-direct]
  sql_check: snowflake-direct
publish: [files]
settings:
  snowflake-direct:
    history_source: account_usage
    semantic_views: [ANALYTICS.METRICS.SALES_VIEW]
    witness_mode: explain
```

## More

- [Getting started](../../docs/getting-started.md), section C: Snowflake, step by step.
- [Declared models](../../docs/design/declared-models-contract.md): how a semantic view's
  declarations reach the metric cards.
- [Plugs](../../docs/design/plugs.md): every plug and setting.

## Access limits are named, never a failure

The SQL check tells Snowflake's access refusals apart from a failed statement
(`access_blocker`): a warehouse over its resource monitor's quota or with none usable
(`warehouse_unavailable`: nothing more is sent after the first), a role the user does not
hold or a refused sign-in (`warehouse_sign_in_failed`), no SELECT on a table
(`no_select_grant`), and a table or database the role cannot see (`object_not_visible`,
`database_not_visible`; Snowflake answers a missing object the same way, so the ask is the
grant or the right name). Each statement it touches is `skipped_no_substrate` (unverified),
never quarantined, and the run's `access_report.json` names the grant to ask the warehouse
admin for.

## Typed connector options

The plug converts connector-declared boolean DSN fields from `true`/`false` to actual booleans
and integer fields from decimal strings to integers. Invalid values are refused before
connection, with the option name only. This includes OAuth refresh, keep-alive and timeout
options. Conversion is owned by this plug; common DSN parsing remains vendor-neutral.
