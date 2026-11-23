# Demo: Ticketfuddle, the box office

Read right after the person picks this demo, and again at each later moment for its facts.
The demo is `box-office`. It lives in this skill's `assets/box-office/`; `DEMO open box-office`
sets up its working folder, and `DEMO requests box-office` prints its dashboard file, database
and schema.

## The welcome

The welcome is the text of the "Ready to build?" question, as [the tour rules](tour.md)
describe. Send these lines as the question, whole and as written, with their line breaks
and leading spaces. The fence only marks the lines; it is not part of the question. Do not
also print the welcome as a reply before the question.

```
I'll build a ticket fees agent for Ticketfuddle, a made-up ticketing
firm. It answers questions about Ticketfuddle's sales and fees in
plain English, and shows how each number is worked out.

To build it, I read Ticketfuddle's dashboard and past queries, define
each metric once and test each one on Ticketfuddle's data. Then I
suggest questions to ask your agent. I build one on your own data the
same way, from your dashboards.

What you're working with:
  Dashboard      Tableau, 7 tiles: 4 metrics (gross sales, fees as a
                 share of what fans paid, net fee revenue,
                 sell-through) and 3 charts (gross sales by month,
                 fee income by type, sell-through by event)
  Data           6 venues, 45 shows (July 2025 to December 2026),
                 6,911 orders, 15,327 tickets, 509 refunds,
                 10,760 gate scans
  Past queries   75 past queries from Ticketfuddle's analysts, run
                 756 times, asking 68 different questions

  Steps          1. Choose the sources
                 2. Build your context: metrics and saved queries
                    (about 5 min)
                 3. Choose where to store your context
                 4. Ask your agent a question
                 5. Build a dashboard (optional)

About 15 to 20 minutes. The data is made up. I add two folders here
and change none of your files.
See the firm first:
https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/

Ready to build?
```

## Facts for the later moments

| Fact | Value |
| --- | --- |
| Firm | Ticketfuddle |
| Agent | ticket fees agent |
| Tour | https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/ |
| Working folder | `atlan-analytics-demo/box-office/` |
| Dashboard | a Tableau workbook with 7 tiles: 4 metrics and 3 charts |
| Tiles | gross sales, fees as a share of what fans paid, net fee revenue, sell-through; charts of gross sales by month, fee income by type and sell-through by event |
| Past queries | 75 distinct, over 756 runs, asking 68 different questions |
| Data tables | 10 |
| Focus to record | Ticket fees |
| Purpose to record | Build a ticket fees agent that answers how much of what fans paid went on fees and why |
| What a metric is, by example | one number Ticketfuddle cares about, such as fees as a share of what fans paid, with one definition for working it out |
| Why build them | The fee share says how much went on fees. Gross sales, net fee revenue and sell-through say what moved it. |
| What the agent can answer | Questions about gross sales, fees, net fee revenue and sell-through |

Amounts are in US dollars. Customer records hold names, email addresses, phone numbers,
dates of birth and card details: never show any of them, in a sample, a check or an answer.
Acts and venues may be named.

The dashboard to offer at the end is named `ticketfuddle-fees-dashboard.html`.
