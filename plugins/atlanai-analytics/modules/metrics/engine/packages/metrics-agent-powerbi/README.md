# metrics-agent-powerbi

The Power BI plug for [metrics engine](../../README.md). With this package installed, a run can
fetch Power BI report and semantic-model definitions straight from Microsoft Fabric, signed in
as your own service principal or as yourself. No Atlan tenant is involved.

## What it adds

| Plug | Kind | Custody | What it does |
|---|---|---|---|
| `powerbi-fabric` | input | `customer` | fetches report (PBIR) and semantic-model (TMDL) definitions through Fabric's public `getDefinition` API, or reads an extract already on disk |

The core's Power BI reader parses what it fetches. The extractor it runs
(`pbi_definition_extract.py`) uses the Python standard library only, so the core is this
package's one dependency.

## Install

From a clone of the repository, after the core (packages are not published to a package index):

```bash
pip install -e .
pip install -e packages/metrics-agent-powerbi
metrics-agent plugs
```

## Signing in

Reading an extract already on disk needs no sign-in. A fetch signs in with the first of these
that applies:

1. **A service principal**, when `PBI_TENANT_ID`, `PBI_CLIENT_ID` and `PBI_CLIENT_SECRET` are
   all set: for CI and unattended runs. In Fabric, the tenant setting *Service principals can
   use Fabric APIs* must allow it, and the service principal needs a role on the workspace.
2. **An access token**, when `PBI_ACCESS_TOKEN` is set: a token already issued for the Power
   BI API, used as is and never stored. Its claims (never its signature) are read first: a
   token that has expired, or was issued for another API (its audience) or another tenant than
   `PBI_TENANT_ID`, is refused by name before any call.
3. **Your own sign-in**, when the run is at a terminal: the run prints a device code, you sign
   in in a browser with your usual account (single sign-on included), and the token is cached
   owner-only (mode 0600, under `~/.cache/pbi_definition_extract/`) so the next run within
   the hour does not ask again. `PBI_TENANT_ID`, when set, picks the tenant. You need a role
   on the workspace. Where the tenant blocks the default sign-in app (a first-party public
   client), set `PBI_PUBLIC_CLIENT_ID` to a public-client app registration of your own; the
   cached sign-in is kept per app.
4. **Your own sign-in in your browser**, with `sign_in: browser` in the settings, when the run
   is at a terminal: the OAuth 2.0 authorization code flow with PKCE (S256). The run prints the
   sign-in address and opens it in your browser; after you sign in, the answer comes back to a
   one-request listener on `127.0.0.1` (redirect `http://localhost:<port>/`), its `state` is
   checked, and the code is exchanged with the PKCE verifier. Nothing from the answer is
   echoed, printed or written; the token is cached like the device code's. Use it where the
   tenant's Conditional Access blocks the device code flow. The public client (the default
   one, or `PBI_PUBLIC_CLIENT_ID`) must allow the redirect `http://localhost`. It waits
   `fetch.max_sign_in_wait_seconds` (300) for you, then fails by name. Tested offline against
   a stand-in identity provider (state, error answer, timeout, a refused exchange); a live
   sign-in needs you at the keyboard.

A service principal that has only a certificate (`PBI_CLIENT_CERTIFICATE_PATH`) is not
supported: `setup --check` says so by name. Give it a client secret, or hand over a token it
obtained with its certificate.

A run that is not at a terminal (CI, a scheduler) and has neither a service principal nor a
token is refused by name before anything is fetched; it never waits for a device code nobody
will see.

## What it fetches: the richest route your rights allow

You never say what kind of credential you hold. Whichever identity signed in, the fetch takes,
item by item, the richest route that identity's rights allow, and falls back when one is
refused:

| Route | What it brings | Needs |
|---|---|---|
| `getDefinition` | the report (PBIR) and semantic-model (TMDL) definitions | Contributor or higher on the workspace (a Viewer is refused) |
| `export` | a report the definition API refused, as its `.pbix` (a paginated report as its `.rdl`), read by the `.pbix` reader | build or download rights, and a report that allows download |
| `scanner` | the admin scanner API's metadata (tables, columns, measures, expressions, lineage) for every workspace fetched from, saved under `scans/` | Power BI admin API access |

What no route could read is recorded with the reason, and the run goes on with what it got.
When the scanner ran, the core's reader takes each model's and report's endorsement
(`Promoted`, `Certified`) and description from the saved scan and carries them to the metric
cards as context; who certified it is never read. A thin report's own measures (PBIR
`reportExtensions.json`, the legacy layout's `modelExtensions`) are read with the report and
compiled like the model's.
How long the fetch waits is the run's policy (`fetch` in `agent_policy.yml`): one export,
definition or scan job is waited for at most `max_operation_wait_seconds` (900), and a throttled
call is retried after its `Retry-After` only up to `max_retry_after_seconds` (120). A longer
wait fails that item by name (`OperationTimeout`, `RetryAfterOverCap`), never holds the run.
The extract's `manifest.json` records how it signed in (`signIn`), what that identity could
reach (`capabilities`), which route produced each item (`items[].route`) and what was not
available and why (`unavailable`), including a composite model's upstream model this identity
cannot see (`upstream_model_not_visible`). `metrics-agent setup --check run.yml` says which
sign-in a run will use and what it can reach (the workspaces it can see, the identity's own role
on each when it is assigned directly, and what that role allows; whether the admin API
answers), without reading any content; for your own sign-in it probes once you have signed in,
from the cached token.

