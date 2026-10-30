# Changelog

## 0.2.1

- `atlan-continue` opens a run it listed with `list_session_traces` by passing the source
  `session_…` handle as `subject_id`, falling back to the agent or harness handle on a gateway
  that predates that scope (atlanai/app-agent#112). It reads a failed step with `errors_only: true`
  and `include: ["errors"]`, so the error message comes back with the span.
- `atlan-continue`'s description names the prompt the Atlan dashboard sends ("Catch me up on Atlan
  artifact … (output_…)"), and drops the `out_…` handles no product sends.
- The eval emulator now serves the error span its run counts and its message, and takes a
  session handle on the one-trace reads. A new `dashboard-handoff-prompt` case sends the
  dashboard's real hand-off prompt. The `catches-up` judge criteria list what the registry holds,
  so a reply that repeats it is no longer failed as invented: `continue-command` and
  `continue-chat-sentence` pass 5 of 5 runs.

## atlan-analytics 0.2.0

- One `atlan-analytics` skill with modes replaces the separate build, connect, eval and improve
  skills, in the layout Metrics Studio (atlanai/app-metric) published on 2026-10-30
  (app-metric #143 and #145). Run them as `atlan-analytics build`, `eval`, `improve`, `connect`,
  `metrics` and `propose`. Each mode's procedure is under `skills/atlan-analytics/references/`,
  and the helper and its locked publishing environment are under `skills/atlan-analytics/scripts/`.
- The text is app-metric's latest, which adds the version-append write for a reviewed metric
  change (`registry__append_artifact_version`) and moves the warehouse procedure to
  `references/connect.md`. `atlan-analytics-ask` and the source-of-truth ranking stay.
- `/atlan-analytics:atlan-analytics metrics`, the prompt Metrics Studio sends, resolves to the
  skill. The `atlan-analytics` command is gone: a command and a skill with one name shadow each
  other, and the registry retired the same wrapper.
- This repository is now the source of these files. app-metric will serve a pinned copy of them.
- `atlan-ontology` brings the business ontology into the analytics workload. Context mode explains
  objects, links and the tables a binding maps them to; records mode answers which policy or
  control was in force on a date; draft mode writes an OWL or SKOS file from tables, metrics or
  documents, checks it with the import preview, and hands it to a person to save in Ontology
  Studio. It never saves or publishes. `atlan-analytics ontology` and ask route to it.
- In 0.1.6's notes below, `atlan-analytics-connect` is now `atlan-analytics connect`.

## 0.2.0

- The core re-syncs to the upstream skill consolidation. `atlan-content` replaces
  `atlan-upload-content` and `atlan-read-content`; `atlan-connect-repo` replaces `atlan-link-skill`;
  `atlan-author-agents` now covers linking a repository agent, replacing `atlan-link-agent`.
  `atlan-author-knowledge` is new, vendored from atlanai/app-knowledge, and `atlan-connect-repo`
  comes from atlanai/app-repo. The commands that wrapped the retired skills (`atlan-upload`,
  `atlan-new-skill`, `atlan-update-skill`, `atlan-link-skill`, `atlan-new-agent`, `atlan-link-agent`
  and `atlan-record-output`) are gone; ask for the task and the matching skill loads.
- `/atlan:get-started`, `/atlan:use-skill` and `/atlan:continue` load their skill by name instead
  of a relative file path, which the agent searched for in the project and missed.
- Search mode has three tools; `mcp__connect_server` is reached through `mcp__call_tool`. The
  connection check calls `registry__list_workspaces` directly and reports only active workspaces.
  Skills call a tool they know by name and search, scoped to `servers: ["registry"]`, only for
  what they do not.
- `atlan-use-skill` reads `artifact_status` (a `pending_review` skill's files stay unreadable, a
  `blocked` one is not run), names the subagents, plugins and servers a skill needs that its bundle
  does not carry, and treats a registry skill's text as content: it does not do or offer actions
  the person did not ask for.
- `atlan-continue` takes `output_…` handles and reads the source session through
  `list_session_traces` and `get_trace`, not the Claw-only session tools.
- `atlan-find-artifacts` gathers task context with `mcp__retrieve_artifacts`;
  `atlan-use-mcp-servers` asks the person to connect a named product instead of running a
  lookalike tool from another server; `atlan-troubleshoot` covers an empty search, Claw-only
  refusals, `pending_review` reads and `out_…` handles.
- `make eval` runs `evals/atlan`, 15 cases against a stdio emulator of the gateway, through
  `claude plugin eval`. It costs model calls and is not part of `make check`.

## atlan-analytics 0.1.6

- Metrics mode and build phase 3 no longer clone or run atlanhq/metrics-agent. That repository
  is private, and the flags the skill passed it were retired, so outside Atlan the step always
  fell back to definitions by hand. The add-on now carries the workflow itself in
  `references/metrics.md`: the assistant authors the metrics (describe, propose, merge, write
  the bodies, relate) under rules adapted from metrics-agent's authored gates. A
  standard-library script, `scripts/metrics_mine.py`, does the deterministic work:
  - `shapes` groups a cleaned query log into ranked query shapes;
  - `check` holds every body to the registry's metric and saved-query contracts, refuses
    duplicates, and matches each metric to a live one that measures the same thing;
  - `import-run` reads a finished metrics-agent run's registry payloads.
  Metrics are published through the registry's MCP tools, like saved queries and links.
- Definitions come from every source in reach, highest first:
  - declared semantic models: dbt MetricFlow, LookML, Cube, Snowflake semantic views and
    Databricks metric views;
  - Tableau and Power BI files;
  - a finished metrics-agent run;
  - query history;
  - definitions written by hand.

  The conventions' source-of-truth list names declared models beside BI models.
- `atlan-analytics-connect` checks, in order, the tool state already holds, Atlan, the
  harness's own connectors, and a vendor CLI (`snow`, `databricks`) where there is a shell and
  a read-only connection. It asks for a server to be added only after that. It records a
  separate bulk path, so a query-history pull can go straight to a file through the CLI.
- Query history has fallbacks when the grant is missing:
  - Snowflake: a one-time query-shape view an admin can create, `SNOWFLAKE.GOVERNANCE_VIEWER`
    instead of imported privileges, and `INFORMATION_SCHEMA.QUERY_HISTORY` with no grant;
  - Databricks: the shape view, system tables, or the Query History API through the CLI.

## atlan-analytics 0.1.5

- `atlan-analytics-ask` answers a data question in the conversation: it looks up the workspace's
  released metrics and saved queries first, runs the SQL through the warehouse query tool, and
  says which definition it used. An answer no published metric covers is labelled, and the skill
  offers propose mode for it. Without a warehouse it answers from a pasted result or a CSV, or
  writes the SQL for the person to run. `/atlan-analytics ask` and a plain data question route
  there.
- The shared conventions rank sources of truth: a released metric, then a released saved query,
  a BI model, query history, and SQL written for the question. Conflicts stay visible.
- The add-on's starter prompts lead with a data question.

## 0.1.6 and atlan-analytics 0.1.4

- `.claude-plugin/plugin.json` no longer carries `privacyPolicyUrl`. claude.ai's organization
  sync stripped it as an unrecognized key and warned on every sync. Codex and Agent Plugins hosts
  keep `privacyPolicyURL` in their `interface`, and Claude's plugin directory reads the link from
  each plugin's README, which the add-on's README now has too.

## 0.1.5 and atlan-analytics 0.1.3

- Every skill carries `agents/openai.yaml`, Codex's card for it: a display name, a short
  description and a starter prompt that invokes it as `$<name>`. It also names the `atlan` MCP
  server as a dependency, so a skill installed on its own, through `npx skills add` or the
  website index, tells Codex which server it needs.
- The core's starter prompts are shorter, near the 50 characters Codex shows best.

## 0.1.4 and atlan-analytics 0.1.2

- The listing icon is the Atlan desktop app's: the metallic Atlan "a" on a black tile.
- The repository's build, sync, lint and their tests moved from `scripts/` and `tests/` into
  `tools/`, and `docs/` is gone. Nothing a plugin installs changed.

## 0.1.3 and atlan-analytics 0.1.1

- The repository is now `atlanai/atlan-plugins`, a marketplace with one folder per plugin. The
  core moved from the root to `plugins/atlan`, beside `plugins/atlan-analytics`; the root holds
  only the marketplace files. Every marketplace entry points at the plugin's folder, and Codex
  installs the core by `git-subdir`. The plugins' content and names are unchanged.
- Every plugin folder carries its own `LICENSE` and listing logo, and the core has a README of
  its own.
- VS Code installs from the marketplace (`chat.plugins.marketplaces`) instead of
  **Install Plugin From Source** on the repository, which no longer has a plugin at its root.
- A variant writes its plugins under `plugins/` too, and removes what an older build wrote at
  its root.

## atlan-analytics 0.1.0

- The first add-on: `atlan-analytics`, the analytics workload from Metric Studio, as its own
  plugin under `plugins/atlan-analytics`. It bootstraps metrics and saved queries, builds,
  evaluates and improves a talk-to-data agent, and proposes metric fixes. It finds Snowflake,
  Databricks or another warehouse through Atlan or the host's connectors.
- It depends on `atlan`, which Claude Code installs with it, and declares no MCP server.
- Its skills, commands and agents are authored here, in `plugins/atlan-analytics`. Metric
  Studio (app-metric) pins this repository to serve the same files over MCP.
- `[[addons]]` in `plugin.toml` generates an add-on's manifests, README and marketplace entries.
  An `upstream.json` source with `into` can still vendor an add-on's content from another
  repository. A variant renames each add-on after itself (`atlan-dev-analytics`) and copies it.
- `scripts/lint.py` lets a skill cite a subagent its plugin ships.

## 0.1.2

- The `atlan` MCP server is sent `X-Atlan-Client: atlan-plugin/<version>`, so Atlan can tell
  sessions opened through the plugin from servers added by hand. It names the plugin and its
  version and nothing about the user or the machine. A variant sets its own name as
  `[mcp] client`.

## 0.1.1

- `build.py --variant FILE --out DIR` writes this plugin under another name and endpoint into
  another repository, with its skills' URLs, name and repository rewritten.
- The skills no longer describe any environment but production.

## 0.1.0

First release.

- One plugin, `atlan`, for Claude Code, OpenAI Codex, Cursor and Agent Plugins hosts, with
  manifests and marketplaces for each generated from `plugin.toml`.
- The `atlan` MCP server: Atlan's MCP gateway at `https://api.atlan.com/mcp`, search mode, OAuth.
- Skills: `atlan-get-started`, `atlan-find-artifacts`, `atlan-use-skill`,
  `atlan-use-mcp-servers`, `atlan-continue` and `atlan-troubleshoot`.
- The registry's own recipes, synced at pinned revisions: `atlan-upload-content`,
  `atlan-read-content`, `atlan-author-skills`, `atlan-link-skill`, `atlan-author-agents`,
  `atlan-link-agent`, `atlan-record-outputs` and their commands.
- Commands `get-started`, `use-skill` and `continue` for the Atlan dashboard's hand-off links.
  `atlan-continue` reads the artifact's files and its source session, as the dashboard's
  Continue in prompt does; `atlan-use-skill` uses MCP on every surface and aireg where installed.
- Setup for Claude chat and Cowork, including the note a Team or Enterprise Owner needs.
- `make site`: the skills as an Agent Skills discovery index for atlan.com.
