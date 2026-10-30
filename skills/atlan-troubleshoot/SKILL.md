---
name: atlan-troubleshoot
description: Diagnose a failing, refused, missing or duplicated Atlan tool call - sign-in errors, refused sessions, generic 400s, 409 conflicts, rate limits, uploads that fail, files that will not read, servers stuck behind connect, or tools that appear twice. Use when any `atlan` MCP call errors or returns something unexpected.
license: Apache-2.0
---

# Troubleshooting Atlan

Read the error before acting. Most refusals say exactly what to change, and repeating a
write to see whether it works again is how duplicates get made.

## Connection and sign-in

| Symptom | Cause and fix |
|---|---|
| No `atlan` tools at all | The server is not installed or not started. Follow `atlan-get-started/references/install-and-sign-in.md` for the user's tool. |
| `401`, "authentication required", "needs login" | The user has not signed in, or the sign-in expired. They re-authenticate from their tool (`/mcp` in Claude Code, `codex mcp login` in Codex, Tools & MCP in Cursor). Never ask for a token. |
| Signed in but no organization, or sign-in refused | The account has no Atlan organization to join. They need an invitation from their organization's Atlan admin, or support@atlan.com. |
| Every Atlan tool appears twice | Both this plugin and Atlan's own `atlanai` Claude Code plugin are installed. Keep one. |
| Artifacts the user expects are missing | They may be signed in to the other environment: `api.atlan.com` is production, `api.dev.example` is dev. Or the artifact is in a workspace they cannot read, which looks the same as not existing. |

## Calling tools

| Symptom | Cause and fix |
|---|---|
| `tool is unavailable; refresh tools/list` | The name is wrong for this connection's mode. In search mode run every tool through `mcp__call_tool`; on a `?tools=full` URL the four `mcp__*_tools` meta-tools do not exist. |
| A call is refused for a missing session | Send `objective` on the first `mcp__search_tools` or `mcp__call_tool`, then pass the returned `session_id` on every call. An id you did not receive in this conversation is refused. |
| A call is refused for a missing rationale | Add `rationale`, a first-person sentence on why you are making this call, beside `name`. |
| A recipe names `extension__file__request_upload`, `extension__file__get_content` or `request_upload` | Older names. The registry's tools are `registry__request_file_upload` and `registry__get_file_content`. |
| A recipe starts with `mcp__initialise` and search mode does not list it | Skip it: your first search already opened the session. |
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
| `not found` on a file you just saw listed | The file is withdrawn, or the version you asked for is not readable. Pass the `file_version_ordinal` the manifest gave you. |

## MCP servers behind the gateway

| Symptom | Cause and fix |
|---|---|
| A server appears under `connect` | The user has not connected their account for it. Ask, then `mcp__connect_server`. |
| A server's tools never appear | It is disabled (`is_enabled: false`), `unreachable`, or quarantined after repeated failures. An editor of the server can test and re-enable it in the Atlan dashboard's MCP studio. |

When none of these fit, report the tool name, the arguments you sent (without credentials or
upload URLs), and the exact error text to the user.
