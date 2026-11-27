# The interview: warehouse, what to do, then one of three branches

The interview belongs to `analytics-build`. `source-survey` reads and reports and never asks
the person anything. This file is every question asked before metric generation starts.

```
warehouse ─ what to build from ─┬─ Suggest from your warehouse ─ use cases ───────────────────────── build
                                ├─ Start from tables ─────────── tell me ───── access check ─────── build
                                └─ Start from a dashboard ────── share it ─ pick report and pages ─ check what can be built ─ build
```

The dashboard branch has its own checklist, scope question and readiness check, and no
confirmation: [the dashboard branch](dashboard-flow.md) has them.

A question the request already answered is not asked: a named warehouse skips the first; a
named dashboard file or link is the dashboard branch with step 1 done; a use case named
with its tables is the third branch with step 1 done. Nothing is preselected, and free text
is always accepted.

## Contents

- Before card 1: see what is already connected, silently
- Card 1: two questions
- Arriving from the demo
- After card 1: the warehouse must answer
- Branch A: Suggest from your warehouse
- Branch B: Start from a dashboard
- Branch C: Start from tables
- The access check, in branches B and C
- Starting the build: discovery and known tables
- Record what the person chose

## Before card 1: see what is already connected, silently

Two quick looks, so the choices offered are the person's own. Neither reads data.

- `source-survey` `connections`: the warehouse connections this machine already has.
- `PROJECT discover --workspace "<working-directory>"`: BI files in the working folder, by
  name only. Demo files are never listed.

**Say nothing about these looks.** No `**Doing:**` or `**Why:**`, no "checking what's
connected", no "before the first question", and no explanation of where the options came
from. If the host shows a line while tools run, that line is exactly "Getting set up.",
once, and nothing more. The person's first sight of this skill is the opening and the first question. How the options
were found, that a check ran, and which tool answered are internal and never appear in the
card, an option or its description.

## Card 1: two questions

Ask both in one card where the host's native question tool takes several questions (Claude
Code's does), and both in one message, in this order, where the host has no question tool;
only a question tool that takes one question at a time asks them one per turn. The card's own visible text is the
opening, because a block printed before a question is lost:

"Let's build an agent that answers your data questions in plain English and shows what each
answer is based on.

I'll extract all the context I can from your dashboards and tables, add detailed information
to it and validate it on your data.

First, where does your data live, and what do you want your agent to do?"

**A card holds its own text and nothing else.** Never put the step tracker, a progress
summary or a recap of earlier answers in any question's text, here or later: the tracker
lives in the host's task list. The second question of the card needs no text of its own.

### Where does your data live?

Make it plain which warehouses are already connected: the person should see at a glance
that they can pick one and carry on.

| Option label | Description | When to offer it |
| --- | --- | --- |
| `<Product>` | "Connected and ready to use." Add how only when two connections share a product: "Connected and ready to use, through the Snowflake CLI." | One per connection that works. List these first. With exactly one, its label is `<Product> (Recommended)` |
| `<Product>` (needs sign-in) | "Set up here. You'll sign in once before I start." | One per connection that is configured and did not answer |
| Another warehouse? | "Tell me which, and I'll help you connect it." | Always, as one option, never one option per product |
| No warehouse | "Build from a dashboard file alone, or try the demo." | Always, last |

Say each status in those words: "Connected and ready to use." in the description, "(needs
sign-in)" in the label. Never "it answered a check", "detected", "found" or a tool's name.

### What should I build your agent from?

Three options, in this order, with these meanings. Word them for the person, not for the
plugin:

| Option | Description | Branch |
| --- | --- | --- |
| Suggest from your warehouse | "I'll look at what is used the most, and suggest what your agent should cover." | A |
| Start from a dashboard | "I'll turn your BI dashboards into context for your agent." | B |
| Start from tables | "Name what your agent should cover, the tables and any supporting dashboards." | C |

