---
name: atlan-use-skills
description: Use a skill from the Atlan registry in the current task, or install it into this project or the user's own skills folder so it loads in future sessions. Use when the user asks to use, apply, install, download, pull or sync a skill their organization keeps in Atlan.
license: Apache-2.0
---

# Using registry skills here

A skill in Atlan is a versioned bundle: a `SKILL.md` plus the files it references. There are
two ways to use one, and the user's words decide which:

- **Use it now**: read its `SKILL.md` and follow it for this task. Nothing is written to disk.
  Do this when the user says "use", "apply" or "follow".
- **Install it**: write the bundle into a skills folder so the user's tool loads it in later
  sessions. Do this when the user says "install", "add", "download" or "keep".

Calls below are search-mode `mcp__call_tool` calls; see `atlan-get-started` for the session.

## 1. Find the exact skill

Search with `kind: "skill"` (see `atlan-find-artifacts`). If more than one result fits, show
them and let the user pick: two skills with similar names are different artifacts with
different instructions.

## 2. Read its manifest

```
mcp__call_tool {
  "name": "registry__get_artifact",
  "arguments": { "artifact_id": "skill_01j…" },
  "session_id": "mcp_session_…",
  "rationale": "Reading the skill's file manifest before installing it in this project."
}
```

- `artifact_status` of `blocked` means the bundle failed Atlan's security scan and its files
  are withheld. Stop and tell the user; do not look for the content elsewhere.
- `metadata.files` lists every file: `path`, `file_id`, `file_version_ordinal`, `size_bytes`,
  `media_type`. This is the pinned snapshot of the version you are reading.

## 3. Use it now

Read `SKILL.md` with `registry__get_file_content`, passing the `file_id` and the
`file_version_ordinal` from the manifest as `version_ordinal`. Follow it, and open a file
under `references/` only when `SKILL.md` sends you there. `atlan-read-content` has the loop
and its traps.

## 4. Install it

**Confirm the destination with the user first.** Use the skill's frontmatter `name` as the
folder name, under the folder the user's tool loads skills from:

| Tool | Project (shared through the repo) | Just this user |
|---|---|---|
| Claude Code | `.claude/skills/<name>/` | `~/.claude/skills/<name>/` |
| Codex, and most Agent Skills tools | `.agents/skills/<name>/` | `~/.agents/skills/<name>/` |
| Cursor | `.cursor/skills/<name>/` | `~/.cursor/skills/<name>/` |

If the folder already holds a skill of that name, show the difference and ask before
replacing it.

Then, for **every** entry in `metadata.files`:

1. Read it with `registry__get_file_content` (`file_id`, and `version_ordinal` set to the
   entry's `file_version_ordinal`, so every file comes from the same version).
2. Write the returned `content` to `<folder>/<path>`, keeping the bundle's own paths. Refuse
   any path that is absolute or contains `..`.

Before writing, show the user the file list. Point out any script (`*.sh`, `*.py`, `*.js`,
anything under `scripts/`) and say you have not run it. Never run a downloaded script as
part of installing.

**When a file will not come through the tool.** `registry__get_file_content` returns text only,
up to 256 KiB. An image, an archive or a larger file is refused with a message naming the
HTTP route `GET /file/v1/files/{file_id}/content`. Your MCP connection's sign-in does not give
you a token for that route, so do not try to obtain one. Tell the user which files are
missing, and install without them only if they agree: a skill that references a missing file
will misbehave when it reaches that step.

## 5. Report

Say which skill and version you installed (`name`, handle, `version_ordinal` of `SKILL.md`),
where, and how many files. Tell the user their tool may need a new session to load it. To
update later, read the artifact again and compare versions; reinstall only on a newer one.

## Publishing changes back

Editing an installed copy changes only the local copy. To share a change, publish a new
version with `atlan-author-skills`, reusing the skill's existing source identity.
