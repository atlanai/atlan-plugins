---
name: atlan-connect-repo
description: Register repositories and provenance links in Atlan, or set up CI publication of repository skills and agents through GitHub Actions.
---

# Connect a repository

Choose the operation:

- **Register a repository or link existing artifacts to it:** [registration and provenance](references/register.md).
- **Set up GitHub publication or migrate an older sync:** [GitHub sync](references/github-sync.md),
  using [workflow and manifest templates](references/workflows.md).

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
