# Phase 5: the agent

The agent is both of these:
- a **registered agent** in the workspace (the identity its sessions, traces and eval
  experiments hang off);
- a **Claude Code subagent** `.claude/agents/<slug>.md` in the user's project (how it runs).

## 1. Propose the spec

Draft it from the brief:
- `name`: the slug
- `display_name`
- `description`: one line on who it serves
- `model`: default `claude-opus-5-5`, the current most capable model. Offer a cheaper model only
  if the user asks about cost.
- **System prompt**: fill in `skills/atlan-analytics/references/eval/agent-template.md` with the
  brief's audience, domain, out-of-scope list, the table scope, and the workspace name.
  Keep its published metric and query reads, unreleased-query exclusions and exact revision citations.

Show it, then ask:
- `<slug> · agent · the spec`
- **Register it as shown (Recommended)**
- **Edit the prompt first**
- **Change the model**

## 2. Register it

1. Call `registry__get_kind_schema` for `agent`. Required fields vary by deployment.
2. Call `registry__create_artifact` with `kind` `agent`, `metadata.workspace_id` = the project
   workspace, and the spec. `instructions` and `model_id` must be non-blank; omit a field
   rather than sending an empty string. Keep the returned id as `ids.agent_id` in state.
3. Link the context the agent relies on. Pin every rule skill it should always load in the
   agent's `skills` manifest (`[{"skill_id": "skill_…"}]`, in the create or a patch); the
   registry pins the current release and derives the `uses_skill` edge.
   Read back the manifest and fill the template's rule release id from that pin. Do not
   substitute the rule's discovery head if the pinned release cannot be resolved.

## 3. Write the subagent file

1. Discover the exact tool names this session exposes, as their full prefixed names. Don't
   hard-code prefixes. Read them from the session.
   - **Full mode:** `mcp__initialise`, `registry__search_artifacts`, `registry__get_artifact`
     and `registry__get_file_content`. `mcp__initialise` is required: every other registry
     tool refuses a call without the `session_id` it returns.
   - **Search mode:** only `mcp__call_tool` (the agent calls the registry reads through it by
     name) and `mcp__describe_tools` (to read their schemas).
   - The warehouse query tool from state. When it is reached through Atlan in search mode, it
     goes through `mcp__call_tool` too and adds no tool of its own.
   - `registry__get_file_content` is how the agent reads a linked rule skill's `SKILL.md`;
     `registry__get_artifact` on a skill returns only its file manifest.
   - Confirm the discovered `registry__get_artifact` supports `channel` and `release_id`.
     If the deployment lacks these selectors, report that published-context consumption is
     unavailable; do not install a head-reading substitute for the answering agent.
2. Preview `.claude/agents/<slug>.md` in the user's project:

   ```markdown
   ---
   name: <slug>
   description: <description> Talk-to-data agent over <workspace>; answers with SQL and the definitions it used.
   tools: <the tools from step 1>
   model: <model>
   ---
   <system prompt>
   ```

   **Only those tools**: registry and warehouse reads. No Read, Bash, Write, Grep, Glob or web
   tools. That is what keeps the eval answers on disk structurally out of the agent's reach,
   and what makes it behave like the deployed agent.

   In search mode the list is weaker: `mcp__call_tool` can run any tool the user can, writes
   included, so the read-only limit rests on the prompt. Say so in the preview. The eval still
   catches a breach: `EVAL record` reads the tool name inside each `mcp__call_tool` and marks a
   run that called a write tool or touched eval data `invalid_contaminated`.
3. On a yes, write the file and record `subagent_path` in state.
4. Check that the subagent type is available to the Agent tool. Claude Code loads new agent
   files at session start. If it isn't listed:
   - tell the user to restart Claude Code in this directory and run `/atlanai-analytics:atlan-analytics`;
   - state is saved, and the run resumes at the smoke test;
   - end with NEEDS_CONTEXT.

**STOP gate.** "Agent <display_name> registered (<agent_id>) and installed as the subagent
`<slug>`, with tools: registry session, search and read, skill file read, warehouse query." Set `phase` to `smoke`.
