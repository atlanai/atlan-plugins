# The loop: analyse → apply → run → record

Each round N writes to `H/vN/`.

## Analyse

Skip the analyst only when the fix is already clear: the user named it, or the last round
points straight at it. Otherwise spawn **one fresh `atlan-analytics-failure-analyst`** and give it:
- the exact list of **train** trace paths, `H/<prev>/traces/<id>_rep<k>.json` for ids in
  `eval/split.json` `train_ids`, with the instruction to read nothing else;
- the train rows of `H/<prev>/results.jsonl`;
- the context inventory: names and ids of the workspace's metrics and saved queries (from
  `registry__list_artifacts`), plus any definition it asks about (`registry__get_artifact`);
- the subagent file `.claude/agents/<slug>.md`;
- the levers, off-limits, this round's target and guardrails, and the noise floor.

It returns:
- one proposed change;
- evidence quoted from traces, with their paths;
- the number of train cases showing the behaviour;
- the most the change could gain.

Save that as `H/vN/change.md`. If the most it could gain is inside the noise floor, don't
spend a round; go to `references/04-stall.md`.

## Apply

1. **De-fluff.** Cut platitudes and restatements of default behaviour. A change is a
   definition, a fact, a rule or a query, not advice.
2. **Leak check.** The change must not contain an eval question's wording, any gold SQL, or a
   value copied from a gold result. Describe the behaviour instead.
3. If `approve_each_round` is true, show the change and its one-line rationale, and wait.
4. Apply it by lever:
   - **Metric fix**: read the current head with `registry__get_artifact` (no release selector).
     When `metric_access.edit` in `H/_state.json` is true and the user approved this round, save the
     corrected full definition using the revision procedure below. Otherwise propose it for the
     metric's owner (below) and leave the round pending until they apply and release it.
   - **New metric**: when train failures come from a concept no metric covers, first check
     that no proposal already covers it (below). Then draft its complete definition
     (`registry__get_kind_schema` for `metric`) and test its SQL with the
     warehouse query tool. When `metric_access.create` is true and the user approved this
     round, create it as metrics mode does by hand
     (`atlan-analytics-build/references/03-context.md`, section 5). Otherwise propose it for
     the workspace's metric owners (below) and leave the round pending until they create and
     release it.
   - **New saved query**: write the question, SQL and `usage_note`. Test the SQL with the
     warehouse query tool. Create it with `registry__create_artifact` (kind `sql_query`,
     `review_status` `unreviewed`) and link it with `registry__relate_artifact` `source_of`
     to its metric.
   - **Usage note**: read the saved query's current head, change `definition.usage_note` in a
     copy of its full definition, then use the same revision procedure.
   - **Prompt, model or effort**: edit `.claude/agents/<slug>.md`, and mirror the prompt to
     the registered agent with `registry__patch_artifact` (`instructions`, `model_id`). The
     subagent file is re-read by new sessions. If the edit doesn't take effect in this session,
     tell the user to restart and resume with `/atlan-analytics`.
5. Write `H/vN/change.patch` (the diff of the definition JSON or the subagent file) and
   `H/vN/receipt.json` (the artifact id, before/after version ids and ordinals, and any release
   ids actually consumed by the run). Record a proposed change as proposed, never as saved.

### Saving and undoing a definition change

Metric and saved-query definitions are immutable within a revision. `registry__patch_artifact`
cannot edit either definition. The current Registry MCP catalogue has no append-version tool;
do not invent one or use a create to impersonate an update.

Use Studio's edit/review/save flow, or an already configured, authenticated Registry HTTP/SDK
client that supports this operation:

```text
POST /registry/v1/artifacts/{kind}/{id}/versions
If-Match: <the ETag of the current head you reviewed>
```

The body carries the **complete** `definition`, the preserved `source_version` and
`source_metadata`, and `auto_release: false`. Do not copy server-owned hashes or validation
receipts into the request. Re-read the head through that client to obtain its ETag and confirm
that its version id and definition still match the proposal's base before appending. A metric's
positive-integer `definition.version` must never decrease; if any execution input changes,
choose a value greater than the current head's. A usage-note-only query change needs no metric
semantic version.

If only the MCP tools are available, save the proposed full definition and its base version in
`H/vN/`, propose it for the metric's owner (below), and pause this round before Run. Never claim
the change landed or test the unchanged definition as the new variant.

### Proposing a metric fix

A proposal is what Metric Studio shows its owner as suggestions to apply or dismiss; nothing
about the metric changes until a person applies it. Create it with `registry__create_artifact`, kind `file`:

