# The opening interview: warehouse, what to build, a BI source

The interview belongs to `analytics-build`. `source-survey` reads and reports and never asks
the person anything. This file is the three questions asked before discovery; their answers
decide what discovery looks at.

Ask all three in one card where the host's native question tool takes several questions
(Claude Code's takes up to four). Where it takes one, or there is none, ask them in this
order, one per turn. A question the request already answered is not asked: a named warehouse
skips the first, a named subject the second, a named file or link the third. Nothing is
preselected, and free text is always accepted.

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

## 3. Do you have a BI source to share?

| Option | Description |
| --- | --- |
| Each BI file found in the working folder, by name | Offered only when `PROJECT discover` found one |
| A Power BI or Tableau file | Give its path, or drop it here. A `.pbix`, `.pbit`, `.bim`, `.twb` or `.twbx` |
| A dashboard link | Paste the URL. A link that cannot be opened from here needs an export |
| No, use the warehouse only | Metrics come from query history and table definitions |

A BI source is optional. With one, its measures are the agreed rules and come first; without
one, discovery works from the warehouse alone.

## After the answers

Reflect them in one sentence and start discovery in the same turn: "Building context for
sales pipeline on Snowflake, with your Pipeline Review report. Looking at what's there."

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
