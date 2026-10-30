# Contributing to the Atlan plugin

This repository is one plugin, `atlan`, packaged for Claude Code, OpenAI Codex, Cursor and
Agent Plugins hosts (GitHub Copilot, VS Code and others). It holds no service code: the
plugin is a remote MCP server URL plus skills and commands. Contributor guidance lives here
because no host loads a plugin's own `CLAUDE.md`, and `claude plugin validate` warns on one.

## Layout

```
plugin.toml                       the one source for every manifest (edit this)
plugin.json, mcp.json             Agent Plugins 1.0: Codex, Cursor, Copilot, VS Code   (generated)
.mcp.json                         Claude Code's MCP file                               (generated)
.claude-plugin/                   Claude Code manifest and marketplace                 (generated)
.codex-plugin/plugin.json         Codex overlay for builds older than the root manifest (generated)
.agents/plugins/marketplace.json  Codex marketplace                                    (generated)
.cursor-plugin/                   Cursor manifest and marketplace                      (generated)
skills/<name>/SKILL.md            skills; owned here unless upstream.json lists them
commands/*.md                     Claude Code and Cursor commands
upstream.json                     pinned sources and sha256 of every vendored file
assets/logo.svg                   the listing icon (the brand package's mascot app icon)
scripts/                          build, sync and lint, standard library Python 3.11+
tests/                            unit tests for the scripts
docs/GAPS.md                      what the registry is missing for an easy start
```

## Rules

- **Never hand-edit a generated file.** Change `plugin.toml`, run `make build`. CI fails on drift.
- **Never edit a vendored file.** The skills and commands listed in `upstream.json` belong to the
  registry's apps (agent-gateway's files module, app-skill, app-agent), which serve the same files
  as MCP prompts and in the registry's own `atlanai` plugin. Fix them upstream, then move the pin:
  `make sync-adopt` with sibling checkouts, or the **Sync upstream recipes** workflow.
- **Skills this repository owns** are named `atlan-<task>`, one task each, with a description
  that says when to use the skill rather than what it is. Keep `SKILL.md` under 500 lines and put
  detail in `references/`. Teach decisions and point at live tools; do not paste reference data
  that will go stale.
- **Search mode is the default.** Owned skills show calls as `mcp__call_tool { "name", "arguments",
  "session_id", "rationale" }`. Vendored recipes use full-mode notation; `atlan-get-started`
  explains the translation.
- **Portable only.** No hooks, no `bin/`, no symlinks, no submodules, no paths outside the
  plugin root, no secrets. Adding a hook needs a decision first: hooks are the least portable
  component, each host names their events differently, and Codex keeps hook plugins out of its
  directory.
- **One name everywhere.** The plugin `name` (`atlan`) and the MCP server key (`atlan`) are
  immutable once published. Relabel with `display_name`.

## Checks

```
make check       # generated-file drift, vendored hashes, lint, unit tests
make validate    # claude plugin validate --strict, when the claude CLI is installed
```

CI runs both, plus `claude plugin validate --strict` against the Claude Code marketplace, on every
pull request.

## Releasing

1. Bump `version` in `plugin.toml` (SemVer; Claude Code keeps users on the version they
   installed until it changes) and run `make build`.
2. Add the release notes under a new heading in `CHANGELOG.md`.
3. Merge to `main`. The repository is its own marketplace for every tool, so users get the new
   version on their tool's next marketplace refresh. Directory listings (Claude, Codex, Cursor)
   are pinned to a commit and move only when the listing is updated.

Before a release that changes install or sign-in, install from a clean profile in each tool
and run the first prompt in the README.
