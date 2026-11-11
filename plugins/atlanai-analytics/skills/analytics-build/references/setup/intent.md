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

## Before the card: see what is already connected

Two quick looks, so the options are real. Neither reads data, and neither is narrated beyond
one `**Doing:**` line.

- `source-survey` `connections`: the warehouse connections this machine already has.
- `PROJECT discover --workspace "<working-directory>"`: BI files in the working folder, by
  name only. Demo files are never listed.

## 1. Which warehouse are you on?

| Option | When to offer it |
| --- | --- |
| Each connection found, by product and how it is reached ("Snowflake, through its connector") | One option per working connection. With exactly one, mark it recommended |
| Snowflake, Databricks, BigQuery, Postgres or Redshift | Only the products with no connection found, so the person can name what they use. Choosing one leads to connecting it, as [warehouse discovery](../../../source-survey/references/connect.md) describes |
| I don't have one | Continue from a BI source alone, or offer `analytics-demo` when there is none |

Never list a connection that did not answer its check as if it worked: say "needs sign-in"
on its option.

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
