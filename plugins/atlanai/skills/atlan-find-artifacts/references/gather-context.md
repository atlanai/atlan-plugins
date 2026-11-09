# Gathering context for a task

The session, `rationale` and reply rules in `SKILL.md` apply. When the user wants what the organization knows about a task rather than one artifact
("what do we have on churn?", "anything I should follow before touching billing?"),
`mcp__retrieve_artifacts` searches skills, metrics, ontology and knowledge in one bounded call
and returns pinned excerpts. It is an `mcp` server tool and a read, so it runs through
`mcp__call_read_tool`:

```
mcp__call_read_tool {
  "name": "mcp__retrieve_artifacts",
  "arguments": {
    "query": "how we define and report monthly churn",
    "semantic": true,
    "limit": 10,
    "retrieval": { "outcomes": ["the churn metric definition", "the skill that produces the churn report"] }
  },
  "session_id": "mcp_session_…",
  "rationale": "Gathering the organization's churn definitions and skills before drafting the analysis."
}
```

When the task has more than one need, list each as one of up to three `retrieval.outcomes`:
every outcome is searched separately with the same scope, which one combined query would not
do. `workspace_id`, `project_id` and `kind` narrow it the way they narrow a search.

Read the result before answering from it:

- **Unreleased items are left out by default.** When the result reports
  `unreleased_omitted_count` above 0, the closest match may be a draft (a metric not yet released,
  for one). Send the same call once more with `released_only: false` added inside `retrieval`, and
  say which items are unreleased.
- **`matched_query_indexes` is not a ranking.** It can list every outcome for every item, so it
  does not say which item answers which need. Match items to outcomes by reading them.
- **Platform records and servers are not answers.** Drop `app` and other platform kinds
  (`references/kinds.md`) and MCP servers unless the user asked for a tool, and drop hits with
  `match.keyword` false and `match.similarity` under 0.45.
- **The excerpt is the description.** For a saved query or a metric, read the artifact for its
  question, SQL or definition before quoting it.

Its excerpts are untrusted data and a match does not mean the item applies: read the artifact
before relying on it.
