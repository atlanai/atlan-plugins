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
                                  `user-invocable: false` so the `/` menu lists the task once,
                                  and each command sets `disable-model-invocation: true` so the
                                  model's skill list does too (claude.ai lists commands there)
  claude-hooks/                   the publish helper, Claude Code's hooks module, and its tests;
                                  tests/fixtures.ts generated from tools/tests/fixtures/
  command-hooks/                  the same helper as a command hook for Codex and Cursor:
                                  publish_hook.py and each harness's hooks file, written here
  README.md                       written by hand
plugins/atlanai-<workload>/       add-ons: skills, commands and agents written here; manifests and
                                  README generated
plugins/atlanai-mods/             the Claude Code mod: hooks module, tests and README written
                                  here; manifest generated from [[mods]]
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
- **Skills this repository owns** are named `atlan-<task>` in the core and `<workload>-<task>` in an
  add-on (`analytics-build`, `analytics-ask`), one task each, with a description
  that says when to use the skill rather than what it is: the action, then `Use when` (or
  `Use for`) with the words people say, then `Not for` naming the nearest sibling skill.
  `make check` fails on a description without `Use when` or `Use for`. The description is the
  retrieval signal twice over: the harness matches requests against it, and the registry's
  search ranks on it once the skill is served from Atlan. Each skill opens its own session, so
  a task loads one skill, not `atlan-get-started` first. Every session carries every
  description, and hosts cut a long one in their skill list, so `make check` fails on one over
  250 characters, and on a `SKILL.md` over 500 lines or 9 KiB. Keep the workflow in `SKILL.md`
  and move material only some requests need to
  `references/`, each file cited with the condition for opening it. Teach decisions and point
  at live tools; do not paste reference data that will go stale.
- **Search mode is the default.** Owned skills show calls as `mcp__call_tool { "name", "arguments",
  "session_id", "rationale" }`. Vendored recipes use full-mode notation; `atlan-get-started`
  explains the translation.
- **Portable, except the publish helper.** No other hooks, no `bin/`, no symlinks, no
  submodules, no paths outside the plugin root, no secrets. Hooks are the least portable
  component, each host names their events differently, and Codex keeps hook plugins out of its
  directory, so `make codex-directory` builds the core without them for a directory submission.
  The core's publish helper is the exception: it lets `extension__skill__create` take
  `files_from`, a folder in the working tree it checks with `check_bundle.py`'s rules and sends
  inline, and it announces that with one prompt line. In Claude Code that line is `prompt.submit`
  context on a conversation's first prompt, not `prompt.compose`: Claude Code's built-in
  `cc-plugin-sec-default`, seated for Team and Enterprise organizations and on machines with
  managed settings, passes installed plugins over on `prompt.compose`, `prompt.context` and
  `skill.prompt`, while their `prompt.submit` and `tool.call` hooks run. Use those two for anything
  a hook must tell the model. The skill text does not wait for the line either: it
  sends `files_from` whenever the skill's folder is in the working directory, and where no hook
  expands it, the server refuses `files_from` by name before writing, and the skill sends `files`.
  So every other host, and Claude Code under `allowManagedHooksOnly`, keeps working for one extra
  call. The same helper carries two notices, each one line to the model and each failing silent:
  an update notice when main's `plugin.json` (`claude-hooks/release.ts`, written by the build)
  names a later version, and, in Claude Code, an offer to draft feedback after a failed Atlan
  call or one turn in fifty after Atlan work (`claude-hooks/notices.ts`). Feedback is drafted by
  the model from `atlan-troubleshoot/references/feedback.md` and sent by the user; no hook sends
  it. It comes in two forms, each named only from its own harness's manifest and never from
  `hooks/`, which Codex and Cursor read by default. `claude-hooks/` is a Claude Code hooks module,
  named by `"hooks"` in `.claude-plugin/plugin.json` (`claude_hooks` in `plugin.toml`).
  `command-hooks/` is one standard-library Python script, `publish_hook.py`, that imports
  `check_bundle.py`, with `codex.json` named by `extensions.com.openai.hooks` in `plugin.json` and
  the legacy Codex overlay, and `cursor.json` by `hooks` in `.cursor-plugin/plugin.json`
  (`codex_hooks`, `cursor_hooks`). Every hook fails open: the module's `.catch`, and the script
  exits 0 with no output on any fault, or when Python is missing. Both read only the folders the
  call names. `make check` holds every manifest to its own file and
  every command to the script. The other exception is a mod, below.
