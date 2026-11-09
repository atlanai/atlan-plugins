# Demo data or your own: the start choice

Read on a bare `start`: no link, folder, file or question supplied, and no saved run to
resume. A supplied source or a saved run skips this file and follows [shared setup](../setup.md).

## Offer the choice before any work

Ask one native question before surveying anything. Its visible prompt says what the person
will end with, so the choice makes sense to someone new:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on. Try it on demo data first,
or on your own?"

| Option | Description |
| --- | --- |
| Try the demo | Build a job profit agent for Fixit & Co., a made-up building firm: it answers which jobs lost money and why. About 15 minutes. |
| Use my own data | A dashboard link, local folder or BI export of yours. |

"Use my own data" follows [shared setup](../setup.md) and asks for the source once.

## Try the demo: a guided tour

Assume the person knows nothing: not the firm, the data, what Atlan does, or why they will
sign in. The demo is about one experience: watching existing sources become context. The
person never has to learn what a metric is. Before anything runs, they know what will happen, why,
and what they will do. The fixed texts below are printed as written, in full, as ordinary
replies. Do not shorten, summarize, merge or paraphrase them, and do not move them into a
tool description or status line. Setup's word limits do not apply to them.

### 1. The welcome: right after "Try the demo"

Print this before any other tool call, reference read, file search or clone. Replace
`<folder>` with the current working folder's absolute path; that is the only change.

> **You're building a job profit agent for Fixit & Co.**
>
> By the end, this chat is an agent that can tell Fixit which jobs lost money and what ate
> the profit, in plain English. You build it by turning Fixit's dashboard and past queries
> into context.
>
> Fixit & Co. is a made-up building firm. Rob runs the yard, Wanda does the building, Sprout
> helps, and five machines (Dig, Tipper, Whirl, Flatt and Lanky) bill by the hour like crew.
> Between July and October 2026 they ran 10 jobs, from a £150 cat flap to a £150,000 wind
> turbine base. You need no construction knowledge: every question is about money and
> time. The data is made up, so nothing of yours is used.
>
> **The problem.** Rob keeps asking: *which job ate our margin last quarter, and what ate it?*
> The numbers exist, but answering means someone writes SQL, and two analysts can get two
> different answers.
>
> **What you'll build.** A job profit agent. You turn Fixit's dashboard and past queries
> into context, and with that context the agent gives anyone one checked answer.
>
> **What context is.** It is what a new analyst has to learn before answering: what
> "margin" means at Fixit, how it is worked out, which jobs count. Fixit already wrote that
> down without meaning to, in its dashboard and in the queries its analysts ran. Atlan reads
> those sources and turns them into context that anyone, or any AI, can use.
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
> 2. **Choose focus and goal.** You choose what to focus on. Margin is the one Rob asks about.
> 3. **Turn sources into context.** I turn what the dashboard and queries say into context:
>    what each number means, how it is worked out and which jobs count.
> 4. **Check the context on the data.** I run it on Fixit's data to check it gives sensible
>    numbers.
> 5. **Review and choose where to save.** You see one example, then choose where to save
>    the context. This is when you sign in to your company's Atlan, so anyone there can ask
>    the same questions later.
> 6. **Ready for questions.** Your job profit agent is ready. You ask Rob's question and
>    get the answer, with the context it used.
>
> Nothing of yours is read or changed. The only thing saved outside this folder is the
> context, in the Atlan workspace you pick in step 5.

Then ask one native question, "Ready to build?", with "Start building" and "Not now". On
"Not now", stop and say "/atlanai-analytics:start" resumes it. After "Start building", print
the first step tracker (section 3), then read setup's references.

### 2. Run setup on the demo repository

Run [shared setup](../setup.md) exactly as if the person had asked: "Use Atlan Analytics to
set up this folder: https://github.com/atlanai/analytics-agent-accelerator, start from
scratch." Start a new run. Every stage, question, check and checklist rule applies.

