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
> By the end, this chat is an agent that can tell Fixit which jobs lost money and why, in
> plain English. You build it by turning Fixit's dashboard and past queries
> into context.
>
> Fixit & Co. is a made-up building firm, with a crew of people and hired machines that
> bill by the hour. Between July and October 2026 it ran 10 jobs, from a £150 cat flap to
> a £150,000 wind turbine base. You need no construction knowledge: every question is about money and
> time. The data is made up, so nothing of yours is used.
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
| Build the rest | Building the other metrics and queries from the sources you chose. | Margin says which job lost money. Labour, materials, rework and late jobs say why. |
| Check | Running every metric and query on Fixit's data. | One that returns nothing or nonsense would give wrong answers later; this catches it now. |
| Sign-in | Opening the Atlan Trident sign-in in your browser. | Saving needs to know who you are and which workspaces you can save to. |
| Workspace question | Asking which workspace to save to. | A workspace is where your team, and the agents they use, find this context. |
| Save | Saving the context to the workspace you chose and releasing it. | Agents only find released metrics and queries, so releasing is what lets the agent and your colleagues use them. |
| Ready | Your job profit agent is ready. Showing the questions it can answer. | You choose what to ask; each answer comes from what you built, not a guess at the tables. |

After a moment ends, give its result in one line with counts, in the past tense. Print the
first tracker right after "Start building", before reading setup's references or cloning.
Never use file names, helper names or query-shape counts in these lines. After reading
sources, add one sentence on what they revealed, such as which questions the analysts
asked most. Never leave a minute without a line; follow [progress](progress.md).

**No named people.** Never name anyone at Fixit in the demo's own words: the brief, the
steps, a question or an example. The firm is "Fixit" and the question is "which job lost
the most margin, and why". A name from the data may appear in an answer only when the
person's question asks who.

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

"I read Fixit's sources: a Power BI dashboard with 4 tiles, 110 past queries and 5 data
tables. Next I turn them into metrics. A metric is a number Fixit cares about, such as
gross margin, with the rule for working it out. Metrics are how the context is stored, so
the agent uses the same rule every time. I build one first and show you, then the rest.
What should I build them from?"

This is the first time the person hears the word metric, so the prompt keeps all of it.

| Option | Description |
| --- | --- |
| Dashboard and past queries (recommended) | The dashboard's 4 numbers, plus what Fixit's analysts actually ask. More metrics and queries, and a few minutes longer. |
| Dashboard only | The dashboard's 4 numbers as metrics. Fastest. |

Scope the run to the answer through [inspect and scope](inspect-scope.md). With "Dashboard
only", author from the dashboard's measures alone and leave the past queries out. The data
tables are used either way, to check the context.

### 5. The first metric: one block, printed like the welcome

This is where people get lost: a metric appears, they do not know what it is, where it
came from or why it matters, and they are asked to build more. So the first metric gets
its own block, in the same form as the welcome, before anything is asked.

Follow [build and check](build-check.md)'s first-candidate rule with gross margin: build
it, run its check, and stop. Then print the block below before the question, whole and as
written, as an ordinary reply, exactly as the welcome is printed. It is a fixed text: do
not shorten it, summarize it, or replace it with a line about the result or about what
you will do next. The only changes are the values in `<angle brackets>`, each filled from
this run. Never invent a value, and never claim a check that did not run; when a row has
nothing true to say, drop that row.

