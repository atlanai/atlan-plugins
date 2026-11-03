---
name: atlan-author-skills
description: Publish a skill to Atlan as a versioned bundle or a new version of one, or make a new skill for the team and publish it. Use when the user wants to publish, upload, share, push or release a skill to Atlan or the registry, update a skill already there, or create a skill for their team or organization. Not for loading or installing a registry skill here (atlan-use-skill), reading a skill's files (atlan-content), publishing from GitHub CI (atlan-connect-repo) or a skill kept only on this computer (your tool's own skill creator).
license: Apache-2.0
---

# Author skill bundles

Pass a `session_id` and a first-person `rationale` on every tool call, reads included: the
gateway accepts a read without one, but the audit trail needs it. In search mode, the default, the first
`mcp__search_tools` or `mcp__call_tool` opens the session by sending `objective`, and each
`registry__…` or `extension__…` tool named here runs through `mcp__call_tool` with `session_id` and `rationale`
beside `name` and `arguments`. In full mode, call `mcp__initialise` first. Replies lead with the
result in a few lines, and ask before every write.

## Quick path: one new text skill

Most requests are one new skill of a few text files. Run these steps without reading the
references first. The tool names and arguments below are exact: run them through
`mcp__call_tool` without `mcp__describe_tools` first, and without loading another runner,
whatever `run_with` a search answer names.

1. **Search** with the user's words: `registry__search_artifacts` with `query`, `kind: "skill"`
   and `limit: 3`. Judge each hit by its name and description; a hit with `match.keyword` false
   and `match.similarity` under 0.45 is noise. Nothing close: say so in one line and go on. An
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
   the user's own from `registry__list_workspaces` with `{"limit": 1}` (`personal_workspace_id`).
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
   Inline `files` copy each file's bytes as they are on disk, never retyped from memory. The
   `idempotency_key` is the skill's name and eight random letters or digits, new for each
   publish; if the server refuses it by name, nothing was written: send the call without it.
6. **Report** the slot in a few lines: `active` with its release (a new skill ships to `latest`
   as `0.1.0`), its `id`, and that the local folder is a copy that reaches Atlan only as a new
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
- **Review relationships:** [skill edges](references/skill-edges.md).

Read the current entry point and referenced files before updating. Publish the complete
bundle through the skill-specific upload/create procedure and preserve its exact source
identity. Do not replace it with a generic artifact append, rename or recreate to bypass
a source conflict. Files belong inside the bundle and pin exact file ordinals.

For GitHub publication and repository provenance, use `atlan-connect-repo`;
CI publishes a newly Git-backed bundle first. Read file bytes with `atlan-content`.
