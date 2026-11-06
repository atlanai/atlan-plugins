# What access to ask the customer for

Ask for this before the first run, per source. Where a source signs in more than one way,
any one is enough: `metrics-agent setup` does not ask which one you have. It lists every
route with the variables it reads, and `setup --check` reports the route it found and how
complete the input will be. Every credential goes into an environment
variable on the machine that runs the agent, never into `run.yml`, a chat or an email.
`metrics-agent setup --check run.yml` then tests each one and names what is still missing.

**Ask for the warehouse too, whatever the BI tool.** The SQL check compiles every statement on
a read-only connection. Without it nothing is compiled: every statement ships `parse_only`, and
SQL that parses but cannot run (a column its inner query does not return, a table that is not
there) goes unnoticed. A run without one is allowed, for an estate with no warehouse access,
and it says so in `run_inputs.json` and when it ends.

## Snowflake (`snowflake-direct`)

| Ask for | Why |
|---|---|
| A service user with key-pair sign-in, and `DEFAULT_SECONDARY_ROLES = ()` on it | `METRICS_AGENT_WAREHOUSE_DSN` (account, user, role, warehouse, private key file); no other role joins the session |
| A read-only role: `USAGE` on a warehouse, `USAGE` on the databases and schemas in scope, `SELECT` on their tables and views | the SQL check runs `EXPLAIN` (or a row-limited read) and reads column metadata from `INFORMATION_SCHEMA`; the agent also refuses any statement that writes |
| The default database | logged and BI statements often name `SCHEMA.TABLE`, and Snowflake refuses to `EXPLAIN` one on a session with no current database. Set `settings.snowflake-direct.catalog`, or `database=` in the DSN; `setup --check` fails without one |
| Query history as a shape table: one row per distinct statement with its execution and user counts (`sql, exec, users[, query_id]`), and `SELECT` on it for the role | the plug reads that table (`settings.snowflake-direct.shape_table`), not `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`. Someone who can read `ACCOUNT_USAGE` (`IMPORTED PRIVILEGES` on the `SNOWFLAKE` database) builds it; or they export the log to a file for `local-files` |

```sql
-- Run by the customer's administrator; every name is a placeholder.
CREATE ROLE METRICS_READER;
GRANT USAGE ON WAREHOUSE <WH> TO ROLE METRICS_READER;
GRANT USAGE ON DATABASE <DB> TO ROLE METRICS_READER;
GRANT USAGE ON ALL SCHEMAS IN DATABASE <DB> TO ROLE METRICS_READER;
GRANT SELECT ON ALL TABLES IN DATABASE <DB> TO ROLE METRICS_READER;
GRANT SELECT ON ALL VIEWS IN DATABASE <DB> TO ROLE METRICS_READER;
GRANT ROLE METRICS_READER TO USER <SERVICE_USER>;
ALTER USER <SERVICE_USER> SET DEFAULT_ROLE = METRICS_READER, DEFAULT_SECONDARY_ROLES = ();
```

## Power BI

The `powerbi-fabric` plug uses the first of these that is set; any one is enough:

- **A service principal**: `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`. A Fabric
  administrator enables the tenant setting *Service principals can use Fabric APIs* (for a
  security group holding the principal), and the principal gets **Contributor** on each
  report's workspace and on the workspace of the semantic model it binds to.
- **An access token**: `PBI_ACCESS_TOKEN`, for a person or principal that already has one.
  One that has expired, or was issued for another API or another tenant, is refused by name.
- **A person signs in with a code** at a terminal (device code), with nothing set. The person
  needs **Contributor** (or higher) on each report's workspace. Where the tenant blocks the
  default sign-in app, ask for a public-client app registration and set its client id in
  `PBI_PUBLIC_CLIENT_ID` (an id, not a secret).
- **A person signs in in their own browser** (the OAuth authorization code with PKCE), with
  `sign_in: browser` in the plug's settings: for a tenant whose Conditional Access policy
  blocks the device code flow. The sign-in address opens in their browser and the answer comes
  back to a loopback address on their machine; the same role is needed as for the device code,
  and the same public client is used (`PBI_PUBLIC_CLIENT_ID` where the default one is blocked;
  its registration must allow the redirect `http://localhost`). It waits
  `fetch.max_sign_in_wait_seconds` (policy) for them. Tested against a stand-in identity
  provider; a live sign-in needs the person.

A service principal with only a certificate is not supported: ask for a client secret.

What each workspace role yields (`setup --check` reads the identity's own role when it is
assigned directly, not through a group, and says what it allows):

