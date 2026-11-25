# Analytics

The analytics workload on Atlan Trident, as seven skills. analytics-demo walks through a made-up firm end to end. analytics-build does the same on your own data, starting from nothing, a dashboard or a subject: it surveys what you already have, asks what you want to answer, and builds from reports first, then query history, then table definitions. source-survey says what a setup, a subject or a source holds, what can be built from it, and which metric names it offers. metrics-generation generates, checks, edits, proposes and publishes metrics and saved queries with the metrics engine. analytics-ask answers a data question from published metrics, with the definition it used, and turns answers into a dashboard. eval-generation turns the questions a team already asks into an eval set with trusted SQL, kept locally or saved to Atlan. accuracy-check runs a set against the context and scores it, saying why each miss failed. It finds your Snowflake, Databricks or other warehouse through Atlan or your own connector, and uses the Atlan plugin's connection.

## Install

In Claude Code, one command installs Analytics and `atlanai` automatically:

```
claude plugin install atlanai-analytics --marketplace atlanai/atlan-plugins
```

In Claude Code's terminal, a build's progress shows in Claude Code's own task list. Some models have the task list on
by default; for the others, such as Opus 5.5, add this to the `env` in your Claude Code settings
(`~/.claude/settings.json`) once, then start a new session:

```json
{"env": {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}}
```

Without it, each finished build step shows as one line in the chat.


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
Its helpers report usage events to Segment, as `atlanai` does and under the same switches: which stage a run
reached, which choices were made, and counts. Never a metric's or source's name, a question, an answer or SQL.
`ATLAN_PLUGIN_TELEMETRY=0` or `DO_NOT_TRACK=1` turns it off, and the [`atlanai` README](../atlanai/README.md#usage-telemetry) lists every event.
Working files stay in your project. Input plugs use the source connections you select; Atlan Trident
writes use the `atlanai` plugin's connection, signed in as you. See Atlan's
[privacy notice](https://atlan.com/privacy-policy/).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills, commands, agents and scripts in this folder are the only source: nothing else serves
or copies them.