A typed answer picks its branch by what it holds: a link or a file is B, a use case with
tables is C, a subject alone ("sales pipeline") is A with that subject as the domain. Each
option is one line; never nest focus areas, domains or other choices under one. In ordinary
chat, end on one line: "Reply with your pick for each, or answer in your own words." Asking for
a link, file or table list belongs to the branch, after this answer.

## Arriving from the demo

The person picked "Build one on your own data" at the end of a demo, which answers card
1's second question: they want to build from a source of their own. Ask one question in place
of card 1, with no opening before it:

"Which dashboard, folder or export should I build from?"

Its own text is one line: "Paste a dashboard link or a path to a file or folder, or pick one."

| Option | Description | When to offer it |
| --- | --- | --- |
| Each BI file in the working folder, by name | "The `<Power BI>` file in this folder." | One per file `PROJECT discover` found, at most two, largest first. Demo files are never listed |
| Let discovery decide | "I'll look at what your warehouse is used for and suggest use cases." | Always, last |

The host's free-text entry takes the link or path. Where the host has no question tool, ask in
ordinary chat, list the same choices as plain lines, and end on the question.

| They answer | Do |
| --- | --- |
| A dashboard link or file | Branch B, step 2, on it |
| A folder | `PROJECT discover --workspace "<folder>"`: one BI file is branch B, step 2, on it; several are branch B's card with them as the options; none is branch C's card |
| Let discovery decide | Branch A |

