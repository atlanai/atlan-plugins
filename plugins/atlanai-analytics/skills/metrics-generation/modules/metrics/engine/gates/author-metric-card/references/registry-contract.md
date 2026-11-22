# The registry's metric contract

The rules the Atlan Agent Registry applies to a `metric`, for spec version `1.0-draft.4`. The
Registry's specification is public: https://github.com/atlanai/ai-registry. An earlier spec is
refused; a metric stored under one upgrades by an append that raises `definition.version`.

**Unknown keys are refused everywhere.** One unexpected key anywhere fails validation before
any rule runs, and the gateway answers a bare `422` naming no
field. `extensions` is the only place arbitrary data may go.

---

## Definition

| field | type | required | note |
|---|---|---|---|
| `spec_version` | string | **yes** | exactly `1.0-draft.4` |
| `version` | integer | **yes** | > 0 |
| `dialect` | string | **yes** | non-empty; no allowlist |
| `formula` | string | **yes** | non-empty; never parsed or executed |
| `aggregation` | object | **yes** | |
| `output` | object | **yes** | |
| `source` | object | **yes** | |
| `replaced_by` | string? | no | successor declaration; does not withdraw a release |
| `population` | string? | no | free text |
| `exclusions` | string[] | no | free text |
| `grain` | string | no | free text |
| `filters` | string[] | no | opaque |
| `mandatory_filters` | string[] | no | opaque |
| `depends_on` | string[] | no | opaque |
| `joins` | Join[] | no | |
| `dimensions` | Dimension[] | no | |
| `examples` | Example[] | no | |
| `provenance` | object? | no | |
| `extensions` | map | no | preserved, uninterpreted |

**Retired, and refused if sent:** `status`, `approved_by`, `approved_at` (draft 2 : lifecycle and
releases are the registry's), `owner` (draft 4 : the envelope's `owner_id` is the owner) and
`domain` (draft 4). The card still carries a `domain`; the agent publishes it as
`extensions.metrics_agent.domain`.

## Nested structs

| struct | required fields | optional fields |
|---|---|---|
| `aggregation` | `type` | `reaggregation`, `null_handling`, `zero_denominator` |
| `output` | `alias`, `datatype` | `unit` |
| `source` | `entity`, `alias` | `table` \| `sql` (**exactly one**), `timeframe_column`, `parameters` |
| `parameters[]` | `name`, `datatype`, `default` | : |
| `joins[]` | `name`, `to`, `sql`, `cardinality`, `origin` | : |
| `dimensions[]` | `name`, `kind`, `sql`, `datatype` | `filter_form`, `values`, `values_truncated`, `value_map`, `required_join`, `description` |
| `examples[]` | `source`, `sql` | `question`, `group_by`, `filters`, `parameters`, `result` |
| `result` | `columns`, `row_count`, `hash`, `executed_at`, `rows` | : |

`parameters[].default` is a required JSON value, **not** optional. Send `null` if there is no
default; omit the key and the artifact is refused.

---

## Hard failures : the push is refused

- `spec_version` is not `1.0-draft.4`
- `version` ≤ 0
- any of `dialect`, `formula`, `aggregation.type`, `output.alias`, `output.datatype`,
  `source.entity`, `source.alias` is empty
- `source` supplies both `table` and `sql`, or neither
- a duplicate or empty `name` in `parameters`, `joins` or `dimensions`
- `dimensions[].required_join` names a join that is not declared
- **`examples[].group_by` names a dimension that is not declared**
- `examples[].result.rows` width ≠ `columns` width
- any unknown field, anywhere outside `extensions` : including the retired `status`, `owner`,
  `approved_by`, `approved_at` and `domain`

## Advisory only : publishes, records a permanent warning

| field | vocabulary |
|---|---|
| `aggregation.type` | `sum`, `count`, `count_distinct`, `min`, `max`, `ratio`, `custom` : **note there is no `avg`**; a mean is `custom` |
| `aggregation.reaggregation` | `""`, `sum`, `min`, `max`, `none` |
| `aggregation.null_handling` | `""`, `ignore`, `zero_fill`, `propagate` |
| `output.datatype` | `integer`, `decimal`, `string`, `boolean`, `date`, `timestamp` |

Also warned: a `ratio` with no `zero_denominator`; no `examples`; a non-empty `extensions`.

A warning does not block the write. It is stored against the artifact and does not clear, so
an out-of-vocabulary value is a permanent mark on the card.

---

## Retrieval

Search is Postgres full-text over a weighted vector:

| tier | field |
|---|---|
| **A** | `name` |
| **B** | `display_name` |
| **C** | `description` |
| **D** | `search_text` |

**The definition is not indexed.** No metric column is marked searchable : the gateway's own
note reads *"SQL/evidence are not searchable"*. The formula, the SQL, the examples, the joins,
the dimensions and the filters are **not** in the search vector except where a producer copies
them into `search_text` itself.

Everything findable about a metric is in those four strings. A word in none of them cannot find
the metric.

Weights affect ranking only, never the match set : a word in tier D still matches, it just
ranks last.

`name` and `display_name` are frozen at create. A correction needs the artifact archived and
re-created, and an archived name **stays reserved and stays invisible to the listing**, so the
name cannot be reused.
