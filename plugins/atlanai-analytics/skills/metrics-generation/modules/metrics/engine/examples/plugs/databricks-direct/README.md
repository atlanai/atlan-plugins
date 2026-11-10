# databricks-direct (example plug)

The Analytics plugin's `databricks` feature uses the
[maintained package](../../../packages/metrics-agent-databricks/README.md), with native history,
column metadata and browser/token sign-in. This folder remains a SQL-only authoring example;
do not install both distributions into the same runtime.

A community example of a **SQL-check plug**: a read-only connection to a Databricks SQL
warehouse, used to check (explain or execute, read-only) every statement the agent proposes.
It is about fifty lines, and it shows how small a plug for another warehouse is. It is not
maintained with the core.

- Plug name: `databricks-direct` (input plug, `custody: customer`)
- Capability: the SQL check (`sql_runner`), built with its own `settings.databricks-direct`;
  `inputs.sql_check: databricks-direct` in `run.yml` names it
- Dialect: `databricks`
- Every statement passes the core's read-only gate before it reaches the warehouse.

## Install

From the root of this repository, into the same environment as `metrics-agent`:

```bash
pip install -e examples/plugs/databricks-direct
```

It installs `databricks-sql-connector` and registers the plug under the `metrics_agent.plugs`
entry point. `metrics-agent plugs` lists it once installed.

## Environment

The plug reads one variable, and has no default for it:

| Variable | What it holds |
|---|---|
| `METRICS_AGENT_WAREHOUSE_DSN` | `key=value` pairs separated by `;`, handed to `databricks.sql.connect` (`server_hostname`, `http_path`, `access_token`) for a read-only principal |

Keep it in a git-ignored dotenv; the plug never prints it.

## run.yml

```yaml
version: 1
scope: my-estate
dialect: databricks
mode: standalone
inputs:
  history: [local-files]
  bi: []
  schema: [databricks-direct]
  sql_check: databricks-direct
publish: [files]
tables: [main.sales.orders]
settings:
  local-files:
    history: querylog.jsonl
  databricks-direct:
    witness_mode: explain
```

`witness_mode` is `explain` (plan each statement, the default) or `execute` (run it read-only
with a row limit). An optional `catalog` setting names the default catalog.

## Check it

```bash
metrics-agent setup --check run.yml
```

With `METRICS_AGENT_WAREHOUSE_DSN` unset the check refuses and names the variable (never a value); with it
set, it connects read-only and runs `SELECT 1`.

## Conformance kit

The socket contract, as tests, runs against this plug offline (a fake `databricks.sql` stands in
for the warehouse). From the root of this repository:

```bash
# See the plug-author conformance example in docs/design/writing-a-plug.md.
```

To test a plug of your own the same way, subclass `InputPlugConformance` from
`metrics_agent.testing.conformance` (see *Writing a plug*, `docs/design/writing-a-plug.md`).
