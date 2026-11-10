# Registry Mods

A Claude Code mod for the `atlanai` plugin. Most Atlan registry calls go through one of three
runners (`mcp__call_read_tool`, `mcp__call_write_tool` or `mcp__call_tool`), so Claude Code's own
permission rules see the runner, not the tool: they cannot tell a create from an archive run
through `mcp__call_tool`. Reads also arrive as a batch (`mcp__call_read_tools`, up to 8 in one
call) and through the six registry reads the gateway lists by name (`registry__search_artifacts`,
`registry__get_artifact`, `registry__list_workspaces`, `registry__get_artifact_counts`,
`registry__get_related_artifacts`, `registry__get_file_content`); the mod handles all three forms. The mod knows the gateway by its tool names, so a gateway added by hand under another name
(`claude mcp add atlan-prod …`) is covered too. This mod can:

| What | How |
|---|---|
| Registry reads run without a prompt | Searches, reads and listings are allowed at the permission check, through a runner, in a batch or called by name. A batch is allowed only when every call in it is a registry read; one holding another server's tool is left to your rules. Your deny rules and your organization's limits still apply. |
| Writes ask first | A create, patch, append or relate asks, and the prompt names the write. Turn this off with the `confirm_writes` option. |
| Archive, approve and move always ask | The prompt names the artifact, its version and workspace, and what depends on it (`3 artifacts depend on it: agent ...`), read within 8 seconds. Where nobody can answer, as under `claude -p`, the call is refused. A name in another case, with spaces around it, wrapped in a nested runner or placed in a batch asks the same way. |
| A release and its roll-back always ask | They ask like archive, approve and move, with a card in the pane, whether or not the mod has read the gateway's annotations yet, and are refused where nobody can answer. A release's prompt names the artifact, what depends on it, and says the version becomes the one its channel serves. A roll-back names only a release (`rel_…`), so the mod keeps the releases the session listed with `registry__list_releases`: the prompt then names the artifact, the version and channel, what depends on it and the release the channel goes back to. A release the session never listed is asked about by its id, with what a withdrawal does. |
| Calls arrive well formed | `session_id`, `rationale` and `objective` placed inside `arguments` are moved beside them, for the registry's tools and the apps' (`extension__…`) alike; another server's tool keeps an argument of the same name. The session's audit session is reused, for a batch and a read called by name too, and dropped only when the gateway refuses that session, never because a tool reported something else not found. A session this mod opens for its own checks stays its own. A runner or batch call with no rationale is refused with the fix. |
| Outages are absorbed | When Atlan answers 502, 503 or 504, or a server behind it is briefly unavailable, Claude is told to repeat a read after the gateway's wait (2 seconds when it names none), at most 3 tries, and to read back before repeating a write; a lost skill publish is left to `atlanai`'s publish helper and the publish skill's read-back. The mod's own lookups retry once; before a permission prompt the retry waits at most 3 seconds, and is skipped when it would not finish within 8. |
| Connection problems show | The status line says when Atlan needs a sign-in, cannot be reached, is not connected or is answering with temporary errors (`Atlan: degraded, retrying reads`). A failed check is diagnosed from its error, not reported as a timeout. It shows nothing while the connection works. |
| A skipped guard shows | If another plugin answers the permission check for an Atlan write before this mod can, the write did not get its confirmation: the status line says `Atlan: write guard skipped` and `/atlan` says how many. `/atlan` also counts reads that another plugin answered first, which is why they may ask. See [when another plugin answers first](#when-another-plugin-answers-first). |
| Atlan calls read as one line | Each Atlan call's row in the transcript says what it did and what came back (`Atlan · searched skills for "churn"`, then `7 found: pii-scrub 1.0.0, ...`) in place of the raw JSON. A batch reads as `ran 2 reads: ...`, then how many answered. Ctrl+O shows the full call. |
| The Atlan pane | A pane beside the chat lists the artifacts this session read, created, changed, archived or published, with their version and status, and copies an artifact's id. It opens by itself at the session's first change in Atlan, or with `/atlan pane`. |
| A card for archive, approve, move, release and roll-back | While one of these waits for your answer, the pane shows it as a card: the artifact, its version and workspace, each artifact that depends on it, and what the change does to everyone else. |
| A band while a skill publishes | Above the prompt, each skill being published goes from sending to scanning to published (with its version), blocked or unchanged. A toast says when it lands. The band clears at your next request. |
| Registry skills come up by themselves | With each request you type, the mod searches your organization's skills (the request's first 300 characters, `kind: "skill"`, `limit: 3`). A skill that is active, released, not labelled a weak match by the gateway and scores at least 0.45 similarity, and is not installed here, is named to Claude beside your request, at most 2 at once and each once a conversation, so Claude can load it with `atlan-use-skill`. Nothing is added when nothing fits, and a search slower than 0.8 seconds, a failed one, or a session not connected to Atlan adds nothing and holds nothing up. One search runs at a time. While Atlan answers with temporary errors, and for 5 minutes after two searches in a row outlast the wait, nothing is searched; a registry call that succeeds ends the pause. Slash commands, requests under 4 words, a request naming a `skill_…` handle and one holding something credential-shaped are not searched. The first time a request would be searched on a machine, the mod says so once instead (what is sent, and the option that turns it off) and searches from the next request; in a session nobody watches, as under `claude -p`, it searches only on a machine that has already been told. `/atlan` says while it is on. Turn this off with the `suggest_skills` option. |
| `/atlan skills [words]` | Opens a picker over your organization's skills. Search by what the skill should do, pick one, and `/atlanai:use-skill <id>` is put in your prompt to send. The search spends no Claude turn. |
| `/atlan` | Checks the connection and prints the fix. Runs at once, with no Claude turn. |
| `/atlan search <words>` | Searches the registry and lists names, versions, status and handles, with no Claude turn. |

