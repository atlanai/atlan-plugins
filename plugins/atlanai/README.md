# AI Registry

Connects your coding agent to the Atlan registry: your organization's skills, agents,
knowledge, MCP servers and agent runs. It adds Atlan's MCP server, with one OAuth sign-in, and
skills that teach the agent to find what the organization already has, use it in the current
project, and publish new work back.

## Install

```
npx plugins add atlanai/atlan-plugins
```

That installs it into every coding agent on this machine. For Claude Code alone:

```
claude plugin install atlanai --marketplace atlanai/atlan-plugins
```

Then run `/mcp`, pick `plugin:atlanai:atlan` and choose **Authenticate**. For Claude chat, Codex,
Cursor and VS Code, see the [repository README](../../README.md#install).

The first thing to type: *"Connect me to Atlan."*

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
| `atlan-share` | Share a result with your team through Atlan, and post the hand-off to Slack |

The registry's own apps write the last six. This plugin carries them unchanged, and the
gateway also serves them as MCP resources.

## Commands

Claude Code and Cursor run these as `/atlanai:<command>`: `get-started`, `use-skill <skill_…>`
and `continue <output_…>`. Each loads the skill of the same task (`atlan-get-started`,
`atlan-use-skill`, `atlan-continue`), and those three skills hide their own slash entries so
the menu lists each task once. The other skills are invoked by name
(`/atlanai:atlan-author-skills`), or simply by asking.

## Alongside other Atlan plugins

- The registry's own marketplace, `https://api.atlan.com/public/marketplace.git`, serves this
  same plugin under the same name, with the same `atlan` server. A machine that added it by
  hand, or kept it from an older Atlan desktop app, lists the skills twice: keep one of the two.
  The desktop app itself installs no plugin; its `aireg` CLI installs each workspace as its own
  plugin, named after the workspace.
- The `atlan` plugin in Anthropic's directory (atlanhq/agent-toolkit) is Atlan's data catalog,
  on a different server. It can stay installed beside this one.

## Privacy

The plugin stores nothing on your machine and has no binaries. In Claude Code it runs one hook,
the publish helper: when the agent publishes a skill with `files_from`, it reads that one folder
in your working tree (never a link, a credential file or folder, or anything outside the tree)
and sends its files only in that same call to Atlan. It says it is on with one line beside the
conversation's first request. Other tools run no code from it.
Requests to Atlan carry your sign-in and one header, `X-Atlan-Client: atlan-plugin/<version>`,
which tells Atlan the session came from this plugin. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).
