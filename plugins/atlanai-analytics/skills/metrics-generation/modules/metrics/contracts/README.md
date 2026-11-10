# Metric and saved-query publication contracts

Use these contracts for every Analytics input route before writing a Registry body. Internal
mining shapes, proposal cards and SQL observations are not publication schemas.

The schemas were checked against `registry__get_kind_schema` for `metric` and `sql_query`.
They retain the live definition types and required fields, close typed structs against unknown
fields, and pin the supported spec versions and numeric ranges. Arbitrary JSON remains allowed
only where the contract declares it. The local checker adds source, SQL and evidence checks;
passing structural admission does not certify a metric or execute SQL.

- [Metric definition](metric-definition.schema.json): `1.0-draft.4`.
- [Saved SQL definition](sql-query-definition.schema.json): `1.0-draft.1`.

## Types to preserve

| Path | JSON type | Meaning |
|---|---|---|
| `metric.definition.grain` | string | Describe one source row and its key, including composite keys and evidence limits. Never a key array. |
| `metric.definition.exclusions` | array of strings | Each exclusion is one item; use `[]` for none. |
| `metric.definition.population` | string or null | The measured population. |
| `metric.definition.mandatory_filters` | array of strings | Required SQL predicates. |
| `metric.definition.source.parameters` | array of objects | Each has string `name`, string `datatype`, and required JSON `default`. |
| `sql_query.definition.analysis` | object or null | Extracted SQL structure, separate from the SQL itself. |
| `sql_query.definition.analysis.filters` | array of objects | Each has string `column`, string `operator`, and optional JSON `value`. Never predicate strings. |
| `sql_query.definition.parameters` | array of objects | Each has `name`, optional `data_type` and optional `description`. No metric-style `default` field. |
| `sql_query.definition.review_status` | string | `unreviewed`, `verified` or `rejected`; a producer claim, not certification. |
| `metric.definition.examples[].result.rows` | array of arrays | Cells are strings or null, even for numeric results. |

For example, use `"grain": "One row per EXAMPLE.PUBLIC.ORDERS.ORDER_ID; uniqueness checked only in the current snapshot"`
and `"exclusions": ["Cancelled orders"]`. Keep machine-readable key columns and their evidence
in `extensions.atlan_analytics.grain_evidence` when useful; the `grain` field remains a string.

A saved-query filter is `{"column": "EXAMPLE.PUBLIC.ORDERS.STATUS", "operator": "=", "value": "paid"}`.
Do not copy the shape miner's opaque filter strings into this field. Read the full SQL and
author the structure only when supported. Keep unresolved raw predicates in extensions and
the unchanged SQL instead of inventing their meaning.

## Preflight and repairs

Read the live kind schema once per kind in a publication session, before creating artifacts.
Compare its `properties.definition` with the shipped schema. If it differs, hold affected
writes and identify the field; update the schema and regression tests before relying on a
new contract. Do not add a network call per card or silently rewrite invalid bodies.

Run `PROJECT check` for local drafts and `CONTEXT check` on the exact reconciled publication
bodies. Both use these schema files before semantic inspection. Errors name the field path,
including array indices. Repair the authored representation, preserve meaning and evidence,
and recheck the changed revision before publishing. Reuse the checked JSON in the existing
MCP batch calls and existing authorization flow.

## Complete readbacks and workflow mode

Preserve each complete, current released `registry__get_artifact` response. The assistant can
write its own private JSON file from an inline tool response or send the same envelope to
`PROJECT publication -` on stdin. A host-generated file is optional. IDs, search summaries,
local definitions and create acknowledgements cannot replace released readbacks.

Workflow authorization uses `mode: "auto"` or `mode: "ask"`, and `intent: "draft"` or
`intent: "publish"`. These values record the user's task scope; they do not change host
permissions or Registry release policy.
