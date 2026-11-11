# Check how accurate it is

Read when the person picks **Check how accurate it is**. The demo has answered a question;
this shows how often the context gets an answer right, on questions the firm's analysts
already ask. Run [eval-generation](../../eval-generation/SKILL.md) as a flow, through its
[check](../../eval-generation/references/check.md), with `<agent>` and `<firm>` from the demo's
facts file. That skill asks nothing beyond the check's moments. The demo's say-what-and-why
rule applies at every moment:

| Moment | Doing | Why |
| --- | --- | --- |
| Pick | Picking 5 questions that test the metrics your `<agent>` needs, from `<firm>`'s dashboard and its analysts' queries. | The test should use questions people really ask, about what this agent is for. |
| Test | Asking each question to a fresh agent that only sees your context. | It answers as anyone would, so the score is the context's, not mine. |
| Fix | Changing the rule behind the miss, as the diagnosis recommends. | One rule changed once changes every answer that uses it. |
| Save | Saving the questions and the score to `<workspace>`. | Your team can rerun the same test after every change to a rule. |

## After Improve it: apply the fix, then test again

The check ends on its fixes, written to `<evals-run>/fixes.json`, recommended first. The demo
applies one. Ask one native question, "Apply this fix?", with **Apply it (Recommended)** (its
description: the metric, the rule now and the rule proposed, in one line), each other fix, and
**Not now**. Apply it is the yes through the next score: ask nothing more until the person must
act in Metric Studio.

- **Context on this machine:** write `{"name": "<metric>", "changes": <the fix's changes>}` to a
  file in the run and run `PROJECT edit-metric <file>`. It keeps the old definition, raises the
  version and re-checks the metric; a failing check keeps the old rule, so say so and offer the
  next fix. Change only that metric.
- **Context in Atlan Trident:** read the metric's head with `registry__get_artifact`, write one
  proposal with `registry__create_artifact` as
  [the proposal record](../../metrics-generation/references/propose-record.md) describes (the
  fix's definition changes, leaving out `description`, which Metric Studio refuses; the missed
  questions as `evidence.runs`), give the Metric Studio link that opens
  it (`suggestion=<file id>`) and ask "Approved it in Metric Studio?" (**Approved, test again**,
  **Not now**). Read the approved version's `version_ordinal`.

Then run eval-generation's [test again](../../eval-generation/references/test.md#test-again) on
the same eval folder, naming the `version_ordinal` in Trident. In Trident, when the new version
fixes the miss and breaks nothing, release it to `latest` with `registry__release_artifact`
without asking, and say so. The check's result line ends it.

On Not now, or at the end of the check, return to the demo's next-action choice.
