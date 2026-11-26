# Atlan Analytics: Claude directory review notes

What the Claude plugin directory's validator reported for `plugins/atlanai-analytics`, what each
finding is, and the explanation to give the reviewer. The first run was on main at `8adf2e1`
(analytics 0.9.90) on 2026-11-25: 5 policy holds and 22 warnings. Re-validate this branch before
submitting, and update the counts here.

The submission continues the existing **Analytics** draft in the Atlan organization: the portal
refuses a second submission for the same repository and path.

## Fixed in this branch

| Finding | Was | Now |
|---|---|---|
| Files or downloads the validator couldn't inspect | 5 box-office files over 256 KiB, `tickets.csv` over the 1 MiB read limit, the yard `.pbit` (binary), 532 files | The data ships in parts under 240 KB and the `.pbit` as its text entries; the demo helper joins and zips them when a demo opens. 39 unused engine shims removed, and 14 contributor docs and example plugs moved to the repository's `docs/metrics-engine/`: 498 files. `bicompile.py` (639 KB) is the one file left over the limit |
| Name may be confused: display name "Analytics" resembles publisher "KB Analytics" (hold) and plugin "analytics-skills" (warning) | display name `Analytics` | display name `Atlan Analytics` |
| Front matter value may be misread (`analytics-demo/SKILL.md`) | unquoted description holding `: ` | quoted |

`make check` now fails on any file over 256 KiB (but images, fonts and the named `bicompile.py`),
any binary file, or more than 512 files in a plugin folder.

The portal reported "645 files" for 532 tracked files and 115 folders, so it may count folders.
This branch is at 498 files and 110 folders (608 together).
If the re-validation still says the tree was not fully inspected, the count is files and folders
together, and the plugin needs about 110 fewer entries.

## Policy holds to explain

**Name may be confused with an existing listing: author `Atlan` resembles connectors `atlar` and
`atlas`.** Atlan is the publisher's company name (atlan.com). The plugin makes
no claim to represent Atlar or Atlas, and its connector, Atlan Trident, is Atlan's own product.

**Publisher name may be confused with another: GitHub owner `atlanai` resembles `atlar` and
`atlas`.** `atlanai` is Atlan's GitHub organization for its AI products, which already publishes
the Atlan Trident core plugin from this repository. Renaming it would break existing installs.

(The same two explanations were given for the Atlan Trident core submission.)

## Warnings to explain

### Uses a credential from the user's machine (8)

Each one is the plugin connecting to the person's own systems, with credentials they set for that
purpose. None is sent anywhere else.

| File | What it reads, and where it goes |
|---|---|
| `metrics-agent-powerbi/.../plug.py`, `pbi_definition_extract.py` | `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET` or `PBI_ACCESS_TOKEN`, set by the person to read their own Power BI reports. Sent only to Microsoft's sign-in and Power BI or Fabric APIs. The `localhost` address is the redirect for an interactive Microsoft sign-in |
| `metrics-agent-tableau/.../plug.py`, `rest.py` | `TABLEAU_SERVER_URL`, `TABLEAU_SITE` and a personal access token or connected-app values, to read the person's own workbooks. Sent only to the Tableau server they name; `rest.py` refuses internal and metadata hosts |
| `metrics_agent/plugs/local_files.py` | `METRICS_AGENT_WAREHOUSE_DSN`: the path of a local DuckDB or SQLite file, opened read-only. Nothing is sent over the network, and remote reads are refused |
| `analytics-build/scripts/side_page.py` | Serves a progress page on `127.0.0.1` only, behind a random key, and opens it in the person's browser. The browser process inherits the environment, as any launched program does. Nothing is sent off the machine |
| `metrics-engine/docs/guides/customer-access.md` | Documentation of the access routes above |
| `plugin.json` (across files: `analytics_case.py`, `accuracy-check/references/run.md`) | The accuracy check asks each question to a fresh agent: `analytics_case.py` starts the person's own Claude Code or Codex CLI with their environment, so that CLI uses its own sign-in. The plugin never reads, copies or sends the credential |

Later version: ask for these values through `userConfig` with `sensitive: true`, as the checklist
recommends, instead of reading them from the environment.

### Contains a download-and-run command (10)

The validator flagged 10 files (2 skills, 3 references, 5 Python modules: `accuracy-check/SKILL.md`,
`eval-generation/SKILL.md`, `accuracy-check/references/run.md`,
`analytics-build/references/project-conventions.md`, `analytics-demo/references/accuracy.md`,
`dashboard_runtime/trident/dax_syntax.py`, `dashboard_runtime/trident/snowflake_cli.py`,
`analytics-build/scripts/source_inventory.py`, `eval_builder/sources/powerbi/dax.py`,
`eval_builder/sources/tableau/calc.py`). The trigger is not documented. These files share no
installer, download, pipe-to-shell or launcher pattern that the plugin's other files lack.
Searched on 2026-11-26: none of the 10 holds `curl`, `wget`, `uvx`, `npx`, `pip install`, a pipe to a
shell, `shell=True`, `eval(` or `exec(`, and two of them (`powerbi/dax.py`, `tableau/calc.py`) mention no
download, install or command at all. The plugin's two real installers are elsewhere and were not
flagged: `metrics/driver.py` (pip, `--only-binary=:all:` with `constraints.txt` pins) and
`dashboard_runtime/scripts/setup_pbixray.py` (`uv pip sync --require-hashes --only-binary`).
What each flagged file does, to paste as the explanation (none downloads anything or runs code from
the network):

| File | What it is |
|---|---|
| `accuracy-check/SKILL.md`, `accuracy-check/references/run.md` | Instructions that run the plugin's own bundled helper, `python3 "<check-dir>/scripts/check.py"` |
| `eval-generation/SKILL.md` | Instructions that run the bundled eval builder from the skill's own folder |
| `analytics-build/references/project-conventions.md` | Defines the short names for bundled helpers (`python3 "<skill-dir>/scripts/…"`) |
| `analytics-demo/references/accuracy.md` | Defines the short names for the demo's bundled helpers (`accuracy.py`, `edit_metric.py`) |
| `dashboard_runtime/trident/dax_syntax.py` | Parses DAX text into types for a source inventory; no SQL, no I/O beyond its input |
| `dashboard_runtime/trident/snowflake_cli.py` | Calls the person's already-installed Snowflake CLI with bound values; never installs it, never uses a shell, reads no credentials |
| `analytics-build/scripts/source_inventory.py` | Detects what kind of source files a folder holds; parses nothing |
| `eval_builder/sources/powerbi/dax.py` | Translates DAX expressions to SQL text, or refuses |
| `eval_builder/sources/tableau/calc.py` | Translates Tableau calculations to SQL text, or refuses |

**Open:** copy the "View on GitHub" line links from the re-validation report, then reword the
matched text or explain each one here. The plugin's local helpers are standard-library Python, or the
pinned metrics runtime installed once with the person's consent; nothing is downloaded and run at
install time.

## Also in the report (information only)

- *agent-plugins manifest detected*: the root `plugin.json` serves other hosts; Claude reads
  `.claude-plugin/plugin.json`.
- *Already in your submissions*: continue the existing draft.

## Still to do before submitting

1. Re-validate this branch in the existing draft and update the counts above.
2. Split `bicompile.py`, then remove its exception from `tools/lint.py`.
3. Resolve or explain the download-and-run findings line by line.
4. Data handling and compliance answers, and the review contact, confirmed by their owners (open
   questions on CTX-552).

Atlan is a Verified organization (Directory → Help and appeals, 2026-11-25), so the download-and-run
and credential findings are warnings to explain, not grounds to refuse.
