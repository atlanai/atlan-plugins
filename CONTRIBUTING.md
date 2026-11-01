# Contributing

Thanks for helping improve the Atlan plugins. This repository holds plugin content (Markdown
skills, commands and agents, and the manifests that describe them) plus the standard library
Python tools that build and check it. There is no service code here.

[AGENTS.md](AGENTS.md) is the full rulebook: the layout, what is generated,
variants and releases. Coding agents read it too. This page covers what you need to send a
first pull request.

## Ways to help

- **Report a bug.** Open an [issue](https://github.com/atlanai/atlan-plugins/issues/new/choose)
  with the host you use (Claude Code, Codex, Cursor, VS Code), the plugin version, the prompt
  you gave and what happened. Leave out tokens, session ids from private workspaces and any
  customer data.
- **Suggest a skill or an improvement.** Open a feature request and describe the task you
  wanted your agent to do.
- **Send a fix.** Small, focused pull requests are easiest to review. For a new skill or a new
  plugin, open an issue first so we can agree on the shape.

Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Set up

You need Python 3.11 or newer and `make`. Nothing else is needed for the main checks.

```
git clone https://github.com/atlanai/atlan-plugins
cd atlan-plugins
make check
```

`make check` verifies that the generated files match `plugin.toml`, lints every skill and
command, and runs the unit tests in `tools/tests/`.

`make validate` runs `claude plugin validate --strict` on the marketplace and every plugin. It
needs the [Claude Code CLI](https://code.claude.com) (`npm install --global
@anthropic-ai/claude-code`). CI runs both on every pull request.

To try your change in a host, add your checkout as a local marketplace. In Claude Code:

```
/plugin marketplace add ./path/to/atlan-plugins
/plugin install atlanai@atlan-plugins
```

## Making a change

| To change | Edit | Then run |
|---|---|---|
| A plugin's name, version, description, prompts or a skill's card | `plugin.toml` | `make build` |
| A skill this repository owns | `plugins/<plugin>/skills/<name>/` | `make check` |
| The core plugin's README | `plugins/atlanai/README.md` | `make check` |
| An add-on's README or manifests | `[[addons]]` in `plugin.toml` | `make build` |

The rules that catch people out:

- **Never hand-edit a generated file.** The marketplace JSON, every plugin manifest, each
  skill's `agents/openai.yaml`, the add-on READMEs and the copies of `LICENSE` and the logo in
  each plugin folder come from `plugin.toml` and the root files. CI fails on drift.
- **Keep plugins portable.** No hooks, no `bin/`, no symlinks, no submodules, no paths outside
  a plugin's folder, and no secrets.
- **Plugin names (`atlanai`, `atlanai-analytics`) and the `atlan` server key do not change.**
  People have them installed.
- **A new skill** is named `atlan-<task>`, does one task, and has a description that says when
  to use it. Keep `SKILL.md` under 500 lines and move detail into `references/`. Add its
  `[skills.<name>]` entry to `plugin.toml`.

## Pull requests

1. Fork the repository and branch from `main`.
2. Make the change and run `make check` (and `make validate` if you changed a manifest, a
   skill or a command).
3. If the change reaches users, bump the plugin's `version` in `plugin.toml`, run
   `make build`, and say what changes for users in the pull request's description.
4. Open the pull request and fill in the template. Say which hosts you tried it in.

A maintainer reviews every pull request. By contributing, you agree that your contribution is
licensed under the [Apache-2.0 license](LICENSE).

## Code of conduct

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).
