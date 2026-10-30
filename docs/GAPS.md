# What the registry is missing for an easy start

Found while building this plugin, against `main` of each repository on 30 October 2026. Each
item says what a new user hits, the evidence, and the smallest fix. Items marked **decision**
change an architectural or product boundary and need sign-off before anyone builds them.

| # | Gap | Who hits it | Owner | Size |
|---|---|---|---|---|
| 1 | The registry's recipes teach full mode; every install uses search mode | Every agent following an authoring recipe | agent-gateway, app-skill, app-agent | Small |
| 2 | Stale tool names in the skill app's recipes and tool description | Agents publishing a skill | app-skill | Small |
| 3 | Org skills reach Claude Code only, and only when an off-by-default flag is on | Codex, Cursor and Copilot users | agent-gateway (**decision**) | Large |
| 4 | No way for an agent to download a binary or large file | Installing a skill with images, archives or big references | agent-gateway | Medium |
| 5 | Uploads that are not presigned need a bearer token an MCP agent does not hold | Publishing from any OAuth MCP client | agent-gateway | Small |
| 6 | Two plugins register the same `atlan` server | Claude Code users of the desktop app | agent-gateway, desktop app (**decision**) | Small |
| 7 | Production lets only `@atlan.com` emails create an organization | Anyone arriving from a plugin directory | control-plane (**decision**) | Product |
| 8 | The CLI is `brain` in its installer and `aireg` in its adapter docs and in the repo-sync recipe | Agents setting up Git sync | registry-cli, app-skill | Small |
| 9 | No record of which surface a session came from | Measuring the plugin's adoption | app-mcp | Small |
| 10 | OAuth client registration needs checking for each directory's client | Every directory submission | control-plane | Verify |
| 11 | Recipes present on `main` are missing from dev's MCP resources | Dev users | app-skill, app-agent | Verify |

## 1. The recipes teach full mode; every install uses search mode

Search mode is the default since `mcp-2026-10-24-search-mode-default`
(app-mcp `docs/mcp.md`, "Search mode"), and both this plugin and the registry's own umbrella
plugin install the bare `/mcp` URL (app-mcp `mcp/src/lib.rs`, `static_contribution`). In search
mode `tools/list` returns four tools and `mcp__initialise` is not listed, so a host cannot call
it. Yet every contributed recipe opens with `mcp__initialise` and shows direct
`registry__…` calls with `session_id` and `rationale` inside the arguments:
agent-gateway `core/registry-files/defaults/` (`atlan-upload-content`, `atlan-read-content`,
`commands/atlan-upload.md`), app-skill `skill/defaults/` and app-agent `agent/defaults/`.

Agents recover by searching, at the cost of a failed call or two on the first step of every
recipe. **Fix:** replace each recipe's "Before your first call" block with a mode-neutral one:
open the session with `objective` on the first `mcp__search_tools` or `mcp__call_tool`, and run
named tools through `mcp__call_tool` in search mode. `atlan-get-started/references/calling-tools.md`
in this repository is a starting text. This plugin carries the recipes verbatim, so the fix
reaches it with the next pin move.

## 2. Stale tool names in the skill app

`skills/atlan-author-skills/SKILL.md`, `references/update-a-skill.md` and
`commands/atlan-new-skill.md` (app-skill `skill/defaults/`) call `extension__file__request_upload`
and `extension__file__get_content`. Neither exists: the tools are `registry__request_file_upload`
and `registry__get_file_content` (agent-gateway `core/registry-mcp/src/lib.rs`, the `request_file_upload`
and `get_file_content` entries). The live `extension__skill__create` description also says to
upload "with `request_upload`". **Fix:** rename in place; a routine correction.
`atlan-troubleshoot` maps the old names for now.

## 3. Organization skills reach Claude Code only

