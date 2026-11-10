---
name: atlan-author-agents
description: "Register or update an agent in Atlan: link a repository's agent and its skills, register this tool as a harness, or prepare its credential. Use when adding or updating an agent in Atlan. Not for publishing skills (atlan-author-skills)."
license: Apache-2.0
---

# Author agents

Choose the workflow:

- **Register an agent without a checkout:** [registration](references/register.md).
- **Link a repository agent, publish locally or through CI, and set up runtime identity:**
  [repository linking](references/link-repository-agent.md), then its
  [Git-backed](references/link-git-backed-agent.md) or [local](references/link-local-agent.md)
  flow, using the [manifest contract](references/agent-manifest.md).
- **Update an existing agent:** [updates](references/update-an-agent.md).
- **Register the coding tool running this session:** [harness and session](references/harness-and-session.md).
- **Review skill pins and relationships:** `skills/atlan-get-started/references/edges.md`.

In search mode, the default, run each tool named here through the runner for what it does
(`mcp__call_read_tool` for a read, `mcp__call_write_tool` for a create or update,
`mcp__call_tool` for anything destructive), with `session_id` and a first-person `rationale`
beside `name` and `arguments`; your first call opens
the session by sending `objective`. Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner. Send independent reads
together in one turn, or as one `mcp__call_read_tools` call (up to 8) when the host lists it. A create names its `workspace_id`: the one the user names, else
`.atlan/share.json`'s at the project root, else theirs from `registry__list_workspaces` with
`{"kind": "user_default"}`. Write first, and read `registry__get_kind_schema` only after a
refusal that names no field. Open `skills/atlan-get-started/references/calling-tools.md` only for
a failed call, a limit, a source key or full mode. Replies lead with the result, in four sentences or fewer and under 80 words (a table or a list the user asked for aside). Check each: no greeting, apology or closing offer; counts rather than lists, handles only when asked; one next step, not a menu; nothing about how tools work or calls skipped or retried. Ask before every write.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

When the checkout holds no agent (no declaration, agent folder or source), say so in one
sentence and ask where the agent is defined, without listing the paths you searched.

A coding harness is distinct from the autonomous agent the repository implements.
Read back workspace, exact repository and declaration path before reusing an agent;
a name match alone is not identity. Preserve verified local or Git-backed publication.
Git-backed publication starts in CI via `atlan-connect-repo`, then resumes runtime setup here.

Credentials must never enter model context or tool output. Use the CLI's private output-file
path, ensure it is ignored, untracked and private, and never read it. Issuing one needs that
CLI and a shell: in Claude chat or Cowork, register the agent and its edges here, and tell the
user the credential step runs from a terminal with the CLI. Do not recreate an
agent to recover a missing credential. Issue a runtime credential only when requested.

The app owns the native Atlan runtime; other providers may remain catalog-only. Registering
an agent does not deploy it or prove traces are arriving. Record produced deliverables
with `atlan-share` and upload owned files with `atlan-content`.
