# Metrics engine: contributor docs

The metrics engine ships inside the Analytics plugin, at
`plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/`. These files are
for people changing it, so they live here rather than in the plugin: the plugin directory reviews
a plugin of more than 512 files, and nothing at runtime reads them.

| File | What it is |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to change the engine |
| [getting-started.md](getting-started.md) | Running the engine on each source, step by step |
| [versioning.md](versioning.md) | How the engine and its plug packages are versioned |
| [design/](design/) | Sockets, plugs, writing a plug, declared models, threat model |
| [examples/plugs/](examples/plugs/) | Example plugs no runtime feature installs: `jsonl-trace`, `databricks-direct` |

The guides the skills and gates read at runtime stay with the engine, in its `docs/guides/`.
