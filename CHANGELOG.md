# Changelog

## atlan-analytics 0.2.0

- One `atlan-analytics` skill with modes replaces the separate build, connect, eval and improve
  skills, in the layout Metrics Studio (an internal repository) published on 2026-10-30
  (metric service #143 and #145). Run them as `atlan-analytics build`, `eval`, `improve`, `connect`,
  `metrics` and `propose`. Each mode's procedure is under `skills/atlan-analytics/references/`,
  and the helper and its locked publishing environment are under `skills/atlan-analytics/scripts/`.
- The text is metric service's latest, which adds the version-append write for a reviewed metric
  change (`registry__append_artifact_version`) and moves the warehouse procedure to
  `references/connect.md`. `atlan-analytics-ask` and the source-of-truth ranking stay.
- `/atlan-analytics:atlan-analytics metrics`, the prompt Metrics Studio sends, resolves to the
  skill. The `atlan-analytics` command is gone: a command and a skill with one name shadow each
  other, and the registry retired the same wrapper.
- This repository is now the source of these files. metric service will serve a pinned copy of them.

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
  Studio (metric service) pins this repository to serve the same files over MCP.
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
