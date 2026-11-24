# Analytics

Analytics builds an agent that answers your data questions in plain English. Each answer comes
from a metric or saved query you can open, and names the one it used.

It builds those metrics and saved queries from what your team already relies on: dashboards
first, then past queries, then table definitions. It tests each one on your data before you keep
it on this machine or save it to Atlan.

Try it on a demo with made-up data first, or point it at your own warehouse.

## You need

- **To build metrics, including the demo:** Python 3.11 or 3.12. Put `python3.12` or `python3.11` on your `PATH`, or use `py -3.12` on Windows.
- **To save to Atlan and ask from it:** an Atlan account, signed in through the `atlanai` plugin. The demo can keep everything on this machine instead.
- **For your own data:** a warehouse that Atlan or your coding agent can reach, such as Snowflake or Databricks, with read access to its tables. To build from past queries, you also need read access to those.

## Quick start

1. Install it, as below.
2. Type *"Show me an Atlan Analytics demo"*.
3. Pick Fixit & Co. or Ticketfuddle.
4. In about 15 to 20 minutes, it builds the firm's metrics and saved queries from its dashboard and tests them on the demo data.
5. Choose where to keep them, then ask your first question.

## Skills

| Skill | Use it to | Try |
|---|---|---|
| `analytics-demo` | Build your agent on made-up data, step by step | *"Show me how Atlan Analytics works on a demo."* |
| `analytics-build` | Set up analytics on your own data, step by step | *"Set up this project so I can ask questions about its data."* |
| `source-survey` | See what data, dashboards and past queries you can build from | *"Tell me what this folder's dashboards and past queries cover."* |
| `metrics-generation` | Turn dashboards and past queries into metrics your agent answers from | *"Build metrics from our warehouse's past queries."* |
| `analytics-ask` | Answer data questions from your metrics and saved queries in Atlan | *"What was revenue last quarter, by region?"* |
| `eval-generation` | Write test questions, with known answers, from what your team asks | *"Write test questions for our agent from what our team already asks."* |
| `accuracy-check` | Test your agent on questions with known answers | *"Test our agent on the test questions in this project."* |

## Install

In Claude Code, one command installs Analytics and `atlanai` automatically:

```
claude plugin install atlanai-analytics --marketplace atlanai/atlan-plugins
```

In Claude Code's terminal, a build's progress shows in Claude Code's own task list. Some models have the task list on
by default. If yours shows no task list, add this to the `env` in your Claude Code settings
(`~/.claude/settings.json`) once, then start a new session:

```json
{"env": {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}}
```

Without it, each finished build step shows as one line in the chat.


Only Claude Code installs a plugin's dependencies. Other hosts install plugins one at a time, so
add `atlanai` first. Without it, the setup skills show how to install it. In Codex:

```
codex plugin marketplace add atlanai/atlan-plugins
codex plugin add atlanai@atlan
codex plugin add atlanai-analytics@atlan
```

In Cursor, on a Teams or Enterprise plan, an admin adds `https://github.com/atlanai/atlan-plugins` under
**Dashboard**, **Plugins & MCPs**, **Add Marketplace**, **Import from Repo**. Each developer then
installs `atlanai` and `atlanai-analytics` from **Customize**.

In Cortex Code, install each from its folder, one chat message each:

```
/github-plugin-installer https://github.com/atlanai/atlan-plugins/tree/main/plugins/atlanai
/github-plugin-installer https://github.com/atlanai/atlan-plugins/tree/main/plugins/atlanai-analytics
```

## When it goes wrong

| What you see | What to do |
|---|---|
| The Atlan tools are missing | Outside Claude Code, install `atlanai` first. In Claude Code, run `/reload-plugins`. |
| Sign-in is needed or has expired | In Claude Code: `/mcp`, pick `plugin:atlanai:atlan`, **Authenticate**. In Codex: `codex mcp login atlan`. |
| No warehouse is found | Analytics asks to connect one: through Atlan, your coding agent's own connector, or a vendor CLI where there is a shell. |
| Python 3.11 or 3.12 is not found | Install one so `python3.12` or `python3.11` is on your `PATH`, then ask Analytics to continue. |
| There is no workspace to save to | Ask for access to an existing Atlan workspace, or keep the work on this machine. New workspaces are made in Atlan or by an admin. |

## Help

Report a problem in [Issues](https://github.com/atlanai/atlan-plugins/issues). For a vulnerability, see
[SECURITY.md](../../SECURITY.md). Licensed under [Apache-2.0](LICENSE).

## Privacy

Analytics has no hooks and ships no executable binaries.

- **Runs:** the Python helpers bundled with its skills, and a metrics runtime in its own virtual
  environment, with pinned packages pip installs from your package index (PyPI unless pip is set
  otherwise).
- **Reads:** the sources you pick, your warehouse through read-only statements (SELECT, WITH, SHOW
  or DESCRIBE, with a LIMIT on anything that returns rows), and a Power BI workspace through its
  API when you point Analytics at one.
- **Writes:** working files in your project: `.atlan-metrics/`, `.atlan-analytics/` for dashboards,
  `.atlan-evals/` for test questions and `atlan-analytics-demo/` for a demo. Reading a Power BI
  workspace caches its sign-in token under `~/.cache/pbi_definition_extract/`.
- **Sends:** to Atlan, only what you approve saving, through the `atlanai` plugin's connection,
  signed in as you.

See Atlan's [privacy notice](https://atlan.com/privacy-policy/).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills and scripts in this folder are the only source. Atlan's metric service serves a copy
of `analytics-build` pinned to a commit of this repository.
