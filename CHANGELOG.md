# Changelog

## 0.1.4 and atlan-analytics 0.1.2

- The listing icon is the Atlan desktop app's: the metallic Atlan "a" on a black tile.

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
