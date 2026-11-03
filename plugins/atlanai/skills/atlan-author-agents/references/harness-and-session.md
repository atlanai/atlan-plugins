# Harnesses and sessions

## `harness`

One tenant's registration of a coding tool. **Nothing seeds one** - deliberately, because
the row should name the tool the customer actually uses rather than every tool that exists.

Two required properties:

| Property | Rule |
|---|---|
| `harness_vendor_id` | A live `harness_vendor` handle. **Immutable after create** |
| `default_model_provider` | One of `openai`, `anthropic`, `open_router`, `bedrock`, `openai_compatible`, `azure`, `google_ai`, `vertex`, `litellm`, `typesafe`, backed by a real database `CHECK`. Claude Code and Claude are `anthropic`; Codex is `openai`. `registry__get_kind_schema` for `harness` lists the current set |

```
registry__create_artifact {
  "kind": "harness",
  "metadata": {
    "name": "claude-code",
    "workspace_id": "workspace_01J...",
    "harness_vendor_id": "harness_vendor_01J...",
    "default_model_provider": "<provider>"
  },
  "session_id": "...", "rationale": "..."
}
```

Find the vendor first:

```
registry__list_artifacts { "kind": "harness_vendor", "limit": 50, ... }
```

The gateway seeds that catalogue (`claude-code`, `claude-code-web`, `claude`, and others).
It is a starting point, not the set of representable values - patch or add if yours is
missing.

`harness_vendor_id` being immutable is load-bearing: every `session.subject_id` points at
the harness, so repointing it would silently reattribute history. An update that changes it
is refused.

`default_model_providers` (plural) is a JSON column for the multi-provider case. It is
validated on the way in but **not** re-checked for values a hook wrote, so treat it as
advisory when reading.

## Moving a harness is refused

422, and the reason is its children: `session` and `session_message` are create-only and
unmovable, so a harness that moved would leave its whole history behind in the old
workspace.

## `session`

A run. It names its own subject - which harness or agent it belongs to - and carries
`session_message` children.

Both kinds are **create-only**: a turn that was spoken cannot be respoken. There is no
patch path and no version rail. That is the opposite of `output`, which is not create-only
precisely because iterating on a deliverable is the point.

Neither is *discoverable*: they are records you look up once you know the run, not things
someone goes searching for. That is why an `output` exists separately from the session that
produced it.

## `session` and `harness` declare no edges

Which means they accept **any** `rel_type`. A typo from one of them is stored silently
rather than refused - the opposite footgun from the 422 you get on a kind with a
vocabulary.

`registry__get_kind_schema` tells you which case a kind is in. An empty `relationships`
means "anything goes", not "nothing allowed".