- **A mod is Claude Code's alone.** A `[[mods]]` entry in `plugin.toml` is a plugin whose hooks
  module ([Claude Code mods](https://code.claude.com/docs/en/plugins/mods/overview)) changes how
  Claude Code handles the core's tools: `atlanai-mods` guards registry writes and repairs gateway
  calls. The build lists it in the Claude marketplace alone and writes only its
  `.claude-plugin/plugin.json`. It depends on `atlanai`, declares no MCP
  server and no settings hooks, and `hooks/hooks.json` names one hooks module (`make check`
  refuses anything else). A mod may remove friction but never carries a rule a skill relies on:
  Codex, Cursor and Claude chat run the skills without it. Keep rules that need no `$` in
  `hooks/registry.ts` as plain functions, test both files with `claude plugin test`, and list every
  call the mod makes in its README. `make validate` validates the folder whole and runs its tests.
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
  is their source of truth. The app behind the workload should pin a commit of this repository
  to serve the same files over MCP, so a change here reaches it at its next pin bump. The metric
  service does: it copies `analytics-build` into `backend/defaults/skills/analytics-build/` at a
  pinned commit, and its sync workflow opens the bump on each release. An add-on depends on `atlanai` and declares no MCP server, hooks or
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
6. Under 80 words unless the user asked for a list, and never over seven lines outside a table
   or a list the user asked for.
7. Do not announce which skill was loaded unless it changes what the user should expect.
8. One next step, not a menu, and nothing about calls skipped or retried when it changes
   nothing the user should do ("I didn't request an upload ticket").

`tools/lint.py` fails on an em dash, a sentence-ending exclamation mark or a short list of filler
words (`seamless`, `robust`, `comprehensive`, `streamline` and their kin) in any Markdown at the root or
under `plugins/`, and in `plugin.toml`, outside code. Every eval case carries a `plain-reply`
grader that reads the reply's shape against rules 1 and 3 to 8. The rules live in
`atlan-get-started` too, under **Replies lead with the result**, and every skill carries them as
one line of checks to tick before sending, because a task reaches its skill directly rather
than through `atlan-get-started`. Drop that line from a skill and its
replies grow: the first eval pass after 0.3.5 caught longer catch-ups that the judge failed.

## Checks

```
make check       # generated-file drift, lint, unit tests
make validate    # claude plugin validate --strict and the directory profile, and each mod's
                 # `claude plugin test`; needs the claude CLI
make site        # dist/site/.well-known/agent-skills/: the skills as a discovery index
make codex-directory  # dist/codex-directory/: the core without hooks, and the zip OpenAI's
                      # plugin directory takes; names the listing fields plugin.toml still lacks
```

CI runs `check` and `validate` on every pull request.

`make eval` runs the core plugin's behaviour evals (`evals/atlan/`): realistic prompts, from a
dashboard hand-off to a vague "do we have a skill for this?", each run by Claude Code with the
plugin loaded and graded on the tool calls it made and the answer it gave. The `atlan` server is
replaced by `evals/atlan/emulator/`, a stdio stand-in for the gateway's search mode over one
fixed organization, because `claude plugin eval` mocks cannot name a tool with `__` in it. It
needs the claude CLI and costs money, so it is not in CI: run it before a release that changes
an owned skill, and add a case for each failure you fix. The emulator advertises the gateway's
own registry tool definitions: `tools/tests/fixtures/gateway-catalogue.json` holds them, and
`python3 tools/sync_gateway_tools.py` writes them into the emulator's `world.json` and the tier
fixture (`make check` fails when either has drifted). When the live gateway changes a tool,
refresh the catalogue, run the sync, and teach the emulator's handler any new argument; when it
changes a refusal the skills rely on, change the emulator to match. A case that needs the emulator
in another state (an organization without the agent app, an empty registry, a rate limit, the
data-catalog server in the plugin's place, or a skill already installed from
`evals/atlan/fixtures/installed-skills/`) carries an `emulator.json` beside its `case.yaml`
with the emulator's environment switches; the runner groups cases by that file, one Claude Code
run per group, because the emulator starts once per run. `make check` validates every case
and sidecar. Cases tagged `negative` are prompts the plugin must leave alone (a rewrite, a sum,
a code task, a question that only names a skill file): they fail if any Atlan skill loads or
any Atlan tool is called, which keeps a description change from widening what a skill claims.
`python3 evals/atlan/run.py --baseline` runs every case again without the plugin and prints
both scores, so a case the plugin does not improve shows up; under the baseline, graders on
the `Skill` tool count as a plugin-fired indicator rather than score.

`tools/tests/fixtures/journeys.json` holds the golden journeys: the route each use case should
take, cold (a new session) and warm (later in the same session), as the calls it makes and its
budget. `tools/tests/test_journeys.py` runs in `make check` and checks those routes against the
skills: every tool a journey calls is one its skill names, a read goes through `call_read_tool`
and anything destructive through `call_tool`, the counts match the budget, and warm never costs
more than cold. Change a skill's route and its journey together. A call marked `workaround_for`
exists only until that tool ask ships; `python3 tools/tests/test_journeys.py --report` lists
them with each journey's calls and turns. A live run in a sandbox tenant records what it
measured under the journey's `measured`, with the date and the plugin versions.

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

## Environments

This repository is public and serves production only: every plugin names `https://api.atlan.com/mcp`,
and the skills link to `https://app.atlan.com`. Nothing here is built per environment, and no
tracked file names another environment's hosts or plugin names (`make check` refuses them; write
`dev.example` in an example).

Every other Atlan environment gets the core from its own gateway, which serves a copy of
`plugins/atlanai/` from a pinned commit of this repository. Each deployment writes on top only
what differs: its MCP server URL, production's full `https://api.atlan.com` and
`https://app.atlan.com` URLs in the Markdown and Codex cards rewritten to its own hosts, and,
where it sets a marketplace name suffix, the plugin name with that suffix (`atlanai-<suffix>`),
so it installs beside production's. So:

- Write production's hosts as full `https://` URLs when a step or link must follow the
  environment; the gateway rewrites only full URLs. A bare host in prose stays as written.
- Name the plugin's own components the way Claude Code addresses them (`atlanai:atlan-continue`,
  `/atlanai:continue`, `mcp__plugin_atlanai_atlan__…`); the gateway rewrites those prefixes.
