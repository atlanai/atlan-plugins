# Calling Atlan's tools

The `atlan` server is Atlan's MCP gateway. It fronts three families of tools under one
connection and one sign-in:

| Name shape | Owner | Examples |
|---|---|---|
| `registry__<tool>` | The registry itself | `registry__search_artifacts`, `registry__get_artifact`, `registry__create_artifact`, `registry__list_workspaces` |
| `extension__<app>__<tool>` | An Atlan app installed in the registry | `extension__skill__create`, `extension__repo__register_repo`, `extension__ontology__…` |
| `<server>__<tool>` | An MCP server your organization registered | `slack__send_message`, `linear__save_issue` |

## Two modes, one URL apart

| | Search mode (default) | Full mode |
|---|---|---|
| URL | `https://api.atlan.com/mcp` | `https://api.atlan.com/mcp?tools=full` |
| `tools/list` | Three fixed tools | Every tool you can call, possibly hundreds |
| Open a session | `objective` on the first `mcp__search_tools` or `mcp__call_tool` | `mcp__initialise { "objective", "client", "rationale" }` |
| Call a tool | `mcp__call_tool { "name", "arguments", "session_id", "rationale" }` | `registry__… { …arguments, "session_id", "rationale" }` |

This plugin installs search mode. It keeps your context small however many servers your
organization connects, and it sees exactly what full mode would list for you: the same
permission checks run on every call either way.

## The search-mode loop

```
1. mcp__search_tools {
     "query": "<what you need right now, in one sentence, naming the product>",
     "objective": "<what the whole session is for>"           ← first search only
   }
   → { "session_id": "mcp_session_…", "tools": [ { "name", "description", "inputSchema", … } ] }

2. mcp__call_tool {
     "name": "<exact name from the search>",
     "arguments": { …matching its inputSchema… },
     "session_id": "mcp_session_…",
     "rationale": "<why this call, first person>"
   }
```

- Skip the search when you already know a tool's exact name and schema: every `registry__…`
  tool these skills name is one. If you know only its name,
  `mcp__describe_tools { "names": ["registry__get_artifact"], "session_id": "…" }` returns the
  schema. A search that comes back empty does not mean the tool is missing: call or describe it
  by name before concluding it does not exist.
- `servers` narrows a search or describe to some servers, named as their tool names start:
  `["registry"]`, `["slack", "linear"]`.
- A server you have not connected yet comes back under `connect`, not as tools. Ask the user,
  then run `mcp__call_tool { "name": "mcp__connect_server", "arguments": { "server": "<as listed>" }, … }`.
  It is not a tool of its own in `tools/list`. See `atlan-use-mcp-servers`.
- A first `mcp__call_tool` with an `objective` and no `session_id` opens a session too; its
  result carries `mcp_session_id`. Reuse that as `session_id`. Do not repeat a side-effecting
  call just to get a receipt.

## Translating a recipe written in full mode

The registry's authoring skills (`atlan-author-skills`, `atlan-author-agents`,
`atlan-author-knowledge`, `atlan-content`, `atlan-record-outputs`, `atlan-connect-repo`) write
calls in full mode, most of them after an opening `mcp__initialise`, like this:

```
registry__get_kind_schema {
  "kind": "skill",
  "session_id": "mcp_session_01J...",
  "rationale": "checking which skill properties are writable before uploading"
}
```

In search mode, the same call is:

```
mcp__call_tool {
  "name": "registry__get_kind_schema",
  "arguments": { "kind": "skill" },
  "session_id": "mcp_session_01J...",
  "rationale": "checking which skill properties are writable before uploading"
}
```

Move `session_id` and `rationale` out of the arguments and up beside `name`. Where a recipe
starts with `mcp__initialise`, skip that step: search mode does not list it, and your first
search already returned a `session_id`.

## When a call fails

Read the error first. These four cases cover what a retry can fix; anything else is a refusal
that a repeat will not change (see `atlan-troubleshoot`).

| What came back | Do |
|---|---|
| `502`, `503`, `504`, "service unavailable", or `retryable: true` | A read: wait `retry_after_seconds` when the error gives it, else 2 seconds, and repeat the same call; then 4, then 8 seconds, 3 tries in all. Then tell the user Atlan is not answering. A write: do not repeat it. Read the artifact back, or repeat a create with the same `idempotency_key` and identical arguments, and retry only what did not happen. `extension__skill__create` takes its key per skill, inside each `skills` item, and an older skill app has none: without one, read the skill back first, as `atlan-author-skills` says under **When the reply is lost**. |
| JSON-RPC `-32003`, `rate_limited` | The organization's budget is spent for the moment: the same rule, waiting longer. |
| "session not found; call `mcp__search_tools` …" | The gateway no longer knows that session (it expired, or the id came from another conversation). Open a new one once: the next `mcp__search_tools` or `mcp__call_tool` with `objective` and no `session_id`. Use the new id from then on, and tell the user only if a write was in flight. |
| A search or describe result marked `partial` | A server did not answer in time, so tools may be missing from the list. Repeat the search once; if it is still partial, say the results may be incomplete rather than concluding a tool does not exist. |

Never retry on a timer forever, and never repeat an archive, move, approve or release, or a
create without an `idempotency_key`: those are writes a lost response does not undo.

## Limits worth knowing

- **Rate limit.** Calls are admitted per organization. A refusal is JSON-RPC error `-32003`
  with `rate_limited`. Wait before retrying a read; never retry a write blindly.
- **Payloads.** `registry__create_artifact` takes at most 64 KiB of inline `content`. Anything
  larger, and every binary such as a skill `.zip`, goes through `registry__request_file_upload`
  (see `atlan-content`). `registry__get_file_content` returns at most 256 KiB of UTF-8.
- **Batches.** `registry__create_artifacts` takes up to 100 items and reports each in its own
  slot. Read the slots, retry only the failed ones.
