# Contributing to the Atlan plugins

This repository is a plugin marketplace, `atlan`, for Claude Code, OpenAI Codex, Cursor
and Agent Plugins hosts (GitHub Copilot, VS Code and others). Its core plugin, `atlanai`, is a
remote MCP server URL plus skills and commands. Each add-on is skills, commands and agents that
use that server. It holds no service code. Contributor guidance lives here because no host
loads a plugin's own `CLAUDE.md`, and `claude plugin validate` warns on one.

## Layout

```
plugin.toml                       the one source for every manifest (edit this)
.claude-plugin/marketplace.json   Claude and Claude Code marketplace                  (generated)
.agents/plugins/marketplace.json  Codex marketplace                                   (generated)
.cursor-plugin/marketplace.json   Cursor marketplace                                  (generated)
plugins/atlanai/                    the core plugin
  plugin.json, mcp.json           Agent Plugins 1.0: Codex, Cursor, Copilot, VS Code  (generated)
  .mcp.json, .claude-plugin/      Claude Code's MCP file and manifest                 (generated)
  .codex-plugin/, .cursor-plugin/ Codex overlay and Cursor manifest                   (generated)
  skills/<name>/SKILL.md          skills, written here
  skills/<name>/agents/openai.yaml  Codex's card for the skill, from [skills] in plugin.toml (generated)
  commands/*.md                   Claude Code and Cursor commands; each command's skill sets
                                  `user-invocable: false` so the `/` menu lists the task once
  README.md                       written by hand
plugins/atlanai-<workload>/       add-ons: skills, commands and agents written here; manifests and
                                  README generated
plugins/*/assets/logo.svg, LICENSE  copies of the root files, generated: a plugin cannot point
                                  outside its folder
assets/hero.png                   the README's banner and the repository's social preview (written by hand)
assets/logo.svg                   the listing icon: the Atlan desktop app's icon (frontend, apps/dashboard/src-tauri/icons/source/atlan-app-icon.svg)
tools/                            build, lint and the skill index, standard library Python 3.11+
tools/tests/                      unit tests for the tools
evals/atlan/                      the core plugin's behaviour evals and the gateway emulator they run against
```

## Rules

- **The root is the marketplace; every plugin is a folder under `plugins/`.** Nothing a plugin
  installs lives at the root, and `make check` refuses skills, commands or plugin manifests there.
  This is the layout of Anthropic's own marketplace and of vendors that ship several plugins.
