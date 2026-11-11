# Test accuracy

Read when running `test`, and for **Publish** when saving a set to Atlan Trident. The set must
be imported first (`save`), and every case needs its gold result in `<evals-run>/eval/gold/`.

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
| Question asks the opposite | The answer is the trusted result's complement (late share where the SQL counts on time), and both rank the same. The question's wording, not the context, is wrong |

Name the metric involved when there is one. `harness_error` and `incomplete` questions leave
the count; name how many. The caller decides what comes next.

## Improve loop

A fix goes to the metric's owner as a proposal in Metric Studio, never as a direct write. The
owner applies it, which makes a new version of the metric. This needs the context saved to
Atlan Trident: with it only on this machine, say so and offer that save first.

1. **Pick one fix.** A miss marked *question asks the opposite* is fixed on the eval set, not the
   metric: say so, drop it with `EVALGEN review --reject <id>`, and redraft it in the next set.
   Otherwise take the miss whose rule, once changed, fixes the most misses. Show that
   metric's rule now and the proposed rule, one line each, and which questions it should fix.
   A miss with no definition becomes a new-metric proposal instead. Every change names a field
   inside the definition: Metric Studio refuses a change to the metric's top-level `description`.
   A miss on wording ("which run late" answered with the late share) is fixed by adding that
   question to the definition's `examples`, with the SQL that answers it.
   A rule change covers every place the definition carries the rule: its source SQL and filters
   **and** each of its `examples` whose SQL holds the old rule. An example left on the old rule
   still leads agents to the old answer. Name the saved queries linked to the metric that hold
   the old rule in the proposal's `summary`, so the owner updates them in the same pass.
2. **Propose it** through `metrics-generation` `propose`, as a fix to that metric. The person's
   Improve it is the yes its preview asks for: show the preview and write, without asking again. Its `evidence.runs` are the missed
   questions, one entry each, with the test agent as `agent` and this session's
   `EVAL session-run` trace. Say the score in the `summary` ("4 of 5; question 4 reads the late
   share where the analysts' query reads the on-time rate").
3. **Send the person to approve it.** Give the Metric Studio link that opens the proposal
   (`suggestion=<file id>`), and ask one native question, "Approved it in Metric Studio?", with
   **Approved, test again** and **Not now**.
4. **Test the approved version before it is released.** Approving makes a new version that is
   not yet on `latest`. Read its `version_ordinal` with `registry__get_artifact`, then run `test`
   as the next variant (`v1`, then `v2`) on the same questions, telling each answering agent to
   read that metric with that `version_ordinal`. Show `EVAL compare --a baseline --b v1` as one
   line per question that changed.
5. **Offer the release.** When the new version fixes the miss and breaks nothing, say so and ask
   whether to release it to `latest` in Metric Studio, where every agent then reads it.

Then return to the check, which asks whether the score looks good.

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
