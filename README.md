# Atlan plugins

Plugins that connect coding agents to the Atlan registry, where an organization keeps its
skills, agents, knowledge and MCP servers. The marketplace works in Claude, Claude Code, OpenAI
Codex, Cursor and [Agent Plugins](https://agent-plugins.org) hosts such as VS Code with GitHub
Copilot.

| Plugin | What it does |
|---|---|
| [`atlan`](plugins/atlan) | Atlan's MCP server, plus skills to find, use and publish what is in the registry. Every other plugin here needs it. |
| [`atlan-analytics`](plugins/atlan-analytics) | Builds governed metrics and saved SQL queries from your warehouse's query history, then builds, evaluates and improves a talk-to-data agent on them. Works with Snowflake, Databricks or another warehouse. |

## Install

You need an account in an Atlan organization. You sign in through your browser, so there is no
API key to copy.

**Claude Code**

```
/plugin marketplace add atlanai/atlan-plugins
/plugin install atlan@atlan-plugins
/plugin install atlan-analytics@atlan-plugins    # optional; installs atlan too
```

Then run `/mcp`, pick `plugin:atlan:atlan` and choose **Authenticate**.

**Claude chat and Cowork.** In Claude, go to **Customize**, **Plugins**, **Add**, **Add
marketplace** and enter `atlanai/atlan-plugins`. Add **Atlan**, then click **Connect** on its
**Connectors** tab. On Team and Enterprise plans, an Owner first adds the connector
`https://api.atlan.com/mcp` for the organization.

**OpenAI Codex**

```
codex plugin marketplace add atlanai/atlan-plugins
codex plugin add atlan@atlan-plugins
codex plugin add atlan-analytics@atlan-plugins   # optional
```

**Cursor.** Install **Atlan** from the Cursor Marketplace, or import `atlanai/atlan-plugins` as
a team marketplace. Then connect the `atlan` server under **Settings**, **Tools & MCP**.

**VS Code with GitHub Copilot.** Add `atlanai/atlan-plugins` to the `chat.plugins.marketplaces`
setting, then install **Atlan** from the Chat Plugins section of the Extensions view.

**Skills only.** `npx skills add atlanai/atlan-plugins` copies the skills without the MCP
server. Add `--skill <name>` to pick one.

**MCP server only.** Point any MCP client at `https://api.atlan.com/mcp` (Streamable HTTP with
OAuth). For example:

```
claude mcp add --transport http atlan https://api.atlan.com/mcp
```

Only Claude Code installs a plugin's dependencies. In other hosts, install `atlan` before any
other plugin from this marketplace.
[Install and sign-in help](plugins/atlan/skills/atlan-get-started/references/install-and-sign-in.md)
covers each host in detail.

Once you are signed in, ask: *"Connect to Atlan and show me which skills my organization has."*

## Repository layout

```
.claude-plugin/marketplace.json   the marketplace, for Claude and Claude Code
.agents/plugins/marketplace.json  the same marketplace, for Codex
.cursor-plugin/marketplace.json   the same marketplace, for Cursor
plugins/<name>/                   one folder per plugin, with its manifests, skills, commands and agents
plugin.toml                       the source of every manifest
upstream.json                     pins for the skills taken from the registry's apps
scripts/, tests/                  the build, sync and lint, and their tests
```

Every plugin is a folder under `plugins/`, the layout Anthropic's
[official marketplace](https://github.com/anthropics/claude-plugins-official) uses. A workload
built on the registry ships as its own plugin, named `atlan-<workload>`. It uses `atlan`'s
connection and declares no MCP server of its own, so no Atlan tool appears twice.

## Privacy and security

The plugins store nothing and run nothing on your machine: they have no hooks and no binaries.
Your agent talks to Atlan under your own sign-in, and Atlan records each call in a session you
can review. See Atlan's [privacy notice](https://atlan.com/privacy-policy/). Report
vulnerabilities through the
[responsible disclosure program](https://atlan.com/responsible-disclosure-program/).

## Contributing

Change `plugin.toml`, not the generated JSON, and run `make check` before opening a pull
request. [AGENTS.md](AGENTS.md) covers the layout rules, checks, variants and releases.

## License

[Apache-2.0](LICENSE)