Each refusal is named by the code Fabric answers: `InvalidToken` (a token for another API, or
expired: sign in again, never "no role"); `EntityNotFound` / `ItemNotFound` (not visible: no
role); a 403 (the definition API needs Contributor or higher, or an encrypted sensitivity label
the identity has no usage rights to); `PowerBINotAuthorizedException` (no role or Build, or a
composite model whose upstream model the identity cannot query). A refused item listing ends
with a manifest that says why, never a traceback.

## Settings in `run.yml`

| Setting | Meaning |
|---|---|
| `targets` | report or workspace URLs or ids to fetch (a list) |
| `workspace` | a workspace id or URL: every report in it |
| `with_model` | also fetch the semantic model each report binds to, and the models a composite model reads by DirectQuery, up its chain (default `true`) |
| `model` | optional: read only this one of the fetched semantic models; by default a run reads every model the fetch brought, one model each |
| `bi` | an extract already on disk; nothing is fetched. An extract that holds one model a run could read is read as that model |
| `sign_in` | a person's sign-in: `device` (the default) or `browser` (authorization code with PKCE, in your own browser) |

A **composite model** can read tables from other semantic models by DirectQuery, and those can
be composite too. With `with_model` the fetch follows that chain to its end, and the reader
resolves each such table to the definition of the model it comes from (its warehouse table,
its columns, its measures). The upstream models of a chain are read through the model above
them, so they are never a choice you have to make with `model`. A link the fetch could not
find is refused by name when the model is read.

```yaml
version: 1
scope: acme-sales
dialect: snowflake           # the dialect of the warehouse the model reads
mode: standalone
inputs:
  history: []
  schema: []
  bi: [powerbi-fabric]
publish: [files]
settings:
  powerbi-fabric:
    targets:
      - https://app.powerbi.com/groups/<workspace-id>/reports/<report-id>
    with_model: true
```

The definitions are fetched into the run directory at the start of the run; a resumed run
reuses them.

A **model with no report** of its own (a shared or composite model fetched alone) gives one
question per measure, so its measures still become metric definitions.

## Status

Run live: device-code sign-in as a person, an access token, and a service principal with admin
API access; `getDefinition` for reports and models in Pro and dedicated-capacity workspaces; a
whole nine-model workspace read in one run; the admin scan saved beside the definitions; a
composite model followed up its chain to the warehouse table; and the `.pbix` export fallback
tried (and its refusal named) for a user without edit rights. A real dashboard compiled all 27
of its tiles, and all 27 passed a live Snowflake `EXPLAIN`. A scan's formulas need the tenant
setting *Enhance admin APIs responses with DAX and mashup expressions* for a group holding the
identity; Direct Lake is tested offline only.

Access routes run live as a workspace Admin service principal: `getDefinition` of a report, its
model and a composite model; the `.pbix` export; `executeQueries` (and its named refusal on a
composite model whose upstream lives in a workspace this identity cannot see); the identity's
own role read from the workspace; the named refusals for a workspace it has no role on, an
item that does not exist, a token for another API and a malformed token; `setup --check` for
the service principal, a handed-over token, a token for another API and a certificate-only
principal. The lower workspace roles (Member, Contributor, Viewer) were NOT switched live: a
service principal that demotes itself cannot restore its Admin role (the admin API refuses a
service principal), so their limits come from Microsoft's workspace-role table.

## More

- [Getting started](../../docs/getting-started.md), section B: Power BI from Fabric.
- [Plugs](../../docs/design/plugs.md): every plug and setting.

## Over Microsoft's MCP servers (no plug credential)

When the operator's assistant has Microsoft's Power BI Authoring MCP server (npm
`@microsoft/powerbi-modeling-mcp`, run `--readonly`) or the remote Power BI MCP endpoint, the
`fetch-bi-over-mcp` skill saves the answers in a raw folder and
`python3 -m metrics_agent_powerbi.mcp_capture <raw> <capture> --role <role> --custody customer`
turns them into a capture folder (route `mcp`) the run reads with `local-files`:

| Answer | Becomes |
|---|---|
| `database_operations` TMDL export (the folder it wrote) | `definitions/<model>/definition/*.tmdl`, read as a fetched model is |
| `dax_query_operations` Execute, or the remote Execute Query (executeQueries shape), marked with the widget it is about | an oracle file, raw values |
| Get Semantic Model Schema, Get Report Metadata (remote) | not mapped: not a definition format the reader reads |

Neither server gives the report, so the capture is the model alone and records the gap
`report_definition` (and `displayed_values` for DAX answers); S0 prints both. Not run live:
the Authoring server needs its EULA accepted by the operator (the assistant never accepts it),
and the test tenant answers the remote endpoint with `FeatureNotAvailable` (the tenant setting
is off). The mapping is tested offline (`tests/test_mcp_route.py`); with the tenant's own
getDefinition TMDL and executeQueries answers in those shapes, the capture gave the same model
facts and SQL as this plug, and the SQL returned the DAX answers.

## Access limits are named, never a stop

What the fetch could not read is handed to the run as named blockers (`access.py`): a token
for another API or tenant (`sign_in_failed`), an item with no role (`no_access_to_item`), a
Viewer's refused definition (`definition_needs_edit_rights`, the `.pbix` read instead where it
downloads), a refused export (`download_refused`), a report's model in another workspace
(`bound_model_unreachable`), a composite model's unreachable upstream
(`upstream_model_unavailable`), row-level security roles (`row_level_security`) and, when a
definition was refused, no admin scanner (`catalog_api_unavailable`). An extractor that read
nothing (exit 2 or 3) raises them as `AccessBlocked`, and the run goes on with its other
inputs. They land in the run's `access_report.json` with what to ask for and from whom; the
whole matrix is in [customer access](../../docs/guides/customer-access.md).
