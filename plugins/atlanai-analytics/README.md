# Analytics

The analytics workload on the Atlan registry, as five skills. analytics-demo walks through a made-up firm end to end. analytics-build does the same on your own data, starting from nothing, a dashboard or a subject: it surveys what you already have, asks what you want to answer, and builds from reports first, then query history, then table definitions. source-survey says what a setup, a subject or a source holds, what can be built from it, and which metric names it offers. metrics-generation generates, checks, edits, proposes and publishes metrics and saved queries with the metrics engine. analytics-ask answers a data question from published metrics, with the definition it used, and turns answers into a dashboard. It finds your Snowflake, Databricks or other warehouse through Atlan or your own connector, and uses the Atlan plugin's connection.

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

In Cortex Code, install each from its folder, one chat message each:

```
/github-plugin-installer https://github.com/atlanai/atlan-plugins/tree/main/plugins/atlanai
/github-plugin-installer https://github.com/atlanai/atlan-plugins/tree/main/plugins/atlanai-analytics
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
