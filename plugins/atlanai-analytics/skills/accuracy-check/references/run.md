# Run and score a set

Read when running a set, showing its score and testing again. `CHECK` is
`python3 "<check-dir>/scripts/check.py"`, with `<check-dir>` this skill's directory. `EVAL` is
`python3 "<build-dir>/scripts/analytics_eval.py" <command> --dir "<evals-run>"` and `PROJECT` is
`python3 "<build-dir>/scripts/project.py" --run-dir "<run>"` (without `--run-dir` it refuses every
query), with `<build-dir>` the `analytics-build` skill directory, `<run>` load's `run` and `<evals-run>`
load's `evals`. `ACC` is `python3 "<demo-dir>/scripts/accuracy.py"`, with `<demo-dir>` the
`analytics-demo` skill directory: `ACC result --evals <evals-run>` gives the banner and the
result's numbers on any check. Every case needs its trusted result in `<evals-run>/eval/gold/`, which
`CHECK load` writes on a local run and `CHECK gold` writes on a warehouse run.

## Where the SQL runs

`CHECK start` and `CHECK load` return `execution`. Read it before running anything: it decides
how every statement in this file is executed, and the two ways are never mixed. Its `tables` are
the source tables the run's metrics read.

| `execution.via` | The run | SQL runs through |
| --- | --- | --- |
| `local` | Bound to a SQLite or DuckDB file: a demo, CSVs, a local database | `PROJECT execute`, on this machine |
| `host` | Built on a warehouse (Snowflake, Databricks or another), in `execution.dialect` | The warehouse query tool, in this host |

On a warehouse run nothing on this machine can run SQL, and `PROJECT execute` refuses. The
warehouse query tool is the one in `execution.warehouse.tool` when the build recorded it, else the
one this conversation built the context with, else the one steps 0 to 2 of
[warehouse discovery](../../source-survey/references/connect.md) find. Its read-only rules apply.
With no tool, say "I can't reach <warehouse> from this chat, so I can't test your agent. Connect
it here, then ask again." and stop: a check that cannot run its SQL has no score.

## Trusted answers on a warehouse run

`CHECK load` returns `status: pending_gold` and `pending`, one `sql_file` per question. The
trusted results come from the warehouse, and no model copies a value:

1. Start one subagent, the runner, in the foreground, with this prompt, filled in:
   ```
   Run each SQL file below on <warehouse> with <the warehouse query tool>, one call per file.
   Send the statement exactly as the file holds it: no edit, no added LIMIT, no comment.
   Read-only. Do nothing else, and reply "done" with how many ran and which failed.
   <each sql_file, one per line>
   ```
   Keep the Agent tool's `output_file`: it is the runner's transcript.
2. `CHECK gold --evals <evals-run> --transcript <output_file>` reads each result from the tool
   call that ran exactly that SQL, following the path when the host saved a large result to a
   file. It drops a question whose trusted SQL failed, returned nothing, returned over 100 rows
   (a list of records, not an answer), or returned more rows than one response holds (`dropped`, each with its reason), and sets the rest up for scoring.
   It returns what `CHECK load` returns on a local run. Say how many were dropped and why: "I left
   out <N> of <M> questions because I couldn't work out their known answers: <reasons>. I'll test
   your agent on the other <K>." With one left, the last sentence is "I'll test your agent on the
   one that's left." A set of 5 can come back as 3.
3. A `BLOCKED` reply names each SQL with no result: it was changed on the way. Run that file
   again exactly as written, or save its raw result to the `result_file` `CHECK load` named, as
   the tool or a CLI (`--format json`) returned it. Then run `CHECK gold` again. Where the host
   has no subagents, run each file yourself and save each raw result to its `result_file`.

## Why the score can be trusted

- Each question goes to a **fresh** agent whose prompt is the question and the fixed lines
  below, nothing else: no case id, tags, SQL, expected value or other question.
- The agent answers the way a person's question is answered, through `analytics-ask` on the
  same context. A score therefore measures the context the person built.
- **The script** compares the two results, each as the database returned it, and passes every
  exact match. No model copies a result. Where the script refuses an answer whose SQL ran (other
  rounding, a percent for a share, an extra or missing label column), **a judge** rules: a fresh
  agent with fixed instructions that reads only the question and the two results, never the
  answering agent's conversation. An answer with no result fails without a judge. The summary
  says how many answers the judge decided.
