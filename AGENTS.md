# Contributing to the Atlan plugin

This repository is one plugin, `atlan`, packaged for Claude Code, OpenAI Codex, Cursor and
Agent Plugins hosts (GitHub Copilot, VS Code and others), plus its add-ons. It holds no service
code: the plugin is a remote MCP server URL plus skills and commands, and each add-on is skills,
commands and agents that use that server. Contributor guidance lives here
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
plugins/<add-on>/                 add-on plugins: vendored skills, commands and agents; manifests,
                                  README and logo generated
upstream.json                     pinned sources and sha256 of every vendored file
assets/logo.svg                   the listing icon (the brand package's mascot app icon)
scripts/                          build, sync, lint and the skill index, standard library Python 3.11+
tests/                            unit tests for the scripts
docs/GAPS.md                      what the registry is missing for an easy start
```

## Rules

- **Never hand-edit a generated file.** Change `plugin.toml`, run `make build`. CI fails on drift.
- **Never edit a vendored file.** The skills and commands listed in `upstream.json` belong to the
  registry's apps (gateway's files module, skill service, agent service), which serve the same files
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
- **A workload is an add-on, not a core skill.** A workload built on the registry (analytics,
  later sandbox templates) is an `[[addons]]` entry in `plugin.toml` named `atlan-<workload>`.
  Its skills, commands and agents are authored here, under `plugins/<name>`, and this repository
  is their source of truth: the app behind the workload pins a commit of this repository to serve
  the same files over MCP (metric service does, for `atlan-analytics`). A change here reaches that
  app at its next pin bump, where its own tests run against the new text. An add-on depends on `atlan` and declares no MCP server, hooks or
  `bin/`: two plugins registering the `atlan` server would show every Atlan tool twice, and
  `make check` refuses it. Only Claude Code installs the dependency; in other hosts the add-on's
  skills check for the Atlan tools and say what to install.

## Checks

```
make check       # generated-file drift, vendored hashes, lint, unit tests
make validate    # claude plugin validate --strict and the directory profile, needs the claude CLI
make site        # dist/site/.well-known/agent-skills/: the skills as a discovery index
```

CI runs `check` and `validate` on every pull request.

## Prompts other products send

The Atlan dashboard's Open in Claude buttons send `/atlan:continue out_…` and
`/atlan:use-skill skill_…` to Claude Code, and a sentence naming the skill to Claude chat
(*Use the Atlan continue skill on artifact out_…*). `tests/test_scripts.py`
(`DashboardPromptTest`) holds those forms. Renaming the plugin, a command or one of those skills
breaks the buttons: change the dashboard's prompt builders in the same release.

`make site` output is what atlan.com serves at `/.well-known/agent-skills/` for
`npx skills add https://atlan.com --skill atlan-get-started`. Republish it with each release.

## Variants

A variant is this plugin under another name and endpoint, published from its own repository: for
example Atlan's internal plugin for an environment outside production. Its repository keeps a
`variant.toml` and runs

```
python3 <this repo>/scripts/build.py --variant variant.toml --out .
```

which writes the manifests, a one-plugin marketplace, and a copy of `skills/` and `commands/`
with the variant's URLs, plugin name and repository rewritten. Add `--check` in its CI. The file
can set `[plugin]` names, descriptions, `homepage` and `repository`, `[marketplace]` names and
`[mcp] url` and `client` (the name its `X-Atlan-Client` header sends), plus `[rewrite]` pairs and a top-level `keep` list of strings never to rewrite. Keep
the skills here free of any one environment's URLs beyond production's, so every variant reads
correctly after the rewrite. This repository holds no variant of its own.

## Releasing

1. Bump `version` in `plugin.toml` (SemVer; Claude Code keeps users on the version they
   installed until it changes) and run `make build`. An add-on has its own `version` under
   `[[addons]]`; bump it when its content or manifest changes.
2. Add the release notes under a new heading in `CHANGELOG.md`.
3. Merge to `main`. Tag each released plugin `<name>--v<version>` (`atlan--v0.1.2`,
   `atlan-analytics--v0.1.0`), so a dependency with a version range resolves in Claude Code.
   The repository is its own marketplace for every tool, so users get the new version on their
   tool's next marketplace refresh. Directory listings (Claude, Codex, Cursor)
   are pinned to a commit and move only when the listing is updated.

Before a release that changes install or sign-in, install from a clean profile in each tool
and run the first prompt in the README.
