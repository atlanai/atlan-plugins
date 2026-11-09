# Demo data or your own: the start choice

Read on a bare `start`: no link, folder, file or question supplied, and no saved run to
resume. A supplied source or a saved run skips this file and follows [shared setup](../setup.md).

## Offer the choice before any work

Ask one native question before surveying anything. Its visible prompt says what the person
will end with, so the choice makes sense to someone new:

"Atlan Analytics turns a dashboard and the queries your team already runs into metrics you
can ask about in plain English. Each answer shows the definition behind it. Try it on demo
data first, or on your own?"

| Option | Description |
| --- | --- |
| Try the demo | Fixit & Co., a made-up building firm. About 15 minutes. None of your data is used. |
| Use my own data | A dashboard link, local folder or BI export of yours. |

"Use my own data" follows [shared setup](../setup.md) and asks for the source once.

## Try the demo: a guided tour

Assume the person knows nothing: not the firm, the data, what a metric definition is, what
Atlan does, or why they will sign in. Before anything runs, they know what will happen, why,
and what they will do. The fixed texts below are printed as written, in full, as ordinary
replies. Do not shorten, summarize, merge or paraphrase them, and do not move them into a
tool description or status line. Setup's word limits do not apply to them.

### 1. The welcome: right after "Try the demo"

Print this before any other tool call, reference read, file search or clone. Replace
`<folder>` with the current working folder's absolute path; that is the only change.

> **Welcome to Fixit & Co.**
>
> Fixit & Co. is a made-up building firm. Rob runs the yard, Wanda does the building, Sprout
> helps, and five machines (Dig, Tipper, Whirl, Flatt and Lanky) bill by the hour like crew.
> Between July and October 2026 they ran 10 jobs, from a £150 cat flap to a £150,000 wind
> turbine base. The data is synthetic; you need no construction knowledge, because every
> question is about money and time.
>
> **The problem.** Rob keeps asking: *which job ate our margin last quarter, and what ate it?*
> The numbers exist, but answering means someone writes SQL, and two analysts can get two
> different answers.
>
> **What you'll do.** Set Fixit up so anyone can ask that in plain English and get one
> checked answer, with the definition behind it.
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
> **What happens next** (about 15 minutes; the task list shows where you are)
>
> 1. I download the demo files from GitHub (atlanai/analytics-agent-accelerator) into
>    `<folder>/analytics-agent-accelerator`, so I can read them on your machine.
> 2. I read the dashboard and the past queries to learn how Fixit measures a job.
> 3. **You choose** what to focus on. Margin is the one Rob asks about.
> 4. I write metric definitions (a name, a calculation and its rules) and run each on the
>    data to check it returns sensible numbers.
> 5. **You choose** where to save them. This is when you sign in to your company's Atlan,
>    so anyone there can ask the same questions later.
> 6. You ask Rob's question and get the answer, with the definition it used.
>
> Nothing of yours is read or changed. The only thing saved outside this folder is the
> metric definitions, in the Atlan workspace you pick in step 5.

Then ask one native question, "Ready to start?", with "Start the demo" and "Not now". On
"Not now", stop and say "/atlanai-analytics:start" resumes it. Only after "Start the demo"
read setup's references and put [the setup checklist](checklist.md) in the task list.

### 2. Run setup on the demo repository

Run [shared setup](../setup.md) exactly as if the person had asked: "Use Atlan Analytics to
set up this folder: https://github.com/atlanai/analytics-agent-accelerator, start from
scratch." Start a new run. Every stage, question, check and checklist rule applies.

The checkout is `<folder>/analytics-agent-accelerator`, the path the welcome named. Reuse it
when its origin is that repository; otherwise clone there with `git clone --depth 1`. Never
search other folders or the home directory for a copy. Without git, ask the person to
download the repository into that path.

### 3. One line before each step

Before each checklist step, print its line from this table, filling in actual counts. Each
links back to the welcome. The checklist's one-line result still ends each step.

| Step | Line before it |
| --- | --- |
| Read your sources | "Reading the dashboard and the past queries to learn how Fixit measures a job. Under a minute." |
| Choose focus and goal | "Your first choice. Atlan builds definitions around what you care about; margin is Rob's question." |
| Write metric definitions | "Turning what Fixit measures into metric definitions: a name, a calculation and its rules." |
| Check each definition's SQL | "Running each definition on Fixit's data to make sure it returns sensible numbers. About 3 minutes." |
| Review and choose where to save | "Your second choice: where to save. Saving needs you signed in to your company's Atlan." |
| Ready for questions | "Now the payoff: Rob's question." |

After reading sources, add one sentence on what they revealed, such as which questions the
analysts asked most. Never leave a minute without a line; follow [progress](progress.md).
Never say candidate, engine, reconciliation, hold or Registry to the person.

### 4. The two choices

Focus and goal: follow [intent](intent.md), but skip its source summary; the welcome gave
it. List job margin first, labelled "(recommended for the demo)", and for the goal,
answering Rob's question first. The person can pick anything else.

Where to save: before sign-in, say "A browser window opens; sign in to your company's Atlan
there." After sign-in, name the tenant and workspaces the result shows, never a guess.
Suggest the person's own workspace: "Your personal workspace keeps the demo out of your
team's way." When definitions already exist, say which, how many and where, and that they
were reused rather than copied.

### 5. The first metric: teach how to read it

Show gross margin first, with setup's preview fields, then one line: "The calculation is the
formula, the population is which jobs count, the check says it ran on the data."

### 6. The payoff and the bridge (at most 160 words)

Answer Rob's question through the ask route: the job, the amount, what ate it, and the
definition used in one line. Offer two more questions the yard supports, such as rework cost
by job or which jobs finished late. Close with:

"With your own data, the dashboard is yours, the query history comes from your warehouse,
and the workspace is your team's. Run /atlanai-analytics:start and choose Use my own data."
