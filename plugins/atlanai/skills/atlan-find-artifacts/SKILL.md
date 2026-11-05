---
name: atlan-find-artifacts
description: Search and browse the Atlan registry for skills, agents, knowledge, MCP servers, files and outputs, and say how they relate. Use when the user asks what exists in Atlan, what a workspace or project holds, whether the organization already has a skill or agent for something, who owns an artifact or what depends on it.
license: Apache-2.0
---

# Finding things in the Atlan registry

Everything in the registry is an **artifact**: a versioned item of one **kind**, homed in one
**workspace** and optionally one **project**, with a handle shaped `<kind>_<id>`
(`skill_01j…`, `agent_01k…`). Reads are safe: nothing here writes.

Calls below are written in search mode, each through the runner its tool needs: reads use
`mcp__call_read_tool`. The first call opens the session
by sending `objective` beside `name` and returns `mcp_session_id`; pass that as `session_id`,
and a `rationale`, on every later call. Both go beside `name` and `arguments`, never inside.
Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner.
Replies lead with the result: the first sentence is the answer or the step, counts rather
than lists, handles only when asked, one next step rather than a menu, and no greeting,
apology or closing offer.

## Pick the read

| Question | Tool |
|---|---|
| "Is there a skill for X?" "Find the agent that does Y" | `registry__search_artifacts` |
| "What do we have?" "Find me skills" "How many?", with no task named | [Overview](#overview) with `registry__get_artifact_counts`, then a search for the user's task |
| "What changed recently?" | `registry__list_artifacts` |
| "Tell me about this one" | `registry__get_artifact` |
| "What uses it, what does it use?" | `registry__get_related_artifacts` |
| "What does this skill or file actually say?" | `atlan-content` |
| "Gather what we know for this task": skills, metrics, ontology and knowledge together | `mcp__retrieve_artifacts` |
| "What is in this project?" | `registry__get_project`, then `registry__list_artifacts` with `project_id` |
| "What can a kind hold?" | `registry__get_kind_schema` |
| "Which workspaces can I see?" | `registry__list_workspaces` |

## Search

```
mcp__call_read_tool {
  "name": "registry__search_artifacts",
  "arguments": { "query": "deploy a branch to a preview environment", "kind": "skill", "semantic": true, "limit": 10 },
  "session_id": "mcp_session_…",
  "rationale": "Checking whether the organization already has a preview-deploy skill before writing one."
}
```

- **Write `query` as natural words** describing the thing, not a keyword list.
- **Pass `limit`**: 10 is enough to choose from, and lands every row in the conversation.
  Search and browse rows are summaries (name, description, workspace, status, handle). Pass
  `full: true` only when you need every column, such as a kind's own properties; a skill's
  files come from `registry__get_artifact`, not from a list.
- **Make each call once.** Keep what a result told you (each hit's name, handle, workspace and
  status, and `page.total`) and answer follow-ups from it. Never repeat an identical call in
  the same task; call again only for a different page, scope or query.
- **Set `semantic: true`** when the user's wording may not match the artifact's (it fuses
  meaning with keyword relevance). Leave it off for exact names.
- **Narrow with `kind`** when you know the type. Omit it to search every discoverable kind.
  `workspace_id` narrows to one workspace; `project_id` to the artifacts homed in or linked
  into one project.
- **Search returns approved artifacts.** Hits are `active` unless you pass
  `include_unreviewed: true`, which adds `pending_review` (held until its scan or a reviewer
  approves it; its files cannot be read yet) and `blocked` (failed the security scan). Pass it
  only when the user looks for their own upload under review. Still read `artifact_status` on
  every hit, and say which, rather than offering every hit as ready.
- **A hit is a suggestion, not a choice.** Show the ones that fit, each with its name, version
  and workspace, and let the user pick before anything loads. Never load a skill because it
  ranked first. The one exception is the user's own standing instruction to check Atlan before
  each task (an **Atlan registry skills** section in `AGENTS.md`): then `atlan-use-skill` loads
  a single clear match and names it in one line, and still asks when more than one fits.
- **Same name, different skill.** Two artifacts can share a `name` in different workspaces
  (a team's skill and someone's personal copy). Show the workspace and handle of each so the
  user can tell them apart.
- **No results is an answer.** Say so plainly and offer a broader search (no `kind`,
  `semantic: true`) before concluding nothing exists. An artifact in a workspace the user
  cannot read is indistinguishable from one that does not exist, by design.

## Overview

A broad ask ("find me skills", "what agents do we have?") wants the shape of the catalogue,
not its most recently changed rows, which is all a browse returns.

1. **Count.** One call gives the total and the breakdown:
   `registry__get_artifact_counts { "kind": "skill", "group_by": "workspace_id" }` returns
   `total`, the number the user can see, and the largest groups with their counts. Group by
   `kind` to answer "what do we have?" across every kind, or by `project_id`, `repo_id`,
   `owner_id` or `artifact_status`. Name each workspace from `registry__list_workspaces`.
   Gateways without that tool: `registry__list_artifacts { "kind": "skill", "limit": 1 }`
   returns the total as `page.total`.
2. **Ask or narrow.** If the user named no task, give the count and the breakdown and ask what
   they want to do. If they named one, search for it (`semantic: true`, `limit: 10`).
3. **Say what you looked at.** Separate the catalogue's total from the rows you read: "392
   skills in total; these 10 are the closest to preview deploys", or "the 10 most recently
   changed", never a sample presented as the whole.

## Kinds

`skill`, `agent`, `output`, `knowledge`, `mcp_server`, `file` and `repo` are the kinds you will
meet most; `references/kinds.md` says what each holds, for when the user asks. For an
unfamiliar kind, `registry__get_kind_schema { "kind": "…" }` describes it.

## Gather context for a task

When the user wants what the organization knows about a task rather than one artifact ("what
do we have on churn?"), read `references/gather-context.md`: one bounded
`mcp__retrieve_artifacts` call through `mcp__call_read_tool` searches skills, metrics, ontology
and knowledge together.

## Projects

A project is a container, not an artifact: it has no releases and no typed relationships.
`registry__get_project { "project_id": "project_…" }` returns its description, its
`knowledge_urls` and the workspace it is in. What is in it comes from
`registry__list_artifacts { "project_id": "project_…" }`, which lists artifacts homed in the
project or linked into it; add `kind` to include a kind hidden from cross-kind discovery, such
as `file`.

## Read one

```
mcp__call_read_tool {
  "name": "registry__get_artifact",
  "arguments": { "artifact_id": "skill_01j…" },
  "session_id": "mcp_session_…",
  "rationale": "Reading the preview-deploy skill's details to tell the user what it does and who owns it."
}
```

The result carries the envelope (`name`, `description`, `workspace_id`, owner, version) and the
kind's own properties. For a skill, `metadata.files` lists its bundle and `artifact_status`
says whether it is usable: a `blocked` skill failed its security scan and its files are
withheld. Pass `channel` (for example `latest`) or a `release_id` only when the user asks for
a published release rather than the newest version.

## Read the neighbourhood

`registry__get_related_artifacts { "artifact_id": "…" }` returns typed edges in both
directions: an agent's `uses_skill`, an output's `used_skill`, a skill's `sourced_from` repo.
An end marked `is_readable: false` exists in a workspace the user cannot read; say that
rather than guessing what it is. Edges are declared by their source kind, so to find the
agents that use a skill, read the skill's incoming edges rather than its outgoing ones.

## Present results

Lead with the answer. Show a short table of name, kind, workspace and a one-line description,
with each handle, so the user can ask about one by name. Mark a skill whose `name` matches one
already installed here (your tool lists its skills in this conversation) as installed, so the
user does not install it twice; whether that copy is current is `atlan-use-skill`'s check.
Offer the next step that fits:
read it (`atlan-content`), use it here (`atlan-use-skill`), or call its tools
(`atlan-use-mcp-servers`).