The registry's marketplace (`/marketplace.git`) emits only `.claude-plugin/marketplace.json`,
`/public/install.json` names only the Claude Code plugin, and the per-workspace plugins that
carry an organization's own skills are off unless `GATEWAY_REGISTRY_MARKETPLACE_CONTAINER_PLUGINS`
is set (agent-gateway `docs/architecture/02-extension-platform/registry.md`, "What a clone
contains"). Codex, Cursor and Copilot users get no organization skills at all, except by asking
an agent to fetch them one at a time (this plugin's `atlan-use-skills`).

**Decision:** whether the registry serves organization skills to other hosts, and how. Options,
smallest first: an `.agents/skills/` export a CLI or this plugin can sync from; Agent Plugins
manifests (`plugin.json`, `mcp.json`) beside the Claude Code ones in each clone; a Codex
`.agents/plugins/marketplace.json` in the same repository.

## 4. No download path for binary or large files

`registry__get_file_content` returns UTF-8 text up to 256 KiB and refuses everything else,
pointing at `GET /file/v1/files/{file_id}/content`. An agent connected with OAuth cannot call
that route: its MCP client holds the token, not the agent. So a registry skill that bundles an
image, a PDF or a large reference cannot be installed through MCP. **Fix:** a
`registry__request_file_download` tool that mirrors `registry__request_file_upload` and
returns a short-lived presigned URL, with the same audit and permission checks.

## 5. Non-presigned uploads need a token the agent does not have

When `registry__request_file_upload` answers `is_presigned: false`, the `PUT` must carry the
caller's `Authorization` header (agent-gateway `core/registry-files/defaults/skills/atlan-upload-content/SKILL.md`).
An OAuth MCP agent has no such header to send, so publishing a skill fails with a `401` that
reads like a bad URL. **Fix:** make the gateway's ingest URL self-authorizing (a signed,
single-use, expiring URL like the presigned one), so no deployment needs the bearer for an
upload.

## 6. The same server installed twice

The Atlan desktop app installs the umbrella `atlanai` plugin, which registers an `atlan` MCP
server; this plugin registers one too. A Claude Code user with both sees every tool twice and
pays for two tool lists. **Decision:** which one a Claude Code user should have. The simplest
end state is that the desktop app installs this plugin (cross-tool, listed in directories) and
the umbrella keeps serving only what Claude Code alone can use, or stops carrying `mcpServers`.

## 7. New users cannot create an organization

Production limits organization creation to eligible `@atlan.com` users; anyone else needs an
existing membership, an invitation or a company-domain join (control-plane
`docs/sales/organization-access.md`). Someone who installs the plugin from a directory and signs
in with a work email reaches a dead end. **Decision:** open self-serve creation to work emails,
or offer a trial workspace that an agent can create and a person claims later.

## 8. One CLI, two names

The installer ships `brain` (registry-cli `docs/installation.mdx`, `https://app.atlan.com/brain/cli/install.sh`),
while the Atlan adapter docs (registry-cli `docs/adapters/atlan.mdx`) and the repo-sync recipe
(app-skill `skill/defaults/skills/atlan-link-skill/SKILL.md`) tell the agent to run `aireg`. An
agent on a machine with only `brain` fails the recipe's first command. **Fix:** one name in all
three, with the other kept as an alias for a release.

## 9. No record of the install surface

Nothing tells the gateway whether a session came from this plugin, the umbrella, or a hand-added
server, so there is no way to measure the plugin's adoption or a directory listing's effect.
Supabase sends a per-surface header from each plugin for this. **Fix:** record one request
header (for example `X-Atlan-Client: atlan-plugin/<version>`) on the `mcp_session` row; this
plugin then adds it to `mcp.json` and `.mcp.json`. The plugin does not send it yet, because
nothing reads it.

## 10. OAuth client registration, per directory client

The control plane supports Client ID Metadata Documents behind a database-driven host allowlist
(control-plane `internal/oauth/cimd.go`) and dynamic registration (`internal/oauth/register.go`).
Before each directory submission, sign in from a clean profile with that client (claude.ai,
Claude Code, Codex, Cursor, VS Code) and confirm it either is on the CIMD allowlist or falls
back to dynamic registration cleanly.

## 11. Recipes missing from dev

The dev gateway's MCP resources list the `atlan-link-skill` and `atlan-link-agent` commands but
not the skills of the same names, which exist on `main` of app-skill and app-agent. Either dev
runs older images, or the registry's projection dropped the two skills; the
`gateway.registry.marketplace.defaults_total` counter and its rejection log say which.
