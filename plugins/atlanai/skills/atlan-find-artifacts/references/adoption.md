# Who uses an artifact, at which version, and how often

Open this for "what uses it, what does it use?", "who uses this skill?", "who is still on 1.0?",
"is anyone using it?", "how often is it used?" or "which of our skills are used most?". Every call here is a read.

Atlan records **who depends on** an artifact, as edges, and **how often** a skill, agent or
harness runs, as statistics. Edges answer "who uses it" and "at which version"; statistics
answer "how much". Never count usage by reading run traces: one run holds hundreds of steps,
and runs you cannot see read as missing.

## How often it runs

Two read tools count runs, one subject at a time: `extension__skill__query_trace_stats` for a
skill and `extension__agent__query_trace_stats` for an agent or harness. Through
`mcp__call_read_tool`:

```
extension__skill__query_trace_stats {
  "skill_id": "skill_…",
  "query": {
    "start_time_unix_seconds": 1793664000,
    "end_time_unix_seconds": 1794268800,
    "request_type": "scalar",
    "queries": [
      { "name": "runs", "measure": "traces" },
      { "name": "people", "measure": "active_users" }
    ]
  }
}
→ { "data": { "results": [ { "query_name": "runs", "value": 18.0 }, … ] }, "meta": { "window": … } }
```

- The window is Unix seconds and its end is exclusive. Count it from an anchor, never from
  memory: 2026-01-01T00:00:00Z is 1767225600, and a day is 86400. A window a year early
  answers zero, which reads like an unused skill.
- `traces` counts runs and `active_users` the people behind them; `error_rate` is the share
  of runs with a failed step. A result with no `value` has no data, which is not zero.
- For a trend, `"request_type": "time_series"` with `"bucket": "day"` or `"week"`. For the
  change against the weeks before, add `"compare": "previous_period"`. For a split,
  `"request_type": "table"` with one `group_by` on the query, such as `skill_version` or `user`.
- The agent tool takes `"subject_id": "agent_…"` (or `harness_…`) in place of `skill_id`.
- It can answer for a subject whose individual runs you may not read, so ask it before saying
  usage is hidden.
- An organization without the app does not serve the tool, and the call says it is unknown:
  say once that this connection cannot read usage, and that read counts are on the
  artifact's usage view in the Atlan dashboard.

Answer in one line: "preview-deploy ran 18 times in the last 7 days, for 4 people."

## Reading the neighbourhood

`registry__get_related_artifacts { "artifact_id": "…" }` returns typed edges in both
directions: an agent's `uses_skill`, an output's `used_skill`, a skill's `sourced_from` repo.
An end marked `is_readable: false` exists in a workspace the user cannot read; say that
rather than guessing what it is. Edges are declared by their source kind, so to find the
agents that use a skill, read the skill's incoming edges rather than its outgoing ones.

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
> 3 recorded outputs drew on it.

When nothing depends on it, say that plainly: no agent loads it and no output cites it. That
is the answer to "can I archive it?", which then goes to `atlan-manage-artifacts`.

## Across a workspace

No tool counts usage across an organization, so "which of our skills are used most?" is one
statistics call per skill:

1. `registry__list_artifacts` with `kind: "skill"` and the `workspace_id`, at most 20, most
   recently changed first.
2. `extension__skill__query_trace_stats` on each for the same window (30 days unless the user
   names one), all sent together.
3. Rank by runs. Skills with none are listed last as not run in the window.

Say the ranking covers the 20 most recently changed skills and the window. Where the
statistics tool is not served, rank by dependents instead: `registry__get_related_artifacts`
on each, by the number of agents that load it, then by outputs that cite it, and say it counts
dependents, not runs.
