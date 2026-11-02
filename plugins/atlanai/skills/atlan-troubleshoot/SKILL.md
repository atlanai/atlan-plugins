---
name: atlan-troubleshoot
description: Diagnose a failing, refused, missing or duplicated Atlan tool call - sign-in errors, refused sessions, generic 400s, 409 conflicts, rate limits, uploads that fail, files that will not read, servers stuck behind connect, or tools that appear twice. Use when any `atlan` MCP call errors or returns something unexpected.
license: Apache-2.0
---

# Troubleshooting Atlan

Read the error before acting. Most refusals say exactly what to change, and repeating a
write to see whether it works again is how duplicates get made. Reply with the one step that
fixes it, without apology.

## Connection and sign-in

| Symptom | Cause and fix |
|---|---|
| No `atlan` tools at all | The server is not installed or not started. Follow `atlan-get-started/references/install-and-sign-in.md` for the user's tool. |
| `401`, "authentication required", "needs login" | The user has not signed in, or the sign-in expired. They re-authenticate from their tool (`/mcp` in Claude Code, `codex mcp login atlan` in Codex, Tools & MCP in Cursor). Never ask for a token. |
| Signed in but no organization, or sign-in refused | The account has no Atlan organization to join. They need an invitation from their organization's Atlan admin, or support@atlan.com. |
| Every Atlan tool or skill appears twice | The `atlanai` plugin is installed from two marketplaces (this one and the registry's own `api.atlan.com/public/marketplace.git`, added by hand or left by an older Atlan desktop app), or the `atlan` server was also added by hand (`claude mcp add`). Keep one. |
| The `atlan` tools are there but are not `mcp__search_tools`, `mcp__describe_tools` and `mcp__call_tool` | A different plugin's server answers: the data-catalog plugin named `atlan` (Anthropic's directory, atlanhq/agent-toolkit) points at `https://mcp.atlan.com/mcp`. Both can stay installed under their different names; this plugin's tools are the `plugin:atlanai:atlan` ones (`mcp__plugin_atlanai_atlan__…` in Claude Code). If only the other plugin's tools show, install `atlanai`. |
| Artifacts the user expects are missing | They may be signed in to a different Atlan environment from the one that holds it: an artifact's `uri` (`api.atlan.com` or `api.atlan.dev`) says which one answered. Or the artifact is in a workspace they cannot read, which looks the same as not existing. |

## Calling tools

| Symptom | Cause and fix |
|---|---|
| `tool is unavailable; refresh tools/list` | The name is wrong for this connection's mode. In search mode run every tool through `mcp__call_tool`; on a `?tools=full` URL the three `mcp__*_tools` meta-tools do not exist. |
| A call is refused for a missing session | Send `objective` on the first `mcp__search_tools` or `mcp__call_tool`, then pass the returned `session_id` on every call. An id you did not receive in this conversation is refused. |
| "session not found; call `mcp__search_tools` …" on a session that worked earlier | The session expired, or the conversation was compacted or resumed elsewhere. Open a new one once (`objective`, no `session_id`) and use its id from then on. If a write was in flight, read it back before doing it again. |
| `502`, `503`, `504`, "service unavailable", "bad gateway", or `retryable: true` in the error's data | Atlan or a server behind it was briefly unavailable. Repeat a read after `retry_after_seconds`, or 2, 4, then 8 seconds, 3 tries in all; then tell the user Atlan is not answering. Never repeat a write: read it back, or repeat a create with its `idempotency_key`. `may_have_run: true` means the downstream tool may already have run. |
| A search or describe result is marked `partial` | A server did not answer in time. Repeat once; if still partial, say the list may be incomplete. Do not conclude a tool is missing. |
| A call is refused for a missing rationale | Add `rationale`, a first-person sentence on why you are making this call, beside `name`. |
| A recipe names `request_upload` or `get_content`, bare or under the old `file` extension prefix | Older names. The registry's tools are `registry__request_file_upload` and `registry__get_file_content`. |
| A recipe starts with `mcp__initialise` and search mode does not list it | Skip it: your first search already opened the session. |
| `mcp__search_tools` finds nothing, or not the registry tool you expected | Search ranks every connected server. Call a tool you know by name (`registry__…`) directly, or pass `servers: ["registry"]`. An empty search does not mean the tool is missing. |
| `mcp__connect_server` is not a tool | It runs through `mcp__call_tool` with `name: "mcp__connect_server"` and `arguments: { "server": … }`. |
| `403` "Only a project's Claw … can use the Claw session tools" | `extension__agent__session_read` and `session_list` serve a project's Claw, not a person's agent. Read a session through its traces instead (`atlan-continue`, step 2). |
| JSON-RPC `-32003`, `rate_limited` | The organization's call budget is spent for the moment. Wait, then retry reads. Retry a write only by reusing its `idempotency_key` with identical arguments. |

## Writes

| Symptom | Cause and fix |
|---|---|
| A generic `400` on create that names no field | A required property of the kind is missing. Call `registry__get_kind_schema` and add it. |
| `409` creating a `file` | That `path` is taken in the workspace. Use a unique path, for example dated. |
| `409` with `existing_artifact_id` from `extension__skill__create` | The bundle is byte-identical to the current version. Nothing to do. |
| `409` on a skill name | A skill with that name has a different source identity. Reuse the existing skill's `workspace_id`, `source_type` and `source_id` to add a version, or pick another name. |
| A batch call succeeded but items are missing | Batches report per slot. Read each slot and retry only the failed ones. |
| `create_artifact_linked` created the artifact but not the edge | Fix the edge with `registry__relate_artifact` on the returned handle. Do not call it again. |
| A relationship type is refused | Edges are declared by the source kind. An agent-to-skill edge is created from the agent (`uses_skill`), an output-to-skill edge from the output (`used_skill`). |

## Uploads and content

| Symptom | Cause and fix |
|---|---|
| `PUT` to `upload_url` returns `401` | `is_presigned` was `false`: that URL needs the user's bearer token, which an MCP sign-in does not hand to you. Tell the user the upload needs a direct API token. Never ask for one in chat. |
| `PUT` returns `403` or the URL expired | Upload URLs expire (`expires_in_seconds`). Reserve a new one with `registry__request_file_upload`. |
| `registry__get_file_content` refuses over the byte ceiling or as not UTF-8 | The tool returns text up to 256 KiB only. Say which file you could not read; do not guess its content. |
| A skill has no `metadata.files` | Its `artifact_status` is `blocked`: it failed a security scan and its content is withdrawn. |
| A skill's `SKILL.md` answers `not found`, or lists `size_bytes: 0` | Its `artifact_status` is `pending_review`: that version waits for a reviewer and cannot be read until it is approved. |
| A handle such as `out_…` answers `not found` | Outputs are `output_…`. Ask for the handle as the dashboard shows it rather than guessing another. |
| `not found` on a file you just saw listed | The file is withdrawn, or the version you asked for is not readable. Pass the `file_version_ordinal` the manifest gave you. |

## MCP servers behind the gateway

| Symptom | Cause and fix |
|---|---|
| A server appears under `connect` | The user has not connected their account for it. Ask, then `mcp__connect_server`. |
| A server's tools never appear | It is disabled (`is_enabled: false`), `unreachable`, or quarantined after repeated failures. An editor of the server can test and re-enable it in the Atlan dashboard's MCP studio. |

When none of these fit, report the tool name, the arguments you sent (without credentials or
upload URLs), and the exact error text to the user.
