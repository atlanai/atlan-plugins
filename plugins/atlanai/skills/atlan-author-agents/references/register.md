# Registering agents and harnesses

The registry records the agent, its configuration and outputs. The agent app also owns
the native Atlan runtime; local and custom providers can remain catalog-only. Registration
is a JSON create and does not by itself deploy or start the agent. Runtime authentication
uses its machine identity.

## Before your first call

1. Open a session once, and keep the id:

   ```
   mcp__initialise {
     "objective": "<one sentence, for a human auditor reading this later>",
     "client": "<the tool you run in, such as claude-code or claude-ai>",
     "rationale": "<why you are opening it>"
   }
   → { "session_id": "mcp_session_01J..." }
   ```

   Pass that as `session_id` on every later call - it is **required**. Full mode refuses a call
   that omits it; search mode opens a second session instead, splitting the audit trail.
   `mcp__initialise` is the only tool that does not take it, which is what makes this first
   call possible. Do not invent a value - an id that names no session of yours is refused, not
   ignored.

2. **`rationale` is required on every tool call.** It is a first-person sentence about why
   you are making *this* call, written for a human reading the audit trail. Not a
   restatement of the arguments.

3. Every create needs a `workspace_id`. Read it off `registry__list_workspaces`; if you can
   reach more than one, say which you are using rather than picking.

4. Before your first write to a kind you have not written to before, call
   `registry__get_kind_schema { "kind": "<kind>" }`. A missing required property comes back
   as a generic 400 that does **not** name the field.

## Read the kind first

```
registry__get_kind_schema {
  "kind": "agent",
  "session_id": "mcp_session_01J...",
  "rationale": "checking what an agent requires before creating one"
}
```

You will find that almost nothing is required. `name` and `workspace_id` are it -
everything about what the agent *does* is optional, so a catalogue entry with nothing but a
name is valid and useful.

## One agent

```
registry__create_artifact {
  "kind": "agent",
  "metadata": {
    "name": "release-notes-writer",
    "workspace_id": "workspace_01J...",
    "description": "Drafts release notes from merged PRs.",
    "instructions": "You write release notes. Group by user-visible change.",
    "model_id": "<a model handle>",
    "max_step_count": 20,
    "agent_framework_id": "agent_framework_01J..."
  },
  "session_id": "mcp_session_01J...",
  "rationale": "cataloguing the release-notes agent so its runs can be attributed"
}
```

Three things that refuse:

- **A present-but-blank `instructions` or `model_id` is a 400** telling you to omit the key
  instead. Absent means "not configured"; empty string means nothing.
- **`agent_framework_id` and `agent_provider_id` must resolve** to a live, non-archived
  artifact. List `agent_framework` first - the gateway seeds a catalogue of them
  (`claude-agent-sdk`, `openai-agents-sdk`, `langgraph`, `langchain`, and others).
- **`public_url` must be `http` or `https`.** It is stored and echoed and **never**
  dereferenced by the gateway.

`profile_image_uri` is server-owned. Sending it is ignored on create and restored from the
stored row on update.

## Many agents

```
extension__agent__create_bulk {
  "agents": [ { "name": "...", "workspace_id": "...", ... }, { ... } ],
  "session_id": "mcp_session_01J...",
  "rationale": "cataloguing the four agents in this repo"
}
```

**`registry__create_artifacts` refuses `agent` outright**, and the reason is worth knowing
rather than working around: each agent create mints a reveal-once machine credential, and a
bulk response has no per-item channel to hand one back on. So bulk agents is a sequential
fan-out, not a batch.

**No credentials come back from this tool, by design.** A tool result goes into your
context *and* into the permanent audit record, which is the wrong place for a secret twice
over. If an agent needs to authenticate as itself, issue it a credential afterwards as a
separate, deliberate step.

One slot per agent, in order, each with its own status. Retry only the failed slots -
resending everything duplicates the ones that worked.

## Register the harness you are running in

A `harness` is one tenant's registration of a coding tool. Nothing seeds one: the customer
creates the row for the tool they actually use.

Two properties are **both** required, and the first is immutable after create:

- `harness_vendor_id` - from the seeded `harness_vendor` catalogue
- `default_model_provider`

Detail: `skills/atlan-author-agents/references/harness-and-session.md`.

## Updating an agent

One verb, with one sharp edge around absent keys.
See `skills/atlan-author-agents/references/update-an-agent.md`.

## Saying which skills an agent uses

**Write the agent's `skills` manifest**, on the create or in a patch:

```
"skills": [ { "skill_id": "skill_01K..." }, { "skill_id": "skill_01M...", "version_ordinal": 3 } ]
```

Each entry pins one skill version. Omit `version_ordinal` to pin the skill's **current
release**; the registry fills in the ordinal and the version's `source_digest`. A pin never
moves on its own - a skill publishing a new release changes no agent until you write a new
pin, and on a released agent that write is a new agent version.

The `uses_skill` edges follow the manifest: the registry derives one per pinned skill and
archives the rest, shortly after the write. Confirm the pins with `registry__get_artifact` and
the edges with `registry__get_related_artifacts`.

An agent that has **never** written a manifest still carries hand-curated `uses_skill` edges
(`registry__relate_artifact`); the first manifest replaces them. The full edge table and its
refusals: `skills/atlan-author-agents/references/agent-edges.md`.

## Recording what a run produced

That is a different kind and a different recipe:
`skills/atlan-record-outputs/references/record.md`.
