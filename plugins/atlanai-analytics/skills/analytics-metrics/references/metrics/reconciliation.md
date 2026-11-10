# Reuse existing work without replacing its meaning

Read complete existing definitions into `existing.json` for the chosen workspace before
`CONTEXT check`. Keep that inventory current while preparing the plan. A list of names is not
enough. The checker reuses exact contracts, keeps the full existing definition and updates
typed relationship endpoints. It never treats matching sample totals as proof of equivalence.
Preserve `id`, `kind`, `name`, `display_name`, `description`, `source_id` and `definition`.
Registry compact reads may omit repeated descriptions: use `registry__get_artifact` with
`verbose: true` for missing body fields. Never fill missing live prose from the local candidate.

If `reconciliation-review.json` has entries, compare each candidate with the listed existing
definitions. Read both bodies, including extensions. Explain their meaning, population, grain,
dimensions, parameter constraints and limitations. For a parameterized query, identify the
binding that reproduces the candidate statement. A broader query without that binding does
not answer the original question. Inspect case normalization, null handling and empty inputs.

Prefer the existing definition when it covers the chosen task. Additional candidate dimensions
can stay local; show what is uncovered instead of silently discarding them or revising the
existing metric. Do not claim complete coverage when selecting a narrower definition. If a
meaningful population difference changes the requested answer and the sources do not resolve
it, ask one focused business question. Routine exact reuse needs no extra user approval.

Record each semantic decision in the private context's `reconciliation-decisions.json`:

```json
[{"kind":"metric","candidate_name":"order-total",
  "candidate_hash":"copy the current candidate_hash from the review",
  "live_id":"metric_example","live_hash":"copy that option's current live_hash",
  "action":"reuse","reason":"Existing total covers the selected use case.",
  "comparison":{"meaning":"Sum of paid order amounts.",
    "population":"Both include paid orders only.","grain":"One row per order.",
    "dimensions":"Existing supports region; candidate channel remains local.",
    "parameters":"Neither has parameters or viewer binding constraints.",
    "limitations":"Channel breakdown is not covered by this reuse."}}]
```

Replace the example with the actual comparison. `reuse` selects the complete existing body;
`revise` proposes an intentional change; `create` records why this is a distinct measure
despite a suggested match. A decision authorizes no remote mutation. The normal publication
scope and approval rules still apply. Stale hashes or ambiguous targets require a fresh review.

Rerun `CONTEXT check`. Inspect `plan.json` including its `coverage` notes; they persist on repeat
checks. Original candidates and their checks remain in `reconciliation-history.json`. The
checker moves execution evidence across a rename only if the exact definition is unchanged.
After any definition adoption or revision, recheck the selected definition and representative
bindings before calling it executed. Do not transfer an old hash or assert dashboard parity.
Use recorded coverage bindings for parameterized examples, without adding defaults to live SQL.

Before any revision, the checked body already has the next metric semantic version. Never
increment it after approval. Read the remote head again and verify it still matches the reviewed
base; a changed head requires reconciliation. Reuse needs actual release readbacks before the
workflow can promise team retrieval. A zero-write plan alone is not a release receipt.
