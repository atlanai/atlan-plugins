# Changelog

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
