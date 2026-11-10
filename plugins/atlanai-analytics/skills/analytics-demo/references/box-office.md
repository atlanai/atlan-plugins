# Demo: Ticketfuddle, the box office

Read right after the person picks this demo, and again at each later moment for its facts.
The source is the `box-office/` folder of the demo repository.

## The welcome

Print this block next, before any other tool call, file search or clone. Print it whole and
as written, as an ordinary reply. Replace `<folder>` with the current working folder's
absolute path; that is the only change.

> **You're building a ticket fees agent for Ticketfuddle**
>
> By the end, this chat is an agent that can tell Ticketfuddle how much of what fans paid
> went on fees and why, in plain English. You build it by turning Ticketfuddle's dashboard
> and past queries into context.
>
> **See it live first.** Ticketfuddle's dashboard, its data and the queries its teams ran
> are on a tour page you can click through:
> https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/
> Open it in your browser and look around. It is the same environment you are about to
> build on, so the numbers you see there are the ones your agent will answer from.
>
> Ticketfuddle is a made-up ticketing firm. It sells tickets for six venues, from a
> 60-seat basement club to a 1,000-capacity field by the river: 45 shows between July 2025
> and December 2026. Fans buy online, in the app or at the box office, some resell, and
> the gates scan everyone who walks in. You need no ticketing knowledge: every question is
> about money and tickets. The data is made up, so nothing of yours is used.
>
> **The problem.** Ticketfuddle keeps asking one question: *how much of what fans paid
> last quarter went on fees, and why did it go up?* The numbers exist, but answering means
> someone writes SQL, and finance and marketing can get two different answers.
>
> **What you'll build.** A ticket fees agent. You turn Ticketfuddle's dashboard and past
> queries into context, and with that context the agent gives anyone one checked answer.
>
> **What context is.** It is what a new analyst has to learn before answering: what counts
> as a fee at Ticketfuddle, what "what fans paid" includes, which orders count.
> Ticketfuddle already wrote that down without meaning to, in its dashboard and in the
> queries its teams ran. Atlan reads those sources and turns them into context that anyone,
> or any AI, can use. The context is a set of metrics: each one a number Ticketfuddle cares
> about, such as fees as a share of what fans paid, with the rule for working it out.
>
> **What's in the demo**
>
> | What | In plain words |
> | --- | --- |
> | Venues, acts and shows | Where and when each show plays, and its capacity (7, 17, 46) |
> | Fee schedule | Each fee rule, when it applies and who keeps it (16) |
> | Customers and orders | Accounts, and each checkout: primary, resale or box office (2,418 and 6,911) |
> | Payments and refunds | How each order was paid, and money paid back (6,783 and 509) |
> | Tickets and scans | Each ticket sold, and each barcode scanned at a gate (15,327 and 10,760) |
> | Dashboard | Tableau workbook with 4 tiles: gross sales, fees as a share of what fans paid, net fee revenue, sell-through |
> | Query history | 68 different questions Ticketfuddle's teams already asked, as SQL, over 756 runs |
>
> What fans paid is the ticket's face value plus every fee. The dashboard says what
> Ticketfuddle measures; the query history shows how finance, marketing and venue
> operations actually ask.
>
> **The six steps** (about 15 to 20 minutes; I show this list again as each step starts)
>
> 1. **Choose and load demo data.** I download the demo files from GitHub
>    (atlanai/analytics-agent-accelerator) into `<folder>/analytics-agent-accelerator`,
>    read the dashboard and the past queries to learn how Ticketfuddle measures its sales, and
>    load its tables into a small database on this machine.
> 2. **Choose what to build from.** You choose: the dashboard alone, or the dashboard
>    plus the past queries.
> 3. **Build and check metrics.** I show you the metrics your sources hold, by name, then
>    write the rule for each one and run it on Ticketfuddle's data to check it gives sensible
>    numbers.
> 4. **Keep local or save to Atlan Trident.** The context is built. You choose whether to
>    save it to Atlan Trident, where your company keeps context for its AI agents, or keep
>    it on this machine. Saving is when you sign in.
> 5. **Ask a question.** Your ticket fees agent is ready. You pick what to ask it, such as
>    how much of what fans paid went on fees.
> 6. **Build a dashboard.** If you want one, I propose a layout from your metrics and
>    build it as a page you can open.
>
> Nothing of yours is read or changed. The only thing saved outside this folder is the
> context, in the Atlan Trident workspace you pick in step 4.

## Facts for the later moments

| Fact | Value |
| --- | --- |
| Firm | Ticketfuddle |
| Agent | ticket fees agent |
| Tour | https://atlanai.github.io/analytics-agent-accelerator/box-office-tour/ |
| Source folder | `box-office/` |
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
