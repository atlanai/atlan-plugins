---
name: atlan-author-agents
description: "Register an agent in Atlan or update one: link an agent that lives in a repository with the skills it uses, register the coding tool running this session as a harness, or prepare an agent's private runtime credential. Use when the user wants to add, register, catalogue or update an agent in Atlan, record which skills an agent uses, or issue an agent's runtime credential. Not for publishing skills (atlan-author-skills) or setting up GitHub CI publication (atlan-connect-repo)."
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
the session by sending `objective`. **Before your first call** in
`skills/atlan-get-started/references/calling-tools.md` covers the rest: full mode, the workspace
and the kind schema. Replies lead with the result in a few lines, and ask before every write.

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
with `atlan-record-outputs` and upload owned files with `atlan-content`.
