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
4. Check the context on the data
5. Review and choose where to save
6. Ready for questions
```

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
| Build the rest | Building the other metrics from the sources you chose. | Rob's question needs more than margin: labour, materials, rework and late jobs explain what ate it. |
| Check | Running every metric on Fixit's data. | A metric that returns nothing or nonsense would give wrong answers later; this catches it now. |
| Sign-in | Opening the Atlan Trident sign-in in your browser. | Saving needs to know who you are and which workspaces you can save to. |
| Workspace question | Asking which workspace to save to. | A workspace is where your team, and the agents they use, find this context. |
| Save | Saving the context to the workspace you chose. | From now on the agent and your colleagues read the same context. |
| Answer | Your job profit agent is ready. Answering Rob's question from the context. | It shows the agent uses what you built, not a guess at the tables. |

After a moment ends, give its result in one line with counts, in the past tense. Print the
first tracker right after "Start building", before reading setup's references or cloning.
Never use file names, helper names or query-shape counts in these lines. After reading
sources, add one sentence on what they revealed, such as which questions the analysts
asked most. Never leave a minute without a line; follow [progress](progress.md).

**Say context and metrics.** The whole is the context; what it is made of, and what gets
counted, is metrics: "Context built: 14 metrics", "saving 3 new metrics", "one metric,
gross margin". Never say pieces. Where the run also holds saved queries, count them as
saved queries beside the metrics. Say "the context behind this answer", not "the definition
used". Never say metric definition, candidate, engine, reconciliation, hold or Registry to
the person.

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

### 5. One example first, shown well, then ask

Follow [build and check](build-check.md)'s first-candidate rule with gross margin: build
it, run its check, and stop before building anything else. A card with no introduction is
confusing, so print these three parts in order, as one ordinary reply. Every value is read
from this run; never copy the layout's sample values.

First, what the person is about to see and why, as written:

"Here is the first metric I built: **gross margin**. Fixit's dashboard and
queries each worked margin out in their own way. This writes the rule down once, so the
agent, and anyone who asks, uses the same one every time. Read it top to bottom: what the
number means, how it is worked out, which jobs count, where the rule came from, and proof
that it runs on the data."

Second, the card:

> **Gross margin** · one metric in Fixit's context
>
> | | |
> | --- | --- |
> | **What it means** | The profit on a job: what the client paid minus what the job cost. |
> | **How it's worked out** | Contract value, minus crew hours times hourly rate, minus materials. |
> | **Which jobs count** | Completed jobs only. |
> | **Where it came from** | The dashboard's "Gross margin" tile, and 12 past queries that ask about it. |
> | **Checked on Fixit's data** | Ran. Total margin across 7 jobs, with the figure. |
>
> **On Fixit's data:** the three best and three worst jobs by margin, one line each.

Third, what happens next, as written: "If this matches how you would describe margin, I
build the rest the same way. If something looks off, tell me and I fix it before going
further."

Keep SQL out of the card. Then ask one native question, "This is one metric.
Build the rest?":

| Option | Description |
| --- | --- |
| Build the rest (recommended) | The other metrics from the sources you chose, each checked the same way. |
| Show how this one was built | The SQL and the source lines behind gross margin, then this question again. |
| Stop here | Keep this one metric; nothing is saved to Atlan Trident. |

On "Build the rest", finish steps 3 and 4. This card is the setup preview; do not show a
second sample before the save question unless gross margin changed.

### 6. Context built: celebrate, then ask where to keep it

When step 4 ends, before any save, sign-in or workspace question, print this block as an
ordinary reply, in a code fence so the figure keeps its shape. Use the run's actual counts
and the sources the person chose. Every count is true: when some metrics did not pass their
check, say so on the third line ("12 of 14 checked, 2 need a look") and still show the block.

```
 \o/   Context built for Fixit's job margin

       14 metrics
       from 1 dashboard and 110 past queries
       14 of 14 checked on Fixit's data
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

Where to save: before sign-in, say "A browser window opens; sign in to Atlan Trident with
your work account." After sign-in, name the tenant and workspaces the result shows, never a guess.
Suggest the person's own workspace: "Your personal workspace keeps the demo out of your
team's way." When the context already exists there, say how much, where, and that it was
reused rather than copied.

### 7. The payoff and the bridge (at most 160 words)

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Answer Rob's question through the ask route: the job, the amount, what ate it, and the
context behind it in one line. Say once that the answer came from the context built from
Fixit's dashboard and queries, not from a guess at the tables. Name the metrics it used as
links to their pages in Atlan Trident ([definition links](../definition-links.md)); never
print metric or release ids. Offer two more questions the yard supports, such as rework cost
by job or which jobs finished late. Close with:

"With your own data, the same steps turn your dashboards and your warehouse's query history
into your team's context. Run /atlanai-analytics:start and choose Use my own data."