- A transcript that opens anything under `.atlan-evals/` or names an eval artifact is marked
  contaminated and fails.

## Run it

1. `EVAL todo --variant baseline --reps 1` lists the questions not yet run. A stopped run
   resumes here.
2. Start one subagent per question, 10 at a time, each 10 in one message and in the foreground (a
   generated set has 20), so a batch comes back together: start the next 10 when a batch has replied.
   For each:
   1. Spawn a subagent on the Haiku model (the Agent tool's `model: haiku`), which keeps a run's
      cost down, with this prompt, filled in:
      ```
      <question>

      Answer this question about <firm or project>'s data with the analytics-ask skill, from
      <where the context lives>. That is the only context you need; do not search other
      folders. <how to run SQL>
      End with one JSON object and nothing after it:
      {"answer": "<the answer>", "sql": "<the one SQL statement whose result you state>",
       "used_context_ids": ["<the id of each metric or query you read, like metric_... or sql_query_...; never empty when you read one>"]}
      ```
      `<how to run SQL>` follows `execution.via`. Local: "Run SQL only through the run's query
      runner, never by opening a database file: write {"sql": "<sql>", "limit": 1000} to a file
      and run python3 "<build-dir>/scripts/project.py" --run-dir "<run>" execute <that file>".
      A warehouse: "Run SQL only with <the warehouse query tool>, read-only, on <warehouse>,
      in the <dialect> dialect. Run the one statement your answer states, whole, before you
      answer."
      `<where the context lives>` follows `context_saved_to`. In Atlan: "the context
      saved in the Atlan workspace <name>: read its metrics and queries there, at their
      released versions, never from files on this machine; the run folder is only for the query
      runner". On this machine: "the context on this
      machine, in the analytics run at <run-dir>: its metrics and queries are in that run's
      context folder and are not in Atlan, so do not search the registry; unreleased
      local definitions are the ones to use". A small model answers from the registry unless told.
      Never name the eval folder in the prompt: an agent told to avoid it writes the name into
      its own search filters, and the audit then marks the run contaminated. Keep the Agent
      tool's `output_file`: it is the transcript.
   2. Save its final JSON as `<evals-run>/hillclimb/baseline/answers/<id>_rep0.json`. An agent
      that ends without the JSON object is asked once, in the same subagent, for that object
      alone. With none still, save its last message there as it is.
   3. Get the result of its `sql` as
      `<evals-run>/hillclimb/baseline/agent_results/<id>_rep0.json`. Never edit the agent's SQL,
      not even a table prefix: a query that fails as given is `agent_sql_error`. An empty `sql`
      needs no run.
      - **Local:** run it exactly as given with `PROJECT execute` and save the output there.
      - **A warehouse:** the agent already ran it. `EVAL agent-result --transcript <output_file>
        --answer-file <answer> --out <that file>` reads the result from the agent's own tool
        call. An agent that states no SQL, or a stand-in such as "the SQL above", is
        graded on the last read it ran (`source: transcript_last_call`); one that ran nothing
        returns `source: none`, and is recorded without `--agent-result`. When it returns `source: replay`, the result could not be read whole, or the
        agent stated a statement it did not run: have a runner subagent run the `sql_file` it
        names exactly as written, as above, then `EVAL agent-result` again with the runner's
        transcript. A statement the warehouse rejects in the replay too is saved as
        `{"error": "<the tool's message>"}`. A tool timeout is not the agent's miss: record it
        as `--class harness_error`.
   4. `EVAL record --variant baseline --case <id> --rep 0 --judge --answer-file <answer> --agent-result <agent result> --gold-result <evals-run>/eval/gold/<id>.json --transcript <output_file>`.
      A run that failed outside the agent (a timeout, a tool error) is
      `record --class harness_error --reason "<what failed>"` with no answer file. `record`
      refuses an answer that states SQL with no `--agent-result`: finish the replay first.
   5. When `record` returns `status: judge`, it wrote no row: the exact comparison refused an
      answer that ran. Start one subagent on its `model` with its `prompt` as the whole prompt,
      unchanged, save the JSON it replies with to `verdict_file`, and run the same `record` again
      with `--verdict <verdict_file>`. Start every judge a batch needs at once. Never rule
      yourself, and never tell a judge what you expect.
3. `EVAL summary --variant baseline --require-isolation` must show one row per question. It
   refuses a score while a row lacks its agent's transcript: re-record that row with
   `--transcript`. Never quote a partial run.

## Before and after

Run on every check: whether a person asked here directly, the build offered it or the demo
started it, and whatever the size of the set. Never skip it because nobody asked for it. The
same questions
again, each to a fresh agent with only the source tables' schema and no additional context, as the variant
`raw`. It shows what the context changed, on the same questions and the same trusted results.

1. `EVAL todo --variant raw --reps 1` lists the questions. Run them as [run it](#run-it) does,
   in the same batches as the context pass, with three differences:
   - The prompt, filled in, with `<tables>` from `execution.tables` and `<how to run SQL>` as above:
     ```
     <question>

     Answer this question using only the schema of these tables: <tables>.
     You may inspect table and column names, data types and declared keys with read-only
     schema queries; DESCRIBE TABLE is usually the quickest. Do not read comments, descriptions, sample rows, distinct values,
     profiles, query history, dashboards, documentation, metrics or saved queries.
     Do not explore data values or run intermediate data queries. Write the final answer
     SQL from the question and schema alone, then execute that statement to get the answer.
     Use no skill or context-retrieval tool, and open no files in the run folder.
     <how to run SQL>
     End with one JSON object and nothing after it:
     {"answer": "<the answer>", "sql": "<the one SQL statement whose result you state>"}
     ```
   - Supply only the question, table identifiers and execution instructions, with no firm
     background, source-survey findings or other context. Schema reads must select structural
     fields only (never comments or descriptions). The final answer query may read data;
     that permission does not allow sampling or profiling before writing it. Review the
     transcript for this boundary before recording: discard a pass that reads additional
     context and rerun it in a fresh agent with schema only. Never score that attempt.
   - Its files go under `<evals-run>/hillclimb/raw/` and every `EVAL` call names `--variant raw`.
   - `EVAL record` adds `--raw <run>`: an answer whose agent reached for a skill, an Atlan tool,
     any MCP server other than the warehouse's, or the run's context folder is `raw_used_context`
     and leaves both scores, as `report` says. Counted as a miss, it would flatter the context. It keeps `--judge`: both passes are graded the
     same way.
2. `EVAL summary --variant raw --require-isolation` must show one row per question.
3. `EVAL before-after --before raw --after baseline` returns the comparison. Its `result` holds
   every number the person reads: `accuracy` and `baseline` (percentages), `lift` (points),
   `outcome` (`ahead`, `tie` or `behind`), `questions`, `small_set` (under 5), `turned_around`
   and `lost` (question numbers), `misses`, and `lines`, the number lines worded once:
   `accuracy` ("Accuracy: 100%."), `before_after` ("With column names alone: 40%. With your
   agent: 100%.") and `small_set`. Write the result from these, as [show the
   score](#show-the-score) describes. Never work a number out yourself, and never show one pass's
   score without the other. Its `report`, `bars` and `widget` are for a reader that asks for
   detail; none of them goes in the chat.

## Show the score

The result is a win moment: write it warm, specific and celebratory wherever it truthfully can
be, a small set included. It comes first, with nothing around it: no setup recap, no list of
commands, no closing summary. I build and test, you choose, your agent answers: say it that
way, in plain words, with no tool or file names.

**What it holds, in this order.** The script computes every number; you write the words.

1. The banner, only when every question is right: `ACC result`'s `banner`, as plain lines.
2. The accuracy first: `lines.accuracy`, and in the same line what it means for this outcome.
3. Before and after, as percentages: `lines.before_after`. Counts only where they add something
   (a set of 3 reads better as "all 3").
4. One line on what made the difference, in the person's terms: the metric or rule that turned
   answers around (`turned_around` names them by number), never how the check ran.
5. The misses after, under their numbers, as `EVAL report`'s `misses_text` prints them.
6. With `small_set`, after the celebration, `lines.small_set` as it is.
7. Only when `CHECK load` returned `generated: true`: "These questions come from what your team
   already asks. So the score shows your agent can answer those. It doesn't show how it does on
   new questions." With `generated: false` (a set the person brought) say nothing about where
   its questions came from.

Never in the chat: bars, a widget, counts of answers right or wrong both ways, how many answers
a judge decided, the score line said again in other words, or "schema only". The raw pass is
"column names alone" everywhere the person reads. The full `EVAL report` keeps those details
for a reader who asks.

**Never claim a lift that isn't there.** Write by `outcome`:

- **ahead**: celebrate the gain and name what made it.
- **tie**: celebrate the accuracy and that the metrics held up. Never say they made the
  difference. Below 100%, the misses say what to sharpen.
- **behind**: stay upbeat and say what to fix. Never explain it away.

Examples, each a whole result:

```
Ahead (8 questions):
Accuracy: 88%. Your agent gets 7 of 8 right.
With column names alone: 25%. With your agent: 88%.
Net revenue's rule, after refunds, turned five answers around.
<misses_text>

Tie (5 questions, every one right):
<banner>
Accuracy: 100%. Your metrics held up on every question.
With column names alone: 100%. With your agent: 100%.
Your tables already name these clearly, and your agent answered each from the metric your team uses.

Behind (6 questions):
Accuracy: 50%. A good base, and the misses show exactly what to fix.
With column names alone: 67%. With your agent: 50%.
Two answers used an older rule for active accounts. Fixing it should win them back.
<misses_text>

Small set (2 questions, ahead):
<banner>
Accuracy: 100%. Both questions right.
With column names alone: 50%. With your agent: 100%.
Pod targets came out right because your agent used the dashboard's own rule.
Based on 2 questions. Add more for a sharper score.
```

**Where it goes.** With misses and a fix still to offer (the demo's step 3), the result is a chat
message that ends the turn on its reply line. At the end of a check it is the text of the
[ask for more](#ask-for-more) question: plain lines, no Markdown, the banner as plain lines.

On a test again, add `EVAL report`'s `compared_text` after the before and after: it names the run
this one is compared with, where it lives and the questions that changed.

**Every miss has a number, and keeps it.** A question's number is its place in the set, the
same in the list the person confirmed, in every run's report and in a fix. Each miss is four
plain lines under its number: the question; `Tests <metric>`; `Expected <value>. Got <value>.`;
`Why: <one plain sentence>`. No table: they become a question's text.

Before the result, write the why file, `<evals-run>/hillclimb/<variant>/why.json`: for each
missed question id, `{"why": "<one cause from the table below>", "detail": "<one plain
sentence>"}`, read from the agent's answer and SQL against the trusted SQL. The report groups
the misses by that cause. A miss you leave out is printed as "Not yet explained", and the
report's `unexplained` names it: explain it and print the report again.

Write the `Why` value exactly. The report prints the cause as `Shown`, and so do you in chat.

| Why | Shown | Means |
| --- | --- | --- |
| Different rule | Different definition | It used a metric whose formula differs from the trusted SQL's (another denominator, another status) |
| Different filter | Different filter | Same formula, another population or window |
| No definition | No metric fit | No metric covered it, so it wrote its own SQL |
| Query failed | Its SQL failed | Its SQL did not run |
| Question asks the opposite | Question asks the opposite | The answer is the trusted result's complement (the share rejected where the SQL counts the share approved), and both rank the same. The question's wording, not the context, is wrong |

Report the miss as the set states it, without doubting the set. Name the metric involved when there is one. `harness_error` and `incomplete` questions leave
the count; name how many (`result.left_out`). How many answers a judge accepted stays in the
full report, never in the chat.

## Test again

Called again on an eval folder that already has a score, after the context changed: run the test
on the same eval folder as the next variant (`v1`, then `v2`) on the same questions, with no new questions and no
question first. When the caller names a metric version, add one line to
each answering agent's prompt: "If you use <metric>, read it at version <N>; read every other
metric at its released version." Never list only the changed metrics: an agent told to read
those reads nothing else. Show the new result as [show the score](#show-the-score) describes,
with `EVAL before-after --before raw --after <vN>` and `EVAL report`'s `compared_text`: it names
the run this one is compared with, when that run was scored and where it lives (on this
machine, or saved to Atlan once the check is saved), then the questions that changed, by
number. A comparison whose other run the person cannot find is no comparison. `EVAL compare
--a baseline --b v1` gives the same change as numbers when asked. The raw-source pass is not
run again: the source did not change.

A comparison is only ever with a run of this same eval folder. When the person asks to compare
with another run (an older check, another set), `CHECK start` lists the project's eval folders
under `checks`: ask which one, once, and say which you used. Never pick one silently, and never
compare runs graded on different questions.

## Ask for more

Right after the result, at the end of every check, ask for more to build from. `CHECK next
--evals <evals-run> --run <run>` returns `lead`, `question` and up to 4 `options`, chosen from
what this run could not read or left out, biggest gap first, with `keys` naming each. Never add,
drop or reorder an option, and never write one from memory.

Ask one native question: its `question`, whose text is the result (as [show the
score](#show-the-score) describes, plain lines), a blank line and `lead`, and its `options` as
they are. Where the host has no question tool, print that text, then the options as plain lines,
and stop. On each answer:

| Key | Option | What happens |
| --- | --- | --- |
| `past_queries` | Let me read your past queries | When they could not be read (`gaps.history_unreadable`), give the person the ask from its description to send their admin, then close. You never change a permission. When they say it is granted, or when the queries were readable but not used, build again with past queries (`analytics-build`) and check again. |
| `other_pages`, `another_dashboard`, `a_dashboard` | Add the other pages, Add another dashboard, Add a dashboard | Build again from it with `analytics-build`, then check again on the same questions. |
| `tables` | Name the tables your team uses | Ask which tables in plain chat, build again with them, then check again. |
| `own_questions` | Add questions of your own | Ask in plain chat for each question and the SQL that gives its known answer (theirs, or a past query or tile they name). Write them as a set in `.atlan-evals/sets/` and test it as a set the person brought. |
| `save` | Save this score to Atlan | [Save the check](#save-to-atlan), then close. Offered only with 5 questions or more. |
| `done` | I'm done for now | Close. |

Close is the last step: `PROJECT question close`, its message as it prints. A pick that builds
again hands over to that build, which ends on its own. The demo ends on its own next step
instead, as its [result](../../analytics-demo/references/accuracy.md#5-the-result) says.

## Save to Atlan

Offered in [ask for more](#ask-for-more) when the set holds 5 questions or more, as "Save this
score to Atlan", "Save this score to Atlan, so your team can see it." Never explain how the
SQL is kept. The caller gives the workspace; this skill registers nothing, and no agent is needed. Atlan
holds a run without a subject: the set, each run and its scored answers are saved, and only the
per-question sessions, which Atlan ties to an agent, are left out.

1. `EVAL init --workspace-id <workspace>` records it on the eval folder. When the check's agent is
   already registered, add `--agent-id <id>` and its runs are listed under it.
2. Publish each run in this order, so Atlan shows what the context changed and then what each
   fix changed: the raw-source pass first, `EVAL publish --variant raw --no-transcripts`; then the
   context pass against it, `EVAL publish --variant baseline --baseline raw --no-transcripts`;
   then for each test again `EVAL publish --variant <vN> --baseline <the run before it>
   --no-transcripts`. Each with
   `EVAL publish-ack` until `"step": "done"`, as
   [publish](../../eval-generation/references/publish.md) describes: `--ids` for every step but
   `results`, which this skill acknowledges with its tool result (`--responses`), so publish-ack
   checks each saved score against the run's. Send each
   call's arguments exactly as `publish` printed them. When `publish-ack` returns `MISMATCH`, a
   saved score differs from the run's: stop and follow its `fix` before publishing anything else.
   A refused create of the scorer means the workspace already holds `result-match`: find it with
   `registry__search_artifacts` (`kind` `scorer`, that workspace) and pass its id with `--ids`.

The first run saves the set (its questions and a digest of each trusted SQL, never the SQL) and
the run as an experiment, each answer with its SQL and score. The raw-source pass is named "column names only", the context pass "your agent" and each later run "your agent, test <N>", so they read as a comparison. Never the answering agents'
transcripts: they hold this machine's commands and paths. Each later run is an experiment
linked to the one before it, so the workspace shows the score move. Then `CHECK link --evals
<evals-run> --org <organization slug>` gives the Atlan pages: `runs` (every run of the check) and
`last_run` (the last one against its baseline, the page to give the person). The slug is the one the context's proof links use
([definition links](../../analytics-ask/references/definition-links.md)). Return each run's
`experiment_id`, the workspace and both links.
