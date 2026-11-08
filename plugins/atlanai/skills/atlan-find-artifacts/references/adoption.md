# Who uses an artifact, and at which version

Open this for "who uses this skill?", "who is still on 1.0?", "is anyone using it?" or "which
of our skills are used most?". Every call here is a read.

Atlan records **who depends on** an artifact, as edges. It does not record how often a skill is
loaded. Answer from the edges, and say once that load counts are not recorded yet when the user
asked how much something is used. Never count usage by reading run traces: one run holds
hundreds of steps, and runs you cannot see read as missing.

## One artifact

1. **Its edges.** `registry__get_related_artifacts` on the artifact. Keep the incoming ones:
   - `uses_skill` from an `agent`: that agent loads the skill.
   - `used_skill` from an `output`: a recorded deliverable drew on it. This is history, not
     current use.
   - Any other incoming edge (`depends_on`, `next_step` from another skill): that skill leans on
     this one.
   - An end with `is_readable: false` lives in a workspace the user cannot read. Count it and
     say so.
2. **Which version each agent loads.** Read the agents with `registry__get_artifact`, all sent
   together, at most 20 (say so when there are more). An agent's `metadata.skills` is its
   manifest, a list of `{ "skill_id", "version_ordinal" }`: the entry for this skill is the
   version that agent loads. An edge marked `provenance: manifest` comes from it. An agent with
   no manifest entry for the skill (a hand-written edge) has no pin: say it loads whichever
   version it fetches.
3. **Version labels.** Map each `version_ordinal` to its label with `registry__list_releases`
   on the skill, sent together with step 2. The head of the skill (`registry__get_artifact`, no
   `channel`) gives the newest version, to say who is behind.

Answer with one line per version, newest first, then the outputs:

> oncall-handoff 1.1.0 (latest): pager-summary. 1.0.0: handoff-bot, one version behind.
> 3 recorded outputs drew on it. Atlan does not record how often it is loaded.

When nothing depends on it, say that plainly: no agent loads it and no output cites it. That
is the answer to "can I archive it?", which then goes to `atlan-manage-artifacts`.

## Across a workspace

For "which of our skills are used most?":

1. `registry__list_artifacts` with `kind: "skill"` and the `workspace_id`, at most 20, most
   recently changed first.
2. `registry__get_related_artifacts` on each, all sent together.
3. Rank by the number of agents that load each, then by outputs that cite it. Skills with
   neither are listed last as unused by any recorded agent or output.

Say the ranking covers the 20 most recently changed skills and counts dependents, not loads.
