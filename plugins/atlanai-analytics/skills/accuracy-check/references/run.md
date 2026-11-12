# Run and score a set

Read when running a set, showing its score and testing again. `CHECK` is
`python3 "<check-dir>/scripts/check.py"`, with `<check-dir>` this skill's directory. `EVAL` is
`python3 "<build-dir>/scripts/analytics_eval.py" <command> --dir "<evals-run>"` and `PROJECT` is
`python3 "<build-dir>/scripts/project.py" --run-dir "<run>"` (without `--run-dir` it refuses every
query), with `<build-dir>` the `analytics-build` skill directory, `<run>` load's `run` and `<evals-run>`
load's `evals`. Every case needs its trusted result in `<evals-run>/eval/gold/`, which
`CHECK load` writes.

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
   1. Spawn a subagent on the Haiku model (the Agent tool's `model: haiku`), which keeps a run's
      cost down, with this prompt, filled in:
      ```
      <question>

      Answer this question about <firm or project>'s data with the analytics-ask skill, from
      <where the context lives>. That is the only context you need; do not search other
      folders. Run SQL only through the run's query runner, never by opening a database file:
      write {"sql": "<sql>", "limit": 1000} to a file and run
      python3 "<build-dir>/scripts/project.py" --run-dir "<run>" execute <that file>
      End with one JSON object and nothing after it:
      {"answer": "<the answer>", "sql": "<the one SQL statement whose result you state>",
       "used_context_ids": ["<each metric or query you relied on>"]}
      ```
      `<where the context lives>` follows `context_saved_to`. In Atlan Trident: "the context
      saved in the Atlan Trident workspace <name>: read its metrics and queries there, at their
      released versions, never from files on this machine; the run folder is only for the query
      runner". On this machine: "the context on this
      machine, in the analytics run at <run-dir>: its metrics and queries are in that run's
      context folder and are not in Atlan Trident, so do not search the registry; unreleased
      local definitions are the ones to use". A small model answers from the registry unless told.
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
3. `EVAL summary --variant baseline --require-isolation` must show one row per question. It
   refuses a score while a row lacks its agent's transcript: re-record that row with
   `--transcript`. Never quote a partial run.

## Show the score

Lead with the summary's `score_line`, word for word ("67% accuracy: 2 of 3 answered correctly
(likely between 21% and 94%; a small sample)"). Never count the passes yourself. Only when `CHECK load` returned `generated: true`, say so in one
line: the questions came from the past queries and tiles the context was built from, so the score
shows the context reproduces what the analysts asked, not how it handles a new question. With
`generated: false` (a set the person brought) there is no such line, and never say where its
questions came from.

Then one line per miss: the question, the metric it tests, the expected value, the agent's
value, and **why it failed**, read from the agent's answer and SQL against the trusted SQL.
These are plain lines, no table: they become a question's text.

| Why | Means |
| --- | --- |
| Different rule | It used a metric whose formula differs from the trusted SQL's (another denominator, another status) |
| Different filter | Same formula, another population or window |
| No definition | No metric covered it, so it wrote its own SQL |
| Query failed | Its SQL did not run |
| Question asks the opposite | The answer is the trusted result's complement (the share rejected where the SQL counts the share approved), and both rank the same. The question's wording, not the context, is wrong |

Report the miss as the set states it, without doubting the set. Name the metric involved when there is one. `harness_error` and `incomplete` questions leave
the count; name how many.

## Test again

Called again on an eval folder that already has a score, after the context changed: run the test
on the same eval folder as the next variant (`v1`, then `v2`) on the same questions, with no new questions and no
question first. When the caller names a metric version, add one line to
each answering agent's prompt: "If you use <metric>, read it at version <N>; read every other
metric at its released version." Never list only the changed metrics: an agent told to read
those reads nothing else. Show `EVAL compare --a baseline --b v1` as one line
per question that changed, with the new score line.

## Save to Atlan Trident

Called when the caller asks to save a check. The caller gives the workspace and the agent's id
(`agent_...`); this skill registers nothing.

1. `EVAL init --agent-id <id> --workspace-id <workspace>` records them on the eval folder.
2. Publish each run in order, the first one first: `EVAL publish --variant baseline`, then for
   each test again `EVAL publish --variant <vN> --baseline <the run before it>`, each with
   `EVAL publish-ack` until `"step": "done"`, as
   [publish](../../eval-generation/references/publish.md) describes, saving the tool results in
   that run's own folder (`<evals-run>/hillclimb/<variant>/publish-<step>.json`).

The first run saves the set (its questions and a digest of each trusted SQL, never the SQL) and
the run as an experiment, each answer with its SQL and score. Each later run is an experiment
linked to the one before it, so the workspace shows the score move. Return each run's
`experiment_id` and the workspace.
