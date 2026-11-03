# Run an eval

Read [shared conventions](conventions.md) first. `EVAL` is the bundled script
`../scripts/analytics_eval.py`, run as in the conventions. It uses only the standard library
and never touches the network: you make every registry write through the Atlan MCP server, as
[Publish](#publish) describes. A run is one
**variant**: `baseline`, `v1`, `v2`… Everything it writes goes under
`.atlan-analytics/<slug>/hillclimb/<variant>/`.

## Isolation rules (why the numbers can be trusted)

- Each case gets a **fresh** subagent, `subagent_type` = the project slug, with a prompt that
  is the question text and nothing else: no tags, case id, hints, gold SQL, gold numbers, or
  other cases.
- The subagent can reach only the registry read tools and the warehouse query tool. **You**
  run the gold SQL and the agent's SQL, and **the script** compares them. No model grades a
  result-set case.
- The script also reads each transcript. A run whose tool calls name eval data (an eval kind
  such as `dataset_record`, an eval artifact id, or a path such as `answers.jsonl` or
  `.atlan-analytics/`) is marked contaminated. It is recorded as `invalid_contaminated` and
  fails, whatever its SQL returned.
- Don't read test-case transcripts while choosing changes. That's the analyst's train-only job
  in `atlan-analytics improve`.

## Procedure

1. **Plan.** Run `EVAL todo --variant V --reps R [--ids all|train|test]`. It returns the
   (case_id, rep) pairs not yet recorded, so a stopped run resumes where it left off.
   - For a new variant, run one pair first as a canary.
   - Then state the count × time × cost and get a yes before the rest.
2. **Run each pair** (up to 4 subagents in parallel; wait for a batch before starting the next):
   1. Spawn the subagent with the question. Keep the Agent tool's `output_file` (the
      transcript path).
   2. Parse its final message as JSON `{answer, sql, used_context_ids}`. If it isn't JSON,
      still record it; the script classes it `no_sql`.
   3. Save the answer as `hillclimb/V/answers/<case_id>_rep<k>.json`.
   4. Run the agent's `sql` with the warehouse query tool (the read-only rules apply, LIMIT
      10000). Save `{columns, rows, error}` to `hillclimb/V/agent_results/<case_id>_rep<k>.json`.
   5. Gold results are cached in `eval/gold/<case_id>.json` (phase 4). Re-run the gold SQL only
      if that file is missing.
   6. Record the pair:
      ```
      EVAL record --variant V --case <case_id> --rep <k> --answer-file hillclimb/V/answers/<case_id>_rep<k>.json --agent-result hillclimb/V/agent_results/<case_id>_rep<k>.json --gold-result eval/gold/<case_id>.json --transcript <output_file>
      ```
      `record` grades by result set and parses the transcript into the trace, tool calls,
      model and usage. It appends one row to `results.jsonl` and writes
      `traces/<case_id>_rep<k>.json`. A pair that is already recorded is skipped, never
      overwritten.
3. **Check it's complete.** `EVAL summary --variant V` must show N×R rows. If rows are short,
   run `todo` again and finish the missing pairs. Never quote a partial run.
4. **Publish** the variant as [Publish](#publish) describes.
5. **Compare** (for rounds): `EVAL compare --a baseline --b V` gives the paired per-case delta
   on test (the headline) with its band, and on train separately.

## Publish

Publishing writes, as the person and through the registry's MCP tools:
- the `result-match` scorer and the eval dataset, once per project;
- one dataset record per case: the question and a SHA-256 digest of its gold SQL, never the SQL;
- an experiment on the agent, linked to `--baseline` when given;
- one session per case, subject the agent, with every message and tool call from its
  transcript, and one experiment result per case that names its session;
- the experiment's summary, marking it completed.

A smoke run (`--sessions-only`) writes only the sessions and their messages. Trace spans have
no MCP write, so the runs show in the agent's Sessions tab and the experiment's results, and
their span views stay empty.

1. **Preview, then ask once.** Show the counts (records, sessions, results), the target
   workspace and agent, and one sample session's first two messages. Ask:
   - `<slug> · <variant> · publish to <workspace>`
   - **Publish N results and N sessions (Recommended)**
   - **Not yet**

   A yes covers every step of this variant's publish.
2. **Loop until done.** Run `EVAL publish --variant V [--baseline baseline] [--sessions-only]`.
   It prints the next `step` and its `calls`, each a `{tool, arguments}` pair. Send each call
   in order with that exact name and arguments (in search mode, through `mcp__call_tool`, with
   the session id beside them). Save the tool results, in call order, as one JSON list, for
   example `hillclimb/V/publish-<step>.json`, then run
   `EVAL publish-ack --variant V --step <step> --responses <that file>`.
   Repeat until `publish` prints `"step": "done"`.
3. **A failed item** is reported by `publish-ack` under `failed`, with the registry's message.
   The next `publish` resends only those items. Fix a named field before you resend; after
   three failures of the same item, stop and report it.
4. **A lost result.** If a call's result never came back, `publish` refuses to go on. For
   `scorer`, `dataset`, `experiment`, `sessions` and `complete`, run `EVAL publish --resend`
   and send the same calls again: their idempotency keys return what already landed. For
   `records`, `messages` and `results`, list that kind in the workspace with
   `registry__list_artifacts`, save the listing, and run `publish-ack --reconcile`. Never
   resend a batch blindly: it duplicates every item that worked.
5. **Check it landed.** Open one session with `registry__get_artifact` and confirm its
   messages, and count the experiment's results.

Gold SQL never leaves the machine. Publishing needs no SDK, token, API key or `uv`: only the
person's Atlan sign-in.

## Grading, in one paragraph

A case passes when the agent's result set matches the gold one:
- numbers are compared at 4 significant figures;
- column names and order are ignored, with columns matched by their values;
- row order is ignored unless the gold SQL sorts and limits;
- extra agent columns are allowed.

Classes: `pass`, `wrong_result`, `agent_sql_error`, `no_sql`, `gold_error`, `harness_error`,
`incomplete`, `invalid_contaminated`. `harness_error` is the one class you set yourself, for a run
that failed outside the agent (a tool failure, a timeout): `record --class harness_error --reason "…"`,
with no answer file. `incomplete` means the gold or the agent result was truncated, so matching rows
prove nothing: fetch every page, or bound the gold query, and record the case again. It,
`harness_error` and `gold_error` leave the scored denominator; report their counts beside the rate.
Cases tagged `fuzzy` (the answer is prose, not a table) can be judged by the
`atlan-analytics-grader` agent. It sees the question, the gold SQL's result and the agent's answer,
never the agent's context. Report judged cases separately from the result-set pass rate.
To check one case by hand, run `EVAL grade --gold <gold.json> --agent <agent.json> --gold-sql <sql>`.

## Files

- When creating or adopting an answering agent, use [the system prompt template](eval/agent-template.md).
- `../scripts/analytics_eval.py`: the bundled script. `EVAL selftest` checks it; `EVAL --help` lists
  commands.

End with one status line.
