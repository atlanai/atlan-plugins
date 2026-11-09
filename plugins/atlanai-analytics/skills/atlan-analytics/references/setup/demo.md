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

How the person feels at each point decides what to say. They arrive unsure what this is,
get anxious when something runs with no explanation, lose the thread when a result appears
with no reason to care, worry at sign-in about touching their company's data, and at the
end want to know what they made and what to do with it. Each section below answers one of
those. When in doubt, say what just happened, what it means, and what comes next.

Assume the person knows nothing: not the firm, the data, what Atlan does, or why they will
sign in. The demo is about one experience: watching existing sources become context. The
context is made of metrics, each explained in plain words when it first appears. Before anything runs, they know what will happen, why,
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
> those sources and turns them into context that anyone, or any AI, can use. The context
> holds two things. A metric is a number Fixit cares about, such as gross margin, with the
> rule for working it out. A query is a ready-made question about the data, such as margin
> by job.
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
> 6. **Ready for questions.** Your job profit agent is ready. You ask Rob's question and
>    get the answer, with the context it used.
>
> Nothing of yours is read or changed. The only thing saved outside this folder is the
> context, in the Atlan Trident workspace you pick in step 5.

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

The six steps in the welcome are the checklist's six. In the demo, steps 2, 3 and 4 are
shown as "Choose what to build from", "Turn sources into context" and "Check the context on
the data" everywhere, the task list included; take status and counts from the helper, not
those three names. The person must
see the steps twice over, because a task list is easy to miss in a terminal:

- **In the task list.** Use the host's task tool as [the setup checklist](checklist.md)
  says. A task tool that is listed but not loaded is loaded first.
- **In chat, always.** When a step starts, print the tracker below as an ordinary reply,
  whether or not a task list exists. This replaces the checklist's rule against reprinting
  the list, for the demo only. Finished steps carry their one-line result with actual
  counts; the current step says `now`, with `your choice` on steps 2, 3 and 5.

```
**Step 3 of 6: Turn sources into context**

1. Read your sources: done, 1 dashboard, 110 past queries, 5 tables
2. Choose what to build from: done, dashboard and past queries
3. Turn sources into context: now
4. Check the context on the data: next
5. Review and choose where to save
6. Ready for questions
```

Mark the step after the current one `next`, so the person always sees what is coming. When
a step ends, close it in one line before the next tracker: "Step 3 done: 7 metrics and 12
queries built. Next, step 4: I check each one on Fixit's data." A step that waits on the
person says so: "Step 2 needs you: one choice."

### 3a. Say what and why at every moment

The person should never wonder what is happening or why. Before each moment below, print
two short lines as an ordinary reply, at most 35 words together, in this shape:

```
**Doing:** Reading Fixit's dashboard and 110 past queries.
**Why:** They show how Fixit already measures a job, so the context matches the firm instead of my guess.
```

Fill in actual counts and names. Use these, in order; the first of a step goes under its
tracker. Skip a moment that does not happen in this run.

| Moment | Doing | Why |
| --- | --- | --- |
| Download | Downloading Fixit's demo files into this folder. | The dashboard, past queries and data are in those files, and I read them on your machine. |
| Read sources | Reading the dashboard's 4 tiles and the 110 past queries. | They show how Fixit already measures a job, so the context matches the firm instead of my guess. |
| Sources question | Asking what to build from. | More sources give fuller context; the dashboard alone is faster. |
| Load data | Loading Fixit's 5 tables into a small database on this machine. | Each metric gets run on real rows, so you see numbers and not only words. |
| First metric | Building one metric first: gross margin. | You see what the context looks like, and can judge it, before I build the rest. |
| Build the rest | Building the other metrics and queries from the sources you chose. | Rob's question needs more than margin: labour, materials, rework and late jobs explain what ate it. |
| Check | Running every metric and query on Fixit's data. | One that returns nothing or nonsense would give wrong answers later; this catches it now. |
| Sign-in | Opening the Atlan Trident sign-in in your browser. | Saving needs to know who you are and which workspaces you can save to. |
| Workspace question | Asking which workspace to save to. | A workspace is where your team, and the agents they use, find this context. |
| Save | Saving the context to the workspace you chose. | From now on the agent and your colleagues read the same context. |
| Answer | Your job profit agent is ready. Answering Rob's question from the context. | It shows the agent uses what you built, not a guess at the tables. |

After a moment ends, give its result in one line with counts, in the past tense. Print the
first tracker right after "Start building", before reading setup's references or cloning.
Never use file names, helper names or query-shape counts in these lines. After reading
sources, add one sentence on what they revealed, such as which questions the analysts
asked most. Never leave a minute without a line; follow [progress](progress.md).

