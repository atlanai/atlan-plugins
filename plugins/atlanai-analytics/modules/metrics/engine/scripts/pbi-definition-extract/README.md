> **Status: VERIFIED against a live Fabric tenant with a person's own sign-in.** A
> device-code sign-in (`--auth device`, the default public client, the 0600 token cache)
> fetched a report and its semantic model through `getDefinition` with `--with-model`.
> Service-principal sign-in, `--auth token` and following a composite model's chain are
> tested offline only. If it behaves differently on your tenant, please open an issue.

# pbi-definition-extract

> The script now lives in the package at `packages/metrics-agent-powerbi/src/metrics_agent_powerbi/pbi_definition_extract.py`
> (still standard library only: copy that one file to run it anywhere). The command below
> keeps working, and a `.env` here is still read. Inside a run, the `powerbi-fabric` plug
> calls it for you: with a service principal when its three variables are set, else with
> `PBI_ACCESS_TOKEN`, else with your own device-code sign-in at a terminal.

Pulls Power BI **report (PBIR)** and **semantic model (TMDL)** definitions out of a
tenant through the Fabric REST API, and writes two things per item: the decoded
files exactly as the API returns them, and an `envelope.json` describing the
response with the payloads stripped.

Standard library only. Python 3.11+. No `pip install`, no dependencies.

---

## Quick start

```bash
# one report, by portal URL -- paste the address bar
python3 pbi_definition_extract.py \
    "https://app.powerbi.com/groups/<wsId>/reports/<reportId>/pagename" \
    --out ./extract

# the same report plus the semantic model it binds to
python3 pbi_definition_extract.py "<report url>" --with-model --out ./extract

# an entire workspace, every report and model in it
python3 pbi_definition_extract.py --workspace <wsId> --out ./extract

# see what would be fetched, without fetching
python3 pbi_definition_extract.py --workspace <wsId> --list-only
```

The first run opens a device-code sign-in in the browser. The token is cached
for about an hour (mode 0600, under `~/.cache/pbi_definition_extract/`), so
back-to-back runs do not re-prompt.

---

## Targets

Targets can be mixed freely on one command line, and any of these forms work:

| Form | Example | Meaning |
|---|---|---|
| report URL | `https://app.powerbi.com/groups/<ws>/reports/<id>/page` | that report |
| dataset URL | `https://app.powerbi.com/groups/<ws>/datasets/<id>` | that semantic model |
| workspace URL | `https://app.powerbi.com/groups/<ws>/list` | everything in it |
| bare id + `--workspace` | `<itemId> --workspace <wsId>` | that item, kind resolved automatically |
| bare id alone | `<wsId>` | everything in that workspace |
| explicit kind | `report:<id>` or `model:<id>` | when you want to skip resolution |
| flags | `--report <id>` / `--semantic-model <id>` | same, repeatable |
| a file | `--targets-file targets.txt` | one target per line, `#` comments allowed |

A bare item id is resolved against the workspace's item list, so the tool works
out whether it is a report or a semantic model and picks up its display name.

`--with-model` reads each extracted report's `definition.pbir` and also pulls the
semantic model named in its connection string. Models already requested are not
fetched twice.

A **composite model** can read tables from another semantic model by DirectQuery
(`AnalysisServices.Database("powerbi://…/myorg/<workspace>", "<model>")` in its
definition), and that model can be composite too. With `--with-model` the tool follows
the chain: each upstream model is found by its workspace and model name and fetched,
then its own upstream models, to any depth. A model already fetched is never fetched
again, so a chain that loops ends. A link this identity cannot see is recorded in
`errors.json` as `UpstreamNotFound` and the run exits 1. A model fetched this way
carries `workspaceName` in its `envelope.json`.

---

## Routes: the richest one your rights allow

You do not say what kind of credential you hold. After sign-in the tool probes what the
identity can reach, without reading any content (the workspaces' item lists, and whether the
Power BI admin API answers), then takes the richest route item by item:

1. **getDefinition**: report and model definitions. Needs Contributor or higher on the
   workspace.
2. **export**: a report the definition API refused is downloaded as its `.pbix` (a paginated
   report as its `.rdl`) into its report directory. Needs build or download rights, and a
   report that allows download.
3. **scanner**: when the admin API answers, the admin scanner's metadata for every workspace
   fetched from is saved as `scans/scan-<id>.json`.

What none of them could read is recorded with the reason in `manifest.json` (`unavailable`),
and the run continues with what it got. To see what an identity can reach without fetching:

```bash
python3 pbi_definition_extract.py --workspace <wsId> --probe
```

---

## Output

```
out/
  manifest.json                        run coverage
  errors.json                          only when something was refused or failed
  reports/<workspaceId>/<reportId>/
      .platform
      definition.pbir
      definition/pages/pages.json
      definition/pages/<page>/page.json
      definition/pages/<page>/visuals/<visualId>/visual.json
      StaticResources/...
      envelope.json                    ← not an API part; written by this tool
  semanticModels/<workspaceId>/<modelId>/
      .platform
      definition.pbism
      definition/model.tmdl
      definition/tables/<table>.tmdl
      definition/relationships.tmdl
      envelope.json
```

