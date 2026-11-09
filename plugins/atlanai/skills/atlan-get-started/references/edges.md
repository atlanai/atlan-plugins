# Joining artifacts: the edge vocabulary, and which end declares it

The one edges reference for every Atlan skill: skills, agents, outputs, knowledge and the rest.

## Nothing you create together is joined

Creating two artifacts in one conversation creates two unrelated rows. Uploading a skill and
an agent together does not link them; neither does creating a file "as part of" a skill. An
edge between your artifacts exists because you asked for one, and the write's result is the
edge it made.

Some edges are **derived** by an app after a write, and you do not write those:

- the skill app reads each uploaded `SKILL.md` and writes `depends_on`, `next_step` and
  `alternative` toward related skills, and its analysis writes `duplicate_of` and
  `conflicts_with`; each carries `attributes.provenance: "content-derived"` and a `confidence`;
- the agent app writes `uses_skill` from an agent's `skills` manifest (below);
- the metric app writes a metric's `depends_on` from its definition.

Newer gateways also record this in the edge's own `provenance` (`agent_inferred` or
`system_derived` instead of `manual`). Derivation runs after the write returns, so read a
skill's edges with `registry__get_related_artifacts` before writing any of these types by hand.

## An edge is declared by its **source** kind

This is the part that misleads. A `skill` may only ever point at another `skill`. That does
not mean nothing can be joined to a skill: the edge is declared from the other end, and you
create it there.

| Source kind | `rel_type` | Target kinds |
|---|---|---|
| `skill` | `depends_on`, `next_step`, `alternative`, `duplicate_of`, `conflicts_with`, `replaced_by` | `skill` |
| `skill` | `sourced_from` | `repo` |
| `agent` | `uses_skill` | `skill` |
| `agent` | `sub_agent` | `agent` |
| `agent` | `uses_file` | `file` |
| `agent` | `uses_mcp_server` | `mcp_server` |
| `agent` | `uses_knowledge` | `knowledge` |
| `agent` | `sourced_from` | `repo` |
| `output` | `used_skill` | `skill` |
| `knowledge_concept` | `documents` | `output` |
| `experiment` | `uses_context` | `skill`, `file`, `agent` |
| `scorer` | `applies_to` | `skill`, `agent` |
| `scorer` | `sourced_from` | `repo` |

That is the vocabulary of every kind that declares one; `registry__get_kind_schema` is the
live answer when an app adds a kind (its `relationships`). So:

- **to join an agent to a skill**, the source is the **agent**: `uses_skill`. Reading the
  skill's own vocabulary and concluding the join is impossible is the usual mistake.
- **to say a deliverable drew on a skill**, the source is the **output**: `used_skill`.
- **to join a file to a skill**: there is no such edge, and there is nothing to create. A
  skill's own files are not related to it, they are *in* it: the pinned `metadata.files`
  manifest, written by the create hook from the bundle. If a file belongs to a skill, put it
  in the bundle.

`uses_skill` and `used_skill` are spelled differently on purpose and are easy to swap: the
first is a standing configuration on an agent, the second a fact about one deliverable.

`sourced_from` names the repository an artifact is synced from, with the repo-relative `path`
in its `attributes`. The sync writes it; create one by hand only when registering a repo's
existing artifacts.

## What the six skill edges mean

| `rel_type` | Means |
|---|---|
| `depends_on` | This skill needs that one to have run, or needs what it sets up |
| `next_step` | That one is the natural follow-on |
| `alternative` | Two ways to do the same thing |
| `duplicate_of` | Two skills that should be one |
| `conflicts_with` | Running both is wrong |
| `replaced_by` | This skill is deprecated; use that one. Written by whoever deprecates it |

## An agent's skills come from its manifest

Do not write `uses_skill` by hand on an agent that has a `skills` manifest. The manifest,
`[{ "skill_id": ..., "version_ordinal": ... }]` on the agent's own metadata, is the record of
which skill versions that agent version loads, and the registry derives `uses_skill` from it:
one edge per pinned skill, marked `attributes.provenance: "manifest"` (and `provenance:
system_derived` on newer gateways), with every other `uses_skill` on the agent archived. A
hand-written edge on such an agent is removed on its next write. Patch the manifest instead.

The hand-written form below is for an agent that has never written a manifest, and for the
other edge types.

## Attribution is not an edge