| Role | Definitions (`getDefinition`) | `.pbix` export | Values (`executeQueries`, for checking) |
|---|---|---|---|
| Admin, Member, Contributor | yes | yes, where the report allows download | yes |
| Viewer | no: refused, needs Contributor | only with download rights | only with Build |
| none | no: the workspace is not found | no | no |

A composite model that reads another semantic model by DirectQuery needs the same access on
the upstream model's workspace too: without it the fetch names the upstream model it cannot
see, and `executeQueries` on the composite model is refused (`PowerBINotAuthorizedException`),
even for an Admin of the composite model's own workspace. Admin was checked live; the lower
roles follow Microsoft's workspace-role table.

Optional, for the admin scan (a whole tenant's model metadata, when the identity has admin
API access): a Fabric administrator turns on *Service principals can access read-only admin
APIs* (for a principal) and, under Admin API settings, *Enhance admin APIs responses with
detailed metadata* and *Enhance admin APIs responses with DAX and mashup expressions*.
Each of these can be limited to security groups: the identity that scans must be in one of
them. Without the last one Power BI withholds every formula, and the scan is refused saying
so.

Optional: when a workspace is connected to Git (Fabric Git integration), a clone of that
repository holds the model's TMDL files, and `local-files` reads them with no Power BI access.

## Tableau

The recommended route is the `tableau-cloud` plug (package `packages/metrics-agent-tableau`),
which fetches the workbooks and data sources itself. It reads `TABLEAU_SERVER_URL`,
`TABLEAU_SITE` (optional: the default site when unset), and any one of these sign-ins:

- **A Personal Access Token** (recommended): `TABLEAU_PAT_NAME`, `TABLEAU_PAT_SECRET`.
- **A connected app**: `TABLEAU_CONNECTED_APP_CLIENT_ID`, `TABLEAU_CONNECTED_APP_SECRET_ID`,
  `TABLEAU_CONNECTED_APP_SECRET_VALUE`, and `TABLEAU_USERNAME` (the user it acts as). The app
  must allow the scopes `tableau:content:read`, `tableau:workbooks:download`,
  `tableau:datasources:download`, `tableau:users:read` and
  `tableau:insight_definitions_metrics:read`.
- **A user name and password**: `TABLEAU_USERNAME`, `TABLEAU_PASSWORD`.

The account needs a site role that can download (**Explorer** or **Creator**; a Viewer or an
Unlicensed user never can, whatever the permissions say, and the check asks for a seat), and
**View**, **Download Workbook/Save As** and **Download Data Source** on the projects in scope.
Checked live, one user in each role through the connected app: Creator and Explorer fetch and
match; a Viewer is refused every download; an Unlicensed user cannot sign in. Rebuilding a data
source whose download is refused needs the Metadata API, which on Tableau Cloud needs **Data
Management**; without it the downloads still work, and the check says the Metadata API is not
available (`metadata_api: false` in the plug's settings never asks it). Without the plug, download the workbooks and data sources (`.twb`, `.twbx`, `.tds`,
`.tdsx`) and read them with `local-files` (`bi_vendor: tableau`).

## No API credential: the browser route

When the customer cannot give the agent any of the sign-ins above (no service principal, no
token, no admin), their own operator can capture the definitions from their own signed-in
browser. Ask the operator's assistant to use the `fetch-bi-in-browser` skill: it saves what the
tool itself offers that person into one capture folder, and lists every file, with its hash, in
`capture_manifest.json` (`bi_capture.schema.json`):

- **Power BI**: the report's **File > Download this file** (`.pbix`). It needs **Contributor**
  or higher on the workspace and the tenant's download setting; a Viewer cannot download it.
  "A copy of your report and data" carries the model's rows to the operator's disk, so the
  operator is asked first.
- **Tableau**: **Download > Tableau Workbook**, plus **Download** on every published data source
  the workbook uses. It needs **Explorer** or **Creator** with the Download Workbook and Download
  Data Source capabilities; a Viewer can download neither.
- **The values the tool shows**, per widget and filter state: Power BI's **Export data >
  Summarized data** (raw numbers) or **Show as a table** (as displayed); Tableau's **Download >
  Crosstab** (as displayed). Any viewer can take these, unless an admin turned exports off.

