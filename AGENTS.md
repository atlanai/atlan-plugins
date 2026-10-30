# Contributing to the Atlan plugins

This repository is a plugin marketplace, `atlan-plugins`, for Claude Code, OpenAI Codex, Cursor
and Agent Plugins hosts (GitHub Copilot, VS Code and others). Its core plugin, `atlan`, is a
remote MCP server URL plus skills and commands. Each add-on is skills, commands and agents that
use that server. It holds no service code. Contributor guidance lives here because no host
loads a plugin's own `CLAUDE.md`, and `claude plugin validate` warns on one.

## Layout

```
plugin.toml                       the one source for every manifest (edit this)
.claude-plugin/marketplace.json   Claude and Claude Code marketplace                  (generated)
.agents/plugins/marketplace.json  Codex marketplace                                   (generated)
.cursor-plugin/marketplace.json   Cursor marketplace                                  (generated)
plugins/atlan/                    the core plugin
  plugin.json, mcp.json           Agent Plugins 1.0: Codex, Cursor, Copilot, VS Code  (generated)
  .mcp.json, .claude-plugin/      Claude Code's MCP file and manifest                 (generated)
  .codex-plugin/, .cursor-plugin/ Codex overlay and Cursor manifest                   (generated)
  skills/<name>/SKILL.md          skills; owned here unless upstream.json lists them
  skills/<name>/agents/openai.yaml  Codex's card for the skill, from [skills] in plugin.toml (generated)
  commands/*.md                   Claude Code and Cursor commands
  README.md                       written by hand
plugins/atlan-<workload>/         add-ons: skills, commands and agents written here; manifests and
                                  README generated
plugins/*/assets/logo.svg, LICENSE  copies of the root files, generated: a plugin cannot point
                                  outside its folder
upstream.json                     pinned sources and sha256 of every vendored file
assets/hero.png                   the README's banner and the repository's social preview (written by hand)
assets/logo.svg                   the listing icon: the Atlan desktop app's icon (frontend, apps/dashboard/src-tauri/icons/source/atlan-app-icon.svg)
tools/                            build, sync, lint and the skill index, standard library Python 3.11+
tools/tests/                      unit tests for the tools
evals/atlan/                      the core plugin's behaviour evals and the gateway emulator they run against
```

## Rules

- **The root is the marketplace; every plugin is a folder under `plugins/`.** Nothing a plugin
  installs lives at the root, and `make check` refuses skills, commands or plugin manifests there.
  This is the layout of Anthropic's own marketplace and of vendors that ship several plugins.
- **Never hand-edit a generated file.** Change `plugin.toml`, run `make build`. CI fails on drift.
- **Never edit a vendored file.** The core's skills and commands listed in `upstream.json` belong to the
  registry's apps (agent-gateway's files module, app-skill, app-agent, app-knowledge, app-repo), which serve the same files
  as MCP prompts and in the registry's own `atlanai` plugin. Fix them upstream, then move the pin:
  `make sync-adopt` with sibling checkouts, or the **Sync upstream recipes** workflow.
- **Every skill, vendored or owned, in every plugin has a `[skills.<name>]` entry** in
  `plugin.toml`: a display name, a short description and a starter prompt that invokes it as
  `$<name>`. The build writes it, with a dependency on the `atlan` MCP server, to the skill's
  `agents/openai.yaml`, and fails on a skill without one.
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
- **One name everywhere.** Plugin names (`atlan`, `atlan-analytics`) and the MCP server key
  (`atlan`) are immutable once published: users have them installed, and add-ons name `atlan` as
  their dependency. Relabel with `display_name`. `X-Atlan-Client`'s value, `atlan-plugin`, is a
  wire identifier the gateway records, and stays as it is.
- **A workload is an add-on, not a core skill.** A workload built on the registry (analytics,
  later sandbox templates) is an `[[addons]]` entry in `plugin.toml` named `atlan-<workload>`.
  Its skills, commands and agents are authored here, under `plugins/<name>`, and this repository
  is their source of truth: the app behind the workload pins a commit of this repository to serve
  the same files over MCP (app-metric does, for `atlan-analytics`). A change here reaches that
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

`make eval` runs the core plugin's behaviour evals (`evals/atlan/`): realistic prompts, from a
dashboard hand-off to a vague "do we have a skill for this?", each run by Claude Code with the
plugin loaded and graded on the tool calls it made and the answer it gave. The `atlan` server is
replaced by `evals/atlan/emulator/`, a stdio stand-in for the gateway's search mode over one
fixed organization, because `claude plugin eval` mocks cannot name a tool with `__` in it. It
needs the claude CLI and costs money, so it is not in CI: run it before a release that changes
an owned skill, and add a case for each failure you fix. When the live gateway changes a tool
or a refusal the skills rely on, change the emulator to match.

## Prompts other products send

The Atlan dashboard's Open in Claude buttons send `/atlan:continue out_…` and
`/atlan:use-skill skill_…` to Claude Code, and a sentence naming the skill to Claude chat
(*Use the Atlan continue skill on artifact out_…*). `tools/tests/test_tools.py`
(`DashboardPromptTest`) holds those forms. Renaming the plugin, a command or one of those skills
breaks the buttons: change the dashboard's prompt builders in the same release.

`make site` indexes the core's skills; its output is what atlan.com serves at `/.well-known/agent-skills/` for
`npx skills add https://atlan.com --skill atlan-get-started`. Republish it with each release.

## Variants

A variant is this marketplace under another name and endpoint, published from its own repository:
for example Atlan's internal plugins for an environment outside production. Its repository keeps a
`variant.toml` and runs

```
python3 <this repo>/tools/build.py --variant variant.toml --out .
```

which writes the marketplace files and every plugin folder under `plugins/`, named after the
variant (`atlan-dev`, `atlan-dev-analytics`), with the variant's URLs, plugin names and
repository rewritten. It owns `plugins/` in that repository, and removes the files an older build
wrote at its root. Add `--check` in its CI. The file
can set `[plugin]` names, descriptions, `homepage` and `repository`, `[marketplace]` names and
`[mcp] url` and `client` (the name its `X-Atlan-Client` header sends), plus `[rewrite]` pairs and a top-level `keep` list of strings never to rewrite. Keep
the skills here free of any one environment's URLs beyond production's, so every variant reads
correctly after the rewrite. This repository holds no variant of its own.

## Releasing

1. Bump `[plugin] version` in `plugin.toml` (SemVer; Claude Code keeps users on the version they
   installed until it changes) and run `make build`. An add-on has its own `version` under
   `[[addons]]`; bump it when its content or manifest changes.
2. Say what changes for users in the pull request's description; there is no changelog file.
3. Merge to `main`. Tag each released plugin `<name>--v<version>` (`atlan--v0.1.2`,
   `atlan-analytics--v0.1.0`), so a dependency with a version range resolves in Claude Code.
   The repository is its own marketplace for every tool, so users get the new version on their
   tool's next marketplace refresh. Directory listings (Claude, Codex, Cursor)
   are pinned to a commit and move only when the listing is updated.

Before a release that changes install or sign-in, install from a clean profile in each tool
and run the first prompt in the README.
