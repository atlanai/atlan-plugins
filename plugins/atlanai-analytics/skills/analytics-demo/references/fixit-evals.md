# Find questions that test Fixit's meaning

Use for Fixit when asked to strengthen its accuracy check. Keep Ticketfuddle on its existing
flow. The aim is to test a business definition, not whether two SQL strings look alike.

Start with the original Power BI model's measure descriptions, partitions and verified analyst
queries. Look for a term whose everyday meaning differs from Fixit's definition, a hidden
population or grace period, or a familiar word that maps to several plausible columns. Keep
source wording when it is meaningful. A generated metric description alone is not independent
proof of what the business intended.

For each candidate, retain outside the answering packets:

- The original source location and the exact rule it establishes.
- A plain question that uses the business term without giving away its filter or formula.
- Trusted SQL and a plausible alternative interpretation; execute both on the demo to check
  that the distinction actually changes the answer.

For example, the Late Jobs measure establishes a seven-day allowance: asking how many jobs
finished late tests that definition. Saying “more than seven days late” in the question tests
SQL transcription instead. Do not invent company jargon, turn ordinary words into unsupported
policy, reverse the SQL's direction, or ask for losing jobs when the SQL returns all jobs.

Pick a balanced eight-question set before seeing the answering runs. Include controls and
new semantic cases, not eight paraphrases of the same rule. Keep rejected candidates and the
reason; do not replace a passing baseline question merely to make the score gap larger.
Retain the full raw history in both arms even when it contains the reference SQL. If that
makes the baseline strong, report it.

Use the existing generated-set workflow when its source pool supports the questions. For a
curated validation set, use the ordinary brought-set JSONL contract and record its provenance;
do not label it as an automatic generated-set result. Keep test fixtures and trusted answers
outside the demo assets, source survey and answering context.

A percentage and a fraction can mean the same thing. For a ratio question whose source permits
both presentations, declare the result column's representation and meaningful precision in the
trusted grading contract before running it. Do not apply scale equivalence to money, counts,
IDs or every numeric column. Keep the original SQL and values visible; scoring normalization
is not an agent correction.

Run the paired comparison from `accuracy.md`. Diagnose failures from the actual SQL and source
rule. Fix a metric only when it is missing or contradicting that rule, including examples and
other repeated statements. Version the context, reuse the unchanged no-metrics baseline and
rerun the entire metrics arm. A question or reference correction requires a new set and both
arms again. Report 100% only when every question in the fixed set has valid, isolated execution
and passes; a tool failure or dropped question is not an accuracy improvement.
