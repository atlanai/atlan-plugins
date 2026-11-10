# metrics-agent-tableau

The Tableau plug for [metrics engine](../../README.md). With this package installed, a run can
fetch Tableau workbooks, and every published data source they use, straight from Tableau Cloud
or Tableau Server, with your own Personal Access Token. No Atlan tenant is involved.

## What it adds

| Plug | Kind | Custody | What it does |
|---|---|---|---|
| `tableau-cloud` | input | `customer` | downloads each workbook and the published data sources it uses through Tableau's REST API, or reads files already on disk |

A workbook on a published data source carries no warehouse connection; the data source's own
file does. Downloaded by hand, the data sources are easy to forget, and then nothing resolves
to a table. This plug reads each workbook's XML, finds every published data source it uses
(connections of class `sqlproxy`), downloads each one, and follows any published data source
those are built on. The core's Tableau reader parses what it fetches. The plug uses the Python
standard library only, so the core is this package's one dependency.

What the REST API says that the files do not hold (a data source's certification and its note,
the workbook's and each data source's description) is written beside the workbook as
`bi_tags.json`, the core's common format; the reader copies it onto the workbook's assets and
the metric cards carry it as context. Who certified a data source is never written.

## Install

From a clone of the repository, after the core (packages are not published to a package index):

```bash
pip install -e .
pip install -e packages/metrics-agent-tableau
metrics-agent plugs
```

## Environment variables

| Variable | Meaning |
|---|---|
| `TABLEAU_SERVER_URL` | the server, `https://host` with no path: your Tableau Cloud pod (`https://prod-useast-a.online.tableau.com`, say) or your Tableau Server |
| `TABLEAU_SITE` | the site's content URL, as in `/#/site/<site>/`; unset or empty for the Default site of a Tableau Server |
| `TABLEAU_PAT_NAME`, `TABLEAU_PAT_SECRET` | a Personal Access Token (in Tableau: My Account Settings, Personal Access Tokens) |
| `TABLEAU_USERNAME`, `TABLEAU_PASSWORD` | a user name and password: Tableau Server, or Tableau Cloud for a user without multi-factor authentication or single sign-on |
| `TABLEAU_CONNECTED_APP_CLIENT_ID`, `TABLEAU_CONNECTED_APP_SECRET_ID`, `TABLEAU_CONNECTED_APP_SECRET_VALUE` | a connected app with direct trust, acting for `TABLEAU_USERNAME` |

**Set one complete set of credentials; you never say which kind.** The plug signs in by the
first route the environment completes: a Personal Access Token, then a connected app, then a
user name and password. A refused password sign-in on Tableau Cloud says that a Personal Access
Token is needed (password sign-in does not work with multi-factor authentication). A connected
app's token is signed here (HS256, five minutes, scopes `tableau:content:read`,
`tableau:workbooks:download`, `tableau:datasources:download`, `tableau:users:read` (the
user's own site role) and `tableau:insight_definitions_metrics:read` (Pulse); the app must
allow them). A call the token has no scope for (Tableau's code 401002) is named as a missing
scope. A refused sign-in lists the causes, among them an Unlicensed user (no site role). Every
secret is sent once, in the sign-in request, and never printed or written; the plug signs out
at the end of the fetch. Reading files already on disk needs none of these.

**What the sign-in may do.** After signing in, the plug probes, without reading any content, the
user's site role (whether it allows downloads at all) and whether the Metadata API answers.
`metrics-agent setup --check run.yml` reports the route and these capabilities. A site role
that can never download (Viewer, Unlicensed, Read Only) fails the check and asks for an Explorer
or Creator seat: no permission would help. Another role's refused download asks for the
Download permission on that object. A site without the Metadata API (on Tableau Cloud it needs
Data Management) still fetches through the downloads, and the check and the manifest say so;
`metadata_api: false` makes the plug never ask it. During the fetch
it takes the richest path for each object: the download (`includeExtract=false`); when a data
source download is refused (HTTP 403), the Metadata API; when neither is available, the object
is recorded as unavailable, with why and the access to ask for, and the fetch goes on with what
it obtained, saying so. The manifest records the route, the probed capabilities, the capability
each workbook and data source came through (`rest-download`, `metadata-api` or `unavailable`)
and the coverage, so a run says how complete its input was. Only a fetch that obtains no
workbook at all stops.

**Where the token may go.** `TABLEAU_SERVER_URL` must be `https://`, must not be an internal or
metadata address (`localhost`, `10.x`, `169.254.169.254`, `*.internal`, `*.local`), and its host
must be under `tableau.allowed_host_suffixes` in the policy. The package ships
`.online.tableau.com`; for your own Tableau Server, add its domain in your policy file:

```yaml
tableau:
  allowed_host_suffixes: [.online.tableau.com, .tableau.example.com]
  api_version: "3.19"
  timeout_seconds: 120
  max_download_bytes: 268435456
  max_datasource_depth: 5
```

A redirect is never followed, and no URL a server or a workbook names is requested.

**Pulse.** The Pulse metric definitions built on each fetched data source are saved beside it
(`<data source>.pulse.json`), so they come with the workbook; a site without Pulse still fetches.

**Several workbooks.** A run reads every workbook `workbooks` names, one model each.

**Virtual connections.** A workbook or data source on a virtual connection names only its
tables; the plug saves the virtual connection's own file beside it (`<name>.tvcx`, from
`virtualConnections/<id>/content`, served from REST 3.23; an older server's record gives the
same JSON as a `.tvc`), and the reader resolves each table to the warehouse object it names.

