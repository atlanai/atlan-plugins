# Check how accurate it is

Read when the person picks **Check how accurate it is**, or asks how accurate the agent built in
this folder is. The demo has built the context; this shows how often it gets an answer right, on
questions the firm's analysts already ask, and fixes the rule behind a miss. The demo runs the
whole check; [eval-generation](../../eval-generation/SKILL.md) is called only to generate or save a set.
How a run, a score and a diagnosis work is in [run the accuracy check](accuracy-run.md).
`<agent>` and `<firm>` come from the demo's facts file; `ACC`, `EVAL` and `PROJECT` are as that
file names them.

When the host keeps a task list, its items are these, in the person's words, whichever questions
are picked: **Choose the questions**, **Ask each one to a fresh agent**, **Score the answers**.
When the person picks Improve it, add **Find what to fix**, **Apply the fix** and **Test again**
to the list before diagnosing, and mark each done as it finishes.

Every moment follows [text before a native question is lost](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost):
a block to read before choosing is the question's own text, as plain lines with no Markdown, its
Doing and Why lines first; a result is reply text that ends the turn with a plain `Next:` line.

| Moment | Doing | Why |
| --- | --- | --- |
| Choose | Picking questions that test the metrics your `<agent>` needs, from `<firm>`'s dashboard and its analysts' queries. | The test should use questions people really ask, about what this agent is for. |
| Test | Asking each question to a fresh agent that only sees your context. | It answers as anyone would, so the score is the context's, not mine. |
| Fix | Changing the rule behind the miss. | One rule changed once changes every answer that uses it. |

An eval set lives in the project (`.atlan-evals/sets/`), never fetched from Atlan Trident: never
look up Trident, a workspace or saved evals for the questions, the run or the score. Only the
save in step 5 reads a workspace.
`context_saved_to` (from `ACC start` and `ACC load`) says where the context lives; `null` means
this machine only.

## 1. Choose the questions

`ACC start --project <project>` returns the built run, `context_saved_to` and the project's
`sets`. With no sets, or the person asking for new questions, generate. Otherwise, unless the
request named a set, ask one native question, "Which questions should I test with?":

| Option | Description |
| --- | --- |
| `<set name>` (`<N>` questions) | The eval set you already have, one option per set. |
| Generate 5 new questions | From your dashboard and the queries your analysts run most. |

- **A set:** picking it is the approval to run it. `ACC load --project <project> --file <set>`,
  then step 3, with no list to confirm.
- **Generate:** run `eval-generation` `generate` (not its `save`), then `ACC load` on the set it
  wrote, then step 2.

`ACC load` drops a question whose trusted SQL fails or returns nothing; name how many.

## 2. Generated questions: show them, then ask to run them

The person has not seen these, so show them before running. Ask one native question whose text
is this block, as plain lines: each question from `<evals-run>/work/spec.json`, its metric, source
and expected answer from `<evals-run>/work/selection.json`, and the use case from `ACC start`:

```
<N> questions to test your <agent>. They test the metrics it needs to answer
<the use case>, starting with <focus metric>. Each comes from <firm>'s dashboard or a
query its analysts already run, and the expected answer is what that query returns.

1. <question>
   Tests <metric>, from <where it came from>. Expected: <one short value>

Run these <N> questions?
```

The expected answer is one value or one short phrase, never a table, and never SQL unless asked.

| Option | Description |
| --- | --- |
| Run them (Recommended) | Each goes to a fresh agent that sees only your context, about 2 minutes. |
| Not now | Keep the questions on this machine and stop here. |

There is no edit option.

## 3. Run and score

Run the set as [run it](accuracy-run.md#run-it) describes. With every question right, go to
step 5. With misses, ask one native question, "Does the score look good?", whose text is the
score and the misses as [show the score](accuracy-run.md#show-the-score) describes, as plain lines:

| Option | Description |
| --- | --- |
| Improve it (Recommended) | I look at why each miss failed and what would fix it. |
| Looks good | Keep this score as the bar to beat. |

Name no fix in the question's text: the fixes come from the diagnosis.

## 4. Improve it: find the fix, apply it, test again

[Diagnose the misses](accuracy-run.md#diagnose-the-misses). The fixes are a result whose `Next:`
line names the recommended fix. The person's reply is the choice: applying it (or naming another
fix) is the yes through the next score, so ask nothing more until the person must act in Metric
Studio. Keeping the score goes to step 5.

- **Context on this machine:** write `{"name": "<metric>", "changes": <the fix's changes>}` to a
  file in the run and run `PROJECT edit-metric <file>`. It keeps the old version beside the run (never where answering agents read), raises the
  version, re-checks the metric and keeps the engine's own copies of it in step; a failing check
  keeps the old rule, so say so and name the next fix in a `Next:` line. Change only that metric,
  never a file by hand, and do not raise saved queries with the person.
- **Context in Atlan Trident:** read the metric's head with `registry__get_artifact` and write one
  proposal with `registry__create_artifact` as
  [the proposal record](../../metrics-generation/references/propose-record.md) describes: the
  fix's definition changes and the missed questions as `evidence.runs`. Metric Studio refuses a
  change to `description`, so a fix that also rewrites it lands only partly: say so in one line.
  Ask one native question whose text is the Metric Studio link that opens the proposal
  (`suggestion=<file id>`), then "Approved it in Metric Studio?":

  | Option | Description |
  | --- | --- |
  | Approved, test again | I test the approved version on the same questions before anyone reads it. |
  | Not now | The proposal waits in Metric Studio; the context stays as it is. |

Then [test again](accuracy-run.md#test-again), naming the approved `version_ordinal` in Trident.
In Trident, release that version to `latest` with `registry__release_artifact` only when
`EVAL compare` shows the missed question fixed and `flipped_down` empty, and say so; otherwise
leave it unreleased and say which question changed. Then step 5.

## 5. The result

With the context in Atlan Trident, save the set through `eval-generation` `save` on its set file,
which asks where it should live. With the context on this machine, ask nothing: the set stays in
the project.

End with the closing line, a result: reply text with the score's range as its only caveat (plus
the source caveat for a generated set; no significance test, no open questions), then the
demo's own `Next:` line from [ready](../../analytics-build/references/setup/ready.md). Nothing
else comes after it: no offer to change a saved query, another metric or the set, and no note
about saved queries or past queries that still follow the old rule.

- every question right: this banner, then the line:

  ```
   *   .  '   *    .   '  *   .    '   *  .
     '   *   .   ALL <N> RIGHT   .   *   '
   .   *    '   .    *   '    .   *    '  .
  ```

  after a fix, "Your <agent> now answers all <N> questions right: the score went from <k> to
  <N> of <N>, and <what changed, plain words>."; first time, "Your <agent> answers all <N>
  questions <firm>'s analysts ask most."
- some missed: "<k> of <N> right, and <what the misses share>."
