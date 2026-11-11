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

A moment that asks a question carries its Doing and Why lines in the question's own text, as
plain lines before the rest, as [conventions](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost) describes.

## After Improve it: apply the fix, then test again

The check ends on its fixes, a result whose `Next:` line names the recommended fix; they are in
`<evals-run>/fixes.json`, recommended first. The person's reply is the choice: applying it (or
naming another fix) is the yes through the next score, so ask nothing more until the person must
act in Metric Studio. Keeping the score returns to the demo's next-action choice.

- **Context on this machine:** write `{"name": "<metric>", "changes": <the fix's changes>}` to a
  file in the run and run `PROJECT edit-metric <file>`. It keeps the old version, raises the
  version and re-checks the metric; a failing check keeps the old rule, so say so and name the
  next fix in a `Next:` line. Change only that metric.
- **Context in Atlan Trident:** read the metric's head with `registry__get_artifact` and write one
  proposal with `registry__create_artifact` as
  [the proposal record](../../metrics-generation/references/propose-record.md) describes: the
  fix's definition changes and the missed questions as `evidence.runs`. Metric Studio refuses a
  change to `description`, so a fix that also rewrites it lands only partly: say so in one line,
  since an agent that reads the old rule there may still answer with it. Ask one native question
  whose text is the Metric Studio link that opens the proposal (`suggestion=<file id>`), then
  "Approved it in Metric Studio?":

  | Option | Description |
  | --- | --- |
  | Approved, test again | I test the approved version on the same questions before anyone reads it. |
  | Not now | The proposal waits in Metric Studio; the context stays as it is. |

  Read the approved version's `version_ordinal`.

Then run eval-generation's [test again](../../eval-generation/references/test.md#test-again) on
the same eval folder, naming the `version_ordinal` in Trident. In Trident, release the version to
`latest` with `registry__release_artifact` only when `EVAL compare` shows the missed question
fixed and `flipped_down` empty, and say so; otherwise leave it unreleased and say which question
changed. The check's result line ends it.

On Not now, or at the end of the check, return to the demo's next-action choice.
