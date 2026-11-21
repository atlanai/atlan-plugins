# What `availability` says

Open this when a hit or a read is not plainly usable: its `availability.state` is not
`available`, it carries a `head`, or it carries no `availability`.

`availability` is the registry's one answer to "can a person use this now", judged on the
`latest` channel. Decide from it. Never work the answer out from `artifact_status`,
`is_released` or `released_channels`: they describe one version, and the answer is about the
whole artifact.

```json
"availability": { "state": "available", "channel": "latest",
                  "serves": { "release_id": "rel_…", "version_label": "1.2.0" },
                  "head": "awaiting_scan" }
```

| `state` | Tell the user | Who acts next |
|---|---|---|
| `available` | It can be used. `serves.version_label` is the version they get. | Nobody. |
| `awaiting_scan` | Its newest version is being checked, and nothing is released yet. | Nobody: read again shortly. |
| `scan_stalled` | Its check is not running, so it will not release by itself. | Its owner or a workspace admin runs the scan again with `atlan-manage-artifacts`. |
| `blocked` | A check refused it and nothing is released. Its files are withheld. | Its owner reads the findings with `atlan-manage-artifacts`. |
| `awaiting_release` | It passed its checks and nobody has released it. | Its owner or a workspace admin releases it with `atlan-manage-artifacts`. |
| `withdrawn` | Its last release was withdrawn, and nothing took its place. | Its owner releases a fixed version. |
| `archived` | It was archived. | Nobody. |

Only `available` can be used. For any other state, say the sentence and who acts next. Never
offer the unreleased upload, or a similarly named artifact, as the thing to use.

## `head`

`head` is there only when the newest version is not the one `state` describes, and takes one
of `awaiting_scan`, `scan_stalled`, `blocked` or `awaiting_release`, read as in the table.

- **Beside `available`:** a newer version exists and is not out yet. The released one is
  still the one to use, so say nothing of the newer version unless the user asked about an
  update or owns the artifact.
- **Beside `withdrawn`:** what the newest version waits on before it can replace the
  withdrawn release.

## Which reads carry it

It is on artifacts of a kind that has releases, such as a skill:

- `registry__search_artifacts` and `registry__list_artifacts`, when the call names a `kind`.
- `registry__get_artifact` with no `channel`, `release_id` or `version_ordinal`.

A search or list without `kind` carries none. Before calling such a hit usable, a draft or
blocked, read it with `registry__get_artifact` and its handle alone, and only for the hits the
answer rests on, not the whole page. A kind without releases, such as a file, a session or an
output, never carries it: what the user can read, they can use.

A release read (`channel` or `release_id`) carries none because it needs none: when it
answers, that is the released version, and `not found` means nothing is released there.
