# Metrics engine

The Analytics plugin owns this engine, its input plugs, BI readers, schemas, compiler corpus,
authored-gate instructions. Runtime packaging is generated from these readable files.

The core consumes common formats. Inputs and readers are discovered through Python entry
points. The plugin runs the engine with local file output, then uses its existing Registry
publisher. See the plugin's module guide for the customer workflow.

The Snowflake input reads bounded native `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` and
aggregates executions into common records. Existing prepared shape tables remain supported.

## Development

Run the repository's make check and make validate targets. The focused integration checks
live in tools/tests at the repository root; run make test-metrics-runtime with a private
METRICS_RUNTIME. The ported engine test suite is omitted from this repository for now.

Read PIPELINE.md for the core execution contract and docs/design/sockets.md for extension
contracts. Package names and entry-point groups remain stable for existing integrations.
Never commit generated data, environments, customer inputs or credentials.
