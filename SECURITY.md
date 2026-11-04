# Secure coding for agents: atlan-plugins

Read this before writing or changing code here. It captures the security patterns this
codebase already follows and the anti-patterns to avoid, grounded in how atlan-plugins actually
works. Match the pattern already in the code; flag anything you can't match.

## What this code guards
Plugin content that installs into users' agents (Claude Code, Codex, Cursor, Copilot): skills and
commands that steer an agent's calls to the `atlan` MCP server at `https://api.atlan.com/mcp`,
the core's publish helper that reads skill folders off the user's disk and sends them inline, and
the `atlanai-mods` hooks that gate registry writes. The risks are a skill or hook that leaks local
files or credentials, sends them to a non-Atlan server, or lets a destructive registry call run
unconfirmed. There is no service code and no stored credential here; sign-in is the host's OAuth.

## Follow these patterns (DO)
- **One HTTPS endpoint, from one source**: the MCP URL lives in `plugin.toml:65` and the build
  refuses any non-`https://` URL, including variant rewrites. `tools/build.py:118`, `tools/build.py:192`.
- **Publish only to the Atlan server**: the command hook refuses `extension__skill__create` unless
  the server's host matches `*.atlan.com`; the Claude module only binds Atlan's runner names.
  `plugins/atlanai/command-hooks/publish_hook.py:52`, `:108`; `plugins/atlanai/claude-hooks/register.ts:23`.
- **`files_from` stays inside the working tree**: resolve `realpath`, require it under the session's
  roots, refuse `~`, UNC and drive-relative labels, and refuse symlinked folders.
  `plugins/atlanai/command-hooks/publish_hook.py:142`; `plugins/atlanai/claude-hooks/register.ts:80`.
- **Never walk into credentials**: skip `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.azure`,
  `gcloud`; refuse key files (`.env*`, `.pem`, `.key`, `id_*`) and secret-shaped content.
  `plugins/atlanai/command-hooks/publish_hook.py:43`, `plugins/atlanai/claude-hooks/bundle.ts:14`,
  `plugins/atlanai/skills/atlan-author-skills/scripts/check_bundle.py:42`, `:44`.
- **Bounded, no-follow reads**: 128 files, 256 KiB, depth 8, checked before reading; files open
  `O_NOFOLLOW` so a swapped-in link fails. `check_bundle.py:36`, `publish_hook.py:161`, `publish_hook.py:206`.
- **Hooks fail open to the unchanged call, never to a wider one**: on any fault the call passes
  through as sent, and the server refuses an unexpanded `files_from`. `publish_hook.py:405`.
- **Destructive registry calls always ask**: archive, approve and move confirm with the blast
  radius, and are refused where nobody can answer. `plugins/atlanai-mods/hooks/registry.ts:61`,
  `plugins/atlanai-mods/hooks/register.tsx:666`.
- **Subprocesses by argv list, with a timeout**: no shell strings in bundled scripts.
  `plugins/atlanai-analytics/skills/atlan-analytics/scripts/registry_inspection.py:82`.
- **Fence untrusted data in prompts**: the analytics runner labels approved instructions apart
  from `Untrusted input data`. `.../scripts/analytics_case.py:86`.
- **CI least privilege**: `permissions: contents: read`, `persist-credentials: false`, SHA-pinned
  actions, a pinned Claude Code CLI. `.github/workflows/ci.yml:9`, `.github/workflows/ci.yml:42`.

## Avoid these anti-patterns (DON'T)
- **Hand-editing generated manifests**: a changed URL or header in `.mcp.json`/`mcp.json` drifts
  from `plugin.toml` and fails `make check`; change `plugin.toml` and run `make build`.
- **A second MCP server, hooks or `bin/` in an add-on or mod**: lint refuses them
  (`tools/lint.py:305`, `tools/lint.py:291`); two `atlan` servers double every tool.
- **Widening the publish helper**: no new read roots, no following links, no shrinking
  `CREDENTIAL_DIRS`/`KEY_FILES`/`SECRETS`, no limits raised past the server's. Keep the Python and
  TypeScript copies identical (`bundle.ts`, `check_bundle.py`).
- **Trusting another server's tool annotations**: the mod leaves non-Atlan tools to the user's
  permission rules (`plugins/atlanai-mods/README.md:27`); don't auto-allow them.
- **Skill text that asks the agent to read or send secrets, env vars or files outside the task** ,
  skills steer real agents with user credentials; a skill is code for review purposes.
- **Credential-shaped strings anywhere**: `tools/lint.py:383` fails on them; use placeholders such
  as `registry__…`, never a real handle, token or tenant name.
- **Symlinks or paths outside a plugin root**: refused at `tools/lint.py:374`; they also break installs.

## Before you open a PR
- [ ] `make check` passes (build drift, lint incl. secret and symlink scan, `tools/tests`, analytics selftests).
- [ ] `make validate` passes, or the change touches no manifest, skill, command or hooks.
- [ ] Generated files come from `make build`; the MCP URL is still `https://api.atlan.com/mcp`.
- [ ] Publish-helper changes keep the Atlan-only guard, tree containment, no-follow reads, credential skips and limits, in both `command-hooks/` and `claude-hooks/`, with tests.
- [ ] Mod changes keep archive, approve and move confirming, and `claude plugin test plugins/atlanai-mods` passes.
- [ ] New skill text reads no files, env vars or credentials beyond its task and calls only Atlan tools it names.
- [ ] Workflow edits keep `contents: read`, SHA pins and `persist-credentials: false`.

## Reporting a vulnerability
Do not use public issues, discussions or pull requests. Use Atlan's
[responsible disclosure program](https://atlan.com/responsible-disclosure-program/) or this
repository's [private vulnerability reporting](https://github.com/atlanai/atlan-plugins/security/advisories/new),
with the file, skill or manifest, the steps, and the host and plugin version. Fixes ship in each
plugin's latest version.

Report a suspected vulnerability privately in Atlan Slack `#bu-security-and-it`.
