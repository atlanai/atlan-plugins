# Compare answers with and without metrics

Read when analytics-demo requests a Fixit before/after comparison after loading an eval set.
The demo rollout is scoped to Fixit; Ticketfuddle keeps the ordinary accuracy check.
This is separate from correcting a metric: the existing `baseline` variant is a first
**with-context** run, not evidence of performance without metrics.

## Freeze the comparison

Keep the questions, trusted SQL/results, data, schema, raw query history, model, effort,
query tools and scoring method identical. The only added input is the frozen metrics.
Both arms can read the raw history, including a query used to derive an eval question.
Disclose that overlap; this measures reproduction and interpretation, not held-out accuracy.
Do not remove useful history from the baseline to manufacture a difference.

For a local run, prepare the packets with:

```
python3 <check-dir>/scripts/comparison.py --root <comparison-folder> \
  --cases <evals-run>/eval/cases.jsonl --gold <evals-run>/eval/answers.jsonl \
  --schema <schema-json> \
  --history <raw-history-jsonl> --metrics <metrics-json> --data <database> \
  --model <model-id> --effort <effort-or-unsupported> --harness <host-and-version> \
  --host codex --execution-schema <schema>
```

Use the source history, not authored saved queries or metric-engine summaries. The helper
writes separate answering packets and a private manifest. `prepared` is not an executed
baseline. The `baseline_key` excludes metrics so a new metric revision can reuse a verified
baseline; a changed set, source, data, model, effort or harness requires a new baseline.
Never overwrite a prior attempt. Keep the original manifest, raw transcripts, answers,
executed results and score together. Record the actual model returned by the host, not just
an alias requested at launch. A mismatch requires a new identity before reuse.

For a pre-generated set, persist the no-metrics arm once for that identity. Reuse only a
complete, transcript-audited baseline whose inputs still match. Offer **Use saved baseline**
or **Run on another model or effort**. A different model/effort means running both arms
again; comparing a new model with an old model's score does not measure metrics lift.

## Local SQLite execution from the demo

The supported automatic path is the local SQLite demo on a host with Codex CLI. Prefer Codex
for each fresh answering agent; use Claude only when Codex's agent limit prevents the pair and
the same isolation and evidence checks are available. After loading
the set and freezing its inputs, prepare the comparison with `comparison.py`. If a matching
baseline exists, attach it with `comparison_report.py --comparison <comparison> --reuse-baseline
<cache>`. Otherwise run
both arms with `comparison_run.py --host codex --comparison <comparison> --database <database>
--gold <gold> --schema <schema>`, then persist the completed no-metrics arm with
`comparison_report.py --comparison <comparison> --persist-baseline <cache>`. Render the review
artifact with `comparison_report.py --comparison <comparison> --sources <set> --diagnoses
<diagnoses>`; omit `--diagnoses` until source-backed diagnoses are available. The report is the
paired result and review artifact. When the pair is complete, import its metrics arm into the
loaded EVAL folder with `comparison_export.py --comparison <comparison> --evals <evals-run>
--variant baseline`. Then ordinary `ACC result` and save operate on the metrics score only.
Keep the paired report and no-metrics evidence locally; do not publish the no-metrics arm as an
Atlan Trident baseline. For the Codex agent-limit fallback, prepare a new identity with `--host claude` and
run both arms on that host; never reuse a Codex baseline for Claude. Do this only when the
Codex agent limit prevents completing the pair and Claude's transcript and isolation evidence
meet the same checks.

The runner resumes already-scored records unchanged and preserves incomplete or altered
attempts. If Codex CLI or the supported isolated execution path is unavailable, keep the
prepared inputs, offer to run them later on a supported host, and report that no comparison has
run; do not invent scores or call an ordinary with-context run a paired baseline.

## Run the pair

Use a fresh agent with no inherited conversation for each question and arm. Use the same
answering instructions and SQL tool in both. Give each only the question, its packet path,
and the SQL runner instructions from [run it](run.md#run-it). The without-metrics packet
has schema and raw history; the with-metrics packet adds metrics. Do not load analytics-ask
only in one arm: that would change both the instructions and the context. In this controlled
comparison both agents use the fixed instruction below:

```
<question>

Answer using the schema and query history in <packet> and any metric definitions there.
Read only those context files. <same read-only SQL runner instructions>
Run the SQL before answering. End with one JSON object:
{"answer": "...", "sql": "...", "used_context_ids": []}
List metric identifiers in used_context_ids when used; otherwise leave it empty.
```

Never pass tags, expected answers, source-to-question mappings, other questions, previous
answers or the private manifest. Do not give baseline agents registry tools or broad project
search. Packet separation is not a sandbox: enforce a file/tool allowlist where the host
supports it, and inspect transcripts for reads outside the packet and SQL runner. Mark
unverifiable or leaked attempts invalid; the existing golden-answer contamination audit
alone does not prove absence of metrics.

Persist the exact answer and raw transcript. The local runner executes each returned SQL
unchanged and records one result per arm and question. Require complete coverage and isolation
for both arms before reporting lift. Tool failures are not model errors. Show them separately
and use the same paired denominator; never compare percentages from different subsets. The pair
is not directly an ordinary EVAL variant. `comparison_export.py` imports only the verified
metrics arm through the ordinary recorder; the no-metrics evidence remains in the local paired
comparison and is linked by its export sidecar.

## Show what changed per question

Create a local review artifact with a row or expandable card for every question, including
unchanged answers and regressions. Each contains the question, trusted source and result,
each arm's SQL/result/verdict, and links to the raw transcripts. Explain a difference by
pointing to the exact metric rule and SQL difference: column choice, population/filter,
grouping/grain, or business vocabulary. If the trace does not support a cause, say unknown.
A metric present in the packet is not proof it was used.

Lead with the two scores and paired wins/ties/regressions, the model/effort, and whether
this reused a baseline. Show generated-source overlap and sample size. Do not claim a
metrics advantage when scores tie or regress. A source-backed question correction requires a
new set version and both arms to be rerun. Keep previous results visible; never select only
favorable questions. For a context-only correction, prepare a new comparison with the corrected
metrics and the same other inputs, model, effort and runtime; its baseline identity must match.
Use `comparison_report.py --comparison <new-comparison> --reuse-baseline <cache>`, run only
`comparison_run.py --host codex --comparison <new-comparison> --database <database> --gold <gold>
--schema <schema> --arm with_metrics`, and render the complete pair with
`comparison_report.py --comparison <new-comparison> --sources <set> --diagnoses <diagnoses>`.
Export the metrics arm as the next EVAL variant with
`comparison_export.py --comparison <new-comparison> --evals <evals-run> --variant v<N>`.
Then use ordinary `ACC result` and save for the metrics variants. This paired retest replaces
ordinary `accuracy-check` test-again so the answering protocol remains the same. The paired
comparison and no-metrics evidence remain local. If model or effort changes, start a fresh EVAL
folder for that identity rather than comparing it with another identity's variants.