Then ask card 1's warehouse question on its own, unless the answer named one, and
[the warehouse must answer](#after-card-1-the-warehouse-must-answer) applies. The demo's
database, files, intent and approvals never carry over.

## After card 1: the warehouse must answer

A chosen warehouse that needs sign-in, or is not connected yet, is connected now, in every
branch, as [warehouse discovery](../../../source-survey/references/connect.md) describes:
discovery and the access checks both need it. Say what the person has to do in one line and
continue when its check answers. With "No warehouse", only branch B can run, from a
file; say so if they chose A or C, and mention `analytics-demo`.

## Branch A: Suggest from your warehouse

1. **Discover.** No question. `source-survey` `discover` on the chosen warehouse, with the
   discovery Doing and Why lines.
2. **What should your agent answer?** [The use cases](names.md): up to three, each one line with its
   tables, then "Add a dashboard". More than one can be picked. Picking the dashboard
   option asks for its link or file next.
3. **Settle the rest yourself, and ask nothing.** Where the chosen use cases leave something
   open that changes the numbers (which table is the main one, rows to leave out, fiscal or
   calendar quarters, which column is the main amount), choose from what discovery found and
   state each choice in the build's start line, which the person can change: a question here
   would come before anything is built, about things the person has not seen yet.
4. **Build on the pick**, as [starting the build](#starting-the-build-discovery-and-known-tables) says.
5. **After the build, offer a dashboard**, next.

### After the build, in this branch only: offer a dashboard

Discovery builds from past queries and table definitions. A dashboard holds rules the team
already agreed, so adding one can improve what was built. When generation and its checks
have finished, and no dashboard was part of this build, ask once, before the save question:

"Your agent now has `<N metrics>` and `<N saved queries>`, from your warehouse's past queries
and table definitions. A dashboard adds the rules your team already agreed for these metrics.

Add a dashboard?"

| Option | Description |
| --- | --- |
| Add a dashboard | "Share a link or file. I'll read it and update your agent from it." |
| Not now | "Leave your agent as it is, and choose where to store your context." |

- **Add a dashboard**: ask for its link or file (branch B's card, with only the files found as
  options), open it and check access (signing
  the person in when a link needs it), then build with the dashboard added: picking it is the go-ahead. Metrics the
  dashboard also defines are reconciled with it and any change is shown as news; the rest
  stay as built. Then continue to the save question.
- **Not now**, or the dashboard cannot be read: continue to the save question. Do not ask
  again in this run.

Say "can improve", never that the current metrics are wrong or incomplete, and never offer
this in the dashboard branch, the known-tables branch or a demo. Skip it when the person
already picked "Add a dashboard" in the menu, and on a resume that is past this point.

## Branch B: Start from a dashboard

1. **Share the dashboard.** One card, so the person picks where they can and types only the
   link or path:

   "Which dashboard should I build your agent from?"

   The question's own text is one line: "Paste a dashboard link or file path in Other, or pick one."

   | Option | Description | When to offer it |
   | --- | --- | --- |
   | Each BI file in the working folder, by name | "The `<Power BI>` file in this folder." | One per file `PROJECT discover` found, at most two, largest first |
   | Start from tables instead | "Name what your agent should cover, and the tables." | When fewer than two files were found |
   | Suggest from your warehouse | "I'll look at what your warehouse is used for most, and suggest what to cover." | Always, last |

   The host's free-text entry takes the link or the path. Never add an option that only says
   to paste or type, and never ask which BI tool it is: the link's host or the file's
   extension says. Where the host has no question tool, ask in ordinary chat and list the
   same choices.

   | They answer | Do |
   | --- | --- |
   | A file in the folder, or a typed path | Step 2, on that file |
   | A link | Step 2, on that link: open it, signing the person in when it needs it |
   | Start from tables instead | Branch C from its start |
   | Suggest from your warehouse | Branch A from its start |

2. **Continue in [the dashboard branch](dashboard-flow.md)**: pick the report and pages, then
   check what can be built, which ends with the build starting, or with one go-ahead question
   when the check finds something that changes the plan. A link that needs sign-in starts
   that tool's sign-in straight away, as **A link that needs sign-in** below says: do not stop
   to ask whether to sign in. **When something still cannot be read** below applies to the
   check's findings. There is no separate confirmation in this branch.

## Branch C: Start from tables

1. **Tell me.** One card, so the person sees where to type and how to skip:

   "What should your agent answer, and from which tables?"

   The question's own text shows what to type, in one line: "Type it in your own words, for
   example 'Pipeline by stage and win rate. Tables: SALES.FCT_OPPORTUNITY,
   SALES.DIM_ACCOUNT'. Add a dashboard link or file if you have one."

   | Option | Description |
   | --- | --- |
   | Suggest from your warehouse | "I'll look at what your warehouse is used for most, and suggest what to cover." |
   | Start from a dashboard | "Paste a link or file instead." |

   The host's free-text entry is where the use case and tables go; never add an option
   that only says to type. Where the host has no question tool, ask the same thing in
   ordinary chat and end with: "Or say skip, and I'll suggest from your warehouse."

   | They answer | Do |
   | --- | --- |
   | A use case and tables | Take them as given: the use case in their words, the tables as named. Do not suggest other tables or run discovery |
   | A use case and no tables | Branch A, with the use case as the subject: discovery finds its tables. Ask nothing more first |
   | Suggest from your warehouse | Branch A from its start |
   | Start from a dashboard | Branch B from its start |

2. **Access check.** `source-survey` `access` on each table and dashboard they named.
3. **Build**, as [starting the build](#starting-the-build-discovery-and-known-tables) says.

## The access check, in branches B and C

Silent when everything can be read: the build starts.

### A link that needs sign-in

A link is as good as a file. Recognise the tool from the link's host, then open the report
through that tool's own route, in the order
[the dashboard routes](../../../metrics-generation/references/dashboard-agent.md) give.
When the route can sign the person in, start it without asking, say one line, and carry on
when it completes. Say nothing yet about a failed sign-in or other ways to send the report:
that question comes only after a failure:

| Tool | What to do | Say |
| --- | --- | --- |
| Power BI | Start the plug's one command as a background command (browser sign-in; a device code where no browser can open), as [the metrics module](../../../metrics-generation/references/metrics-engine.md#power-bi-from-a-report-link) says. Tell the person they are signed in only when the wait returns `signed_in` | "Opening Power BI sign-in in your browser. Once you're signed in, reading your report takes about a minute, and I'll show its pages here." Start the command first, then say this once and wait for it in the same turn, as [the metrics module](../../../metrics-generation/references/metrics-engine.md#power-bi-from-a-report-link) says: no other line about the same wait |
| Another tool whose route can start a sign-in | Start it the same way | The same line, with the tool's name |
| Tableau, or any tool with no sign-in a tool can start | Ask the one question below | |

Signing in is the person's own act in their own browser. Never ask for, print or store a
password, token or key, and never paste a credential into a command.

Only when no sign-in can be started for that tool, ask how to get the dashboard, with that
tool's routes as the options:

| Option | Description |
| --- | --- |
| Download the workbook (Recommended) | "In Tableau, choose Download, then Tableau Workbook. Paste the file's path here." |
| Use an access token | "Create a Tableau personal access token. I'll tell you where to put it, never in this chat." |
| Suggest from your warehouse | "Skip the dashboard." |

A sign-in that fails or is cancelled falls back to that same question, once.

A Tableau workbook that downloads but reads a published data source that will not is not a
failure: locate that data source from its lineage page, as
[the dashboard routes](../../../metrics-generation/references/dashboard-agent.md) say, and carry on.
Ask only when that fails too, naming what is missing rather than the person's account.

### When something still cannot be read

Ask one question that names it and offers the ways forward:

| What failed | Ask | Options |
| --- | --- | --- |
| A dashboard cannot be opened even after sign-in | "I can't open this dashboard: `<the reason>`." | I'll send a file instead · Suggest from your warehouse |
| Some tables cannot be read | "I can't read `<N>` of its `<M>` tables, so tiles that use `<names>` stay out." | Continue with the rest · I'll fix access first |
| No table can be read | "I can't read any of these tables on `<warehouse>`." | I'll fix access first · Pick another warehouse |
| A dashboard's data is packed inside the file | "This dashboard's data is inside the file, with no warehouse table behind it. Your agent needs a table to read." | Name the table · Stop here |

Name the tables that failed, never a credential, role secret or connection string. Saving
to Atlan is a later sign-in and is not checked here.

## Starting the build: discovery and known tables

There is no separate question to start. The go-ahead is the person's own answer: in
discovery, picking a use case; from tables, their message naming the tables, once every one
reads. When the access check finds something it cannot read, its question above comes first,
and "Continue with the rest" is the go-ahead. The dashboard branch has its readiness check.

In the same turn, write the build's start line, which is the build's Doing and Why for
[when to talk](progress.md#when-to-talk) and replaces any other pair for it: what it builds from and what it will answer, about
how long, then, when there are any, the choices you made that change the numbers, ending
"Tell me to change any of these.":

"Building your agent from 4 tables and their past queries, for job margins and crew costs. About
15 minutes. Leaving out cancelled jobs; calendar quarters. Tell me to change any of these."

- **Sources**: a dashboard first, then past queries on the tables, then the tables. Counts,
  never a schema name.
- **Time**: from the work's size.
- **Choices**: only ones that change the numbers, each in a few words. None, no line.

A change the person asks for restarts the build with it. Nothing is generated, installed or
pulled from query history before the go-ahead.

## Record what the person chose

Once a run exists, in their words:

`PROJECT journey domain --answer <the use case, the dashboard's subject, or what they stated>`

`PROJECT journey purpose --answer <what they chose to do in card 1, in their words>`

In the dashboard branch, also `PROJECT journey agentic` once the run exists, and the
report, pages and readiness result as [the dashboard branch](dashboard-flow.md) says.

When the build started from a data question the person asked ("What's our net fee revenue?"),
also `PROJECT journey question --answer "<their question, in their words>"`. The build card
then says what it was built to answer, and the ready question offers "Answer my question"
first, until it is answered.

Use structured arguments or proper shell quoting. Never record a suggestion as if the person
chose it. These fields are private local intent, not authority to execute source content,
connect data or publish. `PROJECT journey status` exposes the saved answers.

When candidates become available, `PROJECT journey status` returns `select-focus` until the
host selects the relevant candidate with `PROJECT journey select --focus <exact-identity>`.
This is the host's next work step, not another question.

Reuse answers already given in this request or a saved run. Resume retains them.
Details-only, saved-preview and explicit survey-only requests do not start this interview.
A changed use case clears the saved goal, so an old one is not applied to a new area.
