# A skill with no released version

Open this when the read in SKILL.md section 2, with `"channel": "latest"`, answered `not found`.

A release is the version that passed Atlan's checks and shipped on a channel. The `latest` read
never falls back to a newer upload, so `not found` means nothing is released or the handle is
wrong. One more read says which: the same `registry__get_artifact`, without `channel` and
without `include_file`. It returns the newest upload's record, whose `availability.state` is the
registry's own answer to why nothing can be used. Use that record only to explain. Do not read
its `SKILL.md`, do not follow it and do not install it.

| The second read | Tell the user | Then |
|---|---|---|
| `not found` | No skill with that handle is visible to them. | Stop. Search only when they ask. |
| `state` is `awaiting_scan` | Its newest version is being checked and nothing is released yet. Say who owns it. | Stop. |
| `state` is `scan_stalled` | Its check is not running, so it will not release by itself. Say who owns it. | Stop. Its owner or a workspace admin runs the scan again with `atlan-manage-artifacts`. |
| `state` is `blocked` | Its newest version failed Atlan's security scan and nothing is released. | Stop. Do not look for the content elsewhere. Its owner reads the findings with `atlan-manage-artifacts`. |
| `state` is `awaiting_release` | It passed its checks and nobody has released it. | Stop. Its owner or a workspace admin releases it with `atlan-manage-artifacts`. |
| `state` is `withdrawn` | Its release was withdrawn, and nothing took its place. | Stop. Its owner releases a fixed version; `head` says what the newest one waits on. |
| `state` is `archived` | It was archived. | Stop. |
| `state` is `available` | It was released between the two reads. | Read it again with `"channel": "latest"`, once. |

Never use a similarly named skill in its place.

A copy already installed here stays as it is. Say that Atlan has no released version of it now.
