# The interview: warehouse, what to do, then one of three branches

The interview belongs to `analytics-build`. `source-survey` reads and reports and never asks
the person anything. This file is every question asked before metric generation starts.

```
warehouse ─ what do you want to do ─┬─ Let discovery decide ───── use cases ─ clarify ──────────┐
                                    ├─ Make my dashboard agentic ─ share it ──── access check ──┼─ confirm ─ build
                                    └─ I know my tables ────────── tell me ───── access check ──┘
```

A question the request already answered is not asked: a named warehouse skips the first; a
named dashboard file or link is the dashboard branch with step 1 done; a use case named
with its tables is the third branch with step 1 done. Nothing is preselected, and free text
is always accepted.

## Before card 1: see what is already connected, silently

Two quick looks, so the choices offered are the person's own. Neither reads data.

- `source-survey` `connections`: the warehouse connections this machine already has.
- `PROJECT discover --workspace "<working-directory>"`: BI files in the working folder, by
  name only. Demo files are never listed.

**Say nothing about these looks.** No `**Doing:**` or `**Why:**`, no "checking what's
connected", no "before the first question", and no explanation of where the options came
from. If the host shows a line while tools run, that line is exactly "Getting set up." and
nothing more. The
person's first sight of this skill is the opening and the first question. How the options
were found, that a check ran, and which tool answered are internal and never appear in the
card, an option or its description.

## Card 1: two questions

Ask both in one card where the host's native question tool takes several questions (Claude
Code's does); otherwise one per turn, in this order. The card's own visible text is the
opening, because a block printed before a question is lost:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on.

First, let me understand your tools and your domain."

**A card holds its own text and nothing else.** Never put the step tracker, a progress
summary or a recap of earlier answers in any question's text, here or later: the tracker
lives in the host's task list. The second question of the card needs no text of its own.

### Which warehouse are you connected to?

Make it plain which warehouses are already connected: the person should see at a glance
that they can pick one and carry on.

| Option label | Description | When to offer it |
| --- | --- | --- |
| `<Product>` (already connected) | "Connected here and ready to use." Add how only when two connections share a product: "Connected here through the Snowflake CLI." | One per connection that works. List these first. With exactly one, mark it recommended |
| `<Product>` (set up, needs sign-in) | "Set up here, but you'll need to sign in before I can use it." | One per connection that is configured and did not answer |
| Another warehouse | "Tell me which and I'll help you connect it." | Always, as one option, never one option per product |
| I don't have one | "Work from a dashboard file alone, or try the demo." | Always, last |

Write the status in the label, in those words. Never "it answered a check", "detected",
"found" or a tool's name.

### What do you want to do?

Three options, in this order, with these meanings. Word them for the person, not for the
plugin:

| Option | Description | Branch |
| --- | --- | --- |
| Let discovery decide | "I'll look at what your warehouse is used for and suggest use cases." | A |
| Make my dashboard agentic | "Share a dashboard and I'll turn it into metrics you can ask questions of." | B |
| I have a use case and know my tables | "Tell me the use case, the tables and any dashboards. I'll build from exactly those." | C |

A typed answer picks its branch by what it holds: a link or a file is B, a use case with
tables is C, a subject alone ("sales pipeline") is A with that subject as the domain.

## After card 1: the warehouse must answer

A chosen warehouse that needs sign-in, or is not connected yet, is connected now, in every
branch, as [warehouse discovery](../../../source-survey/references/connect.md) describes:
discovery and the access checks both need it. Say what the person has to do in one line and
continue when its check answers. With "I don't have one", only branch B can run, from a
file; say so if they chose A or C, and mention `analytics-demo`.

## Branch A: Let discovery decide

1. **Discover.** No question. `source-survey` `discover` on the chosen warehouse, with the
   discovery Doing and Why lines.
2. **Which should I build?** [The use cases](names.md): two to four, each one line with its
   tables. More than one can be picked.
