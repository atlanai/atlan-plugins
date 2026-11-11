# Check how accurate the context is

The one flow for an accuracy check, the same whether a person asked for it or another skill
called it. Read [test](test.md) for how the test runs and how the score is shown. `<agent>`,
`<firm>` and the use case come from the run: its recorded business focus, purpose and source.

## 1. Choose the questions

`SCOPE inputs --project <project>` returns `choose`, with the sets, when the project already
holds eval sets; with none it goes straight on to generate. On `choose`, unless the request named
a set, ask once, "Which questions should I test with?":

| Option | Description |
| --- | --- |
| `<set name>` (`<N>` questions) | The eval set you already have, one option per set. |
| Generate 5 new questions | From your dashboard and the queries your analysts run most. |

A named set skips the question. Load a set with `SCOPE load --project <project> --file <set>`;
it lands exactly where `generate` would, so every later step is the same. To generate anyway,
run `SCOPE inputs --project <project> --generate`.

## 2. Show the questions, then ask to run them

Run `generate`, or the load above. There is no review step: the person sees the set and runs it.
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

Run them is the approval: run `EVALGEN review --approve all`, `save` on this machine, then
`test`. There is no edit option.

## 3. The score, and whether to improve it

Show the score and the misses as [test](test.md) describes. Then ask "Does the score look good?":

| Option | Description |
| --- | --- |
| Looks good | Keep this score as the bar to beat. |
| Improve it | I propose one change to the rule behind a miss. You approve it in Metric Studio, which makes a new version of the metric, and I test again. |

With no misses, skip this question. **Improve it** follows the [improve loop](test.md#improve-loop),
then asks this question again.

## 4. Where the questions live, then the result

Ask "Where should the questions and the score live?":

| Option | Description |
| --- | --- |
| Save to Atlan Trident (Recommended) | Beside your context in `<workspace>`, so your team can rerun the test. Only when the context is saved there |
| Keep on this machine | Nothing leaves this folder. |

Save as `save` describes. Then end with one line whose tone follows the score, and the source
caveat beside it:
- every question right: "Your `<agent>` answers all `<N>` questions `<firm>`'s analysts ask most."
- some missed: "`<k>` of `<N>` right, and `<what the misses share>`."
