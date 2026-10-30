---
description: Register an agent in the Atlan catalogue.
argument-hint: "[agent-name] [what it does]"
---

Register an agent named $1 in Atlan. It does: $2

Steps:

1. `mcp__initialise` if you have no `session_id` yet.
2. `registry__list_workspaces` for the `workspace_id`. If there is more than one, ask which.
3. `registry__get_kind_schema { "kind": "agent" }` if you have not written an agent before.
   Only `name` and `workspace_id` are required.
4. If you want to set `agent_framework_id`, list the seeded catalogue first:
   `registry__list_artifacts { "kind": "agent_framework", "limit": 50 }`.
5. `registry__create_artifact` with `kind: "agent"`.

Omit any field you have no value for - a present-but-blank `instructions` or `model_id` is
a 400 telling you to omit it. Do not send `profile_image_uri`; it is server-owned.

For several agents at once use `extension__agent__create_bulk`, not `registry__create_artifacts` -
the generic bulk create refuses this kind outright, because each create mints a
reveal-once credential and a batch has no channel to return one on. No credentials come
back from either path, by design.

Report the resulting `agent_...` handle.

If anything refuses, read `skills/atlan-author-agents/SKILL.md`.
