# Adopt an existing agent

Use this when the agent wasn't built by `atlan-analytics-build`: a registry agent someone else made,
or a talk-to-data bot running elsewhere.

1. **Find it.** Call `registry__list_artifacts` (kind `agent`), or `registry__search_artifacts`
   by name. Open it with `registry__get_artifact`. Note its workspace, instructions and model,
   and its linked context via `registry__get_related_artifacts`. Create
   `.atlan-analytics/<slug>/state.json` with `agent_id`, `workspace_id` and `adopted: true`.
2. **How will it run under eval?** Ask:
   - `<slug> · adopt · how the agent runs`
   - **Run it here as a Claude Code subagent (Recommended when its context lives in the
     registry)**: write `.claude/agents/<slug>.md` from its registered instructions, exactly as
     in [the agent procedure](../../atlan-analytics-build/references/05-agent.md),
     using its read-tool allowlist and no file, shell or web tools.
   - **It runs elsewhere; I'll supply its answers**: for each case, the user (or their harness)
     writes one JSON `{answer, sql}` file to `hillclimb/<variant>/answers/<case_id>_rep<k>.json`.
     `EVAL record` grades those files the same way, just without a transcript.
3. **Eval set.** Import the team's existing questions and trusted SQL into `eval/cases.jsonl`
   and `eval/answers.jsonl`, or build them as in
   `skills/atlan-analytics-build/references/04-eval-set.md`. Validate the gold and split once.
   - If gold SQL for a question is **also published as context** in the agent's workspace (the
     same SQL as a `sql_query`), that case measures lookup, not reasoning. Tag it `in_context`
     and report it separately.
4. **Baseline** with `atlan-analytics-eval`, then continue with `references/01-plan.md`.
