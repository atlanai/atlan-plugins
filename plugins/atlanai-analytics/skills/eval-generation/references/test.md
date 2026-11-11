# Test accuracy

Read when running `test`, and for **Publish** when saving a set to Atlan Trident. The set must
be set up for scoring first (`SCOPE load` does it; after generating, `SCOPE stage`), and every case
needs its gold result in `<evals-run>/eval/gold/`.

## Why the score can be trusted

- Each question goes to a **fresh** agent whose prompt is the question and the fixed lines
  below, nothing else: no case id, tags, SQL, expected value or other question.
- The agent answers the way a person's question is answered, through `analytics-ask` on the
  same context. A score therefore measures the context the person built.
- **You** run the agent's SQL and **the script** compares the two results. No model grades a
  result. A transcript that opens anything under `.atlan-evals/` or names an eval artifact is
  marked contaminated and fails.

## Run it

1. `EVAL todo --variant baseline --reps 1` lists the questions not yet run. A stopped run
   resumes here.
2. Start one subagent per question, all at once (a set has 5), so the test takes as long as its
   slowest question. For each:
   1. Spawn a subagent with this prompt, filled in:
      ```
      <question>

      Answer this question about <firm or project>'s data with the analytics-ask skill, from
      the context saved in <workspace name, or: the analytics run at <run-dir>>. That is the
      only context you need; do not search other folders. Run SQL only through the run's query
      runner, as analytics-ask does, never by opening a database file. End with one JSON object
      and nothing after it:
      {"answer": "<the answer>", "sql": "<the one SQL statement whose result you state>",
       "used_context_ids": ["<each metric or query you relied on>"]}
      ```
      Never name the eval folder in the prompt: an agent told to avoid it writes the name into
      its own search filters, and the audit then marks the run contaminated. Keep the Agent
      tool's `output_file`: it is the transcript.
   2. Save its final JSON as `<evals-run>/hillclimb/baseline/answers/<id>_rep0.json`.
   3. Run its `sql` exactly as given with `PROJECT execute` and save the output as
      `<evals-run>/hillclimb/baseline/agent_results/<id>_rep0.json`. Never edit the agent's SQL,
      not even a table prefix: a query that fails as given is `agent_sql_error`. An empty `sql`
      needs no run.
   4. `EVAL record --variant baseline --case <id> --rep 0 --answer-file <answer> --agent-result <agent result> --gold-result <evals-run>/eval/gold/<id>.json --transcript <output_file>`.
      A run that failed outside the agent (a timeout, a tool error) is
      `record --class harness_error --reason "<what failed>"` with no answer file.
3. `EVAL summary --variant baseline` must show one row per question. Never quote a partial run.

## Show the score

Lead with the summary's `score_line`, word for word ("2 of 3 answered correctly (likely between
21% and 94%; a small sample)"). Never count the passes yourself. When the questions came from the
same past queries the context was built from, say so in one line: the score shows the context
reproduces what the analysts asked, not how it handles a new question.

Then one row per miss: the question, the metric it tests, the expected value, the agent's
value, and **why it failed**, read from the agent's answer and SQL against the trusted SQL:

| Why | Means |
| --- | --- |
| Different rule | It used a metric whose formula differs from the trusted SQL's (another denominator, another status) |
| Different filter | Same formula, another population or window |
| No definition | No metric covered it, so it wrote its own SQL |
| Query failed | Its SQL did not run |
| Question asks the opposite | The answer is the trusted result's complement (the share rejected where the SQL counts the share approved), and both rank the same. The question's wording, not the context, is wrong |

Name the metric involved when there is one. `harness_error` and `incomplete` questions leave
the count; name how many. The caller decides what comes next.

## Diagnose the misses

This skill finds why each miss failed and what would fix it. It never applies a fix: a metric,
a saved query and the set stay as they are, whoever called it.

1. **Each miss.** Read the agent's answer and SQL against the trusted SQL and the set's `note`,
   and name the fix: the metric and the definition fields to change, a question to redraft
   (*question asks the opposite*: the set is wrong, not the context), or a metric to add (*no
   definition*).
2. **A complete change.** Each change names a definition field (`source.sql`,
   `mandatory_filters`, `population`, `exclusions`, `examples` and so on) or the metric's own
   `description`. A rule change covers every place the metric states the rule: its source SQL
   and filters, its population and exclusions, each example whose SQL holds the old rule, **and**
   its `description` when that states the old rule. An agent that reads the old rule anywhere in
   the metric still answers with it. A miss on wording is fixed by adding that
   question to `examples`, with the SQL that answers it.
3. **Write the fixes** to `<evals-run>/fixes.json`, the recommended one first (the one that turns
   the most misses): `[{"misses": ["<id>"], "why": "<reason>", "metric": "<name>", "rule_now":
   "<plain words>", "rule_proposed": "<plain words>", "changes": [{"path": "<definition field>",
   "value": <new value>}]}]`. A question redraft or a new metric carries `"kind"` and no
   `changes`.
4. **Show** one row per miss: the question, why it failed, and the fix in one line (rule now,
   rule proposed). Name the recommended fix and where `fixes.json` is. The check ends there.

## Test again

Called again on an eval folder that already has a score, after the context changed: run `test`
as the next variant (`v1`, then `v2`) on the same questions, with no new questions and no
question first. When the caller names a metric version to read, tell each answering agent to
read that metric with that `version_ordinal`. Show `EVAL compare --a baseline --b v1` as one line
per question that changed, then the check's [result](check.md#4-the-result).

## Publish

Each `EVAL publish` prints the next `step` and its `calls`, each a `{tool, arguments}` pair.
Send every call in order with that exact name and arguments (in search mode, through
`mcp__call_write_tool` with the session id beside them). Save the tool results, in call
order, as one JSON list in `<evals-run>/hillclimb/baseline/publish-<step>.json` with the Write
tool (never under a temporary folder outside the project), and run
`EVAL publish-ack --variant baseline --step <step> --responses <that file>`.
Repeat until `publish` prints `"step": "done"`.

- A failed item comes back under `failed` with the registry's message; the next `publish`
  resends only those. Fix a named field first; after three failures of one item, stop and report it.
- A `conflict` on the scorer or dataset means one by that name already lives in the workspace:
  list that kind with `registry__list_artifacts`, save the listing, and run
  `publish-ack --reconcile`, so the set reuses it rather than making a second one.
- A lost result blocks `publish`. For `scorer` and `dataset`, run `publish --resend` and send the
  same calls: their idempotency keys return what already landed. For `records`, list
  `dataset_record` in the workspace with `registry__list_artifacts`, save the listing and run
  `publish-ack --reconcile`. Never resend a batch blindly.
