# Three ways in, and what each one skips

Read right after the opening, before any helper call. The entry decides which survey runs,
how much of [the interview](intent.md) is asked, and what the inventory looks like. The seven
steps and their tracker stay the same in every entry; an entry only shortens a step.

Decide the entry from the request alone. Do not ask which entry the person wants.

| The person gave | Entry | Survey (`source-survey`) | Interview | Inventory |
| --- | --- | --- | --- | --- |
| Nothing | No input | `setup` | One question: what are you trying to do? | Names and questions for the subject |
| A dashboard link, BI export, folder or repository | A source | `inventory` on it | None: the report says what it is for | Names by page, shown and not asked |
| A subject or an unanswered question, such as "sales pipeline" | A domain | `domain` | Replaced by the differences question | Reuse, new and flag |
| A warehouse only | A domain, with the warehouse as its place to look | `domain` | The one question, when no subject was named | Names from query history |
| A source and a subject | A source, with the subject as the focus | `inventory` | Purpose only, when it is not plain | Names for the focus |

A resume, a details request, a survey-only request and a named argument are not entries: the
skill's own sections handle them.

## Say what is happening, in the demo's way

Own-data setup talks as the demo does ([the tour rules](../../../analytics-demo/references/tour.md),
"Say what and why at every moment"), with this run's facts:

- Before each moment, two short lines as an ordinary reply, at most 35 words together:
  `**Doing:**` what is happening, `**Why:**` what it means for the person.
- After each moment, its result in one line, in the past tense, with counts: "Read the sales
  report: 8 pages, 34 measures, all from 5 warehouse tables I can reach."
- Never a file name, helper name or query-pattern count in those lines, and never the words
  candidate, engine, gate, hold, export or Registry.
- A difference from a report or a published metric is news: name it, say why, and count it.

| Moment | Doing | Why |
| --- | --- | --- |
| Setup survey | Getting to know your tools: your connectors, Atlan and the files in this folder. | The context is built from what you already have, so I need to know what that is. |
| Reading a source | Reading the report's pages and measures, and the tables behind them. | The report already says how your team measures this, so the context starts from it. |
| Domain survey | Looking for everything about `<subject>`: Atlan metrics and team rules, certified queries, reports and tables. | If your team already defined these numbers, I reuse them and show where they disagree. |
| Inventory | Listing what the context will hold. | You see the metrics and questions before I write each rule. |
| Build and check | Writing the rule for each metric, then running each one on `<warehouse>`. | A rule that returns nothing or nonsense would give wrong answers later; this catches it now. |

## No input

1. **Open and survey.** The skill's opening, then the setup survey's Doing and Why. Run
   `source-survey` `setup`.
2. **One line, one question.** [The interview](intent.md): "Found: Power BI (2 reports),
   Snowflake, 90 days of query history." then "What are you trying to do?". The found line is
   the question's own text. No table, no readiness notes, no suggested starting point.
3. **Work out the rest and say it.** The subject, the source and the period come from the
   answer and the survey, stated in one sentence. Ask a narrow follow-up only when two
   buildable sources could hold the subject, or none does.
4. **State the sources and start.** Run `source-survey` `inventory` on the chosen source,
   then `plan`, said in one sentence, not asked: reports, then query history, then table
   definitions. The person can still say to leave history out.
5. **Inventory.** [Names and questions](names.md), shown and not asked, with the subject's
   names first. Then step 4 onward as the skill lists it.

When nothing can be built from, stop after the survey with the one thing that would change
it, and mention `analytics-demo`. Ask nothing.

**A report is always built whole.** Every page and every measure of a chosen report is in
scope: never ask which pages to build, and never narrow a report to the subject. The subject
decides what is shown first, which metric represents the collection and which questions are
suggested. A person who asks for less in their own words gets less.

When the survey's readiness is `none`, stop after step 2 with the one thing that would change
it, and mention `analytics-demo`. Ask nothing.

## A source

The person showed their interest by naming it, so nothing is asked before the build.

1. **Read it.** The reading Doing and Why, then `source-survey` `inventory`. The result line
   says what was read and that its tables can be reached. A source that cannot be built stops
   here with the reason and the one way round it; a link that cannot be opened asks once for
   an export. Ask nothing else.
2. **Show the inventory by page.** `source-survey` `candidates` with no focus: a table of
   pages, each with its metric count and a question it would answer. This is information,
   not a choice: the whole report is built, every page.
3. **Record intent without asking.** The report's subject is the focus:
   `PROJECT journey domain` with it, or with the subject the person named. The purpose is
   "Answer questions here" unless the person said otherwise: `PROJECT journey purpose` with
   their words when given. Ask the purpose only when the request made it unclear whether they
   want anything built at all.
4. **State the sources and start.** `source-survey` `plan`, said in one sentence, not asked:
   "Building all 34 metrics from the report's 8 pages. I'm also using 90 days of query history
   on its tables, and their columns, to check every rule." The person can still say to leave
   history out or to build less. Continue into step 4 in the same turn.

## A domain

The person named a subject, so the job is to reuse what exists before building anything.

1. **Survey the subject.** The domain Doing and Why, then `source-survey` `domain`.
2. **Show what exists and where it disagrees.** The result line, the "what exists" table and
   the differences table, as the text of one question: "How should I handle these N
   differences?" with these choices. With no differences, skip the question and say so.

   | Option | Description |
   | --- | --- |
   | Follow Atlan's published rules and flag the rest (recommended) | Answers use the released definitions; what contradicts them gets a note for its owner |
   | Go through each one | One question per difference |
   | Propose fixes to the owners | Each owner reviews a proposed change |

3. **Record intent.** The subject is the focus. Ask the purpose only when it is not plain
   from the request.
4. **Inventory with statuses.** [Names](names.md) labelled reuse, new or flag. Ask whether to
   build the new ones. Reused metrics are never rebuilt; flagged items are never edited.
5. **Build the new names only**, from the sources the survey named, then step 4 onward. The
   celebration counts reused and new metrics separately, and the save question is about the
   new ones.

When nothing exists and no table matches, say which words were searched and ask once for a
source; the entry becomes "A source" with the subject as its focus.
