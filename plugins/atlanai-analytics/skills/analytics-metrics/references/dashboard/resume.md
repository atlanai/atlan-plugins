# Durable dashboard stage receipts

`DASH` uses the selected `--run-dir` from the dashboard workflow. Always read `DASH status`
when resuming. Do not recreate metrics, repeat checked cases or register another agent just
because the conversation restarted. The run index references private source, context,
publication and stage evidence. Its status is derived from those receipts, not from prose.

Metric publication already has its own reviewed plan, pending operation and readback loop.
Keep using `registry-next`, `registry-ack` and `registry-recover`. If an operation is pending,
read by its source identity and recover it; do not resend an uncertain create. `DASH status`
reports pending readback separately from local question availability.

## Agent registration

After the concrete agent and destination have been approved, save an exact request JSON:
`{workspace_id, name, description, instructions}`. All four fields are nonempty strings.
First reconcile existing agents in that workspace through the live Registry. Reuse the
matching intended agent where possible; a name match with different instructions needs review.

Before a create or readback, run:

```sh
DASH stage-begin --stage agent --request <agent-request.json>
```

`execute_once` returns an operation ID. Use the existing core Registry tools and current live
schema for the approved registration. Then read the agent back. Normalize only these fields:
`{id, kind:"agent", workspace_id, name, description, instructions}`. Save this receipt:

```json
{"operation_id":"<returned-operation-id>","read_succeeded":true,"artifact":{"id":"<actual-id>","kind":"agent","workspace_id":"<selected-workspace>","name":"<reviewed-name>","description":"<reviewed-description>","instructions":"<reviewed-instructions>"}}
```

Run `DASH stage-ack --stage agent --receipt <receipt.json>`. A changed workspace or definition
is refused. `already_complete` means reuse the recorded agent. `pending_operation` means
read back the original request and acknowledge it, never repeat the create. When readback
cannot establish the outcome, leave registration pending and continue with local questions.
Updating an existing agent is a separate reviewed action; do not create another to replace it.

## Independent evaluation

Keep the evaluator's gold and transcripts out of the run index. Before an authorized paid
eval, save `{agent_id, runner, dataset_hash, context_snapshot_hash}` from the frozen inputs,
then run `DASH stage-begin --stage heldout_eval --request <eval-request.json>`.

On completion, normalize the actual scorer result into a receipt with the operation ID,
`read_succeeded:true`, those same four input fields, integer `passed`, `scored`, `unmeasured`
counts and `evidence_ref` pointing at its private report. Acknowledge with
`DASH stage-ack --stage heldout_eval --receipt <receipt.json>`. No raw answers or gold belong
here. A resumed pending eval requires inspecting its existing output before starting another.
Missing independent gold or a failed runner leaves this stage unmeasured and local questions
available; do not substitute dashboard parity counts for this receipt.

Changing source/context marks earlier receipts stale. Preserve them for reconciliation;
never silently relabel an older agent or score as current. `Agent ready to try` requires usable
metrics, verified metric releases and the current agent readback. Otherwise use
`Local metrics ready to try` when usable metrics exist.
