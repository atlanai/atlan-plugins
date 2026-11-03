# When the loop stalls, categorise before grinding

The tell: 2–3 rounds in a row where validation hasn't cleared the noise band, even though the changes
looked right. Stop changing content. Spend one round sorting the failures instead.

Spawn a fresh failure analyst (`atlanai-analytics:atlan-analytics-failure-analyst`) in **bucket mode**, with the same train-only inputs
as a normal round. It sorts every remaining train failure into one of these buckets:

| bucket | tell | what to do instead of another content round |
|---|---|---|
| Context gap | The agent never had the definition. It guessed, or searched and found nothing relevant. | Keep going. This is the loop's home ground. |
| Served, not used | Search returned the right metric or query, but the SQL ignored it. | Change the prompt or structure: the order of the "look up first" rule, or a clearer metric description. Adding more content won't help. |
| Wrong definition | The agent followed a governed definition that is itself wrong or stale. | Fix the definition with the data owner (a metric version). Don't paper over it in the prompt. |
| Grader disagreement | The answer looks right, but the comparator or the gold says no. | Fix the gold or comparator, then re-`record` every variant's stored answers into fresh variants. The comparator is harness, so the user re-approves it with `EVAL approve-harness`. If the ranking flips, say so and propose restarting from baseline. |
| Harness / infra | Tool error, timeout, the warehouse refusing a query. | Fix it, and exclude those cases from the denominator until then. |
| Variance | Identical code flips pass/fail between reps. | You're at the noise floor. Report best-so-far, and offer more reps or more cases. |

Write the bucket counts to `H/vN/change.md` in place of a diff. Tell the user "N of the
remaining M failures aren't context gaps", and what each cluster needs.

**Long tail.** When many concepts each cost 1–2 cases and none has context yet, one
**breadth pass** can cover more than ten serial rounds. Draft a minimal metric or saved query
for every uncovered concept and apply them together. This deliberately breaks the
one-change-per-round rule. Say so, and label the round as a breadth pass. A metric the person
running it may not create is proposed instead, as a new metric in `skills/atlan-analytics/references/improve/03-loop.md`.