Other servers reached through Atlan (Slack, Linear and the rest) are left to your permission
rules, whatever their tool annotations say: a server's own read-only claim is not trusted to skip
a prompt. A tool its server marks destructive always asks, and is refused where nobody can answer.

A call through `mcp__call_read_tool` is treated as a read, because the gateway refuses any tool
through it that is not read-only; one through `mcp__call_write_tool` never counts as destructive,
for the same reason. Archive, approve, move, release and roll-back ask in every case, whichever
runner carries them.

An allow rule you set on a runner is not enough to skip a write's prompt while `confirm_writes`
is on: the mod asks anyway. Turn `confirm_writes` off to let your rules decide writes.

### When another plugin answers first

Plugins answer a permission check in order, and a plugin seated above this one can decide the
check without passing it on. Claude Code's default security plugin (`cc-plugin-sec-default`) does
this in some hosts, and the transcript says `atlanai-mods: its tool.check hook was skipped`. Then
none of the rows above about prompts apply: reads ask again, writes and archives run on the other
plugin's verdict, and this mod can only count them afterwards (`/atlan`). Nothing in a mod can
move it above another plugin. Check which plugins answer permission prompts with `/plugin`.

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

- reads the input of the twelve Atlan gateway tools it handles (the three runners, the batch
  runner, `mcp__search_tools`, `mcp__describe_tools` and the six registry reads listed by name),
  and their results, to move rails and learn the session id and tool annotations;
- calls the `atlan` server you signed in to, as you: `mcp__describe_tools` at session start
  (opens no audit session), `registry__get_artifact` and `registry__get_related_artifacts` before
  an archive, approve, move, release or roll-back, and `registry__search_artifacts` for `/atlan search` and the skill
  picker, retried once after a temporary error, and with the first 300 characters of each request
  you type, for the skill hint (no retry; off with `suggest_skills`). Each of these goes through
  `mcp__call_read_tool`, the runner the gateway lets only read, and carries a rationale naming this mod, so
  it is recorded under your audit session, or under a session of the mod's own when none is open;
- reads each Atlan call's input and result again to draw its transcript row, the pane and the
  publish band. What it shows stays in the session's memory and is gone when the session ends;
- puts text in your prompt only when you pick a skill, and copies an id only when you press
  Copy id; adds a line beside a request for Claude only when a registry skill clearly fits it;
- lists the session's commands, to leave out a skill that is already installed;
- keeps the tool annotations, and that the skill hint's notice has been shown, in Claude Code's
  plugin store on this machine, and the session id in the session's memory.

It reads no files, starts no processes, makes no other network requests and sends nothing to a
model. See Atlan's [privacy notice](https://atlan.com/privacy-policy/).

## Develop

```
claude plugin validate plugins/atlanai-mods   # what it hooks and calls
claude plugin test plugins/atlanai-mods       # the tests in tests/, no session needed
claude --plugin-dir plugins/atlanai --plugin-dir plugins/atlanai-mods   # one session; reloads on save
tsc -p plugins/atlanai-mods                   # type-check, once a load has written its types
```

The mod needs the `atlanai` plugin it depends on, so load both folders together.

`hooks/registry.ts` holds the rules and `hooks/view.ts` what is drawn, as plain functions;
`hooks/register.tsx` wires them to Claude Code's events and draws them. `tools/build.py` writes the manifest from `[[mods]]` in `plugin.toml`.
