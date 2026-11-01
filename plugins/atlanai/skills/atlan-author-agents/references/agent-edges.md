# Joining artifacts: the edge vocabulary, and which end declares it

## Nothing is joined automatically

Creating two artifacts in one conversation creates two unrelated rows. Registering an agent
and uploading the skill it uses does not link them. An edge exists because you asked for one,
and you confirm it by reading it back.

## An edge is declared by its **source** kind

| Source kind | `rel_type` | Target kinds |
|---|---|---|
| `agent` | `uses_skill` | `skill` |
| `agent` | `sub_agent` | `agent` |
| `agent` | `uses_file` | `file` |
| `agent` | `uses_mcp_server` | `mcp_server` |
| `agent` | `uses_knowledge` | `knowledge` |
| `agent` | `sourced_from` | `repo` |
| `output` | `used_skill` | `skill` |
| `skill` | `depends_on`, `next_step`, `alternative`, `duplicate_of`, `conflicts_with` | `skill` |
| `skill`, `scorer` | `sourced_from` | `repo` |
| `experiment` | `uses_context` | `skill`, `file`, `agent` |

That is the complete set for every kind that declares one. Two consequences worth stating,
because they are what people get wrong:

- **A `skill` may only point at another `skill`.** So an agent-to-skill join is created from
  the **agent** (`uses_skill`), never from the skill. Reading the skill's own vocabulary and
  concluding the join is impossible is the usual mistake.
- **`file`, `harness` and `session` declare nothing**, so any `rel_type` from them is stored
  without complaint - a typo becomes a relationship nothing will query. A file that belongs to
  a skill belongs *in the bundle*, not on an edge.

Note the two skill edges are spelled differently on purpose and are easy to swap:
`uses_skill` from an **agent** (a standing configuration), `used_skill` from an **output**
(a fact about one deliverable).

## An agent's skills come from its manifest

Do not write `uses_skill` by hand on an agent that has a `skills` manifest. The manifest -
`[{ "skill_id": ..., "version_ordinal": ... }]` on the agent's own metadata - is the record of
which skill versions that agent version loads, and the registry derives `uses_skill` from it:
one edge per pinned skill, marked `provenance: manifest`, with every other `uses_skill` on the
agent archived. A hand-written edge on such an agent is removed on its next write. Patch the
manifest instead.

The hand-written form below is for an agent that has never written a manifest, and for the
other edge types.

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

Safe to repeat: the edge is keyed on both ends and the type.

**If you are creating the source artifact anyway, create it linked.**
`registry__create_artifact_linked` is a create plus `subject_id` and `rel_type` in one call,
so the agent and its `uses_skill` edge land together and there is no second call to forget:

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

One edge per call, so an agent that uses three skills is one linked create plus two
`relate_artifact` calls.

## The three refusals, in the order they fire

1. **Unknown `rel_type`** - 422, and the message **names the vocabulary**:

   > `banana` is not an edge type `agent` declares; it declares sourced_from, sub_agent, uses_file, uses_knowledge, uses_mcp_server, uses_skill

   Read the list out of the error rather than guessing again. If the type you wanted is not
   there, check the table above - it is probably declared on the other kind.

2. **Wrong target kind** - 422:

   > `uses_skill` on `agent` may point at skill, not `agent`

3. **Different workspaces** - 422:

   > `<handle>` is in another workspace; a relationship's two ends must be in the same one

The target's existence is checked **after** the vocabulary check, so a typo'd `rel_type`
tells you about the typo rather than about a missing artifact.

## Attribution is not an edge

The most common mistake on `output` is reaching for `relate_artifact` to say which agent
produced it. That is not an edge - it is the `subject_kind` / `subject_id` column pair in
the output's own `metadata`, set at create time and set **together**.

`used_skill` is the only relationship an `output` has, and it answers a different question:
what the deliverable drew on, not who made it.

## Reading them back

```
registry__get_related_artifacts { "artifact_id": "agent_01J...", ... }
```

Returns both directions - what this agent points at and what points back at it. Call it after
any multi-artifact create: it is the only way to know the join happened rather than assuming
it did.