The run reads the folder with `local-files` (`bi:` the capture folder, `bi_vendor:` the
manifest's `tool`). Only the definition files the manifest lists are read, each checked against
its hash; the value files never are. What the capture could not get is named in its `refused`
list (`download_not_permitted`, `export_disabled`, `session_expired`, ...), and a capture with
no definitions is refused with those reasons. The assistant never types a password or reuses
the page's token, and clicks only reads (download, export, show as a table). Checked live on
one report and one workbook: the SQL the agent wrote from each capture returns the values the
browser showed, unfiltered and filtered.

## No plug credential, but an MCP server: the MCP route

When the operator's own assistant reaches the BI tool through the vendor's own MCP server,
signed in as the operator, ask it to use the `fetch-bi-over-mcp` skill. It calls only read-only
tools, saves every answer verbatim in a raw folder, and the vendor package's converter
(`python3 -m metrics_agent_tableau.mcp_capture`, `python3 -m metrics_agent_powerbi.mcp_capture`)
writes a capture folder with route `mcp`, which the run reads with `local-files` like a
browser capture. No stage calls the MCP server.

- **Tableau** (`tableau-mcp`, npm `@tableau/mcp-server`, or the hosted `mcp.tableau.com`): a
  PAT or a connected app of the operator's, the same site role and capabilities as the
  `tableau-cloud` plug (Explorer or Creator, Download Workbook). `download-workbook` gives the
  workbook's own `.twb`; `get-view-data` the values each sheet shows, filtered or not. It has
  no tool that downloads a **published data source**, so a workbook on one needs that data
  source's `.tds` from the browser (Download Data Source) or the plug; without it the gap
  `data_source_definition` is recorded and those sheets get no SQL. Checked live (tableau-mcp
  4.27.2, PAT and connected app): with the data source added from the browser, the MCP capture
  gives the same bi_facts and the same SQL as the `tableau-cloud` plug, and the SQL returns
  the values `get-view-data` showed, unfiltered and filtered.
- **Power BI**: Microsoft's Power BI Authoring MCP server (npm `@microsoft/powerbi-modeling-mcp`,
  local; the operator accepts its EULA and signs in; Contributor or Build on the model) exports
  the model as TMDL, which the reader reads as it reads a fetched model, and runs DAX queries
  (raw values). Microsoft's remote Power BI MCP endpoint needs the tenant setting *Users can
  use the Power BI Model Context Protocol server endpoint* (it answers `FeatureNotAvailable`
  without it) and Build permission; its Execute Query answers are kept as values, and its
  schema and report-metadata answers are not a definition the reader reads. Neither server
  gives the **report**: the capture is the model alone (gap `report_definition`), and the run
  asks one question per measure. Not run live here: the Authoring server needs the operator
  to accept its licence, and the tenant has the remote endpoint off; the mapping is tested
  offline on the TMDL and executeQueries shapes, and the same TMDL and DAX answers from the
  tenant (by the API) gave the same model facts and SQL as the plug, and SQL equal to the DAX
  answers.

Every gap a capture names is printed by S0 (`WARNING ... has a gap`) and recorded in
`run_inputs.json` (`bi.sources[].capture.gaps`), with what the run therefore cannot verify.

## Every route: what it needs and what it can verify

| Route | Needs | Definitions | Values the tool shows (to check the SQL) |
|---|---|---|---|
| Power BI plug, service principal | `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`; Contributor; the Fabric API tenant setting | report and model | `executeQueries` (raw), with Build |
| Power BI plug, access token | `PBI_ACCESS_TOKEN` for the Power BI API | report and model | as the identity allows |
| Power BI plug, device code | a person at a terminal; Contributor | report and model | as the person allows |
| Power BI plug, browser sign-in (PKCE) | a person at a terminal with a browser; `sign_in: browser`; Contributor | report and model | as the person allows |
| Tableau plug, PAT / connected app / password | the plug's variables; Explorer or Creator with Download | workbook and every published data source | view data and VizQL Data Service |
| Browser capture (`fetch-bi-in-browser`) | the operator's signed-in browser; the role that downloads | the tool's own downloads | as displayed (Crosstab, Show as a table) and raw (Export data) |
| MCP capture, Tableau (`fetch-bi-over-mcp`) | the operator's `tableau-mcp`; Explorer or Creator | the workbook; a published data source only from another route | as displayed (`get-view-data`) |
| MCP capture, Power BI (`fetch-bi-over-mcp`) | the operator's Power BI MCP server, its licence accepted by them | the model only, no report | raw DAX answers only |
| Files on disk (`local-files`) | the files | whatever the files hold | none |

## When access is missing: the run flags it and goes on

A missing permission never stops a run. Each limit the run meets is a **named blocker** in
`access_report.json` in the run directory: its code, the item, what was not read, what the run
did instead, and exactly what to ask for, and from whom. The run reads everything else, and
every statement, widget and metric a blocker touches is published unverified
(`skipped_no_substrate`, labelled `parse_only`), never as a failure of the SQL. Every end of
the run (complete, paused, stopped or refused) prints the report's summary, grouped by whom to
ask. Only a run with nothing readable at all (every BI input blocked and no query history)
stops at S0, and it names the report. The custody and read-only checks are not access limits:
they still refuse.

