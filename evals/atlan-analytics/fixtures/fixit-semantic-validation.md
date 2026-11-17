# Fixit semantic validation, 2026-11-16

The final ten-question set scored **8/10 without metrics and 10/10 with metrics** after a
source-backed description clarification. The initial metrics score was 9/10. This is a small
fictional-data reproduction check, not a claim of production accuracy or stable model lift.

[`fixit-semantic-validation.json`](fixit-semantic-validation.json) preserves every iteration's
questions, executed SQL, raw results, scores, input hashes and runtime/scorer identities.
[`fixit-semantic-v3.jsonl`](fixit-semantic-v3.jsonl) is the final supplied set, not automatic
generator output. Its ten reference queries and counterfactual queries execute against bundled
CSV data in `tools/tests/test_fixit_semantic_set.py`.

| Iteration | Without metrics | With metrics | Change and interpretation |
| --- | --- | --- | --- |
| V1, eight questions | 4/8 | 8/8 | One margin question under-asked for required GBP values; do not count that as a clean win. Two failures used the same grace rule. |
| V2, eight questions | 7/8 | 8/8 | Explicitly requested margin amounts; reran both arms on all eight. Baseline recovered other rules. |
| V3, ten questions | 8/10 | 9/10 | Retained all eight; added two compositions of existing rules before execution. |
| V3, description retest | 8/10 reused | 10/10 | Clarified one metric description, then reran all ten metrics answers. |

The final baseline missed these meanings:

- “Sent to the skip”: looked for a material or supplier named skip and declined to answer.
  Material Waste maps that vocabulary to GBP 7,646.50 of waste. This dataset does not distinguish
  the delivery filter independently because excluded orders have zero waste.
- Gross margin on jobs incurring a late-completion discount: invented a 1% per-day discount,
  used any positive delay, and included undelivered materials, yielding GBP -57,758. The source
  definitions require the completed-job >7-day scope and ordinary Gross Margin, yielding
  GBP -6,540. These multiple errors are not independently attributed.

The original metrics miss was nonbillable labour cost: it translated prose labels into SQL
string literals with spaces, matching only rework (GBP 33,800), instead of the four stored
values (GBP 38,705). The formula was already correct. The local edit helper raised Billable
Utilisation to version 2 and clarified the description with `tea_break`, `yard_tidy`, `rework`
and `sprout_helping`, all present in original history and data. No gold value or eval example was
added to the metric. The fix is a context clarification for a model grounding error, not a
corrected calculation. Exact change is in the validation JSON.

Both arms used Codex CLI 0.169.0, gpt-6-luna, medium effort, fresh isolated sessions, the same
read-only tools, SQLite database, schema and all 110 raw history records. Only the metrics arm
received metric cards. No Claude inference was used. The retest manifest differs only in its
metrics input hash; baseline identity remains unchanged. It reused the verified baseline rather
than sampling a more favorable one. All answers passed transcript/capability auditing. Some
final SQL was replayed by the controller where no matching agent tool result was available;
the per-answer result source is recorded. No billed dollar figure was available.

Percent/fraction equivalence is explicit on four trusted ratio columns at declared precision.
It never applies to money or counts, and does not change raw answers. Offline regrading of the
older comparison changes 5/8 to 7/8 without metrics, with 8/8 unchanged with metrics: those two
old misses were formatting, not semantic lift. That regrade is not a fresh execution.

## Fresh demo journey

A fresh project loaded all five bundled tables, selected dashboard plus history, built seven
metrics and thirty saved queries, checked all 37 items, kept context local, answered the first
job-margin question, loaded the supplied eval set, compared both arms, applied the description
edit, and reached the ordinary `accuracy.py result` 100% block after verified export. Five saved
queries still need source review; zero were held. All thirty queries executed; all seven metrics
had an example, composed calculation and empty-population probe checked. The source engine
retained 108 history rows after two privacy drops; answering packets used the full raw 110.

This was a scripted skill journey with supplied choices, not native choice-UI testing. Gate
answers were authored from the fresh sources by Codex and consumed by the engine; it was not
an unattended metrics-model API run. A matching pinned runtime completed the build after fresh
bootstrap failed on this machine's Python platform metadata/ensurepip path. Fresh data and
context generation passed; a clean runtime installation did not. No registry publish was run.

For reproduction, follow `analytics-demo`, keep Fixit local, then load the V3 JSONL with
`accuracy-check/scripts/check.py load`. Follow
`accuracy-check/references/paired-comparison.md` to prepare/run/report/export. On this observed
miss, `accuracy.py fixes` and `edit_metric.py` applied the recorded description change; a new
comparison reused the baseline and ran `--arm with_metrics`. Export initial metrics as
`baseline` and retest as `v1`; `analytics_eval.py summary --require-isolation` verifies every row,
then `accuracy.py result --changed ...` produces the final demo result. Do not expect identical
baseline mistakes from a new stochastic run.

Validation: `make check` passed 676 tests (99 skipped), 129 scorer checks and 47 context checks.
The HTML table and detail tabs were exercised in Chromium without console errors. Ticketfuddle
and other demos retain the ordinary accuracy path.
