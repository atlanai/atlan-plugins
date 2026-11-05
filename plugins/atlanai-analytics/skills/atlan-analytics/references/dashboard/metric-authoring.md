# Native app-metric authoring

Contract checked against `atlanai/app-metric` main `7d5bda651b7404de3aedf69d6907e46a9e4783b6` on 2026-11-03. The GitHub connector could not access the repo; the existing authenticated GitHub CLI confirmed main was unchanged from the locally inspected source.

Use the native `metric` artifact envelope and `definition.spec_version: 1.0-draft.4`. Required definition fields are version, dialect, formula, aggregation, output and source. Exactly one of source.table/source.sql is present. Standard name, display_name, description and source identity are envelope fields. Never put domain, owner, status, approved_by or approved_at into the definition. Current app-metric supports opaque whole-query formulas; this bounded compiler uses that representation to preserve DAX context and blank semantics. Runtime execution remains in the bound SQL plan.

Follow the metric agent's reasoning, not stale schema fields:

1. Read the dashboard question and source description alongside DAX, model and native import SQL. A measure's name does not establish its population or grain.
2. Trace the measure to its physical rowset, preserving joins, import transformations and mandatory security filters. Do not invent row keys or relationship cardinality.
3. Separate the measure from the ways it can be cut. The current runtime supports only captured scalar scopes; do not imply arbitrary dimensions can be queried merely because a source has those columns.
4. Carry the source description into the standard description envelope. When source evidence establishes population, row grain, unit or exclusions, include `business_context` on that captured measure with the exact fields and an `evidence_ref`. These project to native definition fields. Omit unknowns rather than fill them with generic text. Provenance retains source evidence.
5. Build SQL and inspect each native definition before eval planning. Unsupported expressions remain visible conversion gaps. No owner certification or remote publication is implied.
6. After building, author questions in the dashboard's business language, using visual labels/format, described meaning and exact scope. A percentage format alone is not a denominator definition. Pass evidence-backed questions into the frozen evaluation plan; no answers are included.

Reference logic: metrics-agent `metrics-agent-pilot-author-metric-card` and `metrics-agent-pilot-author-questions`. Older authoring bundles include domain or different source.sql conventions; the current app-metric contract governs output. The plugin is not a replacement for the full metric agent's query-history discovery, merge/relationship stages or isolated evaluation runner.
