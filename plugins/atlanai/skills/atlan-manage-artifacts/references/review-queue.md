# The review queue

Open this when the user asks what is waiting for review, why something is blocked, or to approve
a pending version. Approval is a workspace admin's call on one exact version. Nothing here
approves more than one version per yes.

## What is waiting

1. **The workspaces.** `registry__list_workspaces` lists the workspaces the user can reach.
   Review needs an admin, so keep the rows with `can_manage: true`. A gateway that does not send
   `can_manage` predates it: keep every row and let a `403` on approval say who can. A user who
   is admin of none still gets the queue, read-only, with who can approve.
2. **The counts.** For each kept workspace, `registry__get_artifact_counts` with
   `workspace_id` and `"group_by": "artifact_status"`, all sent together. Keep `blocked` (a
   check, such as a skill's security scan, refused it, and only an admin's approval releases
   it) and `pending_review` (its checks have not settled: the scan is still running, or stuck).
   Report the non-zero ones in one line: "3 waiting in Platform, 1 blocked in Data".
3. **The items.** `registry__list_artifacts` with that `workspace_id`, `full: true` and
   `"status": "pending_review"`, and a second call with `"status": "blocked"`, sent together.
   On a workspace whose release policy is `manual`, also list
   `"is_released": false` with `"status": "active"`: those are approved versions nobody has
   shipped yet. A gateway that refuses `status` predates it: list without it, keep the rows
   with the right `artifact_status`, and page with `offset` only until the counted number is
   found.

List each item with its name, kind, version, owner and status. Offer to review one, newest first.
Do not open their files yet.

## Reviewing one item

Send these reads together:

- **What changed.** For a skill with an earlier active release, compare the pending version with
  it as `skills/atlan-use-skill/references/compare-versions.md` says. Its files cannot be read until
  it is approved (a read answers `not found`), so say which files changed by name and size from
  the two manifests, and that their text is not readable before approval. A first version has
  nothing to compare: list its files.
- **The scan.** `registry__get_security_findings` on the artifact: each finding's rule, file,
  line and remediation. `not_scanned` means the scan has not finished: say so and stop, since
  approving an unscanned version skips the check.
- **The blast radius.** `registry__get_related_artifacts`, read as SKILL.md step 2 says.
  Approving a new version of a skill that agents pin changes nothing for them until their
  manifests move; an edge's `version_ordinal`, when it has one, names the version each pins.
  An agent that follows `latest` gets it on release.

Show one card: name, version, workspace and owner; what changed; the findings (or none); what
depends on it. Then ask once: approve this exact version, yes or no.

## Approving

- Read the head again with `registry__get_artifact` and no `channel` just before approving. If
  its `version_ordinal` moved since the card, show the card again for the new version.
- `registry__approve_artifact` on that version, with the arguments `mcp__describe_tools` gives.
  Read it back: `active` is done.
- A `blocked` version is approved only when the user says the finding is a false positive, and
  the reply says that is what the approval records. Never rewrite its files to get around a
  finding: that is a new version, published by its owner.
- Approving is not releasing. When the workspace's release policy is `manual`, or the user asked
  for a channel, release it as SKILL.md's action table says, after its own yes.
- Never approve several versions on one yes, and never approve a version the user has not seen
  on a card.

After an approval, offer once to tell the owner. With a Slack connection through Atlan, that is
one message the user approves (`atlan-use-mcp-servers`); otherwise give the text to send.

There is no reject action in the registry yet. To turn a version down, tell the user to send the
owner the reason, and leave the version pending.
