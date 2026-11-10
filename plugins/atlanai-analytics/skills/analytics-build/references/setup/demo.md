# Demo data or your own: the start choice

Read on a bare `start`: no link, folder, file or question supplied, and no saved run to
resume. A supplied source or a saved run skips this file and follows [shared setup](../setup.md).

## Offer the choice before any work

Ask one native question before surveying anything. Its visible prompt says what the person
will end with, so the choice makes sense to someone new:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on. Try it on a demo first,
or on your own data?"

| Option | Description |
| --- | --- |
| Demo: Fixit & Co. | Build a job profit agent for a made-up building firm, from its Power BI dashboard: which jobs lost money and why. About 15 minutes. |
| Demo: Ticketfuddle | Build a ticket fees agent for a made-up ticketing firm, from its Tableau dashboard: how much of what fans paid went on fees. More data, about 15 to 20 minutes. |
| Use my own data | A dashboard link, local folder or BI export of yours. |

"Use my own data" follows [shared setup](../setup.md) and asks for the source once. Each
demo has its own file, with its welcome and its facts:

| Demo | File |
| --- | --- |
| Fixit & Co. | [the builder's yard](demo-yard.md) |
| Ticketfuddle | [the box office](demo-box-office.md) |

Everything below is shared by both. Where it says `<firm>`, `<agent>` or
another value in angle brackets, take it from the chosen demo's facts table or from the
run; the examples below use Fixit's.

## Try the demo: a guided tour

**What the demo is for.** The person is here to understand what Atlan Analytics does by
watching it happen on a small example. Success is not that setup finished. Success is
that they can say what they built, why it matters, and that they felt it work. The blocks
printed at each moment (the welcome, the collection review and celebration, the ready screen)
are the product; the commands between them are plumbing. A run that builds everything but
skips a block has failed, however clean the build. Read every later instruction with this
in mind: when a step could be done faster by saying less, say what the person needs.

How the person feels at each point decides what to say. They arrive unsure what this is,
get anxious when something runs with no explanation, lose the thread when a result appears
with no reason to care, worry at sign-in about touching their company's data, and at the
end want to know what they made and what to do with it. Each section below answers one of
those. When in doubt, say what just happened, what it means, and what comes next.

Assume the person knows nothing: not the firm, the data, what Atlan does, or why they will
sign in. The demo is about one experience: watching existing sources become context. The
context is made of metrics, each explained in plain words when it first appears. Before anything runs, they know what will happen, why,
and what they will do. The moment files supply the full visible content and its delivery method. Use their
reply text or native question payload exactly as directed; never replace a moment with
a tool description or status line. This is the [presentation flow](presentation.md) used
throughout setup.

### 1. The welcome: right after the demo is chosen

Read the chosen demo's file, then print its welcome block before any other tool call, file
search or clone: whole and as written, with `<folder>` replaced by the current working
folder's absolute path. The welcome names the demo's live tour page; keep that link exactly
as written, on its own line, so it can be clicked.

Then ask one native question, "Ready to build?":

| Option | Description |
| --- | --- |
| Start building | Begin step 1: download the demo files into this folder and read them. |
| Not now | Stop here; /atlanai-analytics:start picks it up later. |

The person may open the tour while the build runs; do not wait for them to finish looking.
On "Not now", stop and repeat the tour link in one line, so they can explore meanwhile.
After "Start building", print the first step tracker (section 3), then read setup's
references.

### 2. Run setup on the demo repository

Run [shared setup](../setup.md) exactly as if the person had asked: "Use Atlan Analytics to
set up the `<source folder>` folder of this repository so I can ask questions about its
data: https://github.com/atlanai/analytics-agent-accelerator, start from scratch." Start a
new run. Every stage, question, check and checklist rule applies. The repository holds
both demos; survey and build from the chosen demo's source folder only.

The checkout is `<folder>/analytics-agent-accelerator`, the path the welcome named. Reuse it
when its origin is that repository, after `git -C <checkout> pull --ff-only` so the demo is the
published one; if it cannot fast-forward, say so and ask before using it. Otherwise clone there
with `git clone --depth 1`. Never search other folders or the home directory for a copy.
Without git, ask the person to download the repository into that path.

### 3. Show the steps every time one starts

Follow [the setup checklist](checklist.md) in chat and the host's task list when
available. It defines the full tracker, step labels, actual counts and next-step marker.
The demo's second step is "Choose what to build from" because its business intent is preset.
Show the tracker's full list at every step change, including on resume.

### 3a. Say what and why at every moment

The person should never wonder what is happening or why. Before each moment below, print
two short lines as an ordinary reply, at most 35 words together, in this shape:

```
**Doing:** Reading Fixit's dashboard and 110 past queries.
**Why:** They show how Fixit already measures a job, so the context matches the firm instead of my guess.
```

Fill in actual counts and names from the chosen demo. Use these, in order; the first of a
step goes under its tracker. Skip a moment that does not happen in this run.

| Moment | Doing | Why |
| --- | --- | --- |
| Download | Downloading `<firm>`'s demo files into this folder. | The dashboard, past queries and data are in those files, and I read them on your machine. |
| Read sources | Reading the dashboard's tiles and the past queries. | They show how `<firm>` already measures itself, so the context matches the firm instead of my guess. |
| Sources question | Asking what to build from. | More sources give fuller context; the dashboard alone is faster. |
| Load data | Loading `<firm>`'s tables into a small database on this machine. | Each metric gets run on real rows, so you see numbers and not only words. |
| Build | Building the metrics and queries from all the sources you chose. | They capture the rules behind the questions you want to ask. |
| Check | Running every metric and query on `<firm>`'s data. | One that returns nothing or nonsense would give wrong answers later; this catches it now. |
| Sign-in | Opening the Atlan Trident sign-in in your browser. | Saving needs to know who you are and which workspaces you can save to. |
| Workspace question | Asking which workspace to save to. | A workspace is where your team, and the agents they use, find this context. |
| Save | Saving the context to the workspace you chose and releasing it. | Agents only find released metrics and queries, so releasing is what lets the agent and your colleagues use them. |
| Ready | Your `<agent>` is ready. Showing what you can do next. | You can ask AI, build a dashboard or continue building on your real data. |

After a moment ends, give its result in one line with counts, in the past tense. Print the
first tracker right after "Start building", before reading setup's references or cloning.
Never use file names, helper names or query-shape counts in these lines. After reading
sources, add one sentence on what they revealed, such as which questions the analysts
asked most. Never leave a minute without a line; follow [progress](progress.md).

**No named people.** Never name a person at the firm in the demo's own words: the brief,
the steps, a question or an example. Use the firm's name and the demo's question. A name
from the data may appear in an answer only when the person's question asks who, and never
a customer's personal details.

**Two words for what gets built: metrics and queries.** The whole is the context. It holds
metrics (a number the firm cares about, with its rule) and queries (a ready-made question
about the data, such as margin by job). Count them that way every time: "Context built: 7
metrics and 12 queries", "saving 3 new metrics". Never call them pieces, calculations,
KPIs, measures, definitions, saved questions or saved SQL; the dashboard has tiles, and
what analysts ran is past queries. Say "the context behind this answer". Never say
candidate, engine, reconciliation, hold or Registry to the person.

### 4. No focus or goal questions: ask what to build from

The demo's focus is already chosen, so skip both [intent](intent.md) questions and its
source summary. Once the run exists, record the preset; this replaces intent's rule against
recording an unselected answer, for the demo only:

`PROJECT journey domain --answer "<focus to record>"`

`PROJECT journey purpose --answer "<purpose to record>"`

After reading sources, ask one native question. Its visible prompt, with actual counts and
the demo's facts:

"I read `<firm>`'s sources: `<dashboard>`, `<N>` past queries and `<N>` data tables. Next I
turn them into metrics. A metric is `<what a metric is, by example>`. Metrics are how the
context is stored, so the agent uses the same rule every time. I will build and check the
full collection from your chosen sources, then show you the results. What should I build them from?"

This is the first time the person hears the word metric, so the prompt keeps all of it.

| Option | Description |
| --- | --- |
| Dashboard and past queries (recommended) | The dashboard's 4 numbers, plus what `<firm>`'s analysts actually ask. More metrics and queries, and a few minutes longer. |
| Dashboard only | The dashboard's 4 numbers as metrics. Fastest. |

Scope the run to the answer through [inspect and scope](inspect-scope.md). With "Dashboard
only", author from the dashboard's measures alone and leave the past queries out. The data
tables are used either way, to check the context. Continue through the complete build and
checks after this choice, with factual progress updates. The next demo decision is where
to keep the completed context. Ask during the build only for a genuine missing input or blocker.

### 5 and 6. Read each moment's file when it arrives, not before

The welcome prints in full because this file is read right before it. The later blocks sit
too far back by the time they are needed, and a run that relies on memory skips them. So
each has its own short file, read at the moment it is used:

| When | Read, then do what it says before anything else |
| --- | --- |
| Step 4 ends, every check has run | [context built and where to keep it](demo-save.md) |
| The context is saved, or kept on this machine | [ready for questions](demo-ready.md) |

Each of those files takes its values from the chosen demo's facts table, so open the
demo's file again with it. Your reasoning is never shown to the person. Anything they must read is reply text or a
question's prompt; a note in your thinking does not count.
