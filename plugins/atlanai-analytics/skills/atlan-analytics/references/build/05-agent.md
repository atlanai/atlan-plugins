# Phase 5: the agent

The agent is both of these:
- a **registered agent** in the workspace (the identity its sessions, traces and eval
  experiments hang off);
- a host runner: a **Claude Code subagent** `.claude/agents/<slug>.md`, or the isolated
  **Codex/Claude CLI runner** described in [host execution](../eval/hosts.md).

## 1. Propose the spec

Draft it from the brief:
- `name`: the slug
- `display_name`
- `description`: one line on who it serves
- `model`: default to the most capable model the agent kind accepts, as its schema or the
  workspace's model list names it, not a name assumed from this file. Offer a cheaper model only
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

1. Call `registry__create_artifact` with `kind` `agent`, `metadata.workspace_id` = the project
   workspace, and the spec. Keep `instructions` non-blank. Supply `model_id` only when the
   agent uses a fixed model; a host-selected model can omit it when the live schema permits.
   Required fields vary by deployment: when the create is
   refused over a property, fix the `field` it names (or read `registry__get_kind_schema` for
   `agent` when it names none) and send it once more. Keep the returned id as
   `ids.agent_id` in state.
2. Link the context the agent relies on. Pin every rule skill it should always load in the
   agent's `skills` manifest (`[{"skill_id": "skill_…"}]`, in the create or a patch); the
   registry pins the current release and derives the `uses_skill` edge.
   Read back the manifest and fill the template's rule release id from that pin. Do not
   substitute the rule's discovery head if the pinned release cannot be resolved.

## 3. Choose the host runner

In Codex, or when Claude's project subagent is unavailable, use
[the question-only runner](../eval/hosts.md). Keep the registered agent ID, its approved
instructions and the released context snapshot in the project. Set `runner` to
`codex-question-only` or `claude-question-only`, then continue to smoke. No new MCP server
or privileged host configuration is installed. The remaining file steps apply to Claude's
Agent-tool runner only.

### Write the Claude subagent file

1. Discover the exact tool names this session exposes, as their full prefixed names. Don't
   hard-code prefixes. Read them from the session.
   - **Full mode:** `mcp__initialise`, `registry__search_artifacts`, `registry__get_artifact`
     and `registry__get_file_content`. `mcp__initialise` is required: every other registry
     tool refuses a call without the `session_id` it returns.
   - **Search mode:** only `mcp__call_read_tool` (the agent calls the registry reads through it
     by name) and `mcp__describe_tools` (to read their schemas). Never `mcp__call_write_tool` or
     `mcp__call_tool`: the read runner refuses anything that is not read-only, so the gateway
     itself keeps the agent to reads.
   - The warehouse query tool from state. When it is reached through Atlan in search mode, it
     adds no tool of its own. Its runner is `warehouse.runner` in state; if that is missing,
     describe the tool and read its `run_with`. If that is
     `mcp__call_read_tool`, nothing more is needed. If it names another runner (the server
     marks its query tool as a write, or gives no hints), the agent also needs that runner, and
     step 2 says what that costs.
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

   In search mode with only `mcp__call_read_tool`, the gateway refuses every write, so the
   limit holds as it does in full mode. If the warehouse tool needed `mcp__call_write_tool` or
   `mcp__call_tool`, the list is weaker: that runner can run any tool the user can at its tier,
   writes included, so the read-only limit rests on the prompt. Say so in the preview, naming
   the runner and why. Either way the eval catches a breach: `EVAL record` reads the tool name
   inside each runner call and marks a run that called a write tool or touched eval data
   `invalid_contaminated`.
3. On a yes, write the file and record `subagent_path` in state.
4. Check that the subagent type is available to the Agent tool. Claude Code loads new agent
   files at session start. If it isn't listed:
   - tell the user to restart Claude Code in this directory and run `/atlanai-analytics:atlan-analytics`;
   - state is saved, and the run resumes at the smoke test;
   - explain the one missing input in natural language.

**STOP gate.** "Agent <display_name> registered (<agent_id>) and installed as the subagent
`<slug>`, with tools: registry session, search and read, skill file read, warehouse query." Set `phase` to `smoke`.
