# Analytics

The analytics workload on the Atlan registry. Ask a data question and get an answer from your published metrics and saved queries, with the definition it used. Mine your warehouse's query history into managed metrics and saved queries, build a talk-to-data agent that reads them, measure it against a local eval set and improve it round by round, or propose a metric fix from a conversation. It finds your Snowflake, Databricks or other warehouse through Atlan or your own connector, and asks you to connect one when it is missing. It uses the Atlan plugin's connection. Turn a Power BI dashboard into SQL metrics, compare dashboard answers, evaluate a fresh Claude or Codex agent and ask questions from released definitions.

## Install

In Claude Code, one command installs Analytics and `atlanai` automatically:

```
claude plugin install atlanai-analytics --marketplace atlanai/atlan-plugins
```



Other hosts install plugins one at a time, so add `atlanai` first. In Codex:

```
codex plugin marketplace add atlanai/atlan-plugins
codex plugin add atlanai@atlan
codex plugin add atlanai-analytics@atlan
```

## Privacy

Analytics itself has no hooks or shipped executable binaries. Its skills run bundled Python helpers and may
set up an isolated metrics-agent environment with pinned dependencies after authorization.
Working files stay in your project. Input plugs use the source connections you select; Registry
writes use the `atlanai` plugin's connection, signed in as you. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills, commands, agents and scripts in this folder are the only source: nothing else serves
or copies them.
