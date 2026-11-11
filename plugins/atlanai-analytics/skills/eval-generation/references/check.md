# Check how accurate the context is

The one flow for an accuracy check, the same whether a person asked for it or another skill
called it. Read [test](test.md) for how the test runs and how the score is shown. `<agent>`,
`<firm>` and the use case come from the run: its recorded business focus, purpose and source.

Steps 1 to 3 run on this machine: an eval set lives in the project (`.atlan-evals/`), never
fetched from Atlan Trident. Never look up Trident, a workspace or saved evals for them.
`context_saved_to`, from `SCOPE inputs` or `SCOPE load`, says where the context lives (`null`:
this machine only); read it only at **Improve it** and in step 4.

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
Print this block as an ordinary reply, with the values from the run:

> **5 questions to test your `<agent>`.** They test the metrics it needs to answer
> `<the use case>`, starting with `<focus metric>`. Each comes from `<firm>`'s dashboard or a
> query its analysts already run, and the expected answer is what that query returns.
>
> | # | Question | Tests | From | Expected answer |
> | --- | --- | --- | --- | --- |
> | 1 | `<question_natural>` | `<metric>` | dashboard tile, or past query, run `<N>` times | `<one short value>` |

The expected answer is one value or one short phrase ("North region, 12%"), never a
table. Never show SQL here; give it when asked. Then ask one native question, "Run these 5
questions?":

| Option | Description |
| --- | --- |
| Run them (Recommended) | Each goes to a fresh agent that sees only your context, about 2 minutes. |
| Not now | Keep the questions on this machine and stop here. |

Run them is the approval: run `EVALGEN review --approve all`, `SCOPE stage --evals <evals-run>`,
then `test`. There is no edit option.

## 3. The score, and whether to improve it

Show the score and the misses as [test](test.md) describes. Then ask "Does the score look good?",
with improving as the default path:

| Option | Description |
| --- | --- |
| Improve it (Recommended) | I look at why each miss failed and what would fix it. |
| Looks good | Keep this score as the bar to beat. |

Name no fix in this question or the text before it: the fixes come from the diagnosis.

With no misses, skip this question. **Improve it** runs [diagnose the misses](test.md#diagnose-the-misses)
and ends the check on the fixes. A later call on the same eval folder [tests again](test.md#test-again).

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

Then end with the closing line, its tone following the score, and the source caveat beside it:
- every question right, on a test again: "🎉 Your `<agent>` now answers all `<N>` questions
  right: the score went from `<k>` to `<N>` of `<N>`, and `<what changed, plain words>`."
- every question right, first time: "🎉 Your `<agent>` answers all `<N>` questions `<firm>`'s
  analysts ask most."
- some missed: "`<k>` of `<N>` right, and `<what the misses share>`."
