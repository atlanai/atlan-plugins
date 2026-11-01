---
name: atlan-connect-repo
description: Connect a GitHub repository to Atlan so the skills and agents in it publish automatically: register the repository, link existing artifacts to their source, or add the GitHub Actions workflow that previews changes on pull requests and publishes on merge. Use when the user wants a repository's skills or agents kept in sync with Atlan, CI or GitHub Actions publication, or to record where an artifact's source lives. Not for a one-off publish from a local folder (atlan-author-skills).
---

# Connect a repository

Choose the operation:

- **Register a repository or link existing artifacts to it:** [registration and provenance](references/register.md).
- **Set up GitHub publication or migrate an older sync:** [GitHub sync](references/github-sync.md),
  using [workflow and manifest templates](references/workflows.md).

Pass a `session_id` and a first-person `rationale` on every tool call. In search mode, the
default, the first `mcp__search_tools` or `mcp__call_tool` opens the session by sending
`objective`, each `registry__…` tool named here runs through `mcp__call_tool` with `session_id`
and `rationale` beside `name` and `arguments`, and a reference's `mcp__initialise` step is
skipped. In full mode, call `mcp__initialise` first. Replies lead with the result in a few
lines.

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
