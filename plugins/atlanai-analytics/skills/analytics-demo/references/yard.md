# Demo: Fixit & Co., the builder's yard

Read right after the person picks this demo, and again at each later moment for its facts.
The demo is `yard`. It lives in this skill's `assets/yard/`; `DEMO open yard` sets up its
working folder, and `DEMO requests yard` prints its dashboard file, database and schema.

## The welcome

Print this block next, before any other tool call or file search. Print it whole and
as written, as an ordinary reply; it has nothing to fill in. Keep the step strip inside its
code fence so its columns stay aligned.

> **Build a job profit agent for Fixit & Co. that answers questions about its jobs in plain
> English, and shows the rule behind every number.**
>
> It reads the dashboard and the questions analysts already ran, then suggests the questions
> this context can answer once it is built.
>
> | What you're working with | |
> | --- | --- |
> | Dashboard | Power BI, 4 tiles: gross margin, rework cost, material waste, late jobs |
> | Data | 10 jobs (July to October 2026), 8 crew and machines, 746 work logs, 51 material orders, 11 snags |
> | Past queries | 110 questions Fixit's analysts already ran |
>
> ```
> ○─────────○─────────○─────────○─────────○
> Pick      Build     Save to   Ask       Build a
> sources   context   Atlan     questions report
>           ~5 min                        (optional)
> ```
>
> About 15 minutes. Made-up data; nothing of yours is touched. See the firm first:
> https://atlanai.github.io/analytics-agent-accelerator/yard-tour/

## Facts for the later moments

| Fact | Value |
| --- | --- |
| Firm | Fixit |
| Agent | job profit agent |
| Tour | https://atlanai.github.io/analytics-agent-accelerator/yard-tour/ |
| Working folder | `atlan-analytics-demo/yard/` |
| Dashboard | a Power BI dashboard with 4 tiles |
| Tiles | gross margin, rework cost, material waste, late jobs |
| Past queries | 110 |
| Data tables | 5 |
| Focus to record | Job margin |
| Purpose to record | Build a job profit agent that answers which jobs lost margin and why |
| What a metric is, by example | a number Fixit cares about, such as gross margin, with the rule for working it out |
| Why build them | Margin says which job lost money. Labour, materials, rework and late jobs say why. |
| What the agent can answer | Questions about job margin, rework, material waste and late jobs |

The dashboard to offer at the end is named `fixit-job-profit-dashboard.html`.
