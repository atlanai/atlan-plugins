# Updating an agent

One verb:

```
registry__patch_artifact {
  "artifact_id": "agent_01J...",
  "patch": { "instructions": "...", "max_step_count": 30 },
  "session_id": "...",
  "rationale": "..."
}
```

## Absent means cleared, for the reference fields

This is the sharp edge, and it is deliberate rather than accidental.

The registry applies the RFC 7396 merge **before** the extension's update hook runs. So by
the time the hook sees the document, the only reason `agent_framework_id` or
`agent_provider_id` could be absent is that you sent an explicit `null` to clear it - an
omitted key was already filled in from the stored row by the merge.

Which means: **send `null` when you mean to clear, and omit the key when you mean to keep
it.** Both work as written. What does not work is assuming an omission is safe on a field
you have not thought about, because the merge is what makes it safe and the hook is what
would tell you if it were not.

## A definition change is a new version

`instructions`, `model_id`, `tools`, `max_step_count`, `skills` and the other spec fields
define the agent, and runs, evals and traces cite a version by its ordinal. So a patch that
changes one of them does not edit the agent in place: it publishes the **next version** - the
reply's `version_ordinal` is one higher - and the previous version stays readable. That is
also how a released agent changes: there is no `version_already_released` to work around.
The new version ships under the workspace's release policy, and runs pick it up once it is
released.

A patch that touches only `display_name` or `description` still edits in place.

To label the version a patch made, cut its release with `registry__release_artifact`
(`artifact_id`, the new `version_id`, and `version_label`, such as `1.2.0`);
`registry__list_releases` shows what each channel ships. Do not call the agent app's HTTP routes
yourself.

## Merge semantics

- An **omitted** key is preserved.
- An explicit `null` clears the value.
- Nested objects merge key by key rather than being replaced wholesale.
- `workspace_id` cannot be patched.

## What is refused

- **A present-but-blank `instructions` or `model_id`** - 400, telling you to omit the key
  instead. Absent means "not configured"; empty string means nothing.
- **A dangling or archived `agent_framework_id` / `agent_provider_id`** - 400. Both are
  re-resolved on every update, not just on create.
- **`public_url` that is not `http`/`https`** - 400.
- **The gateway's own seeded system agent** - changing its `instructions`, `model_id` or
  `agent_framework_id` is a 400. It is immutable: a catalogue reference point, not a
  runnable agent.

## What is silently restored

`profile_image_uri` is server-owned. Sending it in a patch is not an error; it is
overwritten with the stored value. Set it through the profile-image route instead.

## Moving an agent is refused

422. An agent carries a machine identity, and the OIDC client behind it is seated on the
workspace it was created in - so a moved agent would hold a credential scoped somewhere it
no longer lives.

## Removing an agent

`registry__archive_artifact`. There is no hard delete on this path, and this extension
deliberately declares no per-agent update tool - `registry__patch_artifact` genuinely covers it,
which is why one was never added.
