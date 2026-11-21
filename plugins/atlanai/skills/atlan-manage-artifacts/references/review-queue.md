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
   `workspace_id` and `"group_by": "artifact_status"`, all sent together. The `blocked` and
   `pending_review` groups are the versions held back; the count says how many rows step 3
   must find, and nothing more. Report the non-zero ones in one line: "3 waiting in Platform,
   1 blocked in Data".
3. **The items.** `registry__list_artifacts` with that `workspace_id`, `"kind": "skill"`,
   `full: true` and `"status": "pending_review"`, and a second call with `"status": "blocked"`,
   sent together. Naming the `kind` puts `availability` on each row. On a workspace whose release
   policy is `manual`, also list `"is_released": false` with `"status": "active"`, the same
   `kind`. When the lists hold fewer rows than step 2 counted, the rest are another kind: list
   once more without `kind`, and read each of those with `registry__get_artifact` and no
   `channel`, which carries `availability` for any kind that has releases. A gateway that
   refuses `status` predates it: list without it and page with `offset` only until the
   counted number is found.

Say what each item waits on from its `availability`, never from `artifact_status` or
`is_released`. Read `head` when it is there, since it describes the newest version, and
`state` otherwise:

| `head`, or `state` without one | The item is | What to offer |
|---|---|---|
| `awaiting_scan` | Being scanned now. It waits on no person. | Nothing yet: it settles by itself. |
| `scan_stalled` | Stuck: no scan is queued or running, so it will not settle. | One rerun: [a scan that does not settle](#a-scan-that-does-not-settle). When its findings read `scanned`, an admin can review and approve it instead. |
| `blocked` | Refused by a check. Only an admin's approval, or a corrected version, moves it. | A review: [reviewing one item](#reviewing-one-item). |
| `awaiting_release` | Approved and not shipped. | A release, as SKILL.md's action table says. |

A `state` of `available` beside a `head` means people still get the earlier version that
`serves` names while the newer one waits: say so, because nothing is broken for them. With no
`head`, the item is released and is not in the queue.

List each item with its name, kind, version, owner and what it waits on. Offer the one step
its row names, newest first. Do not open their files yet.

## A scan that does not settle

`scan_stalled` is the registry saying no scan is queued or running for the newest version. A
failed scan is submitted again by itself after ten minutes, so when the row's `updated_at` is
under ten minutes old, read the head again later. Otherwise say the scan is stuck and offer
one rerun:

- `registry__retrigger_artifact_action` with the `artifact_id` and `"action_name": "scan"`,
  through `mcp__call_write_tool`, after a yes that names the artifact and version. It needs
  update rights on the workspace, so the owner can ask for it as well as an admin.
- Each call starts a new run. Send it once, and never again after a timeout or an unclear
  answer: read the head back with `registry__get_artifact` and no `channel` instead. Its
  `availability` reads `awaiting_scan` once the run is queued.
- It approves nothing. When the new scan settles the version reads `blocked` or
  `awaiting_release`, and under an `auto` release policy it then ships by itself.
- On a `blocked` version a rerun judges the same files again, and it reads `awaiting_scan`
  meanwhile. Offer it only when the user says the scanner or its rules changed; otherwise the
  fix is a corrected version from the owner, or an admin's approval.
- When `mcp__describe_tools` answers that it is not a tool you can call, the gateway predates
  it: say the scan cannot be restarted from here and that an admin can open the version in
  the dashboard.

## Reviewing one item

Send these reads together, as one `mcp__call_read_tools` call when the host lists it:

- **What changed.** For a skill with an earlier active release, compare the pending version with
  it as `skills/atlan-use-skill/references/compare-versions.md` says. Its files cannot be read until
  it is approved (a read answers `not found`), so say which files changed by name and size from
  the two manifests, and that their text is not readable before approval. A first version has
  nothing to compare: list its files.
- **The scan.** `registry__get_security_findings` on the artifact: each finding's rule, file,
  line and remediation. `not_scanned` means no scan of it has finished: say so and stop, since
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
  Read the head back: `awaiting_release`, or `available` with no `head`, is done.
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
