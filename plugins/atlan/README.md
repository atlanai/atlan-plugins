# Atlan

Connects your coding agent to the Atlan registry: your organization's skills, agents,
knowledge, MCP servers and agent runs. It adds Atlan's MCP server, with one OAuth sign-in, and
skills that teach the agent to find what the organization already has, use it in the current
project, and publish new work back.

## Install

```
/plugin install atlan --marketplace atlanai/atlan-plugins
```

Then run `/mcp`, pick `plugin:atlan:atlan` and choose **Authenticate**. For Claude chat, Codex,
Cursor and VS Code, see the [repository README](../../README.md#install).

The first thing to ask: *"Connect to Atlan and show me which skills my organization has."*

## MCP server

`atlan`, at `https://api.atlan.com/mcp` (Streamable HTTP, OAuth). This is Atlan's MCP gateway in
search mode. Three tools (`mcp__search_tools`, `mcp__describe_tools` and `mcp__call_tool`)
reach the registry's own tools, every Atlan app's tools, and every MCP
server your organization registered. Atlan checks the permissions on each call and records each
call in a session you can review.

## Skills

| Skill | Use it to |
|---|---|
| `atlan-get-started` | Check the connection, learn how calls work, and find the right skill |
| `atlan-find-artifacts` | Search skills, agents, knowledge, MCP servers, files and outputs |
| `atlan-use-skill` | Run a registry skill now, or install it in this project |
| `atlan-use-mcp-servers` | Call Slack, Linear or any other server registered in Atlan, or register a new one |
| `atlan-continue` | Pick up an artifact handed over from the Atlan dashboard |
| `atlan-troubleshoot` | Diagnose sign-in errors, refusals, conflicts, rate limits and failed uploads |
| `atlan-content` | Upload, read and version files in the registry, including a skill's files |
| `atlan-author-skills` | Publish a skill, or a new version of one |
| `atlan-author-agents` | Register an agent or harness, or link a repository's agent and its skills |
| `atlan-author-knowledge` | Create, read or edit a knowledge bundle |
| `atlan-connect-repo` | Register a repository, or publish its skills and agents to Atlan from GitHub CI |
| `atlan-record-outputs` | Record what a run produced as an Atlan output |

The registry's own apps write the last six. This plugin carries them unchanged, and the
gateway also serves them as MCP resources.

## Commands

Claude Code and Cursor run these as `/atlan:<command>`. The Atlan dashboard's **Open in Claude**
buttons send them: `get-started`, `use-skill <skill_…>` and `continue <output_…>`. The
registry's authoring recipes are skills, invoked by name (`/atlan:atlan-author-skills`).

## Alongside Atlan's desktop app

The Atlan desktop app installs a plugin named `atlanai`, from
`https://api.atlan.com/public/marketplace.git`, that registers the same `atlan` server. Keep one
of the two. With both installed, every Atlan tool appears twice.

## Privacy

The plugin stores nothing and runs nothing on your machine: it has no hooks and no binaries.
Requests to Atlan carry your sign-in and one header, `X-Atlan-Client: atlan-plugin/<version>`,
which tells Atlan the session came from this plugin. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).
