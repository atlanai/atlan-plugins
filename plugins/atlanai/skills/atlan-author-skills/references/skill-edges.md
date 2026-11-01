# Joining artifacts: the edge vocabulary, and which end declares it

## Nothing is joined automatically

Creating two artifacts in one conversation creates two unrelated rows. Uploading a skill and
an agent together does not link them; neither does creating a file "as part of" a skill. An
edge exists because you asked for one. If you meant two things to be related, say so with a
call and then read it back.

## An edge is declared by its **source** kind

This is the part that misleads. A `skill` may only ever point at another `skill`. That does
not mean nothing can be joined to a skill - it means the edge is declared from the other end,
and you create it there.

| Source kind | `rel_type` | Target kinds |
|---|---|---|
| `skill` | `depends_on` | `skill` |
| `skill` | `next_step` | `skill` |
| `skill` | `alternative` | `skill` |
| `skill` | `duplicate_of` | `skill` |
| `skill` | `conflicts_with` | `skill` |
| `skill` | `sourced_from` | `repo` |
| `agent` | `uses_skill` | `skill` |
| `agent` | `sub_agent` | `agent` |
| `agent` | `uses_file` | `file` |
| `agent` | `uses_mcp_server` | `mcp_server` |
| `agent` | `sourced_from` | `repo` |
| `output` | `used_skill` | `skill` |
| `experiment` | `uses_context` | `skill`, `file`, `agent` |
| `scorer` | `applies_to` | `skill`, `agent` |
| `scorer` | `sourced_from` | `repo` |

That is the whole vocabulary of every kind that declares one. So:

- **to join an agent to a skill**, the source is the **agent**: `uses_skill`.
- **to say a deliverable drew on a skill**, the source is the **output**: `used_skill`.
- **to join a file to a skill**: there is no such edge, and there is nothing to create. A
  skill's own files are not related to it, they are *in* it: the pinned `metadata.files`
  manifest, written by the create hook from the bundle. If a file belongs to a skill, put it
  in the `.zip`.

`sourced_from` names the repository an artifact is synced from, with the repo-relative `path`
in its `attributes`. The sync writes it; create one by hand only when registering a repo's
existing artifacts.

`uses_skill` and `used_skill` are spelled differently on purpose and are easy to swap: the
first is a standing configuration on an agent, the second a fact about one deliverable.

## What the five skill edges mean

| `rel_type` | Means |
|---|---|
| `depends_on` | This skill needs that one to have run, or needs what it sets up |
| `next_step` | That one is the natural follow-on |
| `alternative` | Two ways to do the same thing |
| `duplicate_of` | Two skills that should be one |
| `conflicts_with` | Running both is wrong |

## Creating one

```
registry__relate_artifact {
  "artifact_id": "skill_01J...",     <- the source, whose kind declares the rel_type
  "subject_id":  "skill_01K...",     <- the target
  "rel_type": "depends_on",
  "session_id": "...",
  "rationale": "..."
}
```

Safe to repeat: the edge is keyed on both ends and the type, so a second identical call
changes nothing.

**If you are creating the second artifact anyway, create it linked.**
`registry__create_artifact_linked` takes the same `kind` and `metadata` as a create plus
`subject_id` and `rel_type`, and makes the artifact and the edge in one call - so there is no
window where the new artifact exists unattached and no second call to forget.

```
registry__create_artifact_linked {
  "kind": "agent",
  "metadata": { "name": "deck-builder", "workspace_id": "wrk_01J...", ... },
  "subject_id": "skill_01K...",
  "rel_type": "uses_skill",
  "session_id": "...",
  "rationale": "..."
}
```

It cannot create a `skill` - a skill is its bytes and needs `extension__skill__create`. So for a skill
the order is: upload the skill, then relate from the other artifact.

## The three refusals, in the order they fire

1. **Unknown `rel_type`** - 422, and the message **names the vocabulary**:

   > `banana` is not an edge type `skill` declares; it declares alternative, conflicts_with,
   > depends_on, duplicate_of, next_step

   Read the list out of the error rather than guessing again. If the type you wanted is not
   there, check the table above - it is probably declared on the other kind.

2. **Wrong target kind** - 422:

   > `depends_on` on `skill` may point at skill, not `agent`

3. **Different workspaces** - 422:

   > `<handle>` is in another workspace; a relationship's two ends must be in the same one

The target's existence is checked **after** the vocabulary check, so a typo'd `rel_type`
costs no lookup and tells you about the typo rather than about a missing artifact.

## Reading them back

```
registry__get_related_artifacts { "artifact_id": "skill_01J...", ... }
```

Returns both directions - what this skill points at and what points back at it. That second
half is how you confirm an `agent` or an `output` you linked actually landed, since the edge
does not live on the skill. Do this after any multi-artifact upload: it is the only way to
know the join happened.

## The opposite footgun

**A kind that declares no edges accepts any `rel_type`.** `file`, `harness` and `session` are
in that group, and a typo from one of them is stored silently rather than refused - you get a
relationship nothing will ever query.

`registry__get_kind_schema` is the way to know which case you are in: its reply carries the
`rel_type` values the kind may be the source of and the target kinds each permits. An empty
vocabulary means "anything goes", not "nothing allowed".