- `metadata`: `name` (unique in the workspace, for example `improve-<metric>-v<N>`),
  `workspace_id`, `path` `improvements/metric/<metric>-v<N>.json`, `media_type`
  `application/vnd.atlan.improvement+json`, and `source_metadata` `{"atlan_improvement": <record>}`.
- `content`: the same record as JSON.

The record names the metric (`subject_kind` `metric`, `subject_id`, `subject_name`), the head it
was made on (`base_version_ordinal`, and `base_content_hash` set to the head's
`definition_sha256`), `improvement_status` `ready`, `requested_by` `Atlan`, a one-line `summary`,
`evidence.runs` (one `{trace_id, agent, question, at}` per train case behind the change), and `changes`:
one per field, each
`{"id": "chg_1", "op": {"op": "replace_field", "path": "<definition field, dotted>", "value": <its new value>}, "rationale": "...", "improves": "<scorer name>", "evidence": {"run_count": <train cases showing it>, "trace_ids": [<their traces>]}}`.
Never set `version` or `spec_version`; Studio raises the version when the person applies it.
`improves` names what the change should raise: the plan's `goal.target`, or a grader the eval
names.
Each run's `question` is the train case's question as its trace records it. It stays in the
proposal, which Studio shows the metric's owner and agents never read, and never enters the
change or a definition, which the leak check still covers.
Before creating one, list the workspace's `file` artifacts with `registry__list_artifacts` and
read those under `improvements/metric/`. If one for this `subject_id` is still `ready` with the
same change, write no other. Record the proposal's id in `H/vN/receipt.json`.

### Proposing a new metric

First list and read the proposals the same way. If one with an empty `subject_id` already
proposes this `subject_name`, or the same measure under another name, whatever its
`improvement_status`, write no other; record its id in `H/vN/receipt.json`. If it was
dismissed, its owners decided against it: close this round without a Run, tell the user, and
pick another change.

Otherwise create the file as above, named for example `improve-new-<metric>-v<N>` at
`improvements/metric/new-<metric>-v<N>.json`. Its record leaves `subject_id` empty, sets
`subject_name` to the new metric's name, and has no base or `changes`. It carries `proposed`:
`{name, display_name, description, definition}`, the complete definition you drafted and tested,
without `version` (Studio sets it when the person creates the metric). Add `subject_kind`,
`improvement_status`, `requested_by`, `summary` and `evidence.runs` as for a fix, plus a
top-level `rationale`: why the metric is needed.
If no warehouse tool could test its SQL, or a table or column is assumed, say so in the
`rationale`; never present it as tested.
Nothing exists until a person creates the metric in Studio; record the proposal's id in
`H/vN/receipt.json`.

After a successful append, verify the returned artifact/version and definition and record the
receipt. On a conflict, re-read and review the new head. On a timeout or unclear result, inspect
history for the intended revision before doing anything else; never blindly replay an append.

**Undo** restores the earlier definition's content in another new revision using this same
procedure. It does not move the head backwards. If restoring execution inputs, choose a metric
`definition.version` greater than the current head's, even when the copied content had a lower
one. Preserve the current source provenance unless the reviewed change explicitly corrects it.

Saving and releasing are separate. The default answering agent reads published metrics and saved queries, so an
unreleased edit must not be reported as an evaluated improvement. Before Run, arrange an
explicitly authorized release of the reviewed revision through Studio or the release API and
record its release id; otherwise leave the round pending. Restoring a published result also
requires explicitly releasing the new restoration revision. Do not withdraw or move a release
as a hidden part of an edit or undo.

## Run

Run variant `vN` over **all** cases at the baseline's rep count via `atlan-analytics-eval`. Then run
`EVAL push --variant vN --baseline baseline`, so the registry records the round as an
experiment linked to the baseline.

## Record and decide

- `EVAL compare --a baseline --b vN` gives test and train pass rates with their bands, the
  context-engagement rate, tokens and cost.
- Update `H/_state.json` (`current_round`, and `best` when test beats it). Rewrite
  `H/narrative.md` with the status table on top:

  | round | change | test | train | used context | out toks | $/run | spend |
  |---|---|---|---|---|---|---|---|

  Under the table, one paragraph on what's winning and why.
- In chat, one line: `v3 test 52% → 61% (±18), change: <one line>`, then the path to
  `narrative.md`.
- **Keep or revert:**
  - train up and test flat: it overfit. Revert it and try another angle.
  - train down: revert.
  - train down but test up: treat it as noise until a rerun confirms.
  - a tie: keep the later round.
- Check the stopping condition. After 2–3 rounds flat within noise, go to
  `references/04-stall.md`.