The checkout is `<folder>/analytics-agent-accelerator`, the path the welcome named. Reuse it
when its origin is that repository; otherwise clone there with `git clone --depth 1`. Never
search other folders or the home directory for a copy. Without git, ask the person to
download the repository into that path.

### 3. Show the steps every time one starts

The six steps in the welcome are the checklist's six. In the demo, steps 3 and 4 are shown
as "Turn sources into context" and "Check the context on the data" everywhere, the task
list included; take status and counts from the helper, not those two names. The person must
see the steps twice over, because a task list is easy to miss in a terminal:

- **In the task list.** Use the host's task tool as [the setup checklist](checklist.md)
  says. A task tool that is listed but not loaded is loaded first.
- **In chat, always.** When a step starts, print the tracker below as an ordinary reply,
  whether or not a task list exists. This replaces the checklist's rule against reprinting
  the list, for the demo only. Finished steps carry their one-line result with actual
  counts; the current step says `now`, with `your choice` on steps 2 and 5.

```
**Step 3 of 6: Turn sources into context**

1. Read your sources: done, 1 dashboard, 110 past queries, 5 tables
2. Choose focus and goal: done, job margin for team reuse
3. Turn sources into context: now
4. Check the context on the data
5. Review and choose where to save
6. Ready for questions
```

Under the tracker, print the step's line from this table, filling in actual counts:

| Step | Line under the tracker |
| --- | --- |
| Read your sources | "Downloading the demo files, then reading the dashboard and the past queries to learn how Fixit measures a job. Under a minute." |
| Choose focus and goal | "Your first choice. Atlan builds context around what you care about; margin is Rob's question." |
| Turn sources into context | "Turning what the dashboard and past queries say into context: what each number means, how it is worked out and which jobs count." |
| Check the context on the data | "Running the context on Fixit's data to make sure it gives sensible numbers. About 3 minutes." |
| Review and choose where to save | "Your second choice: where to save. Saving needs you signed in to your company's Atlan." |
| Ready for questions | "Your job profit agent is ready. Now the payoff: Rob's question." |

Print the first tracker right after "Start building", before reading setup's references or
cloning. Within a step, keep work lines in the person's words ("Loading Fixit's five tables
so the context can be checked"), never file names, helper names or query-shape counts.
After reading sources, add one sentence on what they revealed, such as which questions the
analysts asked most. Never leave a minute without a line; follow [progress](progress.md).

**Say context.** Wherever setup's replies would say metrics, metric definitions,
definitions or saved SQL, the demo says context: "14 pieces of context", not "9 metrics and
5 saved queries"; "the context behind this answer", not "the definition used". Name a single
one by its business name, such as gross margin. Never say metric, candidate, engine,
reconciliation, hold or Registry to the person. Only when asked what context is made of,
say it holds named calculations and saved queries.

### 4. The two choices

Focus and goal: follow [intent](intent.md), but skip its source summary; the welcome gave
it. List job margin first, labelled "(recommended for the demo)", and for the goal,
answering Rob's question first. The person can pick anything else.

Where to save: before sign-in, say "A browser window opens; sign in to your company's Atlan
there." After sign-in, name the tenant and workspaces the result shows, never a guess.
Suggest the person's own workspace: "Your personal workspace keeps the demo out of your
team's way." When the context already exists there, say how much, where, and that it was
reused rather than copied.

### 5. The first example: show what context looks like

Show gross margin first, with setup's preview fields, introduced as "Here is one piece of
the context, for gross margin." Then one line: "It says what margin means at Fixit, how it
is worked out, which jobs count, and that it ran on the data."

### 6. The payoff and the bridge (at most 160 words)

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Answer Rob's question through the ask route: the job, the amount, what ate it, and the
context behind it in one line. Say once that the answer came from the context built from
Fixit's dashboard and queries, not from a guess at the tables. Offer two more questions the yard supports, such as rework cost
by job or which jobs finished late. Close with:

"With your own data, the same steps turn your dashboards and your warehouse's query history
into your team's context. Run /atlanai-analytics:start and choose Use my own data."
