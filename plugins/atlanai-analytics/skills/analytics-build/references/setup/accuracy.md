# Check accuracy

Read before [the ready question](ready.md), to know whether it offers the check, and when an
own-data person picks its **Check how accurate it is** option or asks later how accurate their
agent is. The check tests how often your agent answers right on questions the person's
dashboard and analysts already ask. The check itself is the demo's
[accuracy](../../../analytics-demo/references/accuracy.md) procedure, steps 1 to 4, run on this
build's run with the differences below. This file owns when it is offered and the ending.

An existing build is checked as it is. A person who asks "check how accurate my agent is" in
the folder of a build made earlier needs no new build and no re-check of its metrics: run
`CHECK start` on that build's run and go on as if they had picked the option.

## When the ready question offers it

Before `PROJECT question ready`, run `CHECK start --project <project> --run <run-dir>`
silently (as accuracy-check's [run and score a set](../../../accuracy-check/references/run.md)
names it), with `<run-dir>` this build's own run folder. Always pass `--run`: a project can hold
earlier runs, and the check must test this one. The check is available when the reply is `ok`
and it returns a set, or the run checked at least one dashboard tile or past query to draw
questions from. Then add `--accuracy` to `PROJECT question ready`, and its options include
**Check how accurate it is**. Leave it out, and say nothing about the check, when:

- the collection holds no checked metric;
- `CHECK start` is blocked, or returns no set and the run has neither tiles nor past queries;
- `execution.via` is `host` and this conversation has no warehouse query tool to run SQL with;
- a check has already run on this run.

The check runs on every kind of build. `execution.via` in the reply says how: `local` runs SQL
on the run's own database, `host` runs it on the warehouse with the tool the build used, as
[where the SQL runs](../../../accuracy-check/references/run.md#where-the-sql-runs) describes.
The option is a promise: never add `--accuracy` without this reply in hand.

## When the person picks it

Picking **Check how accurate it is** is the approval to choose the questions; it runs the demo's
accuracy steps 1 to 4. Start with one line, filled from this run: "I'll take `<N>` questions
`<firm>`'s `<dashboard and analysts>` already answer and ask each twice: once to a fresh agent
that sees only your metrics and saved queries, and once to one that sees only column names.
Nothing changes unless you approve a fix." `<N>` names the project's set when there is one ("`<N>`
questions from `<set name>`"), else 20. With one question, it reads "I'll take the one question
`<firm>`'s `<dashboard and analysts>` already answer and ask it twice", the rest unchanged.
`<agent name>` names the use case the person chose ("job
profit agent"); `<firm>` is the organization the sources belong to, or "your team" when the
sources do not name it.

## Differences from the demo

- **Tracker.** Keep the setup's steps. While the check runs, add its items (Verify your evaluation
  questions, Run your agent on each question, Score the answers, and the fix items when the person
  replies "fix") to the host task list between Save and Ask a question. The check ticks none of the setup's steps.
- **Size.** 20 questions, not the demo's 8: `CHECK start` without `--questions`, and
  `eval-generation`'s `SCOPE pick` without `--limit`.
- **The run.** Pass `--run <run-dir>` to every `CHECK start`, `CHECK load` and `SCOPE inputs`,
  and `--use-case "<agent name>: <what it answers>"` to `SCOPE inputs`, from the interview. Never
  let them pick a run, and never ask the person which run: this build knows its own.
- **Before and after.** Every check here also runs accuracy-check's
  [before and after](../../../accuracy-check/references/run.md#before-and-after) pass, started
  with the context pass so both finish together. The raw pass receives column names alone, with
  no additional context or exploratory data queries; only its final answer SQL reads data. The
  score and the result are written as accuracy-check's
  [show the score](../../../accuracy-check/references/run.md#show-the-score) describes. The Test
  again step runs the context pass only, and its result names the column-names-alone score beside
  the new one.
- **A warehouse build.** `CHECK load` returns `pending_gold` and the answering agents run their
  SQL on the warehouse, as accuracy-check's run procedure describes. Use the warehouse query
  tool this build already used. The option's time stays about 8 minutes.
- **Facts.** There is no facts file: `<agent name>`, `<firm>` and the use case come from this run's
  intent and sources, as above.
- **Generating.** When `eval-generation` `generate` returns blocked, say its reason in one line,
  that a set the person brings goes in `.atlan-evals/sets/`, and go to [ready](ready.md).
- **Releasing in Atlan.** This is the person's real context, read by their team. After a
  tested fix that `EVAL compare` clears, ask one native question, "Release version <N> to
  everyone?", whose text is the score move and the changed rule, before
  `registry__release_artifact`. Options: Release it (Recommended), and Keep it unreleased. A
  workspace that releases on its own needs no question; withdrawing a release that made a
  question worse follows the demo's rule.
- **Saving the check.** Offered as Save this score to Atlan in the ask for more question, with 5
  questions or more, whether the context is in Atlan or on this machine (then the person's
  personal workspace). No agent is registered; `<agent name>` is the name above, used only in the
  saved summary.

## The ending

The check ends on the result and the ask for more, not on the demo's next-step question. Write the
result as the demo's [step 5](../../../analytics-demo/references/accuracy.md#5-the-result) says,
then ask accuracy-check's [ask for more](../../../accuracy-check/references/run.md#ask-for-more)
question: `CHECK next --evals <evals-run> --run <run-dir>`, its options as they are, its text the
result, a blank line and its `lead`. After it resolves, or on I'm done for now, the last step is
`PROJECT question close`, its closing message as it prints. Never run `PROJECT question ready`
again after a check. A pick that builds again hands over to that build.

When the person picked **Check how accurate it is** after answers instead, end the same way.
