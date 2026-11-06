# metrics-agent-snowflake

The Snowflake plug for [metrics engine](../../README.md). With this package installed, a run can
check its SQL on your own read-only Snowflake role, read column metadata and query history from
your account, and read Snowflake semantic views as its BI model.

## What it adds

| Name | Kind | Custody | What it does |
|---|---|---|---|
| `snowflake-direct` | input plug | `customer` | SQL checks on a read-only connection; column names, types and comments from each database's `INFORMATION_SCHEMA`; query history from a shape table; semantic views read as their DDL |
| `snowflake-semantic-view` | BI reader | : | turns a semantic view's DDL or YAML into the core's common BI format, with the metrics, dimensions and relationships the view declares |

Custody `customer`: the plug reads your own account on your own grant, so a `standalone` run may
use it.

**Query history** comes from a *shape table*: one row per distinct statement, with its
execution and user counts (`sql`, `exec`, `users`, optionally `query_id`). Snowflake's own
`SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` is not read directly; aggregate it into a shape table
first, or export the log to a file for the `local-files` plug.

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
| `shape_table` | the shape table, `DB.SCHEMA.TABLE` (for `inputs.history`) |
| `catalog` | the database the SQL check's session uses, so a statement naming `SCHEMA.TABLE` resolves. `setup --check` fails, naming this setting, when the session has none |
| `witness_mode` | for the SQL check: `explain` (plans only, reads nothing) or `execute` (runs read-only with a row limit) |
| `semantic_views` | semantic views to read, `DB.SCHEMA.VIEW` (fetched at the start of the run) |
| `bi` | a semantic view's DDL or YAML already on disk, instead of `semantic_views` |

The policy defaults this package adds (`semantic_view.*`) are in
[`policy.yml`](src/metrics_agent_snowflake/policy.yml); a section of the same name in your own
policy file wins.

```yaml
version: 1
scope: acme-sales
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
    shape_table: ANALYTICS.METRICS.QUERY_SHAPES
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
