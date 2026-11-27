# Atlan Analytics

This plugin builds your agent: it answers your data questions in plain English from your
context, the metrics and saved queries the plugin builds and tests. Each answer names the metric
or saved query it used, or says when it had to write new SQL for the question.

It builds that context from what your team already relies on: dashboards first, then past
queries, then table definitions. It tests each metric and saved query on your data, when it can reach
your data, before you keep them on this machine or save them to share with your team.

## You need

- **To try a sample:** Python 3.11 or 3.12. Put `python3.12` or `python3.11` on your `PATH`, or use `py -3.12` on Windows. Samples bring their own data and need no account.
- **For your own data:** the same Python, and a dashboard file, a warehouse your coding agent can reach (such as Snowflake or Databricks), or both. For a warehouse, read access to its tables, and to past queries if you build from them.
- **To save for your team (optional):** an Atlan account, signed in through the `atlanai` plugin. The plugin installs either way, and without a sign-in everything stays on this machine.

## Quick start

1. Install it, as below.
2. Start the skill for what you want to do:

   | You want to | Claude Code | Codex | Or say |
   |---|---|---|---|
   | Learn how it works on a sample | `/atlanai-analytics:analytics-demo` | `$analytics-demo` | *"Show me how this works on a sample use case"* |
   | Build your agent from your own data or a dashboard | `/atlanai-analytics:analytics-build` | `$analytics-build` | *"Help me use this dashboard to ask questions about my data"* |
   | Ask your agent a question | `/atlanai-analytics:analytics-ask` | `$analytics-ask` | *"What was revenue last quarter, by region?"* |

   In other hosts, name the skill: *"Use the analytics-demo skill"*. Your own words work too, but
   whether they reach the right skill depends on your model, your host and what else is loaded.

3. The skill shows its steps as it goes, and asks before it saves anything to Atlan.

## Samples

Two fictional companies, with made-up data, ship with the plugin. Each sample builds your agent for the company: its context, the metrics and saved queries, from its dashboard, tests them on the sample data, asks where to keep them, then answers your first question.

| Sample | What you can ask it | See it |
|---|---|---|
| Fixit & Co., a builder's yard | Which jobs lost money, and why. Built from a Power BI dashboard in about 15 minutes. | [Tour](https://atlanai.github.io/analytics-agent-accelerator/yard-tour/) |
| Ticketfuddle, a ticketing firm | How much of what fans paid went on fees. Built from a Tableau dashboard in about 15 to 20 minutes. | [Tour](https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/) |

## How it works

- A **metric** is a named number with its rule, such as revenue by month.
- A **saved query** is a tested SQL question your agent can rerun, such as margin by job.
- Together they are **your context**. **Your agent** answers from it, and finds the right
  metric or saved query for each question. It runs in the coding agent you use, such as Claude
  Code or Codex.
- A **workspace** is where your team keeps that context when you save it, so others can ask
  from it too.

## Skills

| Skill | Use it to | Try |
|---|---|---|
| `analytics-demo` | Build your agent on made-up data, step by step | *"Show me how this works on a sample use case."* |
| `analytics-build` | Build your agent on your own data, step by step | *"Set up this project so I can ask questions about its data."* |
| `source-survey` | See what data, dashboards and past queries you can build from | *"Tell me what this folder's dashboards and past queries cover."* |
| `metrics-generation` | Turn dashboards and past queries into metrics your agent answers from | *"Build metrics from our warehouse's past queries."* |
| `analytics-ask` | Answer data questions from your metrics and saved queries | *"What was revenue last quarter, by region?"* |
| `eval-generation` | Write test questions, with known answers, from what your team asks | *"Write test questions for our agent from what our team already asks."* |
| `accuracy-check` | Test your agent on questions with known answers | *"Test our agent on the test questions in this project."* |

## Install

| Host | Install |
|---|---|
| Claude Code | `claude plugin install atlanai-analytics --marketplace atlanai/atlan-plugins` installs Analytics and `atlanai` |
| Codex | `codex plugin marketplace add atlanai/atlan-plugins`, then `codex plugin add atlanai@atlan`, then `codex plugin add atlanai-analytics@atlan` |
| Cursor | On a Teams or Enterprise plan, an admin adds `https://github.com/atlanai/atlan-plugins` under **Dashboard**, **Plugins & MCPs**, **Add Marketplace**, **Import from Repo**. Each developer then installs `atlanai` and `atlanai-analytics` from **Customize**. |
| Cortex Code | Send `/github-plugin-installer https://github.com/atlanai/atlan-plugins/tree/main/plugins/atlanai`, then `/github-plugin-installer https://github.com/atlanai/atlan-plugins/tree/main/plugins/atlanai-analytics`, as two messages |

Only Claude Code installs a plugin's dependencies. Elsewhere, install `atlanai` first. Without
it, the setup skills show how to install it.

