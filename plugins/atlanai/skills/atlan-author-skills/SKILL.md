---
name: atlan-author-skills
description: Publish a skill to Atlan or a new version of one, or write a team skill and publish it. Use when asked to publish, upload, push or release a skill to Atlan. Not for loading a registry skill (atlan-use-skill) or GitHub CI (atlan-connect-repo).
license: Apache-2.0
---

# Author skill bundles

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

## Quick path: one new text skill

Most requests are one new skill of a few text files. Run these steps without reading the
references first. The tool names and arguments below are exact: run them through
`mcp__call_tool` without `mcp__describe_tools` first. It runs any tool, so the skill create
goes through it whatever `run_with` a search answer names; the searches and reads may use
`mcp__call_read_tool`.

1. **Search** with the user's words: `registry__search_artifacts` with `query`, `kind: "skill"`
   and `limit: 3`. Judge each hit by its name and description; a hit whose `match.quality` is
   `weak` is noise. Nothing close: say so in one line and go on. An
   `active` skill that does the job: offer it first, as [creating](references/create.md) step 1
   says.
2. **Draft.** A one-file skill the user described in a sentence or two: write its `SKILL.md`
   yourself, in the working directory (`.claude/skills/<name>/` in Claude Code,
   `.agents/skills/<name>/` in Codex). Frontmatter `name` is lowercase and hyphenated, at most 64
   characters; `description` says when to use it, with `Use when`. More files, tests, or a user
   who wants the interview: draft with [creating](references/create.md) step 2. A skill folder
   the user already has: skip to step 3.
3. **Ask once.** Show the draft, the workspace and "publish?" in one question. The workspace is
   the one the user named, else `workspace_id` from `.atlan/share.json` at the project root, else
   the user's own from `registry__list_workspaces` with `{"kind": "user_default"}`.
4. **Check.** With a shell, run `python3 <this skill's folder>/scripts/check_bundle.py <folder>`
   and fix what it lists.
5. **Create**, after the yes:

   ```
   mcp__call_tool {
     "name": "extension__skill__create",
     "arguments": {
       "skills": [{ "name": "startup-jokes", "workspace_id": "workspace_01J...", "idempotency_key": "startup-jokes-q8w2m4z1", "files": { "SKILL.md": "---\nname: startup-jokes\n..." } }],
       "wait_for_release": true
     },
     "session_id": "mcp_session_…",
     "rationale": "Publishing the skill the user approved."
   }
   ```

   When the skill's folder is on disk inside the working directory, send
   `"files_from": "<the folder, relative to the working directory>"` in place of `files`, so the
   publish helper sends the bytes. If the server refuses `files_from` and says to send `files`,
   nothing was written: send `files` instead, and keep to `files` for the rest of the session.
   A skill that exists only in the conversation has no folder: send it as `files`, even when
   the user asked for `files_from`, and say so in one clause. Never write a folder to disk just
   to use `files_from`.
   Inline `files` copy each file's bytes as they are on disk, never retyped from memory. The
   `idempotency_key` is the skill's name and eight random letters or digits, new for each
   publish; if the server refuses it by name, nothing was written: send the call without it.
6. **Report** the slot in a few lines: `active` with its release (a new skill ships to `latest`
   as `0.1.0` on its own once its scan passes, under the workspace's default release policy;
   that is the expected result, not a problem to flag), its `id`, and that the local folder is a copy that reaches Atlan only as a new
   version. Any other answer (`pending_review`, `blocked`, `409`, or a `502`, `503`, `504` or
   "service unavailable" that may have committed): follow [publishing](references/publish.md),
   step 4 and "When the reply is lost". A call that carried an `idempotency_key` is resent
   unchanged; one without it is read back before anything is sent again.

## Everything else

- **A skill with several files, tests or the skill creator's interview:**
  [creating](references/create.md).
- **Every publish route, limit and outcome:** [publishing](references/publish.md);
  read [the ZIP path](references/publish-zip.md) only for binaries, executables or large bundles.
- **Update a bundle:** [update a skill](references/update-a-skill.md).
- **Review relationships:** `skills/atlan-get-started/references/edges.md`.

Read the current entry point and referenced files before updating. Publish the complete
bundle through the skill-specific upload/create procedure and preserve its exact source
identity. Do not replace it with a generic artifact append, rename or recreate to bypass
a source conflict. Files belong inside the bundle and pin exact file ordinals.

For GitHub publication and repository provenance, use `atlan-connect-repo`;
CI publishes a newly Git-backed bundle first. Read file bytes with `atlan-content`.
