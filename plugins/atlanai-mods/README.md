# Registry Mods

A Claude Code mod for the `atlanai` plugin. Every Atlan registry call goes through one tool,
`mcp__call_tool`, so Claude Code's own permission rules cannot tell a search from an archive. This
mod can:

| What | How |
|---|---|
| Registry reads run without a prompt | Searches, reads and listings are allowed at the permission check. Your deny rules and your organization's limits still apply. |
| Writes ask first | A create, patch, append or relate asks, and the prompt names the write. Turn this off with the `confirm_writes` option. |
| Archive, approve and move always ask | The prompt names the artifact, its version and workspace, and what depends on it (`3 artifacts depend on it: agent ...`). Where nobody can answer, as under `claude -p`, the call is refused. |
| Calls arrive well formed | `session_id`, `rationale` and `objective` placed inside `arguments` are moved beside them. The session's audit session is reused. A call with no rationale is refused with the fix. |
| Connection problems show | The status line says when Atlan needs a sign-in, cannot be reached or is not connected. It shows nothing while the connection works. |
| Atlan calls read as one line | Each Atlan call's row in the transcript says what it did and what came back (`Atlan · searched skills for "churn"`, then `7 found: pii-scrub 1.0.0, ...`) in place of the raw JSON. Ctrl+O shows the full call. |
| The Atlan pane | A pane beside the chat lists the artifacts this session read, created, changed, archived or published, with their version and status, and copies an artifact's id. It opens by itself at the session's first change in Atlan, or with `/atlan pane`. |
| A card for archive, approve and move | While one of these waits for your answer, the pane shows it as a card: the artifact, its version and workspace, each artifact that depends on it, and what the change does to everyone else. |
| A band while a skill publishes | Above the prompt, each skill being published goes from sending to scanning to published (with its version), blocked or unchanged. A toast says when it lands. The band clears at your next request. |
| `/atlan skills [words]` | Opens a picker over your organization's skills. Search by what the skill should do, pick one, and `/atlanai:use-skill <id>` is put in your prompt to send. The search spends no Claude turn. |
| `/atlan` | Checks the connection and prints the fix. Runs at once, with no Claude turn. |
| `/atlan search <words>` | Searches the registry and lists names, versions, status and handles, with no Claude turn. |

Other servers reached through Atlan (Slack, Linear and the rest) follow their tool annotations: a
read-only tool runs without a prompt, a destructive one always asks, and a tool with no
annotations is left to your permission rules.

## Install

Claude Code v2.1.287 or later. It installs `atlanai` with it:

```
claude plugin install atlanai-mods --marketplace atlanai/atlan-plugins
```

In a session, `/plugin install atlanai-mods@atlan`. Mods run in the Claude Code CLI, the Code tab
of the desktop app and the VS Code extension. Under `claude -p`, the Agent SDK and cloud sessions
the hooks run but nothing is drawn: use `/atlan` for the connection's state. The band above the
prompt is drawn in the terminal and the desktop app; the picker's search field is not drawn on
mobile.

### Organizations that allow only managed mods

A plugin installed from a GitHub marketplace counts as the user's own. Where managed settings set
`allowManagedModsOnly` or `allowManagedHooksOnly`, it does not load. To run it there, deploy a
copy of this repository to the same path on every machine, writable only by administrators, and
add to managed settings:

```json
{
  "extraKnownMarketplaces": {
    "atlan": { "source": { "source": "directory", "path": "/opt/atlan/atlan-plugins" } }
  },
  "enabledPlugins": { "atlanai@atlan": true, "atlanai-mods@atlan": true }
}
```

See Claude Code's [Manage mods for your organization](https://code.claude.com/docs/en/plugins/mods/admin).

## What it reaches

It runs inside Claude Code with your permissions. `claude plugin validate plugins/atlanai-mods`
lists every call it makes. It:

- reads the input of the three Atlan gateway tools, and their results, to move rails and learn
  the session id and tool annotations;
- calls the `atlan` server you signed in to, as you: `mcp__describe_tools` at session start
  (opens no audit session), `registry__get_artifact` and `registry__get_related_artifacts` before
  an archive, approve or move, and `registry__search_artifacts` for `/atlan search` and the skill
  picker. Each call carries a rationale naming this mod, so it is recorded under your audit
  session;
- reads each Atlan call's input and result again to draw its transcript row, the pane and the
  publish band. What it shows stays in the session's memory and is gone when the session ends;
- puts text in your prompt only when you pick a skill, and copies an id only when you press
  Copy id;
- keeps the tool annotations in Claude Code's plugin store on this machine, and the session id in
  the session's memory.

It reads no files, starts no processes, makes no other network requests and sends nothing to a
model. See Atlan's [privacy notice](https://atlan.com/privacy-policy/).

## Develop

```
claude plugin validate plugins/atlanai-mods   # what it hooks and calls
claude plugin test plugins/atlanai-mods       # the tests in tests/, no session needed
claude --plugin-dir plugins/atlanai-mods      # load it for one session; it reloads on save
```

`hooks/registry.ts` holds the rules and `hooks/view.ts` what is drawn, as plain functions;
`hooks/register.tsx` wires them to Claude Code's events and draws them. `tools/build.py` writes the manifest from `[[mods]]` in `plugin.toml`.
