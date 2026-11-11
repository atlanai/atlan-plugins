# Commit on names before building

Read after [business focus and purpose](intent.md) on a fresh build of the person's own
data, before runtime setup or generation. Demos skip this file: their source choice selects
what to build, so they continue directly through generation and checks.
A resume with a saved selection skips it too.

The point is relevance before accuracy. The person commits to metric names, so a wrong
focus costs one question and not a generation run.

## What it looks like in each entry

[The entry](entry.md) sets the shape. All three reach this step with a survey of every
supplied source.

| Entry | Shape of the inventory | The question |
| --- | --- | --- |
| No input | Every page of the report with its metric count, the chosen focus's names first, then two or three questions they answer | None: the whole report is built |
| A source | Pages, each with its metric count and one question it answers | None: the whole report is built |
| A domain | Names with a status: reuse, new or flag | "Build the N new metrics?" |
| A stated problem | The names that answer it first, then the rest of the report | None |

A chosen report is built whole, every page and measure. The inventory tells the person what
the context will hold before any rule is written; it does not ask them to pick. Only a domain,
where nothing is a report, asks which new names to build. A person who names the exact
metrics or pages they want, in their own words, has narrowed it: record that and build that.

## 1. Get the candidates

Call `source-survey` `candidates` with the focus or problem. It returns at most ten
names from the sources, ranked by relevance, each with one line of meaning and where it
came from, and two or three questions the names would answer. It generates no SQL and runs
no check. Show the questions under the names, as "Questions it will answer": they tell the
person what the context is for before any rule is written.

## 2. Show it; ask only in a domain

Show the inventory as an ordinary reply under its Doing and Why lines, saying in one sentence
that these are names found in the sources and that nothing is built yet. With a report, go
straight on to building: the selection is every name the report holds.

In a domain entry, ask one native question, "Build the N new metrics?", listing the new names
as choices, most relevant first, with multiple selection where the host allows it:

| Option | Description |
| --- | --- |
| All of these | Build every new name |
| Each new name | Its one line of meaning and its source, such as "41 past queries" |
| Something else | The person names a measure; check it against the survey before accepting |

A measure the survey does not show is not refused: say that no source defines it, and that
it would be authored from the tables alone and marked for review.

For an understanding-only purpose, the names set what to explain; no build follows unless
asked.

## 3. Build from the selection

Record the selection in the run, then select tables and dashboards (step 3) and enter
[Build](build-check.md). Call
`metrics-generation` `generate` with the selection and the whole survey: it reads every
source before authoring, and authors the selected names and what they depend on. The collection review's representative metric is the top selected name.

If the survey's readiness is `none`, do not ask this question. Say the one thing that is
missing and offer `analytics-demo`.