**Permissions.** The signed-in user needs View and Download/Save a Copy on each workbook,
Download Data Source on each published data source, and Connect on each virtual connection. When a data source download is refused
(HTTP 403), the plug rebuilds the data source from the Tableau Metadata API: its fields,
calculated-field formulas and upstream tables. That needs the Metadata API (on Tableau Cloud,
the Data Management add-on; on Tableau Server, the metadata services turned on). A data source
rebuilt this way has no join logic, relationships or data source filters, and the manifest says
so (`reducedFidelity`).

## Settings in `run.yml`

| Setting | Meaning |
|---|---|
| `workbooks` | the workbooks to fetch (a list): a URL on your server (`…/#/site/<site>/workbooks/<id>` or `…/#/site/<site>/views/<workbook>/<sheet>`), a workbook LUID, or `project/name` |
| `model` | optional: read only this one of the fetched workbooks (its folder name, the workbook's name); by default a run reads every workbook it fetched, one model each |
| `api_version` | the REST API version, when your Tableau Server is older than 2023.1 |
| `metadata_api` | `false`: never ask the Metadata API (a site without Data Management); the downloads only |
| `viewer` | `true`: compile `USERDOMAIN()` and `ISMEMBEROF()` for the identity the fetch signs in as: its domain and groups are written to `viewer.json` beside each workbook (never its name). Off by default, and such a widget is then refused by name. `USERNAME()` and `FULLNAME()` always compile to bind placeholders (`:viewer_user_name`, `:viewer_full_name`), never to a name; with `viewer: true` the file also says `user_name_bindable`, and A4 asks the plug for the identity's names again (`viewer_names`), binds them in memory to run the statement, and keeps none |
| `time_zone` | With `viewer: true`, the IANA time zone the site reads the current moment in (for example `America/Los_Angeles`), written to `viewer.json`: a relative date filter counts its periods from the date there, and "to date" on a date and time ends at that moment there. Unset: the warehouse session's own clock |
| `bi` | a workbook with its data sources already on disk; nothing is fetched |

```yaml
version: 1
scope: acme-sales
dialect: snowflake           # the dialect of the warehouse the data sources read
mode: standalone
inputs:
  history: []
  schema: []
  bi: [tableau-cloud]
publish: [files]
settings:
  tableau-cloud:
    workbooks:
      - https://prod-useast-a.online.tableau.com/#/site/acme/views/SalesOverview/Summary
```

The definitions are fetched into the run directory at the start of the run
(`inputs/bi/tableau-cloud/workbooks/<workbook>/`, the workbook with its data sources beside it),
with a `manifest.json` that lists every file, its size and hash, where each data source came
from and what failed, and holds no content. A resumed run reuses them.

## Status

Run live on a Tableau Cloud site: Personal Access Token sign-in; workbooks with their published
data sources; a workbook on a virtual connection (data source, `.tvcx` and table resolved to
the warehouse); a Metadata API rebuild that compiles to the same SQL as the download; Pulse
definitions fetched with their data source; two workbooks read in one run; and a non-admin
Explorer, whose refused data source and hidden Catalog tables are named with the access to
ask for. The connected app was run live as one user in each site role: Creator and Explorer
fetched the workbook and its data source and every widget's SQL matched Tableau's own numbers;
a Viewer's downloads were refused and named as a seat, not a permission; an Unlicensed user's
sign-in was refused (401001). With `metadata_api: false` a run fetched and matched through the
downloads alone. The password route and Tableau Server are tested offline only.

## More

- [Getting started](../../docs/getting-started.md), section E: Tableau from Tableau Cloud or
  Server.
- [Plugs](../../docs/design/plugs.md): every plug and setting.

## Over Tableau's MCP server (no plug credential)

When the operator's assistant has Tableau's own MCP server (`tableau-mcp`, npm
`@tableau/mcp-server`, or the hosted `mcp.tableau.com`), the `fetch-bi-over-mcp` skill saves
its answers in a raw folder and
`python3 -m metrics_agent_tableau.mcp_capture <raw> <capture> --role <site role> --custody customer`
turns them into a capture folder (route `mcp`) the run reads with `local-files`:

| Answer | Becomes |
|---|---|
| `download-workbook` (the file it wrote, copied into the raw folder) | `definitions/<workbook>/<workbook>.twb(x)` |
| `get-view-data` per sheet, with and without `viewFilters` | an oracle file per sheet and state, numbers as displayed |
| `get-workbook` | the manifest's `source` (project, web address without its query) |
| a published data source's `.tds` from another route (`extra.json`) | beside the workbook, with that file's own route |

The server has no tool that downloads a published data source, and `get-datasource-metadata`
names logical tables only: without the `.tds` from the browser or this plug, the capture
records the gap `data_source_definition`, S0 prints it, and A1 names those data sources an
acquisition gap. A view-data answer that lists its rows twice (seen live) is kept once. No
owner name or address reaches the capture. Checked live with tableau-mcp 4.27.2 over stdio
(read-only tool allow-list), signed in by PAT and by connected app: with the data source added
from a browser download, the capture gives the same bi_facts and the same SQL as this plug
(PAT and connected app), and the SQL returns the values `get-view-data` showed for every sheet,
unfiltered and filtered.

## Access limits are named, never a stop

What the fetch could not read is handed to the run as named blockers (`access.py`): a Viewer
or Unlicensed seat (`seat_cannot_download`, `sign_in_failed`), a workbook or data source
without Download (`download_refused`; partial when the Metadata API rebuilt it), one the user
cannot see (`no_access_to_item`), the Metadata API off when it was needed
(`catalog_api_unavailable`), and a connected app without a scope (`missing_scope`). Checked
live through the connected app in each site role. A fetch that read no workbook for an access
reason raises them as `AccessBlocked`, and the run goes on with its other inputs. They land in
the run's `access_report.json` with what to ask for and from whom; the whole matrix is in
[customer access](../../docs/guides/customer-access.md).

## Supplied JWTs, private endpoints and retries

`TABLEAU_JWT` supplies a JWT issued by an external authorization server or connected app.
`TABLEAU_UAT` supplies the signed JWT associated with a Tableau unified access token
configuration, not the UAT configuration ID. Both use Tableau's JWT sign-in exchange and
retain only the resulting session token in memory. The UAT route defaults to REST API 3.27;
an explicit `api_version` must support the selected route. JWT scopes, site and expiry are
validated by Tableau. PAT retains precedence when multiple complete routes are configured.

A private-IP or internal-DNS Tableau Server needs an explicit exact entry in
`tableau.allowed_private_hosts` in the operator's policy. Only RFC1918/ULA addresses and
internal DNS names are eligible. Metadata, localhost, loopback and link-local destinations
remain blocked. HTTPS verification and redirect refusal are unchanged. Public custom hosts
continue to use `allowed_host_suffixes`; no private host is approved automatically.

Reads and sign-in exchanges retry transient network failures and HTTP 429/500/502/503/504
within `retry_attempts` (default 3) and `max_retry_after_seconds` (default 30). Retry-After is
honored; a delay above the cap stops with an explicit error. Authentication and permission
denials are not retried.
