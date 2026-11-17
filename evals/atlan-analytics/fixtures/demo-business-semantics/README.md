# Business-semantics evals for both demos

These are supplied ten-question eval sets for Fixit and Ticketfuddle, grounded in the bundled
BI definitions and analyst history. They exercise the before/after and repair workflow merged
in PRs #190, #191, #193 and #194. They do not add a second comparison implementation or change the
scorer. Load a set with the ordinary `accuracy-check` flow after building that demo.

The intended trajectory is about **4/10 with schema only, 8/10 with the initial context, then
10/10 after repairs**. The revised Fixit set measured **4/8/10**; Ticketfuddle measured **3/8/10 semantic accuracy**,
with a repaired-response formatting caveat below. These are observed development-set scores, not guarantees. Keep all cases, passing controls and unsuccessful attempts in the measured report.
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
| Fixit controls: named-crew rework, total rework labour, delivered-material spend, material-waste cost | Explicit entity scope, ordinary joins and monetary aggregation | Keep ordinary SQL checks distinct from the six business-rule cases. |
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

The original Fixit cohort under the intermediate protocol, which allowed table-value exploration, scored 5/10 without context
and 6/10 with the injected context. Two additional natural failures occurred: a crew name was
mistaken for a job, and the agent did not retrieve the value-weighted waste-rate definition for
"skip-waste percentage". A name-resolution clarification passed its targeted check; a separate
vocabulary clarification was then applied and all ten questions were rerun, scoring 8/10. Those
iterations remain evidence; 8/10 is not claimed as the untouched build's first score. The
classification restoration also makes the stored nonbillable task values explicit, because one
later answer guessed a literal `nonbillable` task value.

Main #194 merged during validation. Under its schema-only protocol, the original Fixit cohort measured **1/10**
without context, **8/10** with the prepared context, **9/10** after classification restoration,
and **10/10** after adding the complete source crew name/ID/type mapping. That mapping includes
all eight crew members, both people and machines, and contains no costs or expected answers.
The unchanged 8/10 context arm was retained from its completed run, with native answers/results
and identical judge briefs verified; it was not resampled. The 1/10 baseline is below the
roughly 4/10 target, so the SQL-control cohort was revised against the stricter
schema-only protocol. No score or question has been silently replaced.

Ticketfuddle measured **3/10 schema-only, 8/10 injected context and 10/10 repaired semantic
answers**. The two seeded misses matched the predicted gross-sales and facility-fee errors.
One repaired response (TF07) appended two stray characters after a complete JSON object. Its
semantic score uses that exact object with only the suffix removed; SQL, answer and transcript
are unchanged, and the model was not rerun. Strict JSON format compliance is **9/10**, so this
is not an unqualified end-to-end 10/10. [Review evidence](validation-results.json) records SQL, observed results, grading
and earlier iterations without private session transcripts. All six current-cohort stages contain ten isolated answers. The full repository checks pass: 664 tests (99 skipped), 151 scoring checks and 47 context checks.

## Fixit control-cohort revision

The current Fixit JSONL is revision 2. Its six business-rule questions are unchanged. Two proposed
controls in the original set also required business definitions: losing-job margin combines cost
streams and a job-status population, while waste spend share overlaps the weighted-waste cases.
They are replaced with source-backed totals for rework labour and delivered material spend. Dig
is explicitly identified as a crew member so that control tests a named-crew slice rather than
an unspecified entity type. The replacements' gold SQL was executed before any revised model run.

The new experiment forks the corrected context, holds the entity/vocabulary clarifications fixed,
and seeds only the declared missing-task classification. Both arms are rerun on all ten questions;
no score is transferred from the previous question set. Earlier questions, SQL and measured
1/8/10 results remain in the review evidence. A fresh baseline near four is a hypothesis, not a
guarantee or a reason to change grading. Revision 2 measured **4/10 schema-only, 8/10 injected context and 10/10 repaired context**. All four ordinary controls passed raw; all six rule cases failed raw. The two initial-context misses exactly match the declared missing-task classification. All ten questions were retested after the normal metric edit restored the rule.
