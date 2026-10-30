---
name: atlan-continue
description: Pick up an Atlan artifact where someone left off - an output, skill, agent, knowledge bundle, file or other item named by its handle or title, usually handed over by an "Open in Claude", "Continue in" or "Copy prompt" button in the Atlan dashboard. Use when a prompt asks to continue, open, resume or work on something in Atlan and names a handle such as `out_…` or `skill_…`.
license: Apache-2.0
---

# Continuing an Atlan artifact here

The Atlan dashboard hands work to a coding agent as a short prompt carrying an artifact's
handle, such as `Use Atlan to continue "Q3 churn analysis" (out_…)` or
`/atlan:continue out_…`. The handle is the whole hand-off: everything else is read live from
the registry. Nothing here writes until the user says what they want.

Open the session as `atlan-get-started` shows, with an objective such as
`Continue work on "<title>" handed over from the Atlan dashboard`.

## 1. Resolve the artifact

```
mcp__call_tool {
  "name": "registry__get_artifact",
  "arguments": { "artifact_id": "<handle>" },
  "session_id": "mcp_session_…",
  "rationale": "Loading the artifact the user handed over from the Atlan dashboard."
}
```

- **Only a title?** Search for it (`registry__search_artifacts`, `semantic: true`) and confirm
  the match with the user before going on.
- **Not found** means it does not exist, the user cannot read it, or this connection is a
  different Atlan environment (production against dev). Say which checks you made; do not
  guess at its content.

## 2. Load the context its kind needs

| Kind | Read next |
|---|---|
| `output` | The file it is pinned to (`registry__get_file_content`), then `registry__get_related_artifacts` for the run that produced it and the skills it `used_skill` |
| `skill` | Its `SKILL.md` through `atlan-read-content`; `artifact_status` first, since a `blocked` skill is withheld |
| `agent`, `harness` | Its properties and `registry__get_related_artifacts` for the skills it `uses_skill` |
| `knowledge` | Its bundle, as the registry's `atlan-author-knowledge` prompt describes |
| `file` | `registry__get_file_content` |
| Anything else | `registry__get_kind_schema` for what the kind means, then its properties |

Read the entry point and stop. Open more only when the next step needs it.

## 3. Brief the user, then follow their lead

In a few lines: what the artifact is, who owns it and in which workspace, its current
version, and what it is connected to. If the prompt already says what to do next, do that.
Otherwise offer the two or three next steps that fit the kind:

- an `output`: revise it and record the result as a new output (`atlan-record-outputs`);
- a `skill`: use it now or install it (`atlan-use-skills`), or publish an edited version
  (`atlan-author-skills`);
- an `agent`: update its skills or configuration (`atlan-author-agents`).

Ask before any write, and say where the result will land.
