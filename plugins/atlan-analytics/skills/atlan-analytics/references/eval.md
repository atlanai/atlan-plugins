# Run an eval

Read [shared conventions](conventions.md) first. `EVAL` is the bundled script
`../scripts/analytics_eval.py`, run as in the conventions. Local grading uses only the
standard library; publishing uses the immutable SDK dependency in `../scripts/pyproject.toml`. A run is one
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
4. **Publish.** Run the publishing command through its locked environment:
   `uv run --frozen --project <skill>/scripts <skill>/scripts/analytics_eval.py push --variant V [--baseline baseline]`.
   Here `<skill>` is the canonical `skills/atlan-analytics` directory, installed or fetched through MCP.
   Keep the same working directory and `--dir` as the grading commands.
   The pinned SDK source is private: this environment needs GitHub contents-read
   access to `an internal repository` through its normal Git credential helper. Keep credentials
   out of the project and lockfile.

   Supply `ATLAN_EVAL_AGENT_API_KEY` through the environment with a credential for the
   evaluated agent. Obtain it through the normal credential workflow; never rotate an
   operational identity just to publish evidence. Missing credentials block before writes.
   - it creates the registry experiment on the agent, linked to its baseline;
   - it uploads one result per case plus a trace and session as the agent;
   - it marks the experiment completed.

   If a publish fails with an unresolved outcome, use `publishing.json` to reconcile
   the trace and session before retrying. A pending receipt blocks replay; do not
   clear it until the accepted evidence is known.

   Gold SQL never leaves the machine; the dataset record carries a digest only. Confirm with
   `extension__agent__list_traces` that the traces landed.
5. **Compare** (for rounds): `EVAL compare --a baseline --b V` gives the paired per-case delta
   on test (the headline) with its band, and on train separately.

## Grading, in one paragraph

A case passes when the agent's result set matches the gold one:
- numbers are compared at 4 significant figures;
- column names and order are ignored, with columns matched by their values;
- row order is ignored unless the gold SQL sorts and limits;
- extra agent columns are allowed.

Classes: `pass`, `wrong_result`, `agent_sql_error`, `no_sql`, `gold_error`, `harness_error`,
`invalid_contaminated`.
Cases tagged `fuzzy` (the answer is prose, not a table) can be judged by the
`atlan-analytics-grader` agent. It sees the question, the gold SQL's result and the agent's answer,
never the agent's context. Report judged cases separately from the result-set pass rate.
To check one case by hand, run `EVAL grade --gold <gold.json> --agent <agent.json> --gold-sql <sql>`.

## Files

- When creating or adopting an answering agent, use [the system prompt template](eval/agent-template.md).
- `../scripts/analytics_eval.py`: the bundled script. `EVAL selftest` checks it; `EVAL --help` lists
  commands.

End with one status line.