Whom to ask: **operator** (you), **item owner**, **workspace or project admin**, **site admin**,
**tenant admin**, **warehouse admin**.

### Power BI (`powerbi-fabric`)

| State | What the run reads | Blocker code | Ask |
|---|---|---|---|
| Admin, Member or Contributor | report and model definitions; the admin scanner when the identity is a Power BI admin | none | |
| Viewer, the report allows download | the report's `.pbix` instead of its definition | `definition_needs_edit_rights` (partial) | Contributor on the workspace (workspace admin) |
| Viewer, no download (no Build, or the tenant's download setting off) | nothing of that report | `definition_needs_edit_rights`, `download_refused` | Contributor, or Build / the report's download permission (item owner); the download setting (tenant admin) |
| No role on an item, or an id it cannot see | every other item | `no_access_to_item` | a workspace role (workspace admin) |
| Model readable but not queryable (no Build) | the definitions; no value is read from the model | `model_not_queryable` (MCP value calls) | Build on the model (item owner) |
| Report on a model in another workspace | the report; its visuals resolve to no model | `bound_model_unreachable` | a role on the model's workspace, and that workspace in `targets` |
| Composite model, upstream model unavailable | the composite model's own tables; what it reads from upstream is refused by name at A1 | `upstream_model_unavailable` | a role or Build on the upstream model |
| Row-level security roles on the model | the model; the SQL has no per-viewer filter | `row_level_security` (unverified) | which role applies to whom (item owner) |
| Not a Power BI admin (no scanner) | the definitions; no scanner fallback | `catalog_api_unavailable`, only when a definition was refused | the scan, or read-only admin APIs for the principal (tenant admin) |
| Expired token, another API's, another tenant's | nothing through this plug; the run goes on with its other inputs | `sign_in_failed` | a fresh sign-in (you) |
| The remote MCP endpoint switched off | the model through another route | `value_service_off` (MCP gap) | the tenant setting for the MCP endpoint (tenant admin) |

### Tableau (`tableau-cloud`)

| State | What the run reads | Blocker code | Ask |
|---|---|---|---|
| Creator or Explorer with Download | the workbook and every published data source | none | |
| Viewer | nothing that needs a download | `seat_cannot_download` | an Explorer or Creator seat (site admin) |
| Unlicensed | nothing: the sign-in is refused (401001) | `sign_in_failed` | a seat (site admin) |
| View without Download on the workbook | every other workbook | `download_refused` | the Download permission (item owner) |
| Workbook not visible, or a wrong name | every other workbook | `no_access_to_item` | the View permission, or the right name |
| Data source without Download, Metadata API on | the data source rebuilt from the Metadata API (reduced fidelity) | `download_refused` (partial) | the Download permission on the data source |
| Data source without Download, Metadata API off or no lineage | the workbook; worksheets on that data source resolve to no table | `download_refused`, `catalog_api_unavailable` | the Download permission, or the Metadata API (site admin) |
| Connected app without `tableau:users:read` | everything; a refused download cannot be told apart (seat or permission) | `missing_scope` (partial) | the scope on the connected app (site admin) |
| Connected app without the Pulse scope | everything but the Pulse metric definitions | `missing_scope` | the scope, or Pulse access (site admin) |
| VizQL Data Service off, or a data source's embedded credentials missing | the definitions; the values that view or data source shows are not read (MCP route) | `value_service_off`, `source_credentials_missing` (MCP gaps) | the service (tenant or site admin), or the credentials (item owner) |

### The warehouse that verifies the SQL (`snowflake-direct`)

| State | What the run does | Blocker code | Ask |
|---|---|---|---|
| The verifying role has no SELECT on a table, or cannot see it | checks every other statement; those over that table are unverified | `no_select_grant`, `object_not_visible` | GRANT SELECT (and schema USAGE), or the right table name (warehouse admin) |
| A database the BI connection names is not visible | the same, per database | `database_not_visible` | GRANT USAGE on the database, or the right name |
| The warehouse is suspended or over its resource monitor's quota | stops sending after the first refusal; every statement is unverified | `warehouse_unavailable` | resume the warehouse: its quota raised or reset (warehouse admin) |
| A role in the connection the user does not hold, or a refused sign-in | nothing is sent; every statement is unverified | `warehouse_sign_in_failed` | GRANT the role, or a working sign-in |

A capture (browser or MCP) adds one blocker per gap it records (`capture_gap`, or the access
code of the limit the route met), with what to capture to close it.
