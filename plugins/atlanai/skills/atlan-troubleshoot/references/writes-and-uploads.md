# Writes, uploads and content

Open this when a create, update, relate, upload or file read was refused or came back
incomplete. Key on the error's `code` and status, as in `SKILL.md`.

## Writes

| Symptom | Cause and fix |
|---|---|
| A `422` `validation_failed` on create or update that names a `field` | Fix that property (`allowed_values` lists the set when given) and send the call once more. Call `registry__get_kind_schema` only when the type or limit it takes is unclear. |
| A `400` or `422` on create or update that names no field (`validation_failed` or `unprocessable`, from an older gateway) | A property is missing, unknown, the wrong type or out of range for the kind. Call `registry__get_kind_schema` and fix it. |
| `409` creating a `file` | That `path` is taken in the workspace. Use a unique path, for example dated. |
| `409` with `existing_artifact_id` from `extension__skill__create` | The bundle is byte-identical to the current version. Nothing to do. |
| `409` on a skill name | A skill with that name has a different source identity. Reuse the existing skill's `workspace_id`, `source_type` and `source_id` to add a version, or pick another name. |
| A batch call succeeded but items are missing | Batches report per slot. Read each slot and retry only the failed ones. |
| `create_artifact_linked` created the artifact but not the edge | Fix the edge with `registry__relate_artifact` on the returned handle. Do not call it again. |
| `publish_output` answered `output: null` with `output_error` | The file was saved and the output was not. Fix what `output_error` names and send the same call again: it pins the saved file. |
| `publish_output` refuses with "The request is not valid." and names no field, or a bare `409` on a new version | Nothing was written. Send the call once more without the optional `file.media_type` and `idempotency_key`. Refused again: record through the older route in `skills/atlan-record-outputs/references/record.md`, with the same source key. |
| `publish_output` is not a tool on this connection | The gateway is older than the tool. Record with separate calls, as `skills/atlan-record-outputs/references/record.md` says under its older route. |
| A write is refused with "You do not have permission" and no reason | The user's role cannot create that kind in that workspace (an agent create needs more than a personal workspace on some organizations). Say which kind and workspace, and ask whether to use another workspace or an admin. Do not retry. |
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
