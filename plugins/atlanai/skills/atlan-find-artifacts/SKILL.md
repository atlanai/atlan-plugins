---
name: atlan-find-artifacts
description: Search Atlan for skills, agents, knowledge, MCP servers, files, outputs and runs. Use when asked what Atlan has or holds on a topic, for our definition of a term, who owns or uses an artifact, or what agents ran. Not for data questions.
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
When the host lists `mcp__call_read_tools`, send up to 8 independent reads as one call,
`{ "calls": [{ "name", "arguments" }, …], "session_id", "rationale" }`; otherwise in parallel.
Replies lead with the result, in four sentences or fewer and under 80 words (a table or a list the user asked for aside). Check each: no greeting, apology or closing offer; counts rather than lists, handles only when asked; one next step, not a menu; nothing about how tools work or calls skipped or retried.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

## Pick the read

| Question | Tool |
|---|---|
| "Is there a skill for X?" "Find the agent that does Y" | `registry__search_artifacts` |
| "What do we have?" "Find me skills" "How many?", with no task named | [Overview](#overview) with `registry__get_artifact_counts`, then a search for the user's task |
| "What changed recently?" "List all our MCP servers" | `registry__list_artifacts`: [Browse](#browse) |
| "Tell me about this one" | `registry__get_artifact` |
| "What uses it, what does it use?" "How often is it used?" | `registry__get_related_artifacts`, or run statistics, with `references/adoption.md` |
| "What does this skill or file actually say?" | `atlan-content` |
| "Gather what we know for this task" | `mcp__retrieve_artifacts` |
| "What is in this project?" | `registry__get_project`, then `registry__list_artifacts` with `project_id` (`references/kinds.md`) |
| "What can a kind hold?" | `references/kinds.md`, then `registry__get_kind_schema` for a kind it does not list |
| "Which workspaces can I see?" | `registry__list_workspaces` |
| "What did my agents do yesterday?" "What ran, and did it fail?" | `registry__list_traces`, then `references/runs.md` |

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
- **Pass `limit`**: 10 is enough to choose from. Rows are summaries (name, description,
  workspace, status, handle); pass `full: true` only for a kind's own properties. A skill's
  files come from `registry__get_artifact`, not from a list.
- **Make each call once.** Answer follow-ups from what a result told you (names, handles,
  workspaces, statuses, `page.total`); call again only for a different page, scope or query.
- **Set `semantic: true`** when the user's wording may not match the artifact's. Leave it
  off for exact names.
- **Narrow with `kind`** when you know the type. Omit it to search every discoverable kind.
  `workspace_id` narrows to one workspace; `project_id` to the artifacts homed in or linked
  into one project.
- **Search returns approved artifacts.** Hits are `active` unless you pass
  `include_unreviewed: true`, which adds `pending_review` (held for its scan or a reviewer;
  its files cannot be read yet) and `blocked` (failed the security scan). Pass it only when
  the user looks for their own upload under review, and say each hit's `artifact_status`.
  A hit with `is_released: false` is a draft: say it is not released.
- **A hit is a suggestion, not a choice.** Show the ones that fit, each with its name, version
  and workspace, and let the user pick before anything loads, even when the request says "and
  use it". Never load a skill because it ranked first. The one exception is the user's own standing instruction to check Atlan before
  each task (an **Atlan registry skills** section in `AGENTS.md`): then `atlan-use-skill` loads
  a single clear match and names it in one line, and still asks when more than one fits.
- **Same name, different skill.** Copies of one artifact across workspaces are one hit, the
  rest under `also_in`; two different artifacts can still share a `name`. Name each one's
  workspace (`workspace_display_name`) so the user can tell them apart.
- **Weak hits are noise**, unless one's name and description plainly do the job: `weak` only
  counts shared words. Report that one; leave the rest out.
- **No results is an answer.** Say so plainly and offer a broader search (no `kind`,
  `semantic: true`) before concluding nothing exists. An artifact the user cannot read looks
  the same as one that does not exist. When `page.has_more`, the next page is `offset:
  page.next_offset`.

## Overview

A broad ask ("find me skills", "what do we have?") wants the shape of the catalogue, not its
most recently changed rows.

1. **Count.** One call gives the total and the breakdown:
   `registry__get_artifact_counts { "kind": "skill", "group_by": "workspace_id" }` returns
   `total`, the number the user can see, and the largest groups with their counts. Group by
   `kind` to answer "what do we have?" across every kind, or by `project_id`, `owner_id` or
   `artifact_status`.
   **Platform kinds are not the catalogue.** `app`, `app_action`, `harness_vendor`, `model` and
   `agent_framework` rows are the platform's own (often 80 of 100). Subtract them from `total`
   and give the remainder as the answer, adding at most "not counting N platform records".
   Name or count them by kind only when the user asks about apps or models. A workspace
   group carries its `workspace_display_name`: name workspaces by it.
2. **Ask or narrow.** If the user named no task, give the count and the breakdown and ask what
   they want to do. If they named one, search for it (`semantic: true`, `limit: 10`).
3. **Say what you looked at.** Separate the catalogue's total from the rows you read: "392
   skills in total; these 10 are the closest to preview deploys", or "the 10 most recently
   changed", never a sample presented as the whole.

## Browse

`registry__list_artifacts` returns rows, most recently changed first, each with
`updated_at` and `workspace_display_name`, and `page.total`. It takes no sort or date argument.

- **Everything of one kind** ("list our MCP servers"): one call
  with `kind` and `limit: 50`, then `offset` pages until `page.total` rows are read. Never
  enumerate a kind with repeated searches: search ranks, it does not list.
- **What changed since a date:** `limit: 20` pages, stopping at the first row older than the
  window.

## Gather context for a task

When the user wants what the organization knows about a task rather than one artifact ("what
do we have on churn?"), read `references/gather-context.md`: one
`mcp__retrieve_artifacts` call through `mcp__call_read_tool` searches skills, metrics, ontology
and knowledge together.

## Read one

`registry__get_artifact { "artifact_id": "skill_01j…" }` returns the envelope (`name`,
`description`, `workspace_id`, owner, version) and the kind's own properties. For a skill,
`metadata.files` lists its bundle; a `blocked` skill failed its security scan and its files
are withheld. Pass `channel` (such as `latest`) or a `release_id` only when the user asks for
a published release rather than the newest version.

## Present results

Show a short table of name, kind, workspace and a one-line description,
with each handle, so the user can ask about one by name. Mark a skill whose `name` matches one
already installed here (your tool lists its skills in this conversation) as installed, so the
user does not install it twice; whether that copy is current is `atlan-use-skill`'s check.
**When the request was to find something and use or run it, and gave no handle, the search
ends the turn:** show the hits that fit and ask which one. Load nothing yet (no
`include_file`, no `atlan-use-skill`). Once the user picks, hand that handle to
`atlan-use-skill`. Otherwise offer the one next step that fits: read it (`atlan-content`), use
it here (`atlan-use-skill`), or call its tools (`atlan-use-mcp-servers`).
