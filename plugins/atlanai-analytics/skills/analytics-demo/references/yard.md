# Demo: Fixit & Co., the builder's yard

Read right after the person picks this demo, and again at each later moment for its facts.
The demo is `yard`. It lives in this skill's `assets/yard/`; `DEMO open yard` sets up its
working folder, and `DEMO requests yard` prints its dashboard file, database and schema.

## The welcome

The welcome is the text of the "Ready to build?" question, as [the tour rules](tour.md)
describe. Send these lines as the question, whole and as written, with their line breaks
and leading spaces. The fence only marks the lines; it is not part of the question. Do not
also print the welcome as a reply before the question.

```
I'll build a job profit agent for Fixit & Co., a made-up building
firm. It answers questions about Fixit's jobs in plain English, and
shows how each number is worked out.

To build it, I read Fixit's dashboard and past queries, define each
metric once and test each one on Fixit's data. Then I suggest
questions to ask your agent. I build one on your own data the same
way, from your dashboards.

What you're working with:
  Dashboard      Power BI, 6 tiles: 4 metrics (gross margin, rework
                 cost, material waste, late jobs) and 2 charts
                 (margin by job, rework by crew member)
  Data           10 jobs (July to October 2026), 8 crew and machines,
                 746 work logs, 51 material orders, 11 snags
  Past queries   110 past queries from Fixit's analysts

  Steps          1. Choose the sources
                 2. Build your context: metrics and saved queries
                    (about 13 min)
                 3. Choose where to store your context
                 4. Ask your agent a question
                 5. Build a dashboard (optional)

About 15 minutes. The data is made up. I add two folders here and
change none of your files.
See the firm first:
https://atlanai.github.io/analytics-agent-accelerator/yard-tour/

Ready to build?
```

## Facts for the later moments

| Fact | Value |
| --- | --- |
| Firm | Fixit |
| Agent | job profit agent |
| Tour | https://atlanai.github.io/analytics-agent-accelerator/yard-tour/ |
| Working folder | `atlan-analytics-demo/yard/` |
| Dashboard | a Power BI dashboard with 6 tiles: 4 metrics and 2 charts |
| Tiles | gross margin, rework cost, material waste, late jobs; charts of gross margin by job and rework cost by crew member |
| Past queries | 110 |
| Data tables | 5 |
| Focus to record | Job margin |
| Purpose to record | Build a job profit agent that answers which jobs lost margin and why |
| What a metric is, by example | one number Fixit cares about, such as gross margin, with one definition for working it out |
| Why build them | Margin says which job lost money. Labour, materials, rework and late jobs say why. |
| What the agent can answer | Questions about job margin, rework, material waste and late jobs |

The dashboard to offer at the end is named `fixit-job-profit-dashboard.html`.
