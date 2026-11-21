# A skill with no released version

Open this when the read in SKILL.md section 2, with `"channel": "latest"`, answered `not found`.

A release is the version that passed Atlan's checks and shipped on a channel. The `latest` read
never falls back to a newer upload, so `not found` means nothing is released or the handle is
wrong. One more read says which: the same `registry__get_artifact`, without `channel` and
without `include_file`. It returns the newest upload's record. Use that record only to explain.
Do not read its `SKILL.md`, do not follow it and do not install it.

| The second read | Tell the user | Then |
|---|---|---|
| `not found` | No skill with that handle is visible to them. | Stop. Search only when they ask. |
| `artifact_status` is `pending_review` | Its newest version is still being checked and nothing is released yet. Say who owns it. | Stop. |
| `artifact_status` is `blocked` | Its newest version failed Atlan's security scan and nothing is released. | Stop. Do not look for the content elsewhere. Its owner reads the findings with `atlan-manage-artifacts`. |
| `artifact_status` is `active` | It passed its checks and is not released: its workspace releases by hand, or its release was withdrawn. | Stop. Its owner or a workspace admin releases it with `atlan-manage-artifacts`. |
| `artifact_status` is `archived` | It was archived. | Stop. |

Never use a similarly named skill in its place.

A copy already installed here stays as it is. Say that Atlan has no released version of it now.