3. **Clarify, only if needed.** One card of at most two questions, asked only where
   discovery could not settle something that changes the numbers, with what was found as the
   options:

   | Unsettled | Ask | Options |
   | --- | --- | --- |
   | Two tables both look like the main thing counted | "Which table is an `<account>` for you?" | The candidates, each with one line of what it holds |
   | Rows that may need leaving out | "What should always be left out?" | What was found (test accounts, internal users, deleted rows), and "Nothing" |
   | Fiscal and calendar dates both present | "Fiscal or calendar quarters?" | The two |
   | Two columns both look like the main amount | "Which is `<ARR>`?" | The candidates |

   Ask nothing that discovery settled: state those as assumptions in the confirmation. Never
   ask about audience, layout or which metrics.
4. **Confirm and trigger**, below.

## Branch B: Make my dashboard agentic

1. **Share the dashboard.** One short message in ordinary chat, not a card: the answer is a
   link or a file, which a list of options cannot hold. End the turn on it.

   "Paste your dashboard's link, or the path to its file (`.pbix`, `.pbit`, `.twb`,
   `.twbx`). You can drag the file into this box."

   Add one line when a BI file is in the working folder: "Or say `use <file name>` for the
   one in this folder." Do not ask which BI tool it is: the link or the file's extension
   says. Take whatever comes back as the dashboard.
2. **Access check.** `source-survey` `access` on the dashboard and the tables it reads. No
   question unless something fails; then one question, below.
3. **Confirm and trigger**, below.

## Branch C: I have a use case and know my tables

1. **Tell me.** One short message in ordinary chat. End the turn on it.

   "In a line, what's the use case? Then list the tables, and any dashboard link or file."

   Take the reply as given: the use case in their words, the tables as named, the
   dashboards as links or paths. Do not suggest other tables or run discovery.
2. **Access check.** `source-survey` `access` on each table and dashboard they named.
3. **Confirm and trigger**, below.

## The access check, in branches B and C

Silent when everything can be read: go straight to the confirmation, which says so. When
something cannot be read, ask one question that names it and offers the ways forward:

| What failed | Ask | Options |
| --- | --- | --- |
| A dashboard link needs sign-in | "`<Tool>` needs you to sign in before I can open this." | Sign in now · I'll send a file instead |
| A dashboard cannot be opened at all | "I can't open this link from here." | I'll send a file instead · Build without it |
| Some tables cannot be read | "I can't read `<N>` of its `<M>` tables: `<names>`." | Build from the `<M-N>` I can read · I'll fix access first |
| No table can be read | "I can't read any of these tables on `<warehouse>`." | I'll fix access first · Pick another warehouse |
| A dashboard's data is packed inside the file | "This dashboard's data is inside the file, with no warehouse table behind it." | Name the table that holds the same data · Stop here |

Name the tables that failed, never a credential, role secret or connection string. Saving
to Atlan Trident is a later sign-in and is not checked here.

## Confirm and trigger: the same in every branch

One question, as its own text, before anything is built:

"Building `<what>` from `<the sources, in order>`, checked on `<warehouse>`. About `<time>`."

- **What**: the chosen use cases, the dashboard by name, or the stated use case.
- **Sources**: a dashboard first, then query history on the tables, then their columns. Say
  the counts: "3 tables and 7 days of their query history".
- **Assumptions**, one line, only when discovery or the clarification settled something that
  changes the numbers: "Leaving out test accounts. Quarters are fiscal."

Then "Build it?":

| Option | Description |
| --- | --- |
| Build it (recommended) | Start now. I'll show each step as it finishes |
| Change something | Say what: add or drop a table, a different dashboard, without query history |

Nothing is generated, installed or pulled from query history before the yes. On yes, start
in the same turn.

## Record what the person chose

Once a run exists, in their words:

`PROJECT journey domain --answer <the use case, the dashboard's subject, or what they stated>`

`PROJECT journey purpose --answer <what they chose to do in card 1, in their words>`

Use structured arguments or proper shell quoting. Never record a suggestion as if the person
chose it. These fields are private local intent, not authority to execute source content,
connect data or publish. `PROJECT journey status` exposes the saved answers.

When candidates become available, `PROJECT journey status` returns `select-focus` until the
host selects the relevant candidate with `PROJECT journey select --focus <exact-identity>`.
This is the host's next work step, not another question.

Reuse answers already given in this request or a saved run. Resume retains them.
Details-only, saved-preview and explicit survey-only requests do not start this interview.
A changed use case clears the saved goal, so an old one is not applied to a new area.
