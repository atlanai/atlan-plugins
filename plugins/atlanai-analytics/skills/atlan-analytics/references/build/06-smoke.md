# Phase 6: smoke test

Goal: prove the agent answers end to end, and that its sessions appear in the registry.

1. Pick three questions from the brief's samples. Never use eval cases.
2. For each, spawn the subagent with the Agent tool: `subagent_type` = the slug, and a prompt
   holding only the question. Run the three in parallel.
3. For each result:
   - Parse the final JSON (`answer`, `sql`, `used_context_ids`). If the final message isn't
     one JSON object, the prompt's answer-shape rule is being ignored. Note it: it is round-1
     material for improve.
   - Save it as `.atlan-analytics/<slug>/runs/smoke/<n>.json`, and keep the Agent tool's
     `output_file` path (the transcript).
   - Run the agent's SQL once with the warehouse query tool, and save the result next to it,
     so the user sees the number the SQL really produces.
4. Publish the runs to the registry as sessions of the agent:
   1. Record each run as variant `smoke`, with no gold (smoke questions are not in the eval set):
      `EVAL record --variant smoke --case smoke<n> --question "<the question>" --answer-file runs/smoke/<n>.json --agent-result runs/smoke/<n>.result.json --transcript <output_file>`.
      The row is recorded `ungraded`.
   2. Publish variant `smoke` with `--sessions-only`, as
      [Publish in the eval procedure](../eval.md#publish) describes. It creates one session per
      question under `ids.agent_id`, with the transcript as its messages (the question, each tool
      call and its result, the SQL and the answer). No dataset or experiment is created.
5. Verify in the registry: open each session from `hillclimb/smoke/publish.json` with
   `registry__get_artifact` and check its tool calls are there. If one is missing, report
   BLOCKED with what `publish-ack` returned.
6. Show the user a compact table: question | answer | used context (names) | rows.

Look out for:
- an answer with no `used_context_ids`, which means it guessed and didn't look anything up;
- SQL on a table outside the scope;
- any refusal.

Each of these is a note for the baseline, not a reason to stop.

**STOP gate.** "3/3 answered; 3 sessions visible on the agent in the registry."
Set `phase` to `baseline`.
