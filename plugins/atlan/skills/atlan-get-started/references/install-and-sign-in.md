# Installing the plugin and signing in

Give the user the steps for the tool they are in, and wait for them to confirm. Signing in is
always theirs to do in the browser: never ask for a token, a password or a code in chat.

## Before anything: an Atlan account

Sign-in needs an account in an Atlan organization: an existing membership, an open
invitation, or a company domain the organization lets people join. Atlan signs people in
with an emailed code, Google, or the company's single sign-on. Someone with none of these
should ask their organization's Atlan admin for an invitation, or contact
support@atlan.com. The web dashboard is https://app.atlan.com.

## Install, per tool

**Claude chat and Cowork** (claude.ai, the Claude desktop and mobile apps)

One install covers chat, Cowork and Claude Code on the same account. In Claude, open
**Customize**, then **Plugins**, **Add**, **Add marketplace**; enter `atlanai/atlan-plugins` and add
**Atlan**. Adding the plugin does not sign anyone in: open the Atlan plugin's **Connectors** tab
and click **Connect**, which opens Atlan's sign-in page.

On a Team or Enterprise plan, an Owner adds connectors for the organization, so **Connect** may
be missing until they do. Give the user this note to send their Claude Owner:

> Please add Atlan as a connector for our Claude organization: Settings, Connectors, add a
> custom connector with the URL `https://api.atlan.com/mcp`. Each of us then signs in with our
> own Atlan account. Owners can also make the Atlan plugin installed by default.

**Claude Code** (terminal, desktop, IDE extensions)

```
/plugin install atlan --marketplace atlanai/atlan-plugins
```

Claude Code before 2.1.275 needs the two-step form:

```
/plugin marketplace add atlanai/atlan-plugins
/plugin install atlan@atlan-plugins
```

Then run `/mcp`, pick `plugin:atlan:atlan`, and choose **Authenticate**. A browser window
opens on Atlan's sign-in page.

**OpenAI Codex** (CLI and app)

```
codex plugin marketplace add atlanai/atlan-plugins
codex plugin add atlan@atlan-plugins
```

The marketplace asks Codex to sign in at install time. If it did not, find the server with
`codex mcp list` and run `codex mcp login <name>`.

**Cursor**

Install **Atlan** from the Cursor Marketplace, or have a team admin import
`atlanai/atlan-plugins` as a team marketplace. Then open Cursor Settings, **Tools & MCP**, and
connect the `atlan` server to start the browser sign-in.

**VS Code with GitHub Copilot, and other Agent Plugins hosts**

Add `atlanai/atlan-plugins` to the `chat.plugins.marketplaces` setting, then install **Atlan**
from the Chat Plugins section of the Extensions view. Start the `atlan` MCP server when
prompted and sign in in the browser.

**Skills only, any tool that reads Agent Skills**

```
npx skills add atlanai/atlan-plugins --skill atlan-get-started
```

This copies skills but not the MCP server. Without `--skill` it offers every skill in the
marketplace, add-ons included. Add the server as below.

## The MCP server on its own

The skills are optional; the server is what does the work. Any MCP client that speaks
Streamable HTTP with OAuth can use it:

| Client | Configuration |
|---|---|
| Claude Code | `claude mcp add --transport http atlan https://api.atlan.com/mcp` |
| Claude desktop and claude.ai | Settings, Connectors, add a custom connector with URL `https://api.atlan.com/mcp` |
| Codex | In `~/.codex/config.toml`: `[mcp_servers.atlan]` then `url = "https://api.atlan.com/mcp"`; then `codex mcp login atlan` |
| Cursor | In `~/.cursor/mcp.json`: `{ "mcpServers": { "atlan": { "url": "https://api.atlan.com/mcp" } } }` |
| VS Code | In `.vscode/mcp.json`: `{ "servers": { "atlan": { "type": "http", "url": "https://api.atlan.com/mcp" } } }` |

Append `?tools=full` to the URL to list every tool directly instead of the four search-mode
tools (see `calling-tools.md`). Atlan's own setup guide is
https://platform.atlan.com/gateway/mcp/how-tos/connect-a-client, with pages for
[Claude Code](https://platform.atlan.com/clients/claude/how-tos/claude-code) and
[Codex](https://platform.atlan.com/clients/openai/how-tos/codex).

## Already using Atlan's own Claude Code plugin?

The Atlan desktop app and the registry's marketplace (`https://api.atlan.com/public/marketplace.git`)
install a plugin named `atlanai` that registers the same `atlan` server. Keep one of the two:
with both, every Atlan tool appears twice. This plugin is the one that also installs in Codex,
Cursor and Copilot.
