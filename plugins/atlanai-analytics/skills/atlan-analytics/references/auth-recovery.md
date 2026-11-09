# Recover the Registry connection and resume

Keep ownership of the analytics run. A missing connection must not discard candidates,
hide their preview or require the user to type "continue" after a successful sign-in.
Use the existing core plugin, `atlanai`, and its selected Atlan MCP connection. A search of
a generic app directory does not establish whether a coding-host plugin is installed.

`PROJECT` is the project helper and the same saved run directory from [setup](setup.md).
The `auth` command takes a JSON request file; it records host observations, not credentials.
It does not authenticate, launch a browser, run a watcher or verify the network itself.
The parent owns the actual host background task, browser step and MCP readiness watch below.

## 1. Identify the actual failure

Look for the current host's prefixed Atlan search/describe/read runners. If tools are deferred,
discover them through the host's tool inventory. If available, make one small read through
`mcp__call_read_tool`: `registry__list_workspaces` with `{"kind":"user_default"}` and the
existing session, or the original task objective on the first call. A successful read proves
this host can reach Registry; it does not grant publication or workspace write permission.

When that read succeeds without a sign-in flow, record the actual observation directly:

```json
{"operation":"record_read","host":"codex","server":"atlan","read_tool":"registry__list_workspaces","read_succeeded":true}
```

Both host and server must name the connection just read. This appends a timestamped,
host/server-scoped observation to `registry-readiness.json`; it leaves `auth-recovery.json`
unchanged. Do not invent a failed connection, `begin` or login event to record an already
working connection. A Codex read does not authenticate Claude or finish Claude's pending
sign-in. The helper reports any preserved recovery and task ownership separately. Recording
an independent read does not authorize switching or abandoning an owned sign-in task;
complete or cancel that task through its owning host before changing the recovery connection.
For the same connection's active owned task, the result asks to resolve that task first.

Presentation may use this evidence only with explicit current host/server selection. Without
that selection, retain the existing recovery state. An older successful read must not override
a newer matching recovery observation; recovery writes carry `updated_at` as well as the
attempt's `started_at`. A matching active owned task still needs resolution. These records
are host-reported observations, not network checks performed by the helper or write grants.

With shell access, inspect only the named plugin/connection using the client's supported
list/get commands. Summarize enabled state and authentication status, never headers, tokens,
environment values or credential files. Check CLI help before using a version-specific command.

| Observation | Recovery |
| --- | --- |
| Core `atlanai` plugin absent | Show the core-plugin installation commands below; install only within existing authorization. |
| Plugin or selected server disabled | State that it is disabled and request enablement through the host. Do not override user/managed controls or reinstall a duplicate server. |
| OAuth/sign-in required | Start the browser sign-in and bounded background watch below. |
| Plugin enabled but tools missing | Refresh/reconnect the host's tool inventory. Absence alone is not proof of expired credentials. |
| Registry responds with permission denied | Preserve the run and resolve the selected workspace's access; do not repeat OAuth or switch identities. |
| Connection error | Give one retry for that selected endpoint; preserve candidates and show the manual recovery if it persists. |

Record the observation with `PROJECT auth` and a request such as:

```json
{"operation":"begin","host":"codex","reason":"sign_in","server":"atlan","resume_stage":"publish","timeout_seconds":600}
```

Hosts are `codex` or `claude_code`. Reasons are `sign_in`, `tools_missing`, `plugin_missing`,
`disabled`, `permission_denied` or `connection_error`. Use the actual pending setup stage
and observed server name. Repeating `begin` while a login is active reuses its task; never
start multiple OAuth flows. Keep the returned `attempt_id` for later observations.

## 2. Open the sign-in page and watch the host login task

When sign-in is needed, tell the user: "I'm opening the Atlan sign-in page. I'll resume
this setup when the connection is ready." Launch the supported client command as a host
background task, or a yielding terminal task with a retained task/session ID:

- Codex: `codex mcp login atlan` for the observed server of that name.
- Claude Code: `claude mcp login plugin:atlanai:atlan` for the plugin's observed server name.
  If this CLI lacks `mcp login`, use `/mcp`, select that server and Authenticate through the
  available host UI. Do not invent a CLI flag or another MCP server.

Record `login_started` with the real `task_id`. The client usually opens its issued
authorization URL in the user's default browser itself; leave it there. Only when it did not,
open that same issued URL with the host's browser tool or a supported local opener (Chrome
when the host offers Chrome control), never a guessed dashboard or sign-in page, and never
restart OAuth to switch browsers or change the system default browser.
Leave account entry, MFA and consent to the user. Do not read or copy cookies, codes, tokens,
callback URLs or credentials. Do not persist authorization URLs or raw login output in run files.
Record `browser_opened` only after observing the browser action succeed.

Keep the parent turn alive with as few turns as the host allows. Prefer the task's completion
notification; without one, check the native task handle at most every 30 seconds. This is
the background watcher: a real host task with a retained handle, not a promise to check
later. Continue independent survey/authoring work while waiting; serialize run mutations.
Give status only when it changes, or once a minute at most. Do not schedule an automation,
create an OS daemon, add an agent, inspect credential files or start hidden model calls.