**Two words for what gets built: metrics and queries.** The whole is the context. It holds
metrics (a number Fixit cares about, with its rule) and queries (a ready-made question
about the data, such as margin by job). Count them that way every time: "Context built: 7
metrics and 12 queries", "saving 3 new metrics". Never call them pieces, calculations,
KPIs, measures, definitions, saved questions or saved SQL; the dashboard has tiles, and
what analysts ran is past queries. Say "the context behind this answer". Never say
candidate, engine, reconciliation, hold or Registry to the person.

### 4. No focus or goal questions: ask what to build from

The demo's focus is already chosen, so skip both [intent](intent.md) questions and its
source summary. Once the run exists, record the preset; this replaces intent's rule against
recording an unselected answer, for the demo only:

`PROJECT journey domain --answer "Job margin"`

`PROJECT journey purpose --answer "Build a job profit agent that answers which jobs lost margin and why"`

After reading sources, ask one native question. Its visible prompt, with actual counts:

"I'm building context for Fixit's job margin. I found a Power BI dashboard with 4 tiles,
110 past queries and 5 data tables. What should I build the context from?"

| Option | Description |
| --- | --- |
| Dashboard and past queries (recommended) | Everything the dashboard measures, plus how Fixit's analysts actually ask. The fullest context. |
| Dashboard only | Bring over the dashboard's 4 tiles as they are. Fastest. |

Scope the run to the answer through [inspect and scope](inspect-scope.md). With "Dashboard
only", author from the dashboard's measures alone and leave the past queries out. The data
tables are used either way, to check the context.

### 5. One metric first: explain it, prove it, then ask

This is where people get lost: a metric appears, they do not know where it came from or
why it matters, and they are asked to build more. So build one, and do not ask anything
until the person has seen four things: why it exists, what it is, that it works on real
numbers, and that it is finished.

Follow [build and check](build-check.md)'s first-candidate rule with gross margin: build
it, run its check, and stop. Then print the four parts below in order, as one ordinary
reply. Every value comes from this run; never copy the sample values here, and never claim
a check that did not run.

**Part 1, why this exists**, as written: "Here is the first metric I built: **gross
margin**. Fixit's dashboard and its analysts' queries each worked margin out in their own
way. A metric writes the rule down once, so the agent, and anyone who asks, uses the same
one every time."

**Part 2, the metric.** Two source rows, so the person sees what each source gave:

> **Gross margin** · metric 1 of Fixit's context
>
> | | |
> | --- | --- |
> | **What it means** | The profit on a job: what the client paid minus what the job cost. |
> | **How it's worked out** | Contract value, minus crew hours times hourly rate, minus materials. |
> | **Which jobs count** | Completed jobs only. |
> | **From the dashboard** | The "Gross margin" tile gave the formula. |
> | **From past queries** | 12 queries asked about margin. They gave the breakdown by job and confirmed completed jobs only. |
> | **How I checked it** | Ran it on Fixit's data: it returns a number for all 7 completed jobs, none blank, and the total matches the dashboard tile's own formula. |
> | **Status** | Done. Built and checked. |

With "Dashboard only", drop the past-queries row and say so in one line.

**Part 3, see it work.** One job, broken down with real numbers, so the metric stops being
abstract. Use the job with the lowest margin:

> **Gross margin on one job: Buttercup Vale Wind Turbine Base**
>
> | | |
> | --- | --- |
> | Client paid | £150,000 |
> | Crew and machine hours | £98,400 |
> | Materials | £61,250 |
> | **Gross margin** | **−£9,650 (−6%)** |
>
> One rule, one job, every number traceable to a row in Fixit's data. Rob's question is
> this same sum for every job, plus what drove the cost.

**Part 4, where this leaves you**, with actual counts: "That is 1 metric, built and
checked. From the sources you chose I can build about 6 more metrics and the queries
behind them, such as rework cost, material waste and late jobs, each checked the same way.
If margin looks wrong to you, say so and I fix it before going further."

Keep SQL out of all four parts. Only then ask one native question, "Gross margin is built
and checked. Build the rest?":

| Option | Description |
| --- | --- |
| Build the rest (recommended) | The other metrics and queries from the sources you chose, each checked the same way. A few minutes. |
| Show how this one was built | The SQL and the source lines behind gross margin, then this question again. |
| Stop here | Keep this one metric; nothing is saved to Atlan Trident. |

