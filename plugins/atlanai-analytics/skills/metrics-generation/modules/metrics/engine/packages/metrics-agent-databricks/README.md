# Databricks input plug

The maintained `databricks-direct` plug reads native query history and Unity Catalog column
metadata and verifies SQL through the existing read-only socket. Custody is `customer`.
The package also registers the `databricks-metric-view` BI reader: see
[Metric views](#metric-views).
The optional `databricks` runtime feature installs this package. The older SQL-only example
remains separate; do not install both distributions into the same runtime.

## Connection and sign-in

Use the selected warehouse's non-secret `server_hostname` and `http_path` settings, or
`DATABRICKS_SERVER_HOSTNAME` and `DATABRICKS_HTTP_PATH` in the environment. Browser OAuth
(`sign_in: browser`) uses the official SDK OAuth cache and a genuine host terminal, including the
module wrapper's prompt relay and deadline. It needs no password or token in chat.

Unattended runs use `DATABRICKS_TOKEN` with `sign_in: token`. An existing
`METRICS_AGENT_WAREHOUSE_DSN` remains supported for the connection fields `server_hostname`,
`http_path`, `access_token`, `auth_type`, `catalog` and `schema`. Credentials stay in the
environment or an approved secret manager, never run configuration or source files. The
plug refuses to reuse a token when selected host metadata conflicts with its configured host.

| Sign-in | Configuration |
| --- | --- |
| Databricks browser OAuth U2M | `sign_in: browser`, real host TTY |
| Microsoft Entra browser OAuth | `sign_in: azure-browser`, real host TTY |
| PAT or externally obtained bearer token | `sign_in: token`, `DATABRICKS_TOKEN` |
| Databricks service-principal OAuth M2M | `sign_in: oauth-m2m`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` |
| Existing SDK/CLI profile | `sign_in: unified`, optional non-secret `profile`, or `DATABRICKS_CONFIG_PROFILE` |
| Azure service principal or CLI identity | `sign_in: unified`, `sdk_auth_type: azure-client-secret` or `azure-cli` |
| Google credentials or service-account impersonation | `sign_in: unified`, `sdk_auth_type: google-credentials` or `google-id` |
| OIDC workload federation | `sign_in: unified`, `sdk_auth_type: env-oidc`, `file-oidc`, `github-oidc`, `github-oidc-azure` or `azure-devops-oidc` |
| Databricks runtime, metadata service or model-serving identity | `sign_in: unified`, the SDK's configured runtime provider |

The unified route delegates credential acquisition and refresh to the pinned official
Databricks SDK. Its profile or `DATABRICKS_HOST` must already name the selected workspace;
the plug checks the resolved host before calling a credential provider. It never substitutes
a different host into an existing profile. `sdk_auth_type` selects an SDK provider explicitly;
otherwise the SDK uses its configured priority. Azure managed identities can use an already
authenticated Azure CLI session where supported by that CLI and the workspace. These routes
depend on the provider's SDK, CLI, cloud identity and federation policy prerequisites.

With no explicit `sign_in`, a token selects token mode, a complete client ID/secret pair
selects M2M, and an explicit profile or `DATABRICKS_AUTH_TYPE` selects unified authentication.
Otherwise browser sign-in is used. A bare default profile is used only through an explicit
unified selection. Username/password authentication is unsupported by Databricks SQL and
refused. The optional non-secret `identity_federation_client_id` is passed to the connector
for its service-principal token-federation route. Secrets and token files remain under the
provider's custody; never copy their contents into the run or a diagnostic.

These are connector routes, not a claim that every identity has the required warehouse and
data grants. PAT SQL and metadata access have been tested live. Other routes have offline
contract coverage; their tenant-specific sign-in and policies need a live test.

[SQL connector authentication](https://docs.databricks.com/aws/en/dev-tools/python-sql-connector)
and [SDK unified authentication](https://docs.databricks.com/aws/en/dev-tools/auth/unified-auth)
describe the supported provider setup.

The identity needs CAN USE on the selected SQL warehouse. Column reads need Unity Catalog
access to the selected catalogs, schemas and tables. A workspace-admin identity is not a
substitute for checking those grants and the compute's Unity Catalog configuration.

## Native query history

Select `databricks-direct` in `inputs.history`, supply bounded `tables` and the numeric
`workspace_id` from the selected workspace URL. Native `system.query.history` is regional
and can include other workspaces, so the workspace filter is mandatory. Absolute run window
start/end dates pin coverage. The UTC end bound is exclusive. The package policy defaults to
90 days, a 365 day maximum and 5,000 groups. Overflow blocks before partial admission.

Only successful SELECTs are grouped by exact SQL. Execution and distinct-user counts are
returned with one representative statement ID. User names and user IDs are not exported.
Original SQL formatting is preserved. The core performs exact table-lineage admission.

Before filtering query text by table names, the plug checks for masked or missing SQL in the
selected workspace/window. Those rows cause an explicit access blocker, rather than a
successful empty-history result. Ask an administrator for an approved source with visible
SQL text or supply a scoped export; never change grants automatically.

The native history table does not record historical default catalog/schema. This version
therefore requires physical table references to be fully qualified. CTE names need no prefix.
Unqualified, unparseable or dynamic table sources block the extraction before any partial
history is emitted. Provide qualified SQL or a local history export with evidenced session
context; never assign the current connection's defaults to past executions.

[Databricks documents native history coverage and SQL-text masking](https://docs.databricks.com/aws/en/admin/system-tables/query-history).

```yaml
version: 1
scope: example-domain
dialect: databricks
mode: standalone
tables: [example_catalog.sales.orders]
window:
  start: '2026-01-01'
  end: '2026-02-01'
inputs:
  history: [databricks-direct]
  schema: [databricks-direct]
  bi: []
  sql_check: databricks-direct
settings:
  databricks-direct:
    workspace_id: '123456789'
    server_hostname: example.cloud.databricks.com
    http_path: /sql/1.0/warehouses/example_warehouse
    sign_in: browser
publish: [files]
observers: []
```

Run preparation and advancement with a real TTY for browser sign-in. An explicit parse-only
run does not establish warehouse execution, and example execution does not prove formula
parity. Publication remains the host plugin's responsibility.

## Metric views

A metric view is a declared model. Each measure becomes one metric with the view's own
formula, the fields it can be cut by with their comments and synonyms, and the joins that
reach them ([declared models](../../docs/design/declared-models-contract.md)).

The plug reads a saved definition and fetches none: the definition is returned by `DESCRIBE`
and by no query, and the plug's read-only gate admits queries alone. Save one of these and
name the file, or a folder of them, as `bi`:

| File | How to get it |
| --- | --- |
| `.json` | the output of `DESCRIBE TABLE EXTENDED <view> AS JSON`, or of Unity Catalog's table API. It types every field and measure |
| `.yaml`, `.yml` | the view's YAML definition |
| `.sql` | its `CREATE VIEW ... WITH METRICS LANGUAGE YAML AS $$ ... $$` statement |

```yaml
inputs:
  history: []
  schema: []
  bi: [databricks-direct]
  sql_check: databricks-direct
settings:
  databricks-direct:
    bi: /absolute/private/example_metrics.json
    server_hostname: example.cloud.databricks.com
    http_path: /sql/1.0/warehouses/example_warehouse
    sign_in: unified
```

`local-files` with `bi_vendor: databricks-metric-view` reads the same files with no
connection.

A file is UTF-8 text of at most 1 MiB, and a `.sql` file holds one statement. A folder is
read as every file in it that is a metric view with at least one measure, in sorted order.
Any other file in the folder is left out and never stops the run: a dimension-only view, a
view the reader refuses, a file that is not a view, a file that is not UTF-8. Hidden folders
and symbolic links are not read. To be told why a file was left out, name that file alone. A
folder with no readable view is refused by name, and so is a single file with no measure.

Read: YAML specification 0.1 and 1.1, `fields` or `dimensions`, many-to-one joins (nested
too, `on` or `using`), `MEASURE()` references, and a top-level `filter`, which is written
into every aggregate as `FILTER (WHERE ...)`, after `WITHIN GROUP (...)` where there is one. A
view declares no key, so the card author states each metric's grain.

Names follow two rules, both observed on a Databricks workspace on 2026-11-26:

- The view's `filter` reads the source's columns (and a joined table's through its join
  name), never a field. With a field `status` defined as `LOWER(status)`, the filter
  `status = 'f'` compares the column.
- A field or a measure reads fields first: a bare name that an earlier field defines is that
  field, and any other bare name is a column of the source.

When a field redefines a column that the filter reads, the model cannot hold both under one
name. The filter keeps the column, the field is not listed as a dimension, and a measure that
reads the field carries its expression. A note names the field.

A table name is `catalog.schema.table`, each part a plain identifier (letters, digits and
underscores), with or without backticks. A `using` column and a key column are one plain
identifier. A name that needs its quoting (a space, a hyphen) is refused like a column that
needs quoting, because a name is written into SQL as it stands. The definition does not say
whether a source is a table or another metric view, so a source that is a metric view is read
as a table, and the run's live check is what catches it.

Not read, each named in the run's reader notes:

- a view whose own source is not a table name (a query, or a name that needs quoting): the
  whole view;
- a join whose source is not a table name, or whose `using` is not a list of plain column
  names, with everything that reads it; a one-to-many join; a join condition that is not
  equalities between its two tables;
- window measures and measures built on them;
- an expression that reads a `parameters` entry or holds a parameter marker (`:name`, `?`);
- wildcard fields, columns that need quoting, and an expression that is more than one
  expression or holds a query or a lambda;
- an expression with a string literal that has a quote or a backslash in it, or two literals
  side by side (`'it''s'`). When the view's filter is one, no measure is read;
- under a view filter, a measure the filter cannot be shown to reach: one that calls an
  unknown function, or reads a column outside every aggregate the reader recognises
  (`ANY(is_late)`, `amount * 2`);
- an ordered aggregate the SQL writer would change: `PERCENTILE_CONT` and `PERCENTILE_DISC`
  with `WITHIN GROUP`, and `ARRAY_AGG(x ORDER BY y)`. `LISTAGG ... WITHIN GROUP` is read;
- a field or measure longer than 20,000 characters once the fields and measures it reads are
  written out;
- a filter that names a field which `source_columns` shows is not a column of the source.
  Databricks accepts no such view.

A join written without table prefixes (`on: o_custkey = c_custkey`) is read only when
the tables' columns come with the view. Wrap the description to supply them, and the source
table's key. List each table's columns in full: when the view's filter names a field that
the source's list does not hold, the reader takes it as not a column and refuses the filter.
One column may be given as a string. A value of another shape is ignored, and a note says so:

```json
{"metric_view": {"view_text": "..."},
 "source_columns": {"example_catalog.sales.orders": ["o_custkey"],
                    "example_catalog.sales.customer": ["c_custkey"]},
 "primary_keys": {"example_catalog.sales.orders": ["o_orderkey"]}}
```

A view's definition does not say which workspace it was read from. To give each metric a link
back to the view, add the workspace's host beside the description:

```json
{"metric_view": {"catalog_name": "example_catalog", "schema_name": "sales",
                 "table_name": "orders_metrics", "view_text": "..."},
 "workspace_host": "example-workspace.cloud.databricks.com"}
```

The reader then records the view's page in Catalog Explorer
(`https://<host>/explore/data/<catalog>/<schema>/<view>`) on the model's asset, and each
metric built from the view carries it as a source reference (`provenance.observed_in`). Only a
bare workspace host is taken, and only for a view with a plain three-part name; anything else
is named in the reader notes and no link is recorded.

Checked live on a Unity Catalog workspace: for 20 metric views, each declared formula over
its base tables returned the value `MEASURE()` returns on the view.

## Connection isolation and reuse

Prefer `DATABRICKS_DSN` for Databricks connection fields. The legacy shared warehouse DSN is
used only when it names `server_hostname`; a Snowflake or Postgres DSN does not select this
plug's connection. An explicitly supplied Databricks DSN with unsupported fields is refused.
Browser OAuth delegates cache and refresh to the official SDK and checks the resolved host
before credential acquisition. Direct Entra browser OAuth reuses an in-memory connector cache
within a process; separate processes can still require sign-in. For durable reuse of a
configured identity, use the SDK profile or CLI route. No plug writes a custom token file.
