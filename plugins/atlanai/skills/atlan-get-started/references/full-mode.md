# Full mode

Search mode is what this plugin installs; full mode is one URL apart. Read this only when a
recipe shows calls as `registry__… { … }` or opens with `mcp__initialise`, or when the user's
server URL ends in `?tools=full`.

## Two modes, one URL apart

| | Search mode (default) | Full mode |
|---|---|---|
| URL | `https://api.atlan.com/mcp` | `https://api.atlan.com/mcp?tools=full` |
| `tools/list` | The helpers (search, describe, the runners and the batch read runner), `mcp__connect_server` and the core `registry__` reads | Every tool you can call, possibly hundreds |
| Open a session | `objective` on the first search or runner call | `mcp__initialise { "objective", "client", "rationale" }` |
| Call a tool | its runner, such as `mcp__call_read_tool { "name", "arguments", "session_id", "rationale" }` | `registry__… { …arguments, "session_id", "rationale" }` |

This plugin installs search mode. It keeps your context small however many servers your
organization connects, and it sees exactly what full mode would list for you: the same
permission checks run on every call either way.

In full mode, open the session with `mcp__initialise` before anything else:

```
mcp__initialise {
  "objective": "<one sentence, for a human auditor reading this later>",
  "client": "<the tool you run in, such as claude-code or claude-ai>",
  "rationale": "<why you are opening it>"
}
→ { "session_id": "mcp_session_01J..." }
```

Full mode refuses a call without that `session_id`.

## Translating a recipe written in full mode

The registry's authoring skills (`atlan-author-skills`, `atlan-author-agents`,
`atlan-author-knowledge`, `atlan-content`, `atlan-connect-repo`) and `atlan-share`'s run recording write
calls in full mode, most of them after an opening `mcp__initialise`, like this:

```
registry__get_kind_schema {
  "kind": "skill",
  "session_id": "mcp_session_01J...",
  "rationale": "checking which skill properties are writable before uploading"
}
```

In search mode, the same call goes through its runner, here the read runner:

```
mcp__call_read_tool {
  "name": "registry__get_kind_schema",
  "arguments": { "kind": "skill" },
  "session_id": "mcp_session_01J...",
  "rationale": "checking which skill properties are writable before uploading"
}
```

Move `session_id` and `rationale` out of the arguments and up beside `name`. Where a recipe
starts with `mcp__initialise`, skip that step: search mode does not list it, and your first
search already returned a `session_id`.

