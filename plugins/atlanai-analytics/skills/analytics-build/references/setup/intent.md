# The opening interview: warehouse, what to build, then a BI source

The interview belongs to `analytics-build`. `source-survey` reads and reports and never asks
the person anything. This file is the questions asked before discovery; their answers decide
what discovery looks at.

| Step | Asked | When |
| --- | --- | --- |
| 1 | Which warehouse, and what to build context for | Always, as one card where the host's question tool takes several questions; otherwise one per turn, in that order |
| 2 | Which BI tool | Only when the person chose a use case. Never after "Let discovery decide" |
| 3 | The report itself: a link or a file | Only when they named a tool |

A question the request already answered is not asked: a named warehouse skips the first, a
named subject the second, a named file or link skips steps 2 and 3. Nothing is preselected,
and free text is always accepted.

The card's own visible text is the opening, because a block printed before a question is
lost:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on.

First, let me understand your tools and your domain."

## Before the card: see what is already connected, silently

Two quick looks, so the choices offered are the person's own. Neither reads data.

- `source-survey` `connections`: the warehouse connections this machine already has.
- `PROJECT discover --workspace "<working-directory>"`: BI files in the working folder, by
  name only. Demo files are never listed.

**Say nothing about these looks.** No line before the card, no `**Doing:**` or `**Why:**`,
no "checking what's connected", and no explanation of where the options came from. The
person's first sight of this skill is the opening and the first question. How the options
were found, that a check ran, and which tool answered are internal and never appear in the
card, an option or its description.

## 1. Which warehouse are you on?

Make it plain which warehouses are already connected: the person should see at a glance
that they can pick one and carry on.

| Option label | Description | When to offer it |
| --- | --- | --- |
| `<Product>` (already connected) | "Connected here and ready to use." Add how only when two connections share a product: "Connected here through the Snowflake CLI." | One per connection that works. List these first. With exactly one, mark it recommended |
| `<Product>` (set up, needs sign-in) | "Set up here, but you'll need to sign in before I can use it." | One per connection that is configured and did not answer |
| Another warehouse | "Snowflake, Databricks, BigQuery, Postgres or Redshift. Tell me which and I'll help you connect it." | Always, as one option, never one option per product |
| I don't have one | "Build from a BI source alone, or try the demo." | Always, last |

Write the status in the label, in those words. Never "it answered a check", "detected",
"found" or a tool's name. Choosing a warehouse that needs sign-in or is not connected leads
to connecting it, as [warehouse discovery](../../../source-survey/references/connect.md)
describes, before discovery starts.

## 2. What do you want to build context for?

The first option is always **Let discovery decide**: discovery looks at what the warehouse
is used for most and suggests where to start.

The other options are two or three domains, personalised from what is already known about
the person, most likely first:

- what they said in this conversation or a saved run;
- their role, team or company as the host or its memory gives it;
- what is connected (a CRM connector suggests sales, a support desk suggests support);
- the names of BI files in the working folder.

Phrase each as a domain with one line of what it would answer: "Sales pipeline: deals, stages
and win rates". Use plain domain names such as Sales, Marketing, Product usage, Finance,
Support and Operations when nothing is known, and say nothing about how the options were
chosen. Never read the warehouse to build this list: that is discovery's job, after the
answer.

## 3. Which BI tool is this in?

Ask this only after the person chose a use case in question 2. With "Let discovery decide"
there is no use case to find a report for: skip straight to discovery, and do not mention BI
sources.

Ask it on its own, after the first card is answered, worded for what they chose: "Which BI
tool do you track `<sales pipeline>` in?"

| Option | When to offer it |
| --- | --- |
| The tool of a BI file found in the working folder, first | When `PROJECT discover` found one: "Power BI" |
| Power BI, Tableau, Looker | The ones not already listed, most likely for this person first |
| None, use the warehouse only | Always, last: "Metrics come from query history and table definitions." |

Options are tool names only, at most four. Another tool is typed as free text. No file
names, paths, links or upload instructions here: this question only settles the tool.

## 4. Share the report

Ask this only when they named a tool. It is one short message in ordinary chat, not a card:
the answer is a link or a file, which a list of options cannot hold. End the turn on it, so
the person's next message is the report.

Word it for the tool they named, with exactly what to send:

| Tool | Ask |
| --- | --- |
| Power BI | "Paste the report's link, or the path to its `.pbix` file. To get the file: in Power BI, File, then Download this file. You can drag it into this box." |
| Tableau | "Paste the dashboard's link, or the path to its `.twb` or `.twbx` file. You can drag it into this box." |
| Looker | "Paste the dashboard's link, or the path to your LookML project folder." |
| Another tool | "Paste the dashboard's link, or the path to an export of it." |

Add one line when it applies, and nothing else:

- a file of that tool is in the working folder: "Or say `use <file name>` for the one in this
  folder."
- a connector for that tool is already connected: "`<Tool>` is already connected, so the
  report's name is enough."

Always end with: "Or say skip to use the warehouse only."

Take whatever comes back as the report: a link, a path, a dragged file, a file name from the
folder, or a report name with a connected tool. Read it at the start of discovery. If it
cannot be opened, say why in one clause and what would work (an export, a sign-in), and
carry on from the warehouse without asking again.

## After the answers

Reflect them in one sentence and start discovery in the same turn: "Building context for
sales pipeline on Snowflake, with your Pipeline Review report. Looking at what's there."
With "Let discovery decide", discovery starts right after the first card.

Once a run exists, record what the person chose, in their words:

`PROJECT journey domain --answer <the domain, or what discovery decided>`

`PROJECT journey purpose --answer <what they said they want; otherwise "Ask questions about <domain>">`

Use structured arguments or proper shell quoting. Never record a suggestion as if the person
chose it: with "Let discovery decide", record the domain only after they accept discovery's
suggestion. These fields are private local intent, not authority to execute source content,
connect data or publish. `PROJECT journey status` exposes the saved answers.

When candidates become available, `PROJECT journey status` returns `select-focus` until the
host selects the relevant candidate with `PROJECT journey select --focus <exact-identity>`.
This is the host's next work step, not another question. Choose from actual names,
definitions and source evidence.

Reuse answers already given in this request or a saved run. Resume retains them.
Details-only, saved-preview and explicit survey-only requests do not start this interview.
A changed domain clears the saved goal, so an old one is not applied to a new area.