> **Your first metric: gross margin**
>
> Gross margin is the profit on a job: what the client paid, minus what the job cost Fixit
> in crew, machines and materials. It is the number behind the question Fixit keeps asking.
>
> **Why it needed writing down.** Fixit's dashboard and its analysts' queries each worked
> margin out their own way. A metric states the rule once, so the agent, and everyone who
> asks, gets the same number.
>
> **The rule**
>
> | | |
> | --- | --- |
> | What it measures | Profit on each job |
> | How it is worked out | Client paid, minus crew and machine hours at their hourly rate, minus materials |
> | Which jobs count | `<which jobs, such as: all jobs except cancelled ones>` |
> | Where the rule came from | The dashboard's "Gross margin" tile`<, and N past queries that ask about margin>` |
> | How I checked it | Ran it on Fixit's data: `<total>` across `<N>` jobs`<, matching a separate sum from the raw tables>` |
>
> **On one job: `<the job with the lowest margin>`**
>
> | | |
> | --- | --- |
> | Client paid | `<amount>` |
> | Crew and machines | `<amount>` |
> | Materials | `<amount>` |
> | **Gross margin** | **`<amount and percent>`** |
>
> That is the metric at work: one rule, one job, every number traceable to a row in
> Fixit's data. Finding the job that lost the most is this same sum for every job, plus
> what drove the cost.
>
> **What's next.** This is 1 of about `<N>` metrics. The others, such as rework cost,
> material waste and late jobs, are built and checked the same way.

Keep SQL out of the block. With "Dashboard only", the source row names the tile alone.
Then ask one native question, "Gross margin is built and checked. Build the rest?":

| Option | Description |
| --- | --- |
| Build the rest (recommended) | The other metrics and queries from the sources you chose, each checked the same way. A few minutes. |
| Ask a question now | Try gross margin straight away, such as which job had the lowest margin. Answered from this machine; this question comes back after. |
| Show how this one was built | The SQL and the source lines behind gross margin, then this question again. |

A person who wants to stop says so in their own words: keep the one metric, save nothing
to Atlan Trident. On "Build the rest", finish steps 3 and 4, with a count as the work goes
("4 of 7 metrics built"). This block is the setup preview; do not show a second sample
before the save question unless gross margin changed.

### 6. Context built: celebrate, then ask where to keep it

