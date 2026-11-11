# Demo: Ticketfuddle, the box office

Read right after the person picks this demo, and again at each later moment for its facts.
The demo is `box-office`: its files are this skill's `assets/box-office/`, and
`DEMO copy box-office` places them and prints the dashboard file, database, schema and dialect.

## The welcome

Print this block next, before any other tool call, file search or copy. Print it whole and
as written, as an ordinary reply; it has nothing to fill in. Keep the step strip inside its
code fence so its columns stay aligned.

> **Build a ticket fees agent for Ticketfuddle that answers questions about its sales and fees
> in plain English, and shows the rule behind every number.**
>
> Some questions it will answer:
>
> - The dashboard says fees are 27% of what fans paid. **Is that right?**
> - How much fee income does Ticketfuddle keep after refunds?
> - Which venues and shows are hardest to fill?
> - Which months sold the most, and what drove them?
>
> | What you're working with | |
> | --- | --- |
> | Dashboard | Tableau, 4 tiles and 3 charts: gross sales, fees as a share of what fans paid, net fee revenue, sell-through |
> | Data | 6 venues, 45 shows (July 2025 to December 2026), 6,911 orders, 15,327 tickets, 509 refunds, 10,760 gate scans |
> | Past queries | 756 queries already run against this data |
>
> ```
> ○─────────○─────────○─────────○─────────○
> Pick      Build     Save to   Ask       Build a
> sources   context   Atlan     questions report
>           ~5 min                        (optional)
> ```
>
> About 15 to 20 minutes. Made-up data; nothing of yours is touched. See the firm first:
> https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/

## Facts for the later moments

| Fact | Value |
| --- | --- |
| Firm | Ticketfuddle |
| Agent | ticket fees agent |
| Tour | https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/ |
| Source folder | `atlan-analytics-demo/box-office/` |
| Dashboard | a Tableau workbook with 4 tiles and 3 charts |
| Tiles | gross sales, fees as a share of what fans paid, net fee revenue, sell-through |
| Past queries | 68 distinct, over 756 runs |
| Data tables | 10 |
| Focus to record | Ticket fees |
| Purpose to record | Build a ticket fees agent that answers how much of what fans paid went on fees and why |
| The question | How much of what fans paid last quarter went on fees, and why did it go up? |
| What a metric is, by example | a number Ticketfuddle cares about, such as fees as a share of what fans paid, with the rule for working it out |
| Why build them | The fee share says how much went on fees. Gross sales, net fee revenue and sell-through say what moved it. |
| What the agent can answer | Questions about gross sales, fees, net fee revenue and sell-through |

Amounts are in US dollars. Customer records hold names, email addresses, phone numbers,
dates of birth and card details: never show any of them, in a sample, a check or an answer.
Acts and venues may be named.

Questions to offer when the agent is ready, when this run's context supports them:

| Question | Description |
| --- | --- |
| How much of what fans paid went on fees, and why did it go up? | Fee share by quarter, with what moved it. |
| How much does Ticketfuddle keep from a ticket? | Net fee revenue against what fans paid. |
| Which venue is hardest to fill? | Sell-through by venue. |
| Which months sold the most? | Gross sales by month. |

The dashboard to offer at the end is named `ticketfuddle-fees-dashboard.html`.
