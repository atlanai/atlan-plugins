# postgres-direct (example plug)

A community example of a **SQL-check plug**: a read-only connection to a PostgreSQL database,
used to check (explain or execute, read-only) every statement the agent proposes. It is about
fifty lines, and it shows how small a plug for another warehouse is. It is not maintained with
the core.

- Plug name: `postgres-direct` (input plug, `custody: customer`)
- Capability: the SQL check (`sql_runner`), built with its own `settings.postgres-direct`;
  `inputs.sql_check: postgres-direct` in `run.yml` names it
- Dialect: `postgres`
- Every statement passes the core's read-only gate before it reaches the database.

## Install

From the root of this repository, into the same environment as `metrics-agent`:

```bash
pip install -e examples/plugs/postgres-direct
```

It installs `psycopg` and registers the plug under the `metrics_agent.plugs` entry point.
`metrics-agent plugs` lists it once installed.

## Environment

The plug reads one variable, and has no default for it:

| Variable | What it holds |
|---|---|
| `METRICS_AGENT_WAREHOUSE_DSN` | a libpq connection string or URL for a read-only role |

Keep it in a git-ignored dotenv; the plug never prints it.

## run.yml

```yaml
version: 1
scope: my-estate
dialect: postgres
mode: standalone
inputs:
  history: [local-files]
  bi: []
  schema: [postgres-direct]
  sql_check: postgres-direct
publish: [files]
tables: [analytics.public.orders]
settings:
  local-files:
    history: querylog.jsonl
  postgres-direct:
    witness_mode: explain
```

`witness_mode` is `explain` (plan each statement, the default) or `execute` (run it read-only
with a row limit).

## Check it

```bash
metrics-agent setup --check run.yml
```

With `METRICS_AGENT_WAREHOUSE_DSN` unset the check refuses and names the variable (never a value); with it
set, it connects read-only and runs `SELECT 1`.

## Conformance kit

The socket contract, as tests, runs against this plug offline (a fake `psycopg` stands in for
the server). From the root of this repository:

```bash
# See the plug-author conformance example in docs/design/writing-a-plug.md.
```

The same plug against a real server: `tests/test_postgres_live.py` seeds a small table and
a read-only role, then checks the connection and witnesses statements (explain and execute)
as that role. It is skipped unless `METRICS_AGENT_TEST_PG_DSN` names a server it may write
that fixture to; the module docstring shows a throwaway container to run it against.

To test a plug of your own the same way, subclass `InputPlugConformance` from
`metrics_agent.testing.conformance` (see *Writing a plug*, `docs/design/writing-a-plug.md`).
