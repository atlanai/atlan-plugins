# Demo: Fixit & Co., the builder's yard

Read right after the person picks this demo, and again at each later moment for its facts.
The source is the `yard/` folder of the demo repository.

## The welcome

Print this block next, before any other tool call, file search or clone. Print it whole and
as written, as an ordinary reply. Replace `<folder>` with the current working folder's
absolute path; that is the only change.

> **You're building a job profit agent for Fixit & Co.**
>
> By the end, this chat is an agent that can tell Fixit which jobs lost money and why, in
> plain English. You build it by turning Fixit's dashboard and past queries into context.
>
> **See it live first.** Fixit's dashboard, its data and the queries its analysts ran are
> on a tour page you can click through:
> https://atlanai.github.io/analytics-agent-accelerator/yard-tour/
> Open it in your browser and look around. It is the same environment you are about to
> build on, so the numbers you see there are the ones your agent will answer from.
>
> Fixit & Co. is a made-up building firm, with a crew of people and hired machines that
> bill by the hour. Between July and October 2026 it ran 10 jobs, from a £150 cat flap to
> a £150,000 wind turbine base. You need no construction knowledge: every question is
> about money and time. The data is made up, so nothing of yours is used.
>
> **The problem.** Fixit keeps asking one question: *which job lost the most margin last
> quarter, and why?* The numbers exist, but answering means someone writes SQL, and two
> analysts can get two different answers.
>
> **What you'll build.** A job profit agent. You turn Fixit's dashboard and past queries
> into context, and with that context the agent gives anyone one checked answer.
>
> **What context is.** It is what a new analyst has to learn before answering: what
> "margin" means at Fixit, how it is worked out, which jobs count. Fixit already wrote that
> down without meaning to, in its dashboard and in the queries its analysts ran. Atlan reads
> those sources and turns them into context that anyone, or any AI, can use. The context is
> a set of metrics: each one a number Fixit cares about, such as gross margin, with the
> rule for working it out.
>
> **What's in the demo**
>
> | What | In plain words |
> | --- | --- |
> | Jobs | Each job, its client and what it was worth (10) |
> | Crew | People and machines, with hourly rates (8) |
> | Work logs | Who worked on which job, which day, for how long (746) |
> | Material orders | What each job bought, and how much was wasted (51) |
> | Snags | Things that went wrong on site (11) |
> | Dashboard | Power BI report with 4 tiles: margin, rework cost, material waste, late jobs |
> | Query history | 110 different questions Fixit's analysts already asked, as SQL |
>
> A job's cost is crew hours times hourly rate, plus materials. Its margin is what the
> client paid minus that cost. The dashboard says what Fixit measures; the query history
> shows how people actually ask.
>
> **The six steps** (about 15 minutes; I show this list again as each step starts)
>
> 1. **Read your sources.** I download the demo files from GitHub
>    (atlanai/analytics-agent-accelerator) into `<folder>/analytics-agent-accelerator`, then
>    read the dashboard and the past queries to learn how Fixit measures a job.
> 2. **Choose what to build from.** You choose: the dashboard alone, or the dashboard
>    plus the past queries.
> 3. **Turn sources into context.** I build one metric first, gross margin, and show it to
>    you: what it means, how it is worked out and which jobs count. You say whether to
>    build the rest.
> 4. **Check the context on the data.** I run it on Fixit's data to check it gives sensible
>    numbers.
> 5. **Review and choose where to save.** The context is built. You choose whether to save
>    it to Atlan Trident, where your company keeps context for its AI agents, or keep it
>    on this machine. Saving is when you sign in.
> 6. **Ready for questions.** Your job profit agent is ready. You pick what to ask it,
>    such as which job lost the most margin and why.
>
> Nothing of yours is read or changed. The only thing saved outside this folder is the
> context, in the Atlan Trident workspace you pick in step 5.

## Facts for the later moments

| Fact | Value |
| --- | --- |
| Firm | Fixit |
| Agent | job profit agent |
| Tour | https://atlanai.github.io/analytics-agent-accelerator/yard-tour/ |
| Source folder | `yard/` |
| Dashboard | a Power BI dashboard with 4 tiles |
| Tiles | gross margin, rework cost, material waste, late jobs |
| Past queries | 110 |
| Data tables | 5 |
| Focus to record | Job margin |
| Purpose to record | Build a job profit agent that answers which jobs lost margin and why |
| The question | Which job lost the most margin last quarter, and why? |
| What a metric is, by example | a number Fixit cares about, such as gross margin, with the rule for working it out |
| First metric | gross margin |
| First metric, in plain words | Gross margin is the profit on a job: what the client paid, minus what the job cost Fixit in crew, machines and materials. |
| What it measures | Profit on each job |
| How it is worked out | Client paid, minus crew and machine hours at their hourly rate, minus materials |
| One example to break down | the job with the lowest margin, with rows: Client paid; Crew and machines; Materials; Gross margin |
| After the example | Finding the job that lost the most is this same sum for every job, plus what drove the cost. |
| Why build the rest | Margin says which job lost money. Labour, materials, rework and late jobs say why. |
| What the agent can answer | Questions about job margin, rework, material waste and late jobs |

Questions to offer when the agent is ready, when this run's context supports them:

| Question | Description |
| --- | --- |
| Which job lost the most margin, and why? | Margin by job, with what drove the cost. |
| Which jobs finished late? | Planned against actual finish dates. |
| Where did rework cost the most? | Rework cost by job. |
| Where was the most material wasted? | Material waste by job. |

The dashboard to offer at the end is named `fixit-job-profit-dashboard.html`.
