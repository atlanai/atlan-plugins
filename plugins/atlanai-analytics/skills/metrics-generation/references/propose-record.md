# The proposal record

Read when drafting or writing a proposal in [the proposal workflow](propose.md). A proposal
is what Metric Studio shows a metric's owners as a suggestion to apply or dismiss. Nothing
about the metric changes until a person applies it.

## Check for a duplicate first

List the workspace's `file` artifacts with `registry__list_artifacts`, passing `full: true`
so each row carries its path (list rows are summaries otherwise), and read those under
`improvements/metric/`.

- **Fix:** if a proposal for this `subject_id` is still `ready` with the same change, write
  no other. Tell the person it exists.
- **New metric:** if a proposal with an empty `subject_id` already proposes this
  `subject_name`, or the same measure under another name, whatever its
  `improvement_status`, write no other. If it was dismissed, its owners decided against it:
  say so.

## The file

Create it with `registry__create_artifact`, kind `file`:

- `metadata`: `name` (unique in the workspace), `workspace_id`, `path` under
  `improvements/metric/`, `media_type` `application/vnd.atlan.improvement+json`, and
  `source_metadata` `{"atlan_improvement": <record>}`.
- `content`: the same record as JSON.

## The record for a fix

The record names the metric (`subject_kind` `metric`, `subject_id`, `subject_name`), the
head it was made on (`base_version_ordinal`, and `base_content_hash` set to the head's
`definition_sha256`), `improvement_status` `ready`, `requested_by` `Atlan`, a one-line
`summary`, `evidence.runs` (one entry per question, as the proposal workflow's section 4
describes), and `changes`, one per field:

`{"id": "chg_1", "op": {"op": "replace_field", "path": "<definition field, dotted>", "value": <its new value>}, "rationale": "...", "evidence": {"run_count": <questions showing it>}}`

Never set `version` or `spec_version`; Studio raises the version when the person applies
the change. Each run's `question` is the question as the person asked it. It stays in the
proposal, which Studio shows the metric's owners and agents never read, and never enters
the change or a definition.

## The record for a new metric

The record leaves `subject_id` empty, sets `subject_name` to the new metric's name, and has
no base or `changes`. It carries `proposed`: `{name, display_name, description,
definition}`, the complete definition drafted and checked, without `version` (Studio sets
it when the person creates the metric). Add `subject_kind`, `improvement_status`,
`requested_by`, `summary` and `evidence.runs` as for a fix, plus a top-level `rationale`:
why the metric is needed.

If no warehouse tool could test its SQL, or a table or column is assumed, say so in the
`rationale`; never present it as tested. Nothing exists until a person creates the metric
in Studio.