The most common mistake on `output` is reaching for `relate_artifact` to say which agent
produced it. That is not an edge: it is the `subject_kind` / `subject_id` column pair in the
output's own `metadata`, set at create time and set **together**.

`used_skill` is the only relationship an `output` starts, and it answers a different question:
what the deliverable drew on, not who made it.

## Creating one

```
registry__relate_artifact {
  "artifact_id": "agent_01J...",     <- the source, whose kind declares the rel_type
  "subject_id":  "skill_01K...",     <- the target
  "rel_type": "uses_skill",
  "session_id": "...",
  "rationale": "..."
}
```

Safe to repeat: the edge is keyed on both ends and the type, so a second identical call
changes nothing. A repeat is not a no-op when it differs: an older gateway replaces the
edge's `attributes` with what you send (none, if you send none), and a newer one merges them.
So never re-relate a derived edge just to confirm it, and when you restate one of yours, send
every attribute you want kept.

**If you are creating the source artifact anyway, create it linked.**
`registry__create_artifact_linked` takes the same `kind` and `metadata` as a create plus
`subject_id` and `rel_type`, and makes the artifact and the edge in one call, so there is no
window where the new artifact exists unattached and no second call to forget. The gateway writes
both in one transaction: a refused edge leaves no artifact behind, so fix what the refusal names
and call again. An older gateway wrote them in two steps; there, a result that carries the new
handle beside an edge error is fixed with `registry__relate_artifact` on that handle, never by
calling again. An `output` is recorded with `registry__publish_output`, whose `edges` does the
same for its file and output.

```
registry__create_artifact_linked {
  "kind": "agent",
  "metadata": { "name": "deck-builder", "workspace_id": "workspace_01J...", ... },
  "subject_id": "skill_01K...",
  "rel_type": "uses_skill",
  "session_id": "...",
  "rationale": "..."
}
```

One edge per call, so an agent that uses three skills is one linked create plus two
`relate_artifact` calls. It cannot create a `skill`: a skill is its bytes and needs
`extension__skill__create`. So for a skill the order is: upload the skill, then relate from
the other artifact.

## One edge, three spellings

Each tool names the two ends differently. Read the row for the tool you are calling: a guess the
other way round can write a reversed edge wherever both kinds declare the type. Newer gateways
also take `to_id` for the target on the first two tools; `subject_id` works on every gateway, so
send that, and never both with different values.

| Tool | Source (declares `rel_type`) | Target |
|---|---|---|
| `registry__relate_artifact` | `artifact_id` | `subject_id` |
| `registry__create_artifact_linked` | the artifact being created | `subject_id` |
| `registry__publish_output` | the output being published | `to_id`, inside each `edges` entry |

`subject_id` inside an output's `metadata` is its producer, not an edge end.

## The refusals, in the order they fire

1. **Unknown `rel_type`**: 422, and the message **names the vocabulary**:

   > `banana` is not an edge type `skill` declares; it declares alternative, conflicts_with,
   > depends_on, duplicate_of, next_step, replaced_by, sourced_from

   Read the list out of the error rather than guessing again. If the type you wanted is not
   there, check the table above: it is probably declared on the other kind.

2. **Wrong target kind**: 422:

   > `uses_skill` on `agent` may point at skill, not `agent`

3. **Missing or unreadable target**: 404. The target may be in another workspace of the
   account, as long as you can read it; readers who cannot see that workspace get the far end
   as `{id, kind, is_readable: false}`.

The target's existence is checked **after** the vocabulary check, so a typo'd `rel_type`
costs no lookup and tells you about the typo rather than about a missing artifact.

## Reading them back

```
registry__get_related_artifacts { "artifact_id": "agent_01J...", ... }
```

Returns both directions: what this artifact points at and what points back at it.

You do not need it to confirm an edge you wrote: `registry__relate_artifact` returns the edge
it stored, and a refusal means nothing was stored. Read edges for what no result of yours
shows: links that existed before you started, and edges the registry derives after a write,
such as an agent's `uses_skill` edges from its `skills` manifest or a metric's `depends_on`
edges.

## The opposite footgun

**A kind that declares no edges accepts any `rel_type`.** `file`, `harness` and `session` are
in that group, and a typo from one of them is stored silently rather than refused: you get a
relationship nothing will ever query.

`registry__get_kind_schema` is the way to know which case you are in: its `relationships` lists
the `rel_type` values the kind may be the source of and the target kinds each permits. An empty
`relationships` means "anything goes", not "nothing allowed".