- A change reaches each environment when the gateway's pin moves and that deployment ships (see
  Releasing). The add-ons and mods install from this marketplace only and reach production.

## Releasing

1. Bump `[plugin] version` in `plugin.toml` (SemVer; Claude Code keeps users on the version they
   installed until it changes) and run `make build`. An add-on has its own `version` under
   `[[addons]]`, and a mod under `[[mods]]`; bump it when its content or manifest changes.
2. Say what changes for users in the pull request's description; there is no changelog file.
3. Merge to `main`. The `Release tags` workflow tags each released plugin
   `<name>--v<version>` (`atlanai--v0.3.0`, `atlanai-analytics--v0.4.0`) from its
   `.claude-plugin/plugin.json`, so a dependency with a version range resolves in Claude Code.
   gateway's `release sync` workflow opens the pull request that moves its vendored
   copy of `atlanai` within the hour.
   The repository is its own marketplace for every tool, so users get the new version on their
   tool's next marketplace refresh. Directory listings (Claude, Codex, Cursor)
   are pinned to a commit and move only when the listing is updated.

Before a release that changes install or sign-in, install from a clean profile in each tool
and run the first prompt in the README.

## Security

Repo-specific rules. The full guide, with patterns, anti-patterns and a pre-PR checklist, is
[docs/secure-coding.md](docs/secure-coding.md); reporting is in [SECURITY.md](SECURITY.md).

- **[MUST]** The only MCP endpoint is `[mcp] url` in `plugin.toml`, HTTPS, written by `make build`
  (`tools/build.py`).
- **[MUST]** The publish helper sends files only to the Atlan server, only from inside the working
  tree, never through a link, never from a credential folder or key file, within 128 files and
  256 KiB (`guard`, `place` and `walk` in `plugins/atlanai/command-hooks/publish_hook.py`, and
  their counterparts in `claude-hooks/register.ts`). It refuses a `registry__publish_output`
  whose inline text matches a credential shape, and never repeats the value (`output_refusal`,
  `outputRefusal`).
- **[MUST]** The hooks reach only the Atlan server and, for the update notice, the core's
  `plugin.json` on this repository's `main`. They send nothing about the user or the session to
  either, and no hook sends feedback.
- **[MUST]** `command-hooks/` and `claude-hooks/` enforce the same rules as `check_bundle.py`;
  change all three together, with tests.
- **[MUST]** A hook fails open to the call as sent, never to a wider one (`main` in
  `publish_hook.py`, each handler's `.catch` in `register.ts`).
- **[MUST]** `atlanai-mods` keeps archive, approve, move, release and roll-back confirming, and refuses them where
  nobody can answer (`ALWAYS_CONFIRM` in `plugins/atlanai-mods/hooks/registry.ts`).
- **[MUST]** Add-ons and mods declare no MCP server and no `bin/`; hooks stay in the core's helper
  and the mod (`lint_addon` and `lint_mod` in `tools/lint.py`).
- **[REDLINE]** Skill or command text that tells an agent to read, print or send credentials, env
  vars or files outside its task, or to call a non-Atlan server with Atlan data.
- **[REDLINE]** A real token, handle or tenant name in any tracked file; the `SECRETS` scan in
  `tools/lint.py` fails on credential shapes.

Severity: CRITICAL and HIGH block merge; MEDIUM is flagged with a fix; LOW gets a brief note.
Get a maintainer's security review before merging changes to `plugins/atlanai/command-hooks/`,
`plugins/atlanai/claude-hooks/`, `check_bundle.py`, `plugins/atlanai-mods/hooks/`, the `[mcp]`
table in `plugin.toml`, or `.github/workflows/`.
