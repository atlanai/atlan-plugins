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
Build a ticket fees agent for Ticketfuddle. It answers questions about
its sales and fees in plain English, with the rule behind every number.

It reads the dashboard and the questions analysts already ran, then
suggests the questions this context can answer once it is built.

What you're working with:
  Dashboard      Tableau, 4 tiles and 3 charts: gross sales, fees as a
                 share of what fans paid, net fee revenue, sell-through
  Data           6 venues, 45 shows (July 2025 to December 2026),
                 6,911 orders, 15,327 tickets, 509 refunds,
                 10,760 gate scans
  Past queries   756 queries already run against this data

  ○─────────○─────────○─────────○─────────○
  Pick      Build     Save to   Ask       Build a
  sources   context   Atlan     questions dashboard
            ~5 min                        (optional)

About 15 to 20 minutes. Made-up data; nothing of yours is touched.
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
| Dashboard | a Tableau workbook with 4 tiles and 3 charts |
| Tiles | gross sales, fees as a share of what fans paid, net fee revenue, sell-through |
| Past queries | 68 distinct, over 756 runs |
| Data tables | 10 |
| Focus to record | Ticket fees |
| Purpose to record | Build a ticket fees agent that answers how much of what fans paid went on fees and why |
| What a metric is, by example | a number Ticketfuddle cares about, such as fees as a share of what fans paid, with the rule for working it out |
| Why build them | The fee share says how much went on fees. Gross sales, net fee revenue and sell-through say what moved it. |
| What the agent can answer | Questions about gross sales, fees, net fee revenue and sell-through |

Amounts are in US dollars. Customer records hold names, email addresses, phone numbers,
dates of birth and card details: never show any of them, in a sample, a check or an answer.
Acts and venues may be named.

The dashboard to offer at the end is named `ticketfuddle-fees-dashboard.html`.