To update, run `claude plugin update atlanai-analytics` and restart Claude Code, or in Codex run
`codex plugin marketplace upgrade atlan`.

**Build progress in Claude Code.** In the terminal, a build's progress shows in Claude Code's own task list. Some models have the task list on
by default. If yours shows no task list, add this to the `env` in your Claude Code settings
(`~/.claude/settings.json`) once, then start a new session:

```json
{"env": {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}}
```

Without it, each finished build step shows as one line in the chat.

## When it goes wrong

| What you see | What to do |
|---|---|
| The skills say the `atlanai` tools are missing | Outside Claude Code, install `atlanai` first. In Claude Code, run `/reload-plugins`. |
| Sign-in is needed or has expired | In Claude Code: `/mcp`, pick `plugin:atlanai:atlan`, **Authenticate**. In Codex: `codex mcp login atlan`. |
| No warehouse is found | The plugin asks to connect one: your coding agent's own connector, a vendor CLI where there is a shell, or the `atlanai` connection. |
| Python 3.11 or 3.12 is not found | Install one so `python3.12` or `python3.11` is on your `PATH`, then ask the plugin to continue. |
| There is nowhere to save for your team | Keep the work on this machine, or ask for access to an existing Atlan workspace. An admin makes new ones. |

## Help

Report a problem in [Issues](https://github.com/atlanai/atlan-plugins/issues). For a vulnerability, see
[SECURITY.md](../../SECURITY.md). Licensed under [Apache-2.0](LICENSE).

## Privacy

This plugin has no hooks and ships no executable binaries.

- **Runs:** the Python helpers bundled with its skills, a metrics runtime, and the eval tools, each
  in its own virtual environment, with exact package versions pip installs from your package index
  (PyPI unless pip is set otherwise). The eval tools' one package is also checked against its hash.
  Reading a Power BI file uses a pinned, hash-checked PBIXRay environment in
  `~/.trident/runtimes/`. It also calls two command-line tools when they're installed: `atlanai`
  (Atlan Trident, signed in as you) and Snowflake's `snow`.
- **When:** nothing runs when the plugin installs. A helper runs when you start the skill that uses
  it, as a command in the conversation that you can see; the environments are set up the first
  time a skill needs them.
- **Reads:** the sources you pick, your warehouse through read-only statements (SELECT, WITH, SHOW
  or DESCRIBE, with a LIMIT on anything that returns rows), and a Power BI workspace or Tableau
  site through its API when you point the plugin at one.
- **Credentials:** the ones you set for your own systems: `PBI_*` for Power BI, `TABLEAU_*` for
  Tableau, `SNOWFLAKE_*` for Snowflake, `DATABRICKS_*` for Databricks, `ATLAN_BASE_URL` and
  `ATLAN_API_KEY` for your Atlan tenant, and `METRICS_AGENT_WAREHOUSE_DSN` for a local DuckDB or
  SQLite file. Each goes only to the system it names (Microsoft's sign-in and Power BI or Fabric
  APIs, your Tableau server, your Snowflake account, your Databricks workspace, your Atlan tenant),
  never anywhere else.
- **Writes:** working files in one folder in your project, `.atlan-analytics/`: each run, the
  metrics runtime, test questions and the eval tools (`evals/`) and a demo's data (`demo/`). Add it
  to `.gitignore`. Outside the project: a Power BI workspace's sign-in token is cached under
  `~/.cache/pbi_definition_extract/`; Snowflake sign-in profiles you create in the browser, and runs
  you choose to keep in your home folder, go under `~/.atlan/analytics/`; the PBIXRay environment
  and dashboard jobs go under `~/.trident/`.
- **Sends:** to Atlan, only what you approve saving, through the `atlanai` plugin's connection,
  signed in as you.
- **Starts:** a progress page served on `127.0.0.1` for this machine only, opened in your browser;
  and, when you check accuracy, your own Claude Code or Codex CLI to answer each test question
  fresh, signed in as you.
- **To stop it:** end the skill, or decline its next command. Delete `.atlan-analytics/` to remove
  the runs, both environments and the test questions. Delete the folders above outside the project
  to remove the Power BI sign-in, Snowflake profiles and the PBIXRay environment. Unset the
  variables listed under Credentials to stop it reaching those systems.

Its helpers report usage events to Segment, as `atlanai` does and under the same switches: which stage a run
reached, which choices were made, and counts. Never a metric's or source's name, a question, an answer or SQL.
`ATLAN_PLUGIN_TELEMETRY=0` or `DO_NOT_TRACK=1` turns it off, and the [`atlanai` README](../atlanai/README.md#usage-telemetry) lists every event.
See Atlan's [privacy notice](https://atlan.com/privacy-policy/).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills and scripts in this folder are the only source.
