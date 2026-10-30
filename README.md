# Atlan plugin

The Atlan plugin connects your coding agent to the **Atlan registry**, your organization's
catalogue of skills, agents, knowledge, MCP servers and agent runs. It adds Atlan's remote MCP
server, signed in once with OAuth, and skills that teach the agent to find what your
organization already has, use a registry skill in the current project, reach every MCP server
your organization registered, and publish skills, agents and outputs back.

One repository installs in Claude Code, OpenAI Codex, Cursor, and GitHub Copilot or VS Code
through the [Agent Plugins](https://agent-plugins.org) format.

## Install

You need an account in an Atlan organization. Sign-in happens in your browser; there are no
API keys to copy.

| Tool | Install |
|---|---|
| Claude chat and Cowork | In Claude, **Customize**, **Plugins**, **Add**, **Add marketplace**, enter `atlanai/atlan-plugin`, add **Atlan**. Then **Connect** on the plugin's **Connectors** tab. On Team and Enterprise plans an Owner adds the connector (`https://api.atlan.com/mcp`) for the organization first. |
| Claude Code | `/plugin install atlan --marketplace atlanai/atlan-plugin`, or `/plugin marketplace add atlanai/atlan-plugin` then `/plugin install atlan@atlan-plugins`. Then `/mcp`, select `plugin:atlan:atlan`, **Authenticate**. |
| OpenAI Codex | `codex plugin marketplace add atlanai/atlan-plugin`, then `codex plugin add atlan@atlan-plugins`. |
| Cursor | Install **Atlan** from the Cursor Marketplace, or import `atlanai/atlan-plugin` as a team marketplace. Connect `atlan` under Settings, Tools & MCP. |
| VS Code, GitHub Copilot | Run **Install Plugin From Source** with `https://github.com/atlanai/atlan-plugin`. |
| Skills only | `npx skills add atlanai/atlan-plugin`, then add the MCP server below. |
| MCP server only | `https://api.atlan.com/mcp` (Streamable HTTP, OAuth). For example `claude mcp add --transport http atlan https://api.atlan.com/mcp`. |

Per-tool details and sign-in help:
[`skills/atlan-get-started/references/install-and-sign-in.md`](skills/atlan-get-started/references/install-and-sign-in.md).

Then ask your agent something like *"Connect to Atlan and show me which skills my organization
has"*. The `atlan-get-started` skill checks the connection and routes from there.

## What's inside

**MCP server `atlan`** at `https://api.atlan.com/mcp`: Atlan's MCP gateway in search mode. Four
tools (`mcp__search_tools`, `mcp__describe_tools`, `mcp__call_tool`, `mcp__connect_server`) reach
the registry's own tools, the tools of every Atlan app, and every MCP server your organization
registered, under one sign-in with every call permission-checked and audited.

**Skills written for this plugin**

| Skill | Use it to |
|---|---|
| `atlan-get-started` | Check the connection, learn how calls work, and route to the right skill |
| `atlan-find-artifacts` | Search and browse skills, agents, knowledge, MCP servers, files and outputs |
| `atlan-use-skill` | Use a registry skill now, or install it with aireg or into a skills folder, whichever the surface allows |
| `atlan-use-mcp-servers` | Call Slack, Linear or any registered server through Atlan, or register a new one |
| `atlan-continue` | Catch up on an artifact handed over from the Atlan dashboard: its files and the session that made it |
| `atlan-troubleshoot` | Diagnose sign-in errors, refusals, conflicts, rate limits and failed uploads |

**Skills from the registry's own apps**, carried verbatim at pinned revisions (see
[`upstream.json`](upstream.json)): `atlan-upload-content`, `atlan-read-content`,
`atlan-author-skills`, `atlan-link-skill`, `atlan-author-agents`, `atlan-link-agent` and
`atlan-record-outputs`. The gateway also serves these, and recipes for other kinds, as MCP
prompts.

**Commands** (Claude Code and Cursor): `get-started`, `use-skill` and `continue`, the short
forms the Atlan dashboard's Open in Claude buttons send (`/atlan:continue out_…`,
`/atlan:use-skill skill_…`), plus the registry's `atlan-new-skill`, `atlan-update-skill`,
`atlan-link-skill`, `atlan-new-agent`, `atlan-link-agent`, `atlan-record-output` and
`atlan-upload`. In Claude Code they run as `/atlan:<command>`. In Claude chat, the same buttons
send a sentence that names the skill, such as *Use the Atlan continue skill on artifact out_…*.

**A website skill index.** `make site` writes `/.well-known/agent-skills/index.json` and one
archive per skill, so atlan.com can serve the skills to
`npx skills add https://atlan.com --skill atlan-get-started`.

No hooks, no local binaries, no telemetry. Everything runs through the MCP server you sign in to.

## How it relates to the registry's own Claude Code plugin

The registry also serves a Claude Code marketplace at `https://api.atlan.com/public/marketplace.git`
with a plugin named `atlanai`, which the Atlan desktop app installs. It registers the same `atlan`
MCP server. Install one or the other: with both, every Atlan tool appears twice. This plugin is
the one that also works in Codex, Cursor and Copilot, and the one listed in plugin directories.

## Privacy and security

The plugin itself stores and sends nothing. Your agent talks to Atlan's MCP server under your
own sign-in, and Atlan records each call in a session you can review. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/). Report a vulnerability through Atlan's
[responsible disclosure program](https://atlan.com/responsible-disclosure-program/).

## Contributing

Every manifest is generated from [`plugin.toml`](plugin.toml), and the registry's recipes are
synced, never edited here. [AGENTS.md](AGENTS.md) has the layout, the checks and the release steps.
Run `make check` before opening a pull request.

## License

[Apache-2.0](LICENSE).
