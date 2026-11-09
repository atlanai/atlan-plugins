# Databricks input plug

The maintained `databricks-direct` plug reads native query history and Unity Catalog column
metadata and verifies SQL through the existing read-only socket. Custody is `customer`.
The optional `databricks` runtime feature installs this package. The older SQL-only example
remains separate; do not install both distributions into the same runtime.

## Connection and sign-in

Use the selected warehouse's non-secret `server_hostname` and `http_path` settings, or
`DATABRICKS_SERVER_HOSTNAME` and `DATABRICKS_HTTP_PATH` in the environment. Browser OAuth
(`sign_in: browser`) uses the official connector and a genuine host terminal, including the
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
