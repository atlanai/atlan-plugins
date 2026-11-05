---
name: atlan-troubleshoot
description: Diagnose a failing, refused or duplicated Atlan tool call, such as a sign-in error, a refused session, a 400, 409 or rate limit, a failed upload, a file that will not read or a server stuck behind connect. Use when any `atlan` MCP call errors or returns something unexpected.
license: Apache-2.0
---

# Troubleshooting Atlan

Read the error before acting, and key on its `code` (in the result's JSON, or in a JSON-RPC
error's `data`) rather than its wording, which can change. Most refusals say exactly what to change, and repeating a
write to see whether it works again is how duplicates get made. Reply with the one step that
fixes it, without apology.

## Connection and sign-in

| Symptom | Cause and fix |
|---|---|
| No `atlan` tools at all | The server is not installed or not started. Follow `atlan-get-started/references/install-and-sign-in.md` for the user's tool. |
| `401`, "authentication required", "needs login" | The user has not signed in, or the sign-in expired. They re-authenticate from their tool (`/mcp` in Claude Code, `codex mcp login atlan` in Codex, Tools & MCP in Cursor). Never ask for a token. |
| Signed in but no organization, or sign-in refused | The account has no Atlan organization to join. They need an invitation from their organization's Atlan admin, or support@atlan.com. |
| Every Atlan tool or skill appears twice | The `atlanai` plugin is installed from two marketplaces (this one and the registry's own `api.atlan.com/public/marketplace.git`, added by hand or left by an older Atlan desktop app), or the `atlan` server was also added by hand (`claude mcp add`). Keep one. |
| The `atlan` tools are there but are not `mcp__search_tools`, `mcp__describe_tools` and the `mcp__call_…tool` runners | A different plugin's server answers: the data-catalog plugin named `atlan` (Anthropic's directory, atlanhq/agent-toolkit) points at `https://mcp.atlan.com/mcp`. Both can stay installed under their different names; this plugin's tools are the `plugin:atlanai:atlan` ones (`mcp__plugin_atlanai_atlan__…` in Claude Code). If only the other plugin's tools show, install `atlanai`. |
| Artifacts the user expects are missing | They may be signed in to a different Atlan environment from the one that holds it: an artifact read with `verbose: true` carries a `uri` (`api.atlan.com` or `api.atlan.dev`) that says which one answered. Or the artifact is in a workspace they cannot read, which looks the same as not existing. |

## Calling tools

| Symptom | Cause and fix |
|---|---|
| `"code": "unknown_tool"` | No registry tool has that name. Registry tools are `registry__…`, app tools `extension__<app>__…`; find the name with `mcp__search_tools` or `mcp__describe_tools` rather than guessing a variant. |
| `"code": "invalid_handle"` | The handle is not `<kind>_<identifier>`. Pass the `id` a search or read returned, whole, never a name or a bare identifier. |
| `"code": "unknown_kind"` | The kind named, or the handle's prefix, is not registered for this account: a typo (`skills`, `out_…`), or an app the organization does not have. `registry__get_artifact_counts` with `group_by: "kind"` lists the kinds it holds. |
| `"code": "not_found"` | No artifact by that handle, or one the user cannot see; the two read alike on purpose. Do not retry with variants. Search by name (`registry__search_artifacts`), and if it is still missing, see "Artifacts the user expects are missing". |
| `"code": "invalid_arguments"` | The arguments are malformed or contradict each other, and the message names which. Fix those and send once more. |
| `tool is unavailable; refresh tools/list` | The name is wrong for this connection's mode. In search mode run every tool through a runner (`mcp__call_read_tool`, `mcp__call_write_tool` or `mcp__call_tool`); on a `?tools=full` URL the five search-mode tools do not exist. |
| "Not run: this tool is … Retry the same call … through `mcp__call_…`" (`reason: runner_below_tier`) | The runner is below the tool's tier: the read runner runs only reads, the write runner nothing destructive. Send the same `name`, `arguments` and `session_id` through the runner its `run_with` names. A write still needs the user's yes first. |
| A call is refused for a missing session | Send `objective` on the first search or runner call, then pass the returned `session_id` on every call. An id you did not receive in this conversation is refused. |
| "session not found; call `mcp__search_tools` …" on a session that worked earlier | The session expired, or the conversation was compacted or resumed elsewhere. Open a new one once (`objective`, no `session_id`) and use its id from then on. If a write was in flight, read it back before doing it again. |
| `502`, `503`, `504`, "service unavailable", "bad gateway", or error data with `retryable: true` or a `code` of `downstream_unavailable`, `downstream_timeout` or `downstream_rate_limited` | Atlan or a server behind it was briefly unavailable. Repeat a read after `retry_after_seconds`, or 2, 4, then 8 seconds, 3 tries in all; then tell the user Atlan is not answering. Never repeat a write: read it back, or repeat a create with its `idempotency_key`. `may_have_run: true` means the downstream tool may already have run. A skill create often commits before this error: resend it unchanged only if each skill carried an `idempotency_key`; otherwise search the workspace for the skill by name and read its head before sending it again. |
| A search or describe result is marked `partial` | A server did not answer in time. Repeat once; if still partial, say the list may be incomplete. Do not conclude a tool is missing. |
| A call is refused for a missing rationale | Add `rationale`, a first-person sentence on why you are making this call, beside `name`. |
| A recipe names `request_upload` or `get_content`, bare or under the old `file` extension prefix | Older names. The registry's tools are `registry__request_file_upload` and `registry__get_file_content`. |
| A recipe starts with `mcp__initialise` and search mode does not list it | Skip it: your first search already opened the session. |
| `mcp__search_tools` finds nothing, or not the registry tool you expected | Search ranks every connected server. Call a tool you know by name (`registry__…`) directly, or pass `servers: ["registry"]`. An empty search does not mean the tool is missing. |
| `mcp__connect_server` is not a tool | It runs through `mcp__call_write_tool` (or `mcp__call_tool`) with `name: "mcp__connect_server"` and `arguments: { "server": … }`. |
| `403` "Only a project's Claw … can use the Claw session tools" | `extension__agent__session_read` and `session_list` serve a project's Claw, not a person's agent. Read a session through its traces instead (`atlan-continue`, step 2). |
| JSON-RPC `-32003`, `rate_limited` | The organization's call budget is spent for the moment. Wait, then retry reads. Retry a write only by reusing its `idempotency_key` with identical arguments. |

## Writes

| Symptom | Cause and fix |
|---|---|
| A generic `400` or `422` `validation_failed` on create or update that names no field | A property is missing, unknown or out of range for the kind. Call `registry__get_kind_schema` and fix it. |
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
| A skill has no `metadata.files` | Its `artifact_status` is `blocked`: it failed a security scan and its content is withdrawn. `registry__get_security_findings` says why: each finding's rule, file, lines and fix. |
| A skill's `SKILL.md` answers `not found`, or lists `size_bytes: 0` | Its `artifact_status` is `pending_review`: that version waits for a reviewer and cannot be read until it is approved. |
| A handle such as `out_…` answers `not found` | Outputs are `output_…`. Ask for the handle as the dashboard shows it rather than guessing another. |
| `not found` on a file you just saw listed | The file is withdrawn, or the version you asked for is not readable. Pass the `file_version_ordinal` the manifest gave you. |

## MCP servers behind the gateway

| Symptom | Cause and fix |
|---|---|
| A server appears under `connect` | The user has not connected their account for it. Ask, then `mcp__connect_server`. |
| A search names the server under `unavailable` (`"reason": "disabled"`) | It is registered but disabled, so its tools cannot be found or called. Tell the user an admin can enable it in Atlan's MCP servers, and do not use another product's tool in its place. |
| A server's tools never appear | It is disabled (`is_enabled: false`), `unreachable`, or quarantined after repeated failures. An editor of the server can test and re-enable it in the Atlan dashboard's MCP studio. |

When none of these fit, report the tool name, the arguments you sent (without credentials or
upload URLs), and the exact error text to the user.
