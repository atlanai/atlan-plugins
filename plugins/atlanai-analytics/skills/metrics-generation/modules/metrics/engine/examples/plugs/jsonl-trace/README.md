# jsonl-trace (example plug)

A community example of an **observer plug**: a minimal tracing backend that writes every run
event (each stage's start and end, every artifact, gate and decision) as one line of JSON. It
is the one to copy when writing an observer for another system (Langfuse, OpenTelemetry, a log
pipeline). It is not maintained with the core.

- Plug name: `jsonl-trace` (observer plug)
- Writes to `<run>/.trace/events.jsonl`, or to the file `METRICS_AGENT_TRACE_FILE` names
- Paths, never contents: an artifact event records where the artifact is, not what is in it.

## Install

From the root of this repository, into the same environment as `metrics-agent`:

```bash
pip install -e examples/plugs/jsonl-trace
```

It has no dependency beyond `metrics-agent`, and registers the plug under the
`metrics_agent.plugs` entry point. `metrics-agent plugs` lists it once installed.

## Environment

The plug reads one optional variable:

| Variable | What it holds |
|---|---|
| `METRICS_AGENT_TRACE_FILE` | where to write the events instead of `<run>/.trace/events.jsonl` |

## run.yml

An observer is chosen by listing it under `observers`:

```yaml
version: 1
scope: my-estate
dialect: duckdb
mode: standalone
inputs:
  history: [local-files]
  bi: []
  schema: [local-files]
  sql_check: local-files
publish: [files]
observers: [jsonl-trace]
tables: [retail.merch.fct_season_performance]
settings:
  local-files:
    history: querylog.jsonl
    database: retail.duckdb
    witness_mode: explain
```

Tracing is optional: when an observer fails, the run says so once and goes on untraced. With
`METRICS_AGENT_TRACE_REQUIRED=1` set, a failure stops the run instead, for you to fix tracing or unset the
requirement.

## Check it

```bash
metrics-agent setup --check run.yml
```

## Conformance kit

The observer contract, as tests, runs against this plug offline. From the root of this
repository:

```bash
python -m pytest tests/test_conformance.py -k TestJsonlTrace
```

To test an observer of your own the same way, subclass `ObserverConformance` from
`metrics_agent.testing.conformance` (see *Writing a plug*, `docs/design/writing-a-plug.md`).