This is the moment the person finished the hard part, and they should feel it. When step
4 ends, before any save, sign-in or workspace question, print the block below whole and
as written, as an ordinary reply, exactly as the welcome is printed. It is a fixed text:
never replace it with a line such as "the checks passed, moving on". Keep it in its code
fence so the confetti keeps its shape. The only changes are the values in
`<angle brackets>`, filled from this run and the sources the person chose. Every count is
true: when some did not pass their check, the last line says so ("17 of 19 checked on
Fixit's data, 2 need a look") and the block still prints.

```
  *   .  '   *    .   '  *   .    '   *  .   '    *   .
    '    *   .    CONTEXT BUILT    .   *    '    .   *
  .   *    '   .    *   '    .   *    '   .   *    '   .

    You turned Fixit's dashboard and queries into context.

      <N> metrics and <N> queries
      built from <1 dashboard and N past queries>
      <N> of <N> checked on Fixit's data
```

Then one line, as written: "That was the hard part. Fixit's numbers now each have one
agreed rule, and an agent can answer from them."

Under it, say why the next choice matters, as written: "Right now this context lives in
one folder on this machine, and only this chat can use it. In Atlan Trident it becomes the
one place your agents look: each question finds the right metric, and everyone gets the
same number from the same rule."

Then ask one native question. The prompt carries the reason, because the lines above can
collapse: "Your context works here, in this folder. Saved to Atlan Trident, any agent can
find it and every answer uses the same rule. Where should it live?"

| Option | Description |
| --- | --- |
| Save to Atlan Trident (recommended) | Agents find the right metric for each question, from any chat or tool. Everyone gets the same number, and a change to a rule reaches all of them at once. You sign in once. |
| Keep it on this machine | Works in this folder only. Other chats, tools and colleagues cannot find it, so each would work the numbers out its own way. |

The two reasons to save are retrieval (an agent finds the metric a question needs) and
consistency (one rule, one number, for everyone). Keep both in the copy, in those plain
words. Do not oversell: say nothing about speed, accuracy scores or governance, and never
suggest the local choice is wrong; it is the right one for a quick look.

Only "Save to Atlan Trident" starts sign-in. With "Keep it on this machine", go straight to
step 6; its answers come from the local context.

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

**The address for proof links.** Once the save finishes, ask for the Trident address as
[definition links](../definition-links.md) says, so the first answer already links to its
proof. Ask it as the last message of the turn, in plain words, and go on when it is given
or declined.

**Already there.** When some of the context already exists in that workspace, say how much,
where, and why that is fine: "4 of the 7 metrics were already in your workspace from an
earlier run, so I reused them instead of making copies; 3 are new."

### 6a. When something does not go to plan

Say what happened, that nothing is lost, and the one way forward. Never leave the person
at an error.

| What happened | What to say |
| --- | --- |
| A metric or query fails its check | "2 of 19 did not return sensible numbers, so I left them out of what gets saved. That is normal, and the other 17 are fine." Name the two only when asked. |
| Sign-in is cancelled or times out | "Sign-in did not finish. Nothing is lost: the context is still on this machine. Try again, or keep it here and ask your question now." |
| Signed in, but no workspace to save to | "You are signed in, but you have no workspace to save to yet. Your Atlan admin can add you to one. Meanwhile the context works from this machine." |
| A save is refused or stops part-way | "12 of 19 saved; the rest were refused. Nothing saved is lost." Then the one reason the result gave, and whether to retry. |
| Counts differ from a colleague's run | "That is expected today: the number of queries built from the history can vary between runs. The metrics from the dashboard's four tiles are the ones to compare." |
| Git or Python is missing | The one thing to install, then "run /atlanai-analytics:start again and it picks up here". |

In every case the demo can still end on the question list, answered from the context on this machine.

### 7. Ready: show what was built and offer questions, never answer unasked

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Do not answer any question the person has not
chosen. When step 6 starts, show what they made, then let them pick what to ask.

First, print this block whole and as written, with the values in `<angle brackets>` from
this run:

> **Your job profit agent is ready**
>
> | | |
> | --- | --- |
> | What you built | `<N>` metrics and `<N>` queries, from `<1 dashboard and N past queries>`, checked on Fixit's data |
> | Where it lives | `<workspace name>` in Atlan Trident, or: on this machine |
> | What it can answer | Questions about job margin, rework, material waste and late jobs |

Then ask one native question, "What do you want to ask your agent?", with three or four
questions this run's metrics and queries can answer, and the host's free-text entry for
anything else. Offer only questions the built context supports. For example:

| Option | Description |
| --- | --- |
| Which job lost the most margin, and why? | Margin by job, with what drove the cost. |
| Which jobs finished late? | Planned against actual finish dates. |
| Where did rework cost the most? | Rework cost by job. |
| Where was the most material wasted? | Material waste by job. |

Answer only the question the person picks or types, through the ask route, with real
numbers. Every answer has three parts, in this order:

1. **The answer**, led by the number or the name, then what explains it.
2. **Proof in Atlan Trident**: one line per metric and query the answer used, each a link
   to its page, as [definition links](../definition-links.md) says. Never print metric or
   release ids. With the context only on this machine, the list says there is nothing to
   open yet and that saving to Atlan Trident gives each answer its links.
3. **One follow-up**, as a native question: "Want to turn this context into a dashboard?"

| Option | Description |
| --- | --- |
| Design a dashboard | I propose a layout from your metrics, you adjust it, and I build it as a page you can open. |
| Ask another question | The questions not asked yet, or your own. |
| I'm done | Wrap up. |

Say once per conversation that answers come from the context built from Fixit's dashboard
and queries, not from a guess at the tables. Names in the data, such as a crew member,
appear only when the question asks who. Never answer a question the person has not asked.

**Design a dashboard.** Keep it real and small:

- Propose the layout in words first, at most six lines: a tile for each metric (gross
  margin, rework cost, material waste, late jobs), one breakdown by job, and one chart.
  Ask one native question to confirm it or change it.
- Build one self-contained HTML file in the working folder, named
  `fixit-job-profit-dashboard.html`: inline CSS and SVG, no external scripts or fonts, and
  readable in light and dark.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
- Each tile names its metric and links to its page in Atlan Trident when one exists.
- Say where the file is and how to open it, in one line. Do not publish it anywhere.

**Wrap up.** On "I'm done", or after the dashboard is built, add once: "With your own
data, the same six steps turn your dashboards and your warehouse's query history into
your team's context. Run /atlanai-analytics:start and choose Use my own data."