Parts are written at the exact paths the API returns, so the tree is a faithful
reconstruction of the item. Re-extracting an item deletes parts the new
definition no longer contains, so a deleted page or dropped table does not
linger from an earlier run.

### manifest.json

```json
{"extractedAt": "2026-09-11T03:20:28+00:00",
 "parts_written": 36, "complete": true,
 "reports":        {"found": 1, "fetched": 1, "failed": 0, "unsupported": 0},
 "semanticModels": {"found": 1, "fetched": 1, "failed": 0, "unsupported": 0}}
```

It also carries `signIn` (`device`, `spn` or `token`), `capabilities` (what the probe
found), `items` (one entry per item: its `outcome`, the `route` that produced it, and
`refused`, what each route said when one declined) and `unavailable` (every route that was
not available, and why).

`unsupported` is Fabric's permanent verdict for an item and does **not** make a
run incomplete. `failed` (a throttle that exhausted retries, a 5xx, a disk
error) does.

| status | counted as | meaning |
|---|---|---|
| 400 | unsupported | item kind has no definition (paginated reports, push datasets) |
| 401 / 403 | unsupported | no workspace role, or an encrypted sensitivity label |
| 404 | unsupported | item not found, or a Premium-Per-User workspace |
| 429 / 5xx | retried, then failed | transient |

If **every** item is refused, the run exits **3** with
`PERMISSION_DENIED_DEFINITION_API` rather than reporting a cheerful empty
result -- an estate-wide permissions problem and an empty estate otherwise look
identical.

### envelope.json

```json
{"workspaceId": "...", "itemId": "...", "itemKind": "reports",
 "displayName": "Account Research — AI Readiness",
 "retrievedAt": "2026-09-11T03:20:28+00:00",
 "partCount": 30,
 "partsWritten": 30,
 "definition": {
   "format": "PBIR",
   "parts": [{"path": "definition/pages/overview/page.json"},
             {"path": "definition/pages/overview/visuals/07a7d248-.../visual.json"}]}}
```

This is the getDefinition response with `payload` and `payloadType` removed.
What survives is the only part of the response that is **not** recoverable from
the extracted files:

* **`format`** -- `PBIR` for reports, `TMDL` for semantic models. The API states
  it outright; consumers that instead probe the file layout to guess the format
  have silently dropped PBIR-format reports.
* **the part-path list** -- an index of every page and visual. The report's whole
  structure is readable from it without decoding a byte.
* **`partsWritten`** -- how many of the listed parts were verified ON DISK after
  the save: each file is re-read and compared with the decoded payload. It is a
  different claim from `partCount` (what the response listed), matching the
  envelope Atlan's Power BI crawler writes. The saved envelope is also read back and checked. If
  either check fails, the item is reported as failed and the run's manifest is
  not `complete`.

Because it holds no content -- no connection strings, no data values, no DAX --
`envelope.json` is safe to attach to a ticket or paste into a thread. The
extracted files are not.

---

## Authentication

**Interactive (default).** `--auth device` signs in through the browser using a
first-party Microsoft public client that is pre-consented in most tenants. No
app registration. The device code expires ~15 minutes after it is printed, so
start the command when you are at the keyboard.

**Unattended.** `--auth spn` reads `PBI_TENANT_ID`, `PBI_CLIENT_ID` and
`PBI_CLIENT_SECRET` from the environment or a local `.env`. Copy `env.template`
to `.env` and fill it in; that file also carries the one-time Azure setup steps.

**Existing token.** `--auth token` with `PBI_ACCESS_TOKEN` set.

The scope requested is `https://analysis.windows.net/powerbi/api/.default`, which
the Fabric endpoints here accept, so one sign-in covers everything. Override with
`--scope` if your tenant needs the Fabric audience instead.

Access: **Contributor** on the workspace for the definitions; with less, a report
whose download is allowed still comes as its `.pbix` (see Routes). A service
principal additionally needs the tenant setting *Service principals can use Fabric
APIs*.

---

## Rate limits

Fabric's definition endpoints share a **200 calls/min per-identity** quota on a
fixed 60-second window. Each item costs about three calls (start the operation,
poll, read the result), so roughly 60 items/minute is the ceiling regardless of
concurrency.

Calls are paced at 180/min by default and an adaptive limiter halves in-flight
work on a 429, easing back up afterwards. Tune with `--calls-per-minute`
(`0` disables pacing) and `--concurrency`.

---

## Handling the output

Extracted files carry live connection strings and can contain data values inside
report metadata. Treat an output directory as customer-confidential: keep it out
of version control, and get customer consent before extracting from a customer
tenant. `envelope.json` is the artifact to share when someone needs to see the
shape without the contents.

---

## Exit codes

| code | meaning |
|---|---|
| 0 | everything fetched (refusals may still be recorded as `unsupported`) |
| 1 | at least one item failed, or the run was interrupted |
| 2 | authentication problem |
| 3 | every discovered item was refused -- a permissions verdict |
