# Three ways in, and what each one skips

Read right after the opening, before any helper call. The entry decides which survey runs,
how much of [the interview](intent.md) is asked, and what the inventory looks like. The seven
steps and their tracker stay the same in every entry; an entry only shortens a step.

Decide the entry from the request alone. Do not ask which entry the person wants.

| The person gave | Entry | Asked first | Survey (`source-survey`) | Confirmed before building |
| --- | --- | --- | --- | --- |
| Nothing | No input | What are you trying to do? | `setup`, after the answer and about it | Source check and inventory |
| A dashboard link, BI export, folder or repository | A source | Nothing: naming it is the intent | `inventory` on it | Source check and inventory by page |
| A subject or an unanswered question, such as "sales pipeline" | A domain | Nothing: the subject is the intent | `domain` | Differences, then the new names |
| A warehouse only | A domain, with the warehouse as its place to look | What are you trying to do?, when no subject was named | `domain` | Names from query history |
| A source and a subject | A source, with the subject shown first | Nothing | `inventory` | Source check and inventory |

The order is always the same: know the intent, then look, then confirm, then build. With no
input the intent is asked; otherwise the request gave it.

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

1. **Ask first.** [The interview](intent.md): the opening and "What are you trying to do?",
   as one question. Read nothing, list nothing and call nothing before the answer.
2. **Survey for the answer.** The setup survey's Doing and Why, then `source-survey` `setup`
   (and `domain` when the answer names a subject), then the result in one line: "Found: Power
   BI (2 reports), Snowflake, 90 days of query history."
3. **One contextual question, only if needed.** When two buildable sources could hold what
   they said, or none does, ask which, from what was found. With one clear source, skip it
   and say the subject, source and period in one sentence.
4. **Check the source and confirm.** `source-survey` `inventory` on the chosen source, then
   `plan` and `candidates`. Show the source check and [the inventory](names.md) and ask
   "Build this?", as the interview's confirmation describes. Record the intent now that the
   run exists.
5. **Build** on yes: step 4 onward as the skill lists it.

When nothing can be built from, stop after the survey with the one thing that would change
it, and mention `analytics-demo`.

**A report is always built whole.** Every page and every measure of a chosen report is in
scope: the confirmation offers the whole report, never a pick-list of pages, and a report is
never narrowed to the subject on the skill's own judgment. The subject decides what is shown
first, which metric represents the collection and which questions are
suggested. A person who asks for less in their own words gets less.

## A source

Naming a source is clear intent, so no interview is asked. The source is checked, the
inventory is shown, and the person confirms once before anything is built.

1. **Read it.** The reading Doing and Why, then `source-survey` `inventory`. A source that
   cannot be built stops here with the reason and the one way round it; a link that cannot
   be opened asks once for an export.
2. **Record intent without asking.** The report's subject is the focus:
   `PROJECT journey domain` with it, or with the subject the person named. The purpose is
   "Answer questions here" unless the person said otherwise: `PROJECT journey purpose` with
   their words when given.
3. **Check the source and confirm.** `source-survey` `plan` and `candidates` with no focus.
   One message, as the confirmation question's text: the source check in one sentence ("I'll
   build from this report's 8 pages, 90 days of query history on its tables, and their
   columns, all reachable."), the inventory as a table of pages with each one's metric count
   and a question it would answer, then "Build this?" with "Build it (recommended)" and
   "Change something".
4. **Build** on yes: the whole report, every page, unless the person changed that.

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
