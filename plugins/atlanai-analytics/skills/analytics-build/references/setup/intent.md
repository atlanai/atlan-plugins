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
2. **Which should I build?** [The use cases](names.md): up to three, each one line with its
   tables, then "I have a dashboard". More than one can be picked. Picking the dashboard
   option asks for its link or file next.
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
5. **After the build, offer a dashboard**, next.

### After the build, in this branch only: offer a dashboard

Discovery builds from query history and table definitions. A dashboard holds rules the team
already agreed, so adding one can improve what was built. When generation and its checks
have finished, and no dashboard was part of this build, ask once, before the save question:

"Built from your warehouse: `<N>` metrics and `<N>` queries, from query history and table
definitions. The quality can improve if you add more sources: a dashboard brings the rules
your team already agreed for these numbers.

Would you like to add a dashboard?"

| Option | Description |
| --- | --- |
| Add a dashboard | "Share its link or file. I'll read it and update the metrics it covers." |
| Not now | "Keep what's built and carry on to saving." |

- **Add a dashboard**: ask for it with branch B's card, open it and check access (signing
  the person in when a link needs it), then confirm and build with the dashboard added. Metrics the
  dashboard also defines are reconciled with it and any change is shown as news; the rest
  stay as built. Then continue to the save question.
- **Not now**, or the dashboard cannot be read: continue to the save question. Do not ask
  again in this run.

Say "can improve", never that the current metrics are wrong or incomplete, and never offer
this in the dashboard branch, the known-tables branch or a demo. Skip it when the person
already picked "I have a dashboard" in the menu, and on a resume that is past this point.

## Branch B: Make my dashboard agentic

1. **Share the dashboard.** One card, so the person picks where they can and types only the
   link or path:

   "Which dashboard should I use?"

   The question's own text is one line: "Paste its link or a file path below, or pick one."

   | Option | Description | When to offer it |
   | --- | --- | --- |
   | Each BI file in the working folder, by name | "The `<Power BI>` file in this folder." | One per file `PROJECT discover` found, at most two, largest first |
   | I know my tables instead | "Skip the dashboard and tell me the use case and tables." | When fewer than two files were found |
   | Skip, let discovery decide | "I'll look at what your warehouse is used for and suggest use cases." | Always, last |

   The host's free-text entry takes the link or the path. Never add an option that only says
   to paste or type, and never ask which BI tool it is: the link's host or the file's
   extension says. Where the host has no question tool, ask in ordinary chat and list the
   same choices.

   | They answer | Do |
   | --- | --- |
   | A file in the folder, or a typed path | Step 2, on that file |
   | A link | Step 2, on that link: open it, signing the person in when it needs it |
   | I know my tables instead | Branch C from its start |
   | Skip, let discovery decide | Branch A from its start |

2. **Open it and check access.** `source-survey` `access` on the dashboard and the tables it
   reads. A link that needs sign-in starts that tool's sign-in straight away, as
   **A link that needs sign-in** below says: do not stop to ask whether to sign in. Ask a
   question only when something cannot be read after that.
3. **Confirm and trigger**, below.

## Branch C: I have a use case and know my tables

1. **Tell me.** One card, so the person sees where to type and how to skip:

   "What's the use case, and which tables power it?"

   The question's own text shows what to type, in one line: "Type it in your own words, for
   example: `Pipeline by stage and win rate. Tables: SALES.FCT_OPPORTUNITY,
   SALES.DIM_ACCOUNT`. Add a dashboard link or file if you have one."

   | Option | Description |
   | --- | --- |
   | Skip, let discovery decide | "I'll look at what your warehouse is used for and suggest use cases." |
   | Share a dashboard instead | "Paste a dashboard link or file and I'll build from that." |

   The host's free-text entry is where the use case and tables go; never add an option
   that only says to type. Where the host has no question tool, ask the same thing in
   ordinary chat and end with: "Or say skip and I'll let discovery decide."

   | They answer | Do |
   | --- | --- |
   | A use case and tables | Take them as given: the use case in their words, the tables as named. Do not suggest other tables or run discovery |
   | A use case and no tables | Branch A, with the use case as the subject: discovery finds its tables. Ask nothing more first |
   | Skip, let discovery decide | Branch A from its start |
   | Share a dashboard instead | Branch B from its start |

2. **Access check.** `source-survey` `access` on each table and dashboard they named.
3. **Confirm and trigger**, below.

## The access check, in branches B and C

Silent when everything can be read: go straight to the confirmation, which says so.

### A link that needs sign-in

A link is as good as a file. Recognise the tool from the link's host, then open the report
through that tool's own route, in the order
[the dashboard routes](../../../metrics-generation/references/dashboard-agent.md) give.
When the route can sign the person in, start it without asking, say one line, and carry on
when it completes:

| Tool | What to do | Say |
| --- | --- | --- |
| Power BI | Start the plug's browser sign-in (a device code where no browser can open) | "Opening Power BI sign-in in your browser. I'll carry on once you're signed in." |
| Another tool whose route can start a sign-in | Start it the same way | The same line, with the tool's name |
| Tableau, or any tool with no sign-in a tool can start | Ask the one question below | |

Signing in is the person's own act in their own browser. Never ask for, print or store a
password, token or key, and never paste a credential into a command.

Only when no sign-in can be started for that tool, ask how to get the dashboard, with that
tool's routes as the options:

| Option | Description |
| --- | --- |
| Download it and give me the file (recommended) | "In Tableau, Download, then Tableau Workbook. Paste the path here." |
| Use an access token | "For Tableau, a Personal Access Token you create. I'll tell you where to put it, never in this chat." |
| Skip, let discovery decide | "Build from the warehouse instead." |

A sign-in that fails or is cancelled falls back to that same question, once.

### When something still cannot be read

Ask one question that names it and offers the ways forward:

| What failed | Ask | Options |
| --- | --- | --- |
| A dashboard cannot be opened even after sign-in | "I can't open this dashboard: `<the reason>`." | I'll send a file instead · Skip, let discovery decide |
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
