# Installing a registry skill

Open this only when the user asked to install, add, download or keep a skill. You have already
read the artifact (step 2): its `artifact_status` is `active` and `metadata.files` is the
pinned file list. First check the surface can hold the files (below). Prefer aireg on the
user's own computer; use the MCP route when aireg is missing or the user declines it.

## Where installing works

| Where you are | Route |
|---|---|
| Claude chat on the web, desktop or mobile, or any host with no shell | Use it now. Installing is not possible here, so do not try. |
| A cloud session: Claude Code on the web, a CI runner, any container discarded when the session ends | Use it now. A project install lasts only if the user commits it, so offer that instead of a user-folder install. |
| A terminal, IDE, desktop or Cowork session on the user's own computer | Use it now, or install it (below). |

No tool that runs commands or writes files means no shell. A cloud session usually says so in
its environment notes; when unsure, ask where they are running.

## With aireg

aireg is Atlan's registry CLI. Its installer currently names the command `brain`; the two take
the same commands. It verifies every file against the registry's digests and keeps a user-scope
install current in the background, so prefer it for a lasting install on the user's computer.
It is an upgrade, never a requirement.

1. Check for it: `command -v aireg || command -v brain`. If neither is there, offer the
   installer or the MCP route below, and wait for the user to choose. Offer the
   installer as a download the user reads before running, never piped into a shell:
   `curl -fsSL -o install.sh https://app.atlan.com/brain/cli/install.sh`, then `sh install.sh`.
   Where the user's or their organization's rules forbid running downloaded installers, offer
   only the MCP route. Never run the installer without a yes.
2. Find the Atlan registry's local name: `aireg registry list -g --json`, the entry whose
   source is `https://api.atlan.com`. If there is none, connecting the CLI is the user's own
   sign-in step; offer the MCP route instead.
3. Install: `aireg add -g <registry>/skill/<name> --yes --json`, with `<name>` the skill's
   frontmatter `name`. If the CLI reports more than one match, `aireg registry show <registry>`
   tags each skill with its workspace; use `<registry>/<workspace>/skill/<name>`.
4. Report what the JSON says was installed, and tell the user their tool may need a new
   session to load it.

## Through MCP

**Confirm the destination with the user first.** Use the skill's frontmatter `name` as the
folder name, under the folder the user's tool loads skills from:

| Tool | Project (shared through the repo) | Just this user |
|---|---|---|
| Claude Code | `.claude/skills/<name>/` | `~/.claude/skills/<name>/` |
| Codex, and most Agent Skills tools | `.agents/skills/<name>/` | `~/.agents/skills/<name>/` |
| Cursor | `.cursor/skills/<name>/` | `~/.cursor/skills/<name>/` |

If the folder already holds a skill of that name, show the difference and ask before
replacing it.

Then read **every** other entry in `metadata.files` (`SKILL.md` already came back as
`included_file` in the skill's step 2):

1. Call `registry__get_artifact` on the same version with `include_files`, a list of up to 8
   `path`s. Group the paths by the manifest's `size_bytes` so each call carries at most about
   64 KiB: many hosts cut a long tool result short, and the gateway stops at 512 KiB. A bundle
   that needs more calls sends them as parallel calls in one turn. Each file comes back in
   `included_files`, in the order asked, at the version the manifest pins.
2. An entry with `code: "budget_exceeded"` has no content: read it in a further call. An entry
   with any other `error` is handled as **When a file will not come through** says.
3. If `get_artifact` refuses `include_files`, the gateway predates it: leave `include_files`
   out for the rest of the session and read the same groups with `registry__get_file_contents`,
   `"files"` holding up to 8 `{file_id, version_ordinal}` entries (`version_ordinal` set to the
   entry's `file_version_ordinal`). Its results come back in the order asked, and an entry's
   `error` or `budget_exceeded` is handled as in step 2. Only when that tool is not served
   either, read each file with `registry__get_file_content` (the same two arguments), as
   parallel calls, up to 8 in one turn.
4. Write each returned `content` to `<folder>/<path>`, keeping the bundle's own paths. Refuse
   any path that is absolute or contains `..`.
5. Record the install in `.atlan-installed.json` in the skills folder that holds `<folder>`
   (beside it, never inside it), merging this skill's entry into any record already there, as
   `compare-versions.md` shows. This is what a later "are my skills up to date?" reads.

Before writing, show the user the file list. Point out any script (`*.sh`, `*.py`, `*.js`,
anything under `scripts/`) and say you have not run it. Never run a downloaded script as
part of installing.

**When a file will not come through the tool.** `registry__get_file_content` returns text only,
up to 256 KiB. An image, an archive or a larger file is refused with a message naming the
HTTP route `GET /file/v1/files/{file_id}/content`. Your MCP connection's sign-in does not give
you a token for that route, so do not try to obtain one. Tell the user which files are
missing, and install without them only if they agree: a skill that references a missing file
will misbehave when it reaches that step. aireg, where it is installed, fetches every file.

Then say which skill and version you installed (`name`, handle, `version_ordinal` of
`SKILL.md`), where, and how many files. Tell the user their tool may need a new session to
load it. To update later, follow `compare-versions.md`: it checks the record against `latest`
and reinstalls only on a newer version.
