# Consistent metric decisions

Use this guidance when proposing, merging or writing metrics, including local authoring.
Keep business judgment: the aim is the same meaning from the same evidence, not a fixed
number of metrics or identical prose. Follow the task's existing schema and validation gates.

## Start from the evidence

Read every selected declaration and reusable statement before finalizing the collection.
Account for each in the existing proposals or source notes: contributes a metric, supports
an existing metric, saved query only, or unresolved, with a short reason. Each declared measure
governs exactly one metric card; its algebraic decomposition does
not declare additional metrics. A separate metric needs its own captured expression or
explicit declaration, not a new aggregate obtained by distributing the source formula.
Reconcile declared and mined measures so a processing order or batch boundary does not drop a measurement.
Do not turn every aggregate into a metric or invent a ratio merely because its parts exist.

For a declared measure, preserve its formula, source representation, population, mandatory
restrictions, grain, units and time meaning. Trace referenced measures and rowsets. Prefer
that declaration over an independently reconstructed calculation. A dialect translation must
preserve semantics, including nulls, empty populations and zero denominators. Do not silently
replace missing values with zero or change which rows contribute to make SQL execute.

For mined measures, read the complete statement, including joins and nested filters. Two
expressions with the same name or today's total can still measure different populations.
Keep those meanings separate, with names and descriptions that expose the difference.
When equivalent statements support one metric, use the task's source authority rules to
choose its representative. Among equally authoritative, semantically equivalent sources,
choose by stable source identifier rather than encounter order. Copy its expression and
retain the other sources as evidence. Do not synthesize a new formula to simplify a merge.

## Reconcile components and populations

Review every aggregate occurrence in the source inventory or gate bundle, including nested
scopes. A business quantity does not cease being a metric because another metric consumes
it. Propose independently meaningful monetary subtotals, durations and managed-population
counts; relate them to their consuming measures. Keep mere arithmetic scaffolding as a
component and explain the distinction from source meaning, not from SQL nesting.

An inner join that may drop unmatched records changes the population. Do not silently merge
that measure into its unjoined version because today's totals match. Only explicit source
constraints proving preservation justify treating it as dimension enrichment. Otherwise keep
the population variants explicit and carry their own safe breakdowns. Apply this rule to
every supporting source before selecting a representative, not only to a convenient example.

## Preserve useful breakdowns

Consider the union of breakdowns evidenced by the declaration and all supporting statements,
not just the representative example. Keep each distinct, compatible business breakdown and
its source trace. Check its expression, grain and join cardinality: adding it must not
multiply contributions or silently narrow the metric's population. An incidental label,
unrelated column or unsafe join is not a dimension. Explain exclusions in existing source
notes rather than choosing an arbitrary short list. Do not merge distinct mandatory
populations just to obtain more dimensions.

## Check meaning as well as execution

Before finishing a card, compare formula, source, population, mandatory filters, grain,
aggregation, units, time semantics and breakdowns against its evidence. For a ratio, trace
both populations and preserve the source's zero-denominator behavior.

Where execution is available, compare the authored calculation with its source on a normal
case, an empty population, and relevant edge cases such as excluded rows, a zero denominator
or an evidenced breakdown. Use read-only probes or synthetic cases without changing source
data. Where a check is unavailable, record the limitation; do not invent a passing result.
An executed example proves that example runs, not that the card's formula matches the source.
Execute the calculation assembled from the actual card's formula, source and mandatory
filters as well. Predicates inside a derived rowset stay inside it; its outer filters can
refer only to exposed columns. A physical qualification cannot be applied to a hidden table
through the derived rowset. Record embedded restrictions in the population description.
The local `check-metric` helper now executes both the example and this assembled calculation,
including an empty-population probe. Fix the authored card after a failed composition, then
rerun the check; do not substitute another passing example.

A translation or validation failure keeps its existing disposition and reason even if a
separate handwritten query executes. An alternative check is additional evidence, not a
repair of the failed translation. Apply the same treatment to the same unresolved condition,
retain its source trace and continue the usable collection. Do not weaken a gate or ask the
user to resolve wording or representation choices already settled by the source evidence.

Before finishing cards, apply the shared [whole-card review](review-metric-card.md). The
assistant retains authorship; use structured evidence and a separate review reading to repair
omissions, unsupported bindings, guessed grain and misleading component descriptions.
