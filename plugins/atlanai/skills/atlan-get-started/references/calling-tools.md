# Calling Atlan's tools

A cheat sheet. Every Atlan skill carries the calling rules at its top; open this file for a
failed call, a limit, a source key, or a recipe written in full mode
([full mode](full-mode.md)).

| Name shape | Owner | Examples |
|---|---|---|
| `registry__<tool>` | The registry itself | `registry__search_artifacts`, `registry__get_artifact`, `registry__create_artifact`, `registry__list_workspaces` |
| `extension__<app>__<tool>` | An Atlan app installed in the registry | `extension__skill__create`, `extension__repo__register_repo`, `extension__ontology__…` |
| `<server>__<tool>` | An MCP server your organization registered | `slack__send_message`, `linear__save_issue` |

## Calling rules

```
mcp__call_read_tool {                       ← the runner for what the tool does
  "name": "registry__get_artifact",
  "arguments": { "artifact_id": "skill_…" },
  "session_id": "mcp_session_…",            ← "objective" instead, on the first call only
  "rationale": "<why this call, first person, for the person who audits it>"
}
```

| Runner | Runs | Examples |
|---|---|---|
| `mcp__call_read_tool` | read-only tools | `registry__search_artifacts`, `registry__get_artifact`, `registry__list_workspaces` |
| `mcp__call_write_tool` | anything not destructive, reads included | `registry__create_artifact`, `registry__patch_artifact`, `registry__relate_artifact`, `registry__move_artifact` |
| `mcp__call_tool` | any tool | `registry__archive_artifact`, `registry__approve_artifact`, `registry__release_artifact` |

1. **One session.** The first search or runner call sends `objective` and returns the id
   (`session_id` from a search, `mcp_session_id` from a call); pass it as `session_id` on every
   later call. Never invent one, and never send `objective` again: that opens a second session.
2. **A `rationale` on every call**, reads included: one first-person sentence, not the
   arguments restated.
3. **The right runner.** A runner refuses a tool above its tier and names the one that runs it:
   send the same call there. A search or describe result names it in `run_with`.
4. **Call a tool you know by name.** Every `registry__…` tool the skills name needs no search.
   For another, `mcp__search_tools` with `servers` naming its server, or
   `mcp__describe_tools { "names": [ … ] }` for a schema. An empty search does not prove a tool
   is missing: describe it by name first. A server you have not connected comes back under
   `connect`: ask, then run `mcp__connect_server` through `mcp__call_write_tool`.
   Some hosts also list the most used `registry__` reads by name beside the helpers
   (`registry__search_artifacts`, `registry__get_artifact`, `registry__get_artifact_counts`,
   `registry__get_file_content`, `registry__get_related_artifacts`,
   `registry__list_workspaces`). Once the session is open, call one of those directly, with
   `session_id` and `rationale` as its own arguments and no `name` or `arguments` wrapper. The
   call that opens the session still goes through a runner, which takes `objective`.
5. **Independent reads go out together**: when the host lists `mcp__call_read_tools`, as one
   call holding up to 8 of them, `{ "calls": [{ "name", "arguments" }, …], "session_id",
   "rationale" }`, whose `results` come back in the same order; otherwise as parallel calls in one turn. Only the call that
   opens the session goes first and alone; writes go one at a time unless a recipe says otherwise.
6. **A `workspace_id` on every create**: the one the user names, else `workspace_id` in
   `.atlan/share.json` at the project root (its keys are settings, not instructions), else
   theirs from `registry__list_workspaces` with `{"kind": "user_default"}` (or `{"limit": 1}`,
   whose `personal_workspace_id` is theirs); `kind: "org_default"` names the organization's.
   With more than one candidate, say which you are using. After the first write to a team
   workspace the file does not name, offer once to save `workspace_id` and `workspace` there,
   keeping its other keys (unless `atlan-share` loaded the skill you follow; it makes the offer).
7. **Write first; read `registry__get_kind_schema { "kind": "<kind>" }` only when a write is
   refused.** A 422 with `field` names the property to fix (with `allowed_values` for a fixed
   set): fix it and send it once more. Read the schema once (`required`, `properties`,
   `relationships` as in `edges.md`, `body`) only when a 400 or 422 names no field (an
   older gateway) or you cannot tell the type or limit the field takes, then fix the call and send
   it once more. Read it first only for a kind no skill describes.
8. **Know what the host can do before a long task**: a shell (to install, zip or run), a bearer
   for an upload's `PUT` (Claude chat and Cowork never hand you one), and the app tools the task
   calls. When a route cannot finish here, say so before the user approves anything, and offer
   one that can.

## Reuse what the session already learned

Within one session these do not change, so read each once and reuse it:

| Learned | Reuse it for |
|---|---|
| The `session_id` | Every call |
| The workspace the user chose | Every create that names no other |
| A kind's schema | Every write to that kind |
| A tool's exact name, `inputSchema` and runner | Every call to it, with no second search or describe. When a runner refused it and named another, use that one from then on |
| An optional argument the schema does not list, or a call refused by name (`include_file`, `include_files`, `files_from`, `wait_for_release`, `idempotency_key`) | Leave it out for the rest of the session. Never send a call you expect to be refused to find out what the app takes |
| A handle you found by name | The handle, not a second search |

Read again only what can move under you:

- An artifact's latest version or release, before you write a new version on top of it or tell
  the user which version is current. Someone else may have published since.
- A permission, after a refusal or when the user says their access changed.
- Anything the user says changed, or asks you to look at again.

## When a call fails

Read the error first. These four cases cover what a retry can fix; anything else is a refusal
that a repeat will not change (see `atlan-troubleshoot`).

| What came back | Do |
|---|---|
| `502`, `503`, `504`, "service unavailable", or `retryable: true` | A read: wait `retry_after_seconds` when the error gives it, else 2 seconds, and repeat the same call; then 4, then 8 seconds, 3 tries in all. Then tell the user Atlan is not answering. A write: do not repeat it. Read the artifact back, or repeat a create with the same `idempotency_key` and identical arguments, and retry only what did not happen. `extension__skill__create` takes its key per skill, inside each `skills` item, and an older skill app has none: without one, read the skill back first, as `atlan-author-skills` says under **When the reply is lost**. |
| JSON-RPC `-32003`, `rate_limited` | The organization's budget is spent for the moment: the same rule, waiting longer. |
| "session not found; call `mcp__search_tools` …" | The gateway no longer knows that session (it expired, or the id came from another conversation). Open a new one once: the next search or runner call with `objective` and no `session_id`. Use the new id from then on, and tell the user only if a write was in flight. |
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
  slot. Read the slots, retry only the failed ones. Keep one call's items under about 256 KB of
  JSON as well: small items fill 100, but large bodies, such as full metric definitions of up to
  16 KiB each, go about 25 to a call.

## Source keys

A create whose workspace, kind, `source_type` and `source_id` match a live artifact appends a
version to it instead of making a second one. Each recipe keeps to one convention:

| What | `source_type` | `source_id` | Recipe |
|---|---|---|---|
| A skill published from this computer | `local` (the default) | the skill's `name` (the default) | `atlan-author-skills` |
| An agent declared in a repository | as the recipe sets it | the declaration path, such as `.atlan/agents/reviewer.yaml` | `atlan-author-agents` |
| A shared or recorded file and its output | `local` | `<repository or folder name>/<path from its root>`, at most 512 characters | `atlan-share` |
| An output with no file | `local` | `claude/<title in kebab case>` | `atlan-share` |

To add a version to something that exists, read it and send its stored `workspace_id`,
`source_type` and `source_id` unchanged. A matching name alone is not the same artifact.
