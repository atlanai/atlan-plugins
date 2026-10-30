# Changelog

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
