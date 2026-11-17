# Business-semantics evals for both demos

These are supplied ten-question eval sets for Fixit and Ticketfuddle, grounded in the bundled
BI definitions and analyst history. They exercise the before/after and repair workflow merged
in PRs #190, #191, #193 and #194. They do not add a second comparison implementation or change the
scorer. Load a set with the ordinary `accuracy-check` flow after building that demo.

The intended trajectory is about **4/10 with schema only, 8/10 with the initial context, then
10/10 after repairs**. These are design targets until validated, not scores promised by the
fixtures. Keep all cases, passing controls and unsuccessful attempts in the measured report.
The original Fixit experiment gave the baseline query history as well as tables. An intermediate
run allowed table-value exploration. Main's current `raw` variant permits schema inspection and
execution of the final answer SQL only: no sample rows, distinct values or intermediate data
queries. These baselines measure different information access; their scores are not interchangeable.

## Why these questions

| Demo / case | Business meaning tested | Why raw SQL can miss it |
| --- | --- | --- |
| Fixit: discount and on-time handover | Contractual seven-day grace period; completed jobs only | Dates do not encode the contract's grace policy. These two cases share a rule. |
| Fixit: material sent to the skip | Local vocabulary for waste value | A skip is not a material or supplier category. |
| Fixit: supplier skip-waste percentage | Delivered value weighting | Quantities mix units; averaging order percentages changes the weighting. |
| Fixit: billable share and nonbillable labour cost | Task classification including Sprout helping | A task label alone does not decide billability. The two cases combine one classification with hours and money respectively. |
| Fixit: margin of discount-triggering jobs | Composition of two business definitions | Combining lateness and margin must not invent a discount amount. |
| Fixit controls: Dig rework, losing-job margins, waste spend share | Entity mapping, cost aggregation and a clearly named monetary share | Retain cases the raw agent can reasonably solve. |
| Ticketfuddle: gross sales | Primary-ticket eligibility and allocation of order fees | Joining tickets to orders can omit or multiply order fees. |
| Ticketfuddle: fee take rate | Finance uses fees divided by face value | A share of total fan spend has a different denominator. |
| Ticketfuddle: fee refunds and income categories | Company-owned fees, ledger categories and refund date basis | Facility fees belong to venues; refunds are separate dated ledger entries. |
| Ticketfuddle: net fee revenue | Ownership, resale-side income and refund offsets | Summing every fee column overstates company revenue. |
| Ticketfuddle: highest sell-through | Event capacity, primary issues and comps | Venue capacity is not the capacity of each show. |
| Ticketfuddle controls: total sell-through, scoped face-value average, online ticket count, tickets per order | Explicit populations, normalized channel labels and order grain | Keep ordinary data-reasoning controls, including cases raw SQL may solve. |

The JSONL carries per-case rationale and planned outcomes outside the answering prompt. Its
`source_paths` list names the bundled source files behind each rationale. Gold SQL and expected
results are controller-only. Questions do not disclose the hidden rule they intend to test.
Control questions may specify their population explicitly. Run
`python3 tools/tests/test_demo_business_semantics.py` to rebuild both databases through the normal
demo loader and compare all 20 gold queries with the expected JSON through read-only `PROJECT
execute`. It also executes the four SQL mutants in `injection-counterfactuals.json`, checks their
recorded values and confirms each differs from gold. The same check validates every declared
source path and rejects moving-date wording or SQL; Ticketfuddle questions with a 2026 window
need an absolute 2026 boundary.

Original sources are the Fixit Power BI measure descriptions and analyst `querylog.jsonl`,
and Ticketfuddle's `box-office-pulse.twb` calculations/custom SQL and `query_history.jsonl`.
Generated metric cards are interpretations of these sources, not independent ground truth.
Ticketfuddle dates are absolute 2026 windows; no moving period is redefined to a fixed year.

## Deliberate repair exercises

Build and validate the correct context first, retaining that checkpoint. The initial-context
faults below are **synthetic test injections**, not claims that the metric generator made these
mistakes. They model plausible semantic authoring defects and exercise the repair path. Do not
label the injected initial score as the accuracy of an untouched build.

| Demo | Injected defect | Expected affected cases | Source-grounded repair |
| --- | --- | --- | --- |
| Fixit | Omit `sprout_helping` from the nonbillable task exclusion list | Billable share; nonbillable labour cost | Restore the full four-category list from analyst history. This is one defect tested two ways. |
| Ticketfuddle | Gross Sales omits allocated order processing/delivery fees | Gross sales | Restore per-order fee allocation from the Tableau source. |
| Ticketfuddle | Net Fee Revenue includes facility fees collected for venues | Net fee revenue | Restore the company-owned fee ledger; facility fees are excluded. |

Apply each injection consistently to all affected calculation, description, example and card
copies; record exactly which saved queries are affected. Preserve the trusted BI/history and
raw data. Keep mutation recipes and expected results out of answering capabilities. Record
source-built, injected and repaired metric versions separately. Apply repairs through the
ordinary local edit helper, retain the original raw run, and retest all ten questions.

If a related question also changes, count it. If the model recovers a rule from remaining
context, count that pass. Do not weaken the raw agent, selectively drop cases, change grading,
or sample repeatedly until a desired score appears. A changed design needs a recorded reason,
a new frozen set, and both arms rerun. Main's script and independent judge handle representational
differences such as fractions versus percentages for both arms.

## Validation status

All twenty trusted answers and four affected-question SQL counterexamples pass the offline
regression on freshly rebuilt demo data. The three injected defects change the intended results.

Fixit's intermediate protocol, which allowed table-value exploration, scored 5/10 without context
and 6/10 with the injected context. Two additional natural failures occurred: a crew name was
mistaken for a job, and the agent did not retrieve the value-weighted waste-rate definition for
"skip-waste percentage". A name-resolution clarification passed its targeted check; a separate
vocabulary clarification was then applied and all ten questions were rerun, scoring 8/10. Those
iterations remain evidence; 8/10 is not claimed as the untouched build's first score. The
classification restoration also makes the stored nonbillable task values explicit, because one
later answer guessed a literal `nonbillable` task value.

Main #194 merged during validation. The schema-only baseline is being run afresh under that
protocol; the unchanged context arm is retained from its completed ten-question run. Final repair
runs and Ticketfuddle model validation remain pending. No measured 4/8/10 claim for both demos is
made yet.