- **Never hand-edit a generated file.** Change `plugin.toml`, run `make build`. CI fails on drift.
- **Every skill is written here.** Until 0.3.1, six of the core's skills (`atlan-author-skills`,
  `atlan-author-agents`, `atlan-author-knowledge`, `atlan-connect-repo`, `atlan-content`,
  `atlan-record-outputs`) were copied from the registry's apps at pinned commits. They are now
  this repository's own and are edited here like the rest; nothing syncs them *into* this
  repository. The gateway's own `atlanai` plugin ships copies of these folders vendored *from*
  it at a pinned commit (gateway's `pinned-files.toml`), and the gateway's checks on
  contributed skills apply to the text: every `registry__…` name is one the gateway serves,
  retired tool names are not quoted, and a placeholder is written `registry__…`, never
  `registry__x`. When the gateway changes a tool or a procedure, change the skill here to match,
  add an eval case, and move the gateway's pin.
- **Every skill in every plugin has a `[skills.<name>]` entry** in
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
- **One name everywhere.** Plugin names (`atlanai`, `atlanai-analytics`) and the MCP server key
  (`atlan`) do not change again: users have them installed, and add-ons name `atlanai` as their
  dependency. Relabel with `display_name`. 0.3.0 renamed the plugins from `atlan` and
  `atlan-analytics` once, because Anthropic's directory lists a different plugin named `atlan`
  (atlanhq/agent-toolkit, the data catalog) and two plugins of one name share a skill prefix and
  an MCP server slot in Claude Code, so one silently replaced the other. `atlanai` is also the
  name of the registry's own plugin, which serves the same server. `X-Atlan-Client`'s value, `atlan-plugin`, is a
  wire identifier the gateway records, and stays as it is.
- **A workload is an add-on, not a core skill.** A workload built on the registry (analytics,
  later sandbox templates) is an `[[addons]]` entry in `plugin.toml` named `atlanai-<workload>`.
  Its skills, commands and agents are authored here, under `plugins/<name>`, and this repository
  is their source of truth: the app behind the workload pins a commit of this repository to serve
  the same files over MCP (metric service does, for `atlanai-analytics`). A change here reaches that
  app at its next pin bump, where its own tests run against the new text. An add-on depends on `atlanai` and declares no MCP server, hooks or
  `bin/`: two plugins registering the `atlan` server would show every Atlan tool twice, and
  `make check` refuses it. Only Claude Code installs the dependency; in other hosts the add-on's
  skills check for the Atlan tools and say what to install.

## Voice

The words a user reads, in a README, a skill's reply or an error, say what happens and what to
do next. Nothing else. The rules, which `make check` enforces where a check can:

1. The first sentence is the result or the step. Never `This document`, `Great question`,
   `I'd be happy to`, `Certainly`.
2. Numbers and names, not adjectives. Counts instead of lists; a table when items have
   attributes; handles in code spans, and only when asked.
3. One step per failure. No apology, no `unfortunately`, no `it seems`.
4. No closing offer unless the next step is real. Never `let me know if you need anything else`.
5. Commas, colons and full stops. No em dashes, no exclamation marks, no emoji.
6. Under 80 words unless the user asked for a list. A first reply or a catch-up stays under
   seven lines.
7. Do not announce which skill was loaded unless it changes what the user should expect.

`tools/lint.py` fails on an em dash, a sentence-ending exclamation mark or a short list of filler
words (`seamless`, `robust`, `comprehensive`, `streamline` and their kin) in any Markdown at the root or
under `plugins/`, and in `plugin.toml`, outside code. Every eval case carries a `plain-reply`
grader that reads the reply's shape. The rules live in `atlan-get-started` too, under **Replies
lead with the result**, because that skill opens every Atlan task.

## Checks

```
make check       # generated-file drift, lint, unit tests
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
or a refusal the skills rely on, change the emulator to match. A case that needs the emulator
in another state (an organization without the agent app, an empty registry, a rate limit, the
data-catalog server in the plugin's place) carries an `emulator.json` beside its `case.yaml`
with the emulator's environment switches; the runner groups cases by that file, one Claude Code
run per group, because the emulator starts once per run. `make check` validates every case
and sidecar.

## Prompts other products send

The Atlan dashboard (an internal repository) sends prompts, not commands. Continue in and Copy agent
prompt send *Catch me up on Atlan artifact "…" (output_…)* with the source session
(`apps/dashboard/src/lib/agents/artifact-handoff.ts`); a skill's Open in Claude sends *Load the
"…" skill from the Atlan skill registry* with its `skill_…` id (`skill-chat-prompt.ts`).
`tools/tests/test_tools.py` (`DashboardPromptTest`) holds those forms against the descriptions
of `atlan-continue` and `atlan-use-skill`, and `evals/atlan/cases/dashboard-handoff-prompt` runs
the first one whole. People also type `/atlanai:continue output_…` and `/atlanai:use-skill skill_…`.
Change a description and the dashboard's prompt builders together.

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
variant (`atlanai-dev`, `atlanai-dev-analytics`), with the variant's URLs, plugin names and
repository rewritten. It owns `plugins/` in that repository, and removes the files an older build
wrote at its root. Add `--check` in its CI. The file
can set `[plugin]` names, descriptions, `homepage` and `repository`, `[marketplace]` names and
`[mcp] url` and `client` (the name its `X-Atlan-Client` header sends), plus `[rewrite]` pairs and a top-level `keep` list of strings never to rewrite. Keep
the skills here free of any one environment's URLs beyond production's, so every variant reads
correctly after the rewrite. Name a variant the way the gateway names its own marketplace for
that environment, `atlanai-<suffix>` (its `the marketplace name suffix`, `dev` on
the dev gateway), with the marketplace `atlan-<suffix>` and the environment's `/mcp`
URL, so the two install side by side with production's the way the gateway's do. This
repository holds no variant of its own.

## Releasing

1. Bump `[plugin] version` in `plugin.toml` (SemVer; Claude Code keeps users on the version they
   installed until it changes) and run `make build`. An add-on has its own `version` under
   `[[addons]]`; bump it when its content or manifest changes.
2. Say what changes for users in the pull request's description; there is no changelog file.
3. Merge to `main`. Tag each released plugin `<name>--v<version>` (`atlanai--v0.3.0`,
   `atlanai-analytics--v0.4.0`), so a dependency with a version range resolves in Claude Code.
   The repository is its own marketplace for every tool, so users get the new version on their
   tool's next marketplace refresh. Directory listings (Claude, Codex, Cursor)
   are pinned to a commit and move only when the listing is updated.

Before a release that changes install or sign-in, install from a clean profile in each tool
and run the first prompt in the README.
