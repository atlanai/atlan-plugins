# Exported context contract

All generation and semantic revisions use [the main metrics engine](../metrics-engine.md).
Its X3 gate owns card authoring and whole-card review; X4 owns relationships. This reference
explains publication boundaries, not an alternative authoring workflow.

The [publication schemas](../../../../modules/metrics/contracts/README.md) are the source of
truth for Registry wire formats. `ENGINE export` projects accepted core outputs to these
schemas. `PROJECT check` and `CONTEXT check` validate the exact bodies. Read the live
`registry__get_kind_schema` if a destination rejects a property. Repair semantic problems
in the engine and export again; do not hand-edit output to bypass its checks.

- `metrics.json` contains Registry metric bodies with stable engine identities, display
  names, descriptions, search text and `definition` at `1.0-draft.4`. The definition carries
  formula, source, population, grain, aggregation, units, mandatory and optional filters,
  parameters, dimensions, joins, examples and provenance. Preserve all supported fields.
- `queries.json` contains saved-query bodies at `1.0-draft.1`, with the verified statement,
  question, dialect, parameters, usage, analysis and source evidence. A query need not
  define a metric. Its parameter contract differs from a metric's source parameters.
- `links.json` preserves supported source, usage and dependency relationships. No self-links
  or dependency cycles; dependency links must agree with the metric definition.
- `metrics-source.json` and `publication-holds.json` bind the exported bundle to accepted
  engine outputs and eligibility checks. Copy them unchanged with the three body files.
  A partial first-card preview cannot produce this completed bundle.

Schema checks do not establish SQL execution, source equivalence, dashboard parity or
certification. Keep those evidence levels separate. Preserve population/security constraints,
source scope and actual revision-bound checks through reconciliation, publication and readback.
Existing legacy bodies remain readable for reconciliation; their existence does not authorize
another generation path.
