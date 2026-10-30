# Atlan Analytics

The analytics workload on the Atlan registry. Mine your warehouse's query history into governed metrics and saved queries, build a talk-to-data agent that reads them, measure it against a local eval set and improve it round by round, or propose a metric fix from a conversation. It finds your Snowflake, Databricks or other warehouse through Atlan or your own connector, and asks you to connect one when it is missing. It uses the Atlan plugin's connection; building and evaluating an agent needs Claude Code.

## Install

It uses the `atlan` plugin's connection to Atlan and has no MCP server of its own.
Claude Code installs `atlan` with it:

```
/plugin marketplace add atlanai/atlan-plugins
/plugin install atlan-analytics@atlan-plugins
```

Other hosts install plugins one at a time, so add `atlan` first. In Codex:

```
codex plugin marketplace add atlanai/atlan-plugins
codex plugin add atlan@atlan-plugins
codex plugin add atlan-analytics@atlan-plugins
```

## Privacy

It stores nothing and runs nothing on your machine: it has no hooks and no binaries. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills, commands and agents in this folder are the source of truth. The Atlan app behind the
workload pins a commit of this repository and serves the same files over Atlan's MCP server.
