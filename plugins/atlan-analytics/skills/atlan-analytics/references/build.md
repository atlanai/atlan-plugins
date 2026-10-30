# Build a talk-to-data agent

Read [shared conventions](conventions.md) first. Work in the project directory
`.atlan-analytics/<slug>/`. Seven phases, each ending in a STOP gate. Read the reference file when
you enter a phase.

| # | phase | reference | the user decides | produces |
|---|---|---|---|---|
| 1 | purpose | [01-purpose](build/01-purpose.md) | audience, 5–10 real questions | `brief.md` |
| 2 | scope | [02-scope](build/02-scope.md) | ≤5 tables, the workspace | `tables.txt`, `workspace_id` |
| 3 | context | [03-context](build/03-context.md) | the source of trusted SQL, publish | metrics + saved queries in the workspace |
| 4 | eval_set | [04-eval-set](build/04-eval-set.md) | which questions and answers are trusted | `eval/cases.jsonl`, `eval/answers.jsonl`, `eval/split.json` |
| 5 | agent | [05-agent](build/05-agent.md) | name, model, the system prompt | registered agent + `.claude/agents/<slug>.md` |
| 6 | smoke | [06-smoke](build/06-smoke.md) | nothing | 3 answered questions, sessions visible in the registry |
| 7 | baseline | [07-baseline](build/07-baseline.md) | nothing (confirm the run cost) | baseline experiment in the registry |

## Rules specific to building

- **Scope:** five tables or fewer, 5–10 sample questions, 20–50 eval cases.
- **Context before prompt.** When the agent is wrong, the fix is usually a definition (a
  metric, a saved query, a usage note), not more prompt text. Say this when the user reaches
  for the prompt.
- **The eval set is not context.** Questions and gold SQL written in phase 4 never feed phase
  3 or the prompt. If the user offers a benchmark file during phase 3, park it for phase 4.
- **The metrics-agent comes from GitHub.** Phase 3 uses `an internal repository`:
  an existing checkout if there is one, otherwise a clone into `~/metrics-agent`. If it can't
  be cloned, `skills/atlan-analytics/references/build/03-context.md` has the hand-authored fallback, so the phase is never
  blocked.
- **Registering an agent and writing the subagent file are outward-facing.** Preview both,
  then ask.

## When it finishes

After phase 7:

- State the baseline train and test pass rates, with the noise band `±1/sqrt(n·reps)`.
- List the three most common failure classes.
- Offer `atlan-analytics improve` as the next step (Recommended).
- Set `phase` to `improve`.

End with one status line.
