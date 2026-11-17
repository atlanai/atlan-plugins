# Check accuracy before the questions

Read when the context is saved or kept on this machine, before [ready for questions](ready.md),
and when an own-data person picks **Check how accurate it is** later. The build has made the
context; this offers to test how often it answers right on questions the person's dashboard and
analysts already ask, before they ask it anything. The check itself is the demo's
[accuracy](../../../analytics-demo/references/accuracy.md) procedure, steps 1 to 4, run on this
build's run with the differences below. This file owns the offer and the ending.

An existing build is checked as it is. A person who asks "check how accurate my agent is" in
the folder of a build made earlier needs no new build and no re-check of its metrics: run
`CHECK start` on that build's run and go on from the offer's yes.

## When to offer

Before the offer, and never after it, run `CHECK start --project <project> --run <run-dir>`
silently (as accuracy-check's [run and score a set](../../../accuracy-check/references/run.md)
names it), with `<run-dir>` this build's own run folder. Always pass `--run`: a project can hold
earlier runs, and the check must test this one. Offer the check when the reply is `ok` and it
returns a set, or the run checked at least one dashboard tile or past query to draw questions
from. Skip the offer, and say nothing about it, when:

- the collection holds no checked metric;
- `CHECK start` is blocked, or returns no set and the run has neither tiles nor past queries;
- `execution.via` is `host` and this conversation has no warehouse query tool to run SQL with;
- the request already supplied a question or another next action: that goes first, and the
  check stays in the next-step choices.

The check runs on every kind of build. `execution.via` in the reply says how: `local` runs SQL
on the run's own database, `host` runs it on the warehouse with the tool the build used, as
[where the SQL runs](../../../accuracy-check/references/run.md#where-the-sql-runs) describes.
An offer is a promise: never make it without this reply in hand.

## The offer

Ask one native question, header `Accuracy`, whose text is these lines, filled from this run, as
[text before a native question](../conventions.md#text-before-a-native-question-is-lost) requires.
`<agent>` names the use case the person chose ("job profit agent"); `<firm>` is the organization
the sources belong to, or "your team" when the sources do not name it.

```
Your <agent> is built: <N> metrics and <N> queries, <where it lives>.

Before you ask it anything, I can test how accurate it is. I take <N>
questions <firm>'s <dashboard and analysts> already answer and ask each
twice: to a fresh agent that sees only this context, and to one that sees
only the raw tables. I score both against the trusted answer and show them
side by side. About 8 minutes. Nothing changes unless you approve a fix.

Check how accurate it is first?
```

With a set already in the project, the second paragraph names it: "<N> questions from
<set name>". With none, it says 20 questions.

| Option | Description |
| --- | --- |
| Check accuracy first (Recommended) | I test it on questions your team already trusts and show how many it gets right. |
| Go to questions | Ask your own questions now. The check stays in the next-step choices. |

**Go to questions** goes straight to [ready](ready.md). **Check accuracy first** is the approval
to choose the questions; it runs the demo's accuracy steps 1 to 4.

## Differences from the demo

- **Tracker.** Keep the seven steps. While the check runs, add its items (Choose the questions,
  Ask each one to a fresh agent, Score the answers, and the Improve it items when picked) to the
  host task list between steps 5 and 6. The check ticks none of the seven steps.
- **Size.** 20 questions, not the demo's 8: `CHECK start` without `--questions`, and
  `eval-generation`'s `SCOPE pick` without `--limit`.
- **The run.** Pass `--run <run-dir>` to every `CHECK start`, `CHECK load` and `SCOPE inputs`,
  and `--use-case "<agent>: <what it answers>"` to `SCOPE inputs`, from the interview. Never
  let them pick a run, and never ask the person which run: this build knows its own.
- **Before and after.** Every check here also runs accuracy-check's
  [before and after](../../../accuracy-check/references/run.md#before-and-after) pass, started
  with the context pass so both finish together. The score question's text opens with its
  `report`, word for word, above the score line and the misses. The Test again step runs the
  context pass only, and its result names the raw-source score beside the new one.
- **A warehouse build.** `CHECK load` returns `pending_gold` and the answering agents run their
  SQL on the warehouse, as accuracy-check's run procedure describes. Use the warehouse query
  tool this build already used. The time in the offer stays about 8 minutes.
- **Facts.** There is no facts file: `<agent>`, `<firm>` and the use case come from this run's
  intent and sources, as in the offer.
- **Generating.** When `eval-generation` `generate` returns blocked, say its reason in one line,
  that a set the person brings goes in `.atlan-evals/sets/`, and go to [ready](ready.md).
- **Releasing in Atlan Trident.** This is the person's real context, read by their team. After a
  tested fix that `EVAL compare` clears, ask one native question, "Release version <N> to
  everyone?", whose text is the score move and the changed rule, before
  `registry__release_artifact`. Options: Release it (Recommended), and Keep it unreleased. A
  workspace that releases on its own needs no question; withdrawing a release that made a
  question worse follows the demo's rule.
- **Saving the check.** The demo's step 5 save question is asked on every check, whether the
  context is in Atlan Trident or on this machine (then the person's personal workspace). No agent
  is registered; `<agent>` is the name from the offer, used only in the saved summary.

## The ending

The check ends on the question step, not on the demo's next-step question. After the demo's
step 5 save question, when it applies, read [ready](ready.md) and ask "What would you like to
ask?" with its ready block, adding one row after What it can answer:

```
  How accurate          <p>% on <N> questions <firm>'s analysts ask
                        <q>% on the raw tables alone
                        <, up from <p>% after one fix>
```

`<q>` is the raw-source accuracy from `EVAL before-after`. When every question was right the
score question was never asked, so the `report` goes above this block, word for word, in the
same question's text. Every question right puts the demo's banner above "Your <agent> is ready."
Leave the score line, the source line and the misses out of the block: the rows carry the result. When the person
picked **Check how accurate it is** after answers instead, end on the demo's result block
(`ACC result`) and ready's `Next:` line, without that option.