On "Build the rest", finish steps 3 and 4, with a count as the work goes ("4 of 7 metrics
built"). These four parts are the setup preview; do not show a second sample before the
save question unless gross margin changed.

### 6. Context built: celebrate, then ask where to keep it

When step 4 ends, before any save, sign-in or workspace question, print this block as an
ordinary reply, in a code fence so the figure keeps its shape. Use the run's actual counts
and the sources the person chose. Every count is true: when some metrics did not pass their
check, say so on the third line ("17 of 19 checked, 2 need a look") and still show the block.

```
 \o/   Context built for Fixit's job margin

       7 metrics and 12 queries
       from 1 dashboard and 110 past queries
       19 of 19 checked on Fixit's data
```

Under it, two lines: "This context is on your machine only. Atlan Trident is where your
company keeps context for its AI agents. Saving it there lets anyone at your company, and
any AI they use, ask the same questions and get the same answers."
Then ask one native question, "Where do you want to keep this context?":

| Option | Description |
| --- | --- |
| Save to Atlan Trident (recommended) | Anyone at your company, and any AI they use, can ask the same questions later. You sign in to Atlan Trident. |
| Keep it on this machine | Nothing leaves this machine; ask Rob's question here straight away. |

Only "Save to Atlan Trident" starts sign-in. With "Keep it on this machine", go straight to
step 6 and answer from the local context.

**Sign-in.** People worry here about touching company data. Before the browser opens, say:
"A browser window opens; sign in to Atlan Trident with your work account. Signing in
changes nothing. Nothing is saved until you pick a workspace." After sign-in, name the
tenant the result shows, never a guess.

**Workspace.** Ask one native question, "Which workspace should hold this context?". List
the person's own workspace first (the `user_default` one from the workspace read):

| Option | Description |
| --- | --- |
| Your personal workspace (recommended) | Only you see it. Nothing changes for your team, and it cannot clash with a colleague running the same demo. |
| A shared workspace, by name | At most three, only ones the read returned. Everyone in it sees the demo metrics. |

Never offer to create a workspace. With many shared workspaces, show the count and let the
person name one; do not list them all.

**Saving.** Saving goes in batches and can take a few minutes. Say so once, then give a
count as it goes: "Saving to your personal workspace: 12 of 19 saved." Finish with one
line: what was saved, where, and that it is now the context the agent reads.

**Already there.** When some of the context already exists in that workspace, say how much,
where, and why that is fine: "4 of the 7 metrics were already in your workspace from an
earlier run, so I reused them instead of making copies; 3 are new."

### 6a. When something does not go to plan

Say what happened, that nothing is lost, and the one way forward. Never leave the person
at an error.

| What happened | What to say |
| --- | --- |
| A metric or query fails its check | "2 of 19 did not return sensible numbers, so I left them out of what gets saved. That is normal, and the other 17 are fine." Name the two only when asked. |
| Sign-in is cancelled or times out | "Sign-in did not finish. Nothing is lost: the context is still on this machine. Try again, or keep it here and ask Rob's question now." |
| Signed in, but no workspace to save to | "You are signed in, but you have no workspace to save to yet. Your Atlan admin can add you to one. Meanwhile the context works from this machine." |
| A save is refused or stops part-way | "12 of 19 saved; the rest were refused. Nothing saved is lost." Then the one reason the result gave, and whether to retry. |
| Counts differ from a colleague's run | "That is expected today: the number of queries built from the history can vary between runs. The metrics from the dashboard's four tiles are the ones to compare." |
| Git or Python is missing | The one thing to install, then "run /atlanai-analytics:start again and it picks up here". |

In every case the demo can still end on Rob's question, from the context on this machine.

### 7. The payoff, then what you made (at most 200 words)

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Answer Rob's question through the ask route: the
job, the amount and what ate it, with real numbers. Under the answer, name the metrics it
used as links to their pages in Atlan Trident ([definition links](../definition-links.md));
never print metric or release ids. Say once that the answer came from the context built
from Fixit's dashboard and queries, not from a guess at the tables.

Then close the loop, so the person leaves knowing what they made. Three short parts, with
actual counts and names:

- **What you built:** "A job profit agent for Fixit: 7 metrics and 12 queries, built from
  1 dashboard and 110 past queries, checked on the data."
- **Where it lives:** the workspace name in Atlan Trident, or "on this machine".
- **Try next:** two more questions the context supports, such as rework cost by job or
  which jobs finished late.

End with: "With your own data, the same six steps turn your dashboards and your
warehouse's query history into your team's context. Run /atlanai-analytics:start and
choose Use my own data."
