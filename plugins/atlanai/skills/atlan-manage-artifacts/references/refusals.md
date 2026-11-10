# Refusals on a release or a move

Open this before the readout for a move, and when `registry__release_artifact` or
`registry__move_artifact` is refused. Key on the error's `code`, never on its message. One step
per refusal, and no second try of the same call unless the row says so.

## A release that is refused

| `code` | Means | Do |
|---|---|---|
| `release_checks_pending` | The version's scan has not settled, so it is still `pending_review`. | Say it will ship when the scan passes. Read the head again shortly, and send the release once more only when it reads `active`. Under the `auto` release policy it ships by itself: check `registry__list_releases` first. A scan that never settles: [the review queue](review-queue.md). |
| `release_blocked` | The version is `blocked`: its scan refused it. | Do not retry. Show the findings with `registry__get_security_findings`; the fix is a corrected version from its owner, or an admin's approval of this exact one through [the review queue](review-queue.md). |
| `version_already_released` | This version already shipped on that channel. | Nothing to do: say which release holds it (`registry__list_releases`). To undo a release, withdraw it; never release it again. |
| `release_label_not_increasing` | The `version_label` is not higher than the channel's current one. | Read the current label from `registry__list_releases`, offer the next one up, and ask before sending it. |
| `release_label_taken` | Another version already carries that `version_label`. | Offer the next free label, and ask before sending it. |
| `403` with no code above | The user holds neither `manage` on the workspace nor `update` as the artifact's owner. | Say who can: the owner, or an admin of the workspace `registry__list_workspaces` names. |

A gateway that answers a bare refusal with none of these codes predates them: read the head's
`artifact_status` and `registry__list_releases`, and say which of the rows above it matches.

## What cannot move

Check these from the reads in steps 1 and 2 before asking. When one applies, the readout is two
sentences: the move is not possible and why, then the one path that works. It never asks whether
to try the refused move anyway, and it leaves out who would lose the artifact, since nothing moves.

| The artifact or target | Why it is refused | Offer instead |
|---|---|---|
| Kind `agent`, `harness`, `dataset`, `experiment`, `mcp_server`, `metric` or `sql_query` | The kind owns workspace-bound children or a machine identity. Other kinds can be refused the same way: the refusal names the kind. | Register it again in the target workspace through its authoring skill, then archive the old one after its own readout. |
| A session, or another record that is only ever created | It is history, tied to where it ran. | Nothing: say it stays where it is. |
| `artifact_status` is `blocked`, or the artifact is the platform's own | A blocked version moves nowhere until it is fixed or approved. | A corrected version or an approval first: [the review queue](review-queue.md). |
| The target is a personal workspace (`kind: user_default` in `registry__list_workspaces`) and the artifact is in a team workspace | A shared artifact cannot become one person's. | A team workspace the user can create in. |
| The target already holds an artifact of this kind with the same name or the same content | Two of one name cannot share a workspace. `registry__search_artifacts` with the name, `kind` and the target `workspace_id` shows it. | Rename this one first, or publish it as a new version of the one already there. |
| The target workspace is not active, or the user cannot create in it | A move needs admin on the current workspace and the right to create in the target. | Name who can, from `registry__list_workspaces`. |

Moving an artifact into the workspace it is already in moves nothing and is safe to repeat.
