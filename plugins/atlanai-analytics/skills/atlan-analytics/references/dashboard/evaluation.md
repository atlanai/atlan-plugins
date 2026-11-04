# What the score means

Three distinct results appear in the experience:

1. **Coverage:** captured measures and structurally valid definitions within the stated page/model scope. Structure does not prove semantics or access to underlying data.
2. **Dashboard parity:** supplied report references versus observed answer results, bound to the exact capture, definition and context. This implementation compares scalars exactly, or numeric values within an explicitly supplied absolute tolerance. Types matter. Explicit `value:null` is an observed blank and can match another blank; an absent `value` is missing evidence, not blank. SQL actual observations require `executor:warehouse` and the current translation query hash. Missing/invalid references, stale definitions and mismatched contexts are unmeasured; execution errors are failed measured cases. Display all denominators.
3. **Held-out question accuracy:** not implemented by this local parity tool. Display Not run. It requires an isolated answerer, a frozen held-out split, gold inaccessible to the answerer and transcript checks. Never infer it from parity or from a caller's “independent=true” flag.

The current App Metrics workflow at revision `7d5bda651b7404de3aedf69d6907e46a9e4783b6` already supplies `/atlan-analytics build`, `eval` and `improve`. Reuse that workflow for warehouse-backed agents: local cases/answers separation, fresh question-only subagents, deterministic result-set grading, contamination checks and train/test reporting. Its agent runner currently requires Claude Code and warehouse SQL; it is not automatically a Codex eval runner. Those need explicit execution adapters.

`dashboard_evaluate` stores references in local `grader-only.json`. Its read/exported runtime context does not expose that file. This is an API separation, not an OS sandbox: a fully privileged host could still read the local file. A genuinely blind benchmark needs a separately restricted process/toolset.

The Registry dataset projection contains `input.question`, `expected:null` and a gold digest in `extra`. Gold must not be published as answerer-retrievable context. Results are a local report until an approved Registry eval workflow uploads them with the correct agent, dataset, experiment and trace identities.

Start with one useful page. After source reconciliation, grow toward 20–50 real questions with breakdowns, date windows, denominators, blanks and known semantic traps. Do not make a universal accuracy claim from a few dashboard cards.

## Automated baseline suite

`dashboard_plan_evals` runs after SQL metric building and creates one question per captured measure with fixed capture/definition/context hashes. This deterministic starter suite is exhaustive over captured measures, not over visual breakdowns, all filters, business logic or natural-language interpretation. It contains no answers and does not qualify as a held-out test. For richer coverage, capture additional real dashboard scopes and run separate jobs; this release does not generate speculative filter values.

`dashboard_plan_evals` accepts optional dashboard-authored business questions with metric IDs and evidence references. These keep the compiled scalar and captured scope. Missing entries retain a measure baseline; fabricated cuts and dates are not allowed. Replanning freezes a new generation. Record that exact `plan_generation` with each result; an old task is refused even if its SQL has not changed.

`dashboard_next_check` returns source and warehouse execution requests. The host executes them using authorized connectors/browser tools and supplies results through `dashboard_record_check`. Each check is accumulated; retries replace one case. Unconverted, unrun and unavailable cases remain visible. Planned source references require the Power BI executor; SQL results require the warehouse executor and exact query hash. These are host-reported bindings, not independently signed receipts.

`dashboard_answer_plan` returns a fresh warehouse query and scoped validation state, without reference values. The question picker draws from the frozen questions of metrics that passed their planned checks once the run has no executable pending cases. Full agent readiness requires a plan, complete scope, warehouse SQL for every captured metric, and every planned check passing. Partial success is labelled as such. New filters, data cutoffs or semantics invalidate the applicability of those checks.
