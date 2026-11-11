# Check how accurate the context is

The one flow for an accuracy check, the same whether a person asked for it or another skill
called it. Read [test](test.md) for how the test runs and how the score is shown. `<agent>`,
`<firm>` and the use case come from the run: its recorded business focus, purpose and source.

An eval set lives in the project (`.atlan-evals/`), never fetched from Atlan Trident: never look
up Trident, a workspace or saved evals for the questions, the run or the score. The context
itself is read where it lives.
`context_saved_to`, from `SCOPE inputs` or `SCOPE load`, says where the context lives (`null`:
this machine only); read it only in step 4.

Each moment below follows [text before a native question is lost](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost):
a block to read before choosing is the question's own text, as plain lines with no Markdown,
and a result is reply text that ends the turn with a plain `Next:` line.

## 1. Choose the questions

`SCOPE inputs --project <project>` returns `choose`, with the sets, when the project already
holds eval sets; with none it goes straight on to generate. On `choose`, unless the request named
a set, ask once, "Which questions should I test with?":

| Option | Description |
| --- | --- |
| `<set name>` (`<N>` questions) | The eval set you already have, one option per set. |
| Generate 5 new questions | From your dashboard and the queries your analysts run most. |

A named set skips the question. Picking a set is the approval to run it: `SCOPE load --project
<project> --file <set>`, then `test` straight away, with no list to confirm; go on to step 3. To generate, run `SCOPE inputs --project <project> --generate`, then
step 2.

## 2. Generated questions: show them, then ask to run them

Only for questions generated now: the person has not seen them, so show them before running.
Earlier checks in the same folder stay out of the reply unless the person asks about them.
Ask one native question whose text is this block, as plain lines, with the values from the run:

```
<N> questions to test your <agent>. They test the metrics it needs to answer
<the use case>, starting with <focus metric>. Each comes from <firm>'s dashboard or a
query its analysts already run, and the expected answer is what that query returns.

1. <question_natural>
   Tests <metric>, from <a dashboard tile, or a past query run <N> times>. Expected: <value>

Run these <N> questions?
```

The expected answer is one value or one short phrase ("North region, 12%"), never a table.
Never show SQL here; give it when asked. The options:

| Option | Description |
| --- | --- |
| Run them (Recommended) | Each goes to a fresh agent that sees only your context, about 2 minutes. |
| Not now | Keep the questions on this machine and stop here. |

Run them is the approval: run `EVALGEN review --approve all`, `SCOPE stage --evals <evals-run>`,
then `test`. There is no edit option.

## 3. The score, and whether to improve it

With every question right, the score is the [result](#4-the-result). With misses, ask one
native question, "Does the score look good?", whose text is the score and the misses as
[test](test.md#show-the-score) describes, as plain lines. Before it, print nothing:

| Option | Description |
| --- | --- |
| Improve it (Recommended) | I look at why each miss failed and what would fix it. |
| Looks good | Keep this score as the bar to beat. |

Name no fix in the question's text: the fixes come from the diagnosis. **Improve it** runs
[diagnose the misses](test.md#diagnose-the-misses), whose fixes are a result that ends the
check. A later call on the same eval folder [tests again](test.md#test-again).

## 4. The result

The check ends here after a score with no diagnosis to follow: every question right, Looks good
or Not now. After **Improve it**, the fixes end it instead. Nothing comes after the closing line: no offer to change a
saved query, another metric or the set.

With the context in Atlan Trident (`context_saved_to` set), first ask "Where should the questions
and the score live?":

| Option | Description |
| --- | --- |
| Save to Atlan Trident (Recommended) | Beside your context in `<workspace>`, so your team can rerun the test. |
| Keep on this machine | Nothing leaves this folder. |

On Save to Atlan Trident, run `save`. With the context on this machine, ask nothing: the set
stays in the project.

Then end with the closing line, a result: reply text that ends the turn, with the source caveat
beside it.

- every question right: this banner, then the line:

  ```
   *   .  '   *    .   '  *   .    '   *  .
     '   *   .   ALL <N> RIGHT   .   *   '
   .   *    '   .    *   '    .   *    '  .
  ```

  on a test again, "Your <agent> now answers all <N> questions right: the score went from <k>
  to <N> of <N>, and <what changed, plain words>."; first time, "Your <agent> answers all <N>
  questions <firm>'s analysts ask most."
- some missed: "<k> of <N> right, and <what the misses share>."
