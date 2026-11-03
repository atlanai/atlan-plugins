# Plan: goal, levers, stopping condition, sign-off

## Goal (ask first)

- `<slug> · improve · what to optimise`
- **Raise the validation pass rate (Recommended when the baseline is below 80%)**
- **Cut tokens per question**, keeping the pass rate within noise
- **Cut latency**, keeping the pass rate within noise
- **Move to another model** and recover the pass rate on it

Record it in `H/_state.json` as `goal: {target, direction, hold[]}`. Every other recorded
metric becomes a guardrail.

## Levers (what may change)

Ask with `multiSelect: true`:
- `<slug> · improve · what I may change`
- **Registry context (Recommended)**: new versions of metrics (definition, filters, notes), new
  metrics, new saved queries, `usage_note` on queries, rule skills linked to the agent
- **The agent's system prompt**: the body of `.claude/agents/<slug>.md`, mirrored to the
  registered agent with `registry__patch_artifact`
- **Model and effort**: the subagent's `model:` field

Then ask in plain text: "Anything off-limits? A definition finance owns, a table we mustn't
touch, wording that has to stay?" Record the answer as `off_limits`.

If registry context is a lever, ask: "Can you create and edit metrics in this workspace, or should
changes go to the metric's owners as proposals?" Record the answer as
`metric_access: {create, edit}`. A round that may not save a metric change proposes it instead.

The eval script, the grader and the eval set are **never** levers.

## Stopping condition

State the cost of one pass from the baseline's measured usage: "N cases × R reps ≈ X min, about
T tokens." Give no dollar figure: the script has no pricing data. Then ask:
- `<slug> · improve · when to check back in`
- **Until plateau (Recommended)**: stop when validation moves less than δ for 3 rounds in a row, or a
  guardrail regresses outside noise
- **One round at a time**: propose, run, report, ask
- **N rounds**: the user gives N

Set δ above the noise floor, the validation `ci95` from
`skills/atlan-analytics/references/improve/02-baseline-check.md`. A round counts as a win only
when `EVAL compare` also reports the change as significant on its sign test. If there's a budget, show
`(rounds + 1) × T` tokens and treat it as a guide, not a wall.

## Sign-off (required before any change)

| Will change | Won't touch |
|---|---|
| … from the levers | … off-limits, the eval set, the split, the grader and script |

Add:
- One line on who applies changes. The default is that you apply them and the user reviews
  after. Offer "show me each change first" (`approve_each_round: true`), and recommend it when a
  governance team owns the definitions.
- Two or three sentences of approach, for example: "Each round a fresh analyst reads the train
  failures and proposes one fix, usually a definition or saved query the agent missed. I apply
  it as a new registry version, rerun every case, and publish the round as an experiment you can
  see in Evals."

Get a clear yes, and record the plan and `approve_each_round` in `H/_state.json`.
