# Commit on names before building

Read after [business focus and purpose](intent.md) on a fresh build of the person's own
data, before runtime setup or generation. Demos skip this file: their source choice selects what to build, and they show the
[metrics found](../../../analytics-demo/references/metrics-found.md) list instead.
A resume with a saved selection skips it too.

The point is relevance before accuracy. The person commits to metric names, so a wrong
focus costs one question and not a generation run.

## Two ways in

| Entry | The person gave | Focus question |
| --- | --- | --- |
| Cold start | A source, no stated problem | Ask it, as [intent](intent.md) describes |
| Problem-first | A problem, such as "churn by plan", with or without a source | Skip it: the problem is the focus. Ask for a source only if none is known |

Both then reach this step with a focus and a survey of every supplied source.

## 1. Get the candidates

Call `analytics-survey` `candidates` with the focus or problem. It returns at most ten
names from the sources, ranked by relevance, each with one line of meaning and where it
came from. It generates no SQL and runs no check.

## 2. Ask once

Ask one native question whose visible prompt says, in one sentence, that these are names
found in the sources and that nothing is built yet. List the candidates as choices, most
relevant first, with multiple selection where the host allows it:

| Option | Description |
| --- | --- |
| Each candidate name | Its one line of meaning and its source, such as "dashboard tile" or "41 past queries" |
| All of these | Build every listed candidate |
| Something else | The person names a measure; check it against the survey before accepting |

A measure the survey does not show is not refused: say that no source defines it, and that
it would be authored from the tables alone and marked for review.

For an understanding-only purpose, the selected names set what to explain; no build follows
unless asked. A person who already named the exact metrics they want has answered this
question: record the selection and continue.

## 3. Build from the selection

Record the selection in the run, then select tables and dashboards (step 3) and enter
[Build](build-check.md). Call
`analytics-metrics` `generate` with the selection and the whole survey: it reads every
source before authoring, and authors the selected names and what they depend on. The collection review's representative metric is the top selected name.

If the survey's readiness is `none`, do not ask this question. Say the one thing that is
missing and offer `analytics-demo`.
