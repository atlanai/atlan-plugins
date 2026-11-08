---
name: atlan-connect-repo
description: "Connect a GitHub repository to Atlan so its skills and agents publish automatically: register it, link artifacts to their source, or add the workflow that previews on pull requests and publishes on merge. Use when the user wants a repository kept in sync with Atlan or CI publication. Not for a one-off publish from a local folder (atlan-author-skills)."
license: Apache-2.0
---

# Connect a repository

Choose the operation:

- **Register a repository or link existing artifacts to it:** [registration and provenance](references/register.md).
- **Set up GitHub publication or migrate an older sync:** [GitHub sync](references/github-sync.md),
  using [workflow and manifest templates](references/workflows.md).

In search mode, the default, run each tool named here through the runner for what it does
(`mcp__call_read_tool` for a read, `mcp__call_write_tool` for a create or update,
`mcp__call_tool` for anything destructive), with `session_id` and a first-person `rationale`
beside `name` and `arguments`; your first call opens
the session by sending `objective`. Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner. Send independent reads
together in one turn, or as one `mcp__call_read_tools` call (up to 8) when the host lists it. A create names its `workspace_id`: the one the user names, else
`.atlan/share.json`'s at the project root, else theirs from `registry__list_workspaces` with
`{"kind": "user_default"}`. Write first, and read `registry__get_kind_schema` only after a
refusal that names no field. Open `skills/atlan-get-started/references/calling-tools.md` only for
a failed call, a limit, a source key or full mode. Replies lead with the result in a few lines.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

Registration and provenance alone do not prove that a publishing workflow ran.
Use the checkout's canonical remote, verify the returned repository's workspace and remote,
and resolve source identity before reusing artifacts; a display name alone is insufficient.
Ask before sending contributor email addresses.

For Git-backed publication, CI publishes first after merge. Never publish locally as a
shortcut, and never force, rename, archive or recreate to bypass an identity conflict.
Preserve existing manifest keys and workflow policy. Check secret names only; never read,
print or transfer credentials. Preview the selected artifacts and destination before writes.
Report prepared, merged, published and read-back-verified states accurately.

Agent authoring and private runtime credentials belong to `atlan-author-agents`.
Bundle authoring belongs to `atlan-author-skills`.