A host with no background task or browser tool gets the core sign-in step for that host in
one line (section 3), the saved run directory, and a resume on the user's next message.

Use `PROJECT auth` with `{"operation":"poll"}` for the durable deadline/status. Record
observations with the current `attempt_id`, for example:

```json
{"operation":"observe","attempt_id":"<current-attempt>","event":"login_succeeded","task_id":"<same-host-task>"}
```

A successful CLI exit moves to `checking_mcp`, not Ready. Refresh the current host tools
and retry the small Registry read from step 1. Only after that read succeeds, record:

```json
{"operation":"observe","attempt_id":"<current-attempt>","event":"registry_ready","read_tool":"registry__list_workspaces","read_succeeded":true}
```

Then resume the recorded stage immediately, without asking for "done" or "continue" and
without re-cloning, re-authoring or repeating existing approvals. Sign-in grants access;
publication still follows its concrete plan and existing authorization.

On cancellation, failure or the ten-minute deadline, stop watching and cancel only the
owned login task if it is still running. Record `canceled`/`failed`; polling records expiry
as `timed_out`. Give the relevant fallback below and the exact saved run directory. Do not
restart OAuth indefinitely. A stale event from an older attempt cannot resume this run.
Tell the user once that candidates and the pending stage are saved, then name the one missing
action. After three failed attempts at the same step, stop that step; do not describe or start
another attempt. Avoid a procedural recap when a short saved-state update answers the request.
If a resumed `begin` returns `timed_out`, clean up its returned owned task before beginning
another attempt. A permission-denied result also ends the owned watch without another login.

If terminal login succeeds but the current chat cannot load tools, record `tools_not_loaded`.
Try the host's supported reconnect/reload once. Where the host requires a new chat, explain
that limitation and give `Use Atlan Analytics to resume <saved-run-directory> at publication`.
Do not claim a watcher can wake a closed chat or that a CLI-only connection is current-host
MCP access. When no browser/background capability exists, give manual instructions directly;
never claim a browser or a watcher was started without a real successful tool action/task ID.

When asked only to explain this recovery, use a short sequence: sign-in from the real
client URL in the browser; bounded host-task watch; successful current-host Registry read; automatic saved
stage resumption. State the disabled, timeout and unloaded-tools paths explicitly. Do not
dump helper JSON or every polling step into chat. A hypothetical explanation has no built
candidates to preview and is not a blocked setup run.
Lead with the action, for example "I would open the client's sign-in link and
resume this saved run once Registry responds." Keep this explanation to roughly 200 words:
the four steps and three recovery branches are enough. Conditional wording makes clear it
is a plan; do not open with a disclaimer, repeat security rules or narrate helper internals.

## 3. Manual installation and sign-in fallback

Here Registry means the **AI Registry core plugin, `atlanai`**, which supplies the MCP
connection used by Analytics. If it is already installed, reconnect it; do not add a second
raw MCP server or a different plugin named `atlan`. Show commands as copyable text.

Claude Code terminal:

```bash
claude plugin install atlanai --marketplace atlanai/atlan-plugins
```

Then `/reload-plugins` in Claude Code, `/mcp`, select `plugin:atlanai:atlan`, and Authenticate.
Older clients without `--marketplace` can use `/plugin marketplace add atlanai/atlan-plugins`
followed by `/plugin install atlanai@atlan`. The user needs an Atlan account/workspace invitation.

Codex terminal:

```bash
codex plugin marketplace add atlanai/atlan-plugins
codex plugin add atlanai@atlan
codex mcp login atlan
```

Reuse an already configured `atlan` marketplace. If terminal login requires a fresh chat,
paste the saved-run resume prompt there. CLI/desktop connection limitations are described in
the [Atlan connection guide](https://platform.atlan.com/gateway/mcp/how-tos/connect-a-client).
Installing the separate `aireg` CLI is not a substitute for loading Registry tools in the
current harness. If the user specifically wants that CLI, use the official
[Registry CLI guide](https://platform.atlan.com/tools/brain/index) and its current installer;
do not install it automatically as an authentication workaround.

## 4. Keep the metrics in the final answer

If access is still unavailable, run `PROJECT experience --format chat` and include one real
sample with its check status and factual local/published counts in the final answer. Add the
one missing action, relevant host-specific installation/sign-in instructions and the saved-run
resume step. Details expose SQL, source and checks without dumping them by default. Repeat
the sample even if it appeared in commentary; progress may collapse. A file link supplements
it. Say "Your drafts are saved; sign in to publish" rather than a machine status token. If
no candidate exists, say so instead of fabricating one. Distinguish local checked
candidates from released Atlan context; do not offer retrieval-backed answers until verified.
When the preview comes from a saved/supplied checkpoint, label check results as prior-run
evidence and state whether any SQL was executed in this turn. Do not imply a fresh check.
