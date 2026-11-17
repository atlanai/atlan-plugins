# Fixit semantic validation set

Use `fixit-semantic-v3.jsonl` for current validation. The original v1 set is retained: its
losing-jobs question asked only for job identities while the gold also required margin amounts.
V2 explicitly requests gross margin in GBP. All eight questions are rerun in both arms, with
unchanged metrics, sources, model, effort and scoring. No question was dropped.

V3 retains all eight V2 cases and adds two compositions: gross margin on discount-triggering
completed jobs, and labour cost of nonbillable time. These combine existing source definitions;
they are not claims that analysts asked those exact questions. Each version was frozen before
its answering runs. Earlier results remain part of the validation record.

Ten curated questions, selected before fresh answering runs. This is a brought-set regression
fixture, not output claimed from the automatic generator. It tests Fixit only.

The original business definitions are shipped in
`plugins/atlanai-analytics/skills/analytics-demo/assets/yard/powerbi/hard-hat-area-site-performance.pbit`
(`DataModelSchema`, UTF-16 JSON). Its measure descriptions establish:

- Late Jobs: completed handovers more than seven days beyond plan trigger the late-completion
  discount; open jobs are not late.
- Rework Cost: putting things right after a snag costs the person's or machine's hourly rate;
  it is never billed.
- Material Waste: value of delivered material sent to the skip; cancelled orders are excluded.
- Gross Margin: contract value less labour (including rework) and delivered material cost;
  cancelled jobs are excluded.

The bundled `history/querylog.jsonl` supplies the on-time rate, billable task exclusions and
cost-weighted waste ratios. These are analyst-query definitions, not original BI measures.
Metric cards are checked interpretations of these sources, not independent ground truth.
The demo is fictional. No customer data or new company policy was invented.

The JSONL deliberately keeps expected SQL outside the demo assets and answering packets. Pass
it to ordinary `accuracy-check/scripts/check.py load` against a built Fixit run. Keep the complete
raw history available in both arms. The reference queries use the same loaded yard tables;
unqualified names resolve in the demo's sole attached data schema.

Four ratio questions opt into `representation_equivalence` on named trusted output columns:

```json
{"representation_equivalence":{"percent_fraction":{"on_time_rate":{"decimal_places":4}}}}
```

This accepts the same ratio as a fraction or percentage at the declared fraction precision.
It does not normalize money, counts or every numeric output. Raw answers and executed SQL remain
unchanged. Both paired reporting and ordinary EVAL recording use the same contract.

Questions ask for the business concept without restating its hidden rule. Reference and
plausible wrong-rule SQL were executed on the demo before model testing and returned distinct
results for every case. The money-loss control returns only negative-margin jobs. Share questions
explicitly request shares; the supplier question requests every supplier to avoid an accidental
top-one test. A Sprout-specific rework case was rejected before testing because both candidate
implementations returned NULL. Passing baseline questions must remain in the reported set.

See [the measured validation and limitations](fixit-semantic-validation.md) for all iterations.
The two composition rows also mention generated cards in their source notes; those are checked
interpretations. Original BI descriptions and raw analyst history establish their rules.
