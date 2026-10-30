# Atlan Analytics

The analytics workload on the Atlan registry. Ask a data question and get an answer from your published metrics and saved queries, with the definition it used. Mine your warehouse's query history into governed metrics and saved queries, build a talk-to-data agent that reads them, measure it against a local eval set and improve it round by round, or propose a metric fix from a conversation. Ask how business objects relate and which tables hold them, check which policy applied on a date, or draft an ontology for review in Ontology Studio. It finds your Snowflake, Databricks or other warehouse through Atlan or your own connector, and asks you to connect one when it is missing. It uses the Atlan plugin's connection; building and evaluating an agent needs Claude Code.

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

It has no hooks and no binaries. Its skills may run the standard-library Python scripts bundled
with them and keep working files in your project, and they reach Atlan only through the `atlan`
plugin's connection, signed in as you. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills, commands, agents and scripts in this folder are the only source: nothing else serves
or copies them.
