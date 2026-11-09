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
Cursor and VS Code, see the [repository README](https://github.com/atlanai/atlan-plugins#install).

The first thing to type: *"Connect me to Atlan."*

## MCP server

`atlan`, at `https://api.atlan.com/mcp` (Streamable HTTP, OAuth). This is Atlan's MCP gateway in
search mode. A few helpers (`mcp__search_tools`, `mcp__describe_tools`, the runners
`mcp__call_read_tool`, `mcp__call_write_tool` and `mcp__call_tool`, and the batch runner
`mcp__call_read_tools`) reach the registry's own tools, every Atlan app's tools, and every MCP
server your organization registered. Atlan checks the permissions on each call and records each
call in a session you can review.

## Skills

| Skill | Use it to |
|---|---|
| `atlan-get-started` | Check the connection, learn how calls work, and find the right skill |
| `atlan-find-artifacts` | Search skills, agents, knowledge, MCP servers, files and outputs |
| `atlan-use-skill` | Run a registry skill now, or install it in this project |
| `atlan-use-mcp-servers` | Call Slack, Linear or any other server registered in Atlan, or register a new one |
| `atlan-manage-artifacts` | Archive, move, deprecate, roll back or approve an artifact, showing what depends on it first, and see what waits for review |
| `atlan-continue` | Pick up an artifact handed over from the Atlan dashboard |
| `atlan-troubleshoot` | Diagnose sign-in errors, refusals, conflicts, rate limits and failed uploads |
| `atlan-content` | Upload, read and version files in the registry, including a skill's files |
| `atlan-author-skills` | Publish a skill, or a new version of one |
| `atlan-author-agents` | Register an agent or harness, or link a repository's agent and its skills |
| `atlan-author-knowledge` | Create, read or edit a knowledge bundle |
| `atlan-connect-repo` | Register a repository, or publish its skills and agents to Atlan from GitHub CI |
| `atlan-share` | Share, save or record a deliverable as an Atlan output, and post the hand-off to Slack |

The five from `atlan-content` to `atlan-connect-repo`, and `atlan-share`'s recording of a run's
output, began as the registry's own apps' recipes, and the gateway also serves them as MCP resources.

## Commands

Claude Code and Cursor run these as `/atlanai:<command>`: `get-started`, `use-skill <skill_…>`,
`continue <output_…>` and `share`. Each loads the skill of the same task (`atlan-get-started`,
`atlan-use-skill`, `atlan-continue`, `atlan-share`). Those four skills hide their own slash
entries, and the commands hide themselves from the model, so the menu and the model's skill list
each show every task once. `feedback [what happened]` drafts feedback for Atlan from the
conversation with `atlan-troubleshoot`, shows it, and leaves sending it to you. The other skills are invoked by name
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

The plugin stores nothing on your machine and has no binaries. In Claude Code, Codex and Cursor
it runs one hook, the publish helper (in Codex and Cursor a Python script, once you trust the
plugin's hooks): when the agent publishes a skill with `files_from`, it reads that one folder in
your working tree (never a link, a credential file or folder, or anything outside the tree) and
sends its files only in that same call to Atlan. It says it is on with one line beside the
conversation's first request. Other tools run no code from it.

The same hook adds two notes for the agent, never for Atlan:

- **Update notice.** At session start it reads the plugin's public manifest on this
  repository's `main` (`raw.githubusercontent.com`, at most 2 seconds, nothing sent but the
  request), and when a later version is released, the agent tells you once how to update.
  `ATLAN_PLUGIN_UPDATE_CHECK=0` turns it off, as does `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
  in Claude Code.
- **Feedback offer** (Claude Code). After an Atlan call fails in a way a retry or sign-in will
  not fix, or after one in fifty turns that used Atlan, the agent may offer once to draft
  feedback. You see the draft and send it yourself from your mail app, to support@atlan.com;
  no hook sends it. `DO_NOT_TRACK=1` or `DISABLE_TELEMETRY=1` turns the offer off.

In Claude Code, the separate [Registry Mods](../atlanai-mods/README.md) plugin (`atlanai-mods`)
changes how this plugin's calls behave: registry reads run without a prompt, writes, archives,
approvals and moves ask first and name what they touch, a `session_id` or `rationale` put inside
`arguments` is moved beside them, temporary gateway errors tell Claude how to retry, and a
fitting registry skill is named beside your request. This plugin works the same without it.
Requests to Atlan carry your sign-in and one header, `X-Atlan-Client: atlan-plugin/<version>`,
which tells Atlan the session came from this plugin. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).
