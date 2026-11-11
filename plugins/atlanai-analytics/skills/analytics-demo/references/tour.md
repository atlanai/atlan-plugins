# Demo tour rules

**What the demo is for.** The person is here to understand what Atlan Analytics does by
watching it happen on a small example. Success is not that setup finished. Success is
that they can say what they built, why it matters, and that they felt it work. The blocks
printed at each moment (the welcome, the collection review and
celebration, the ready screen, the next-action choice) are the product; the commands between
them are plumbing. A run that builds everything but skips a block has failed, however clean
the build. Read every later instruction with this
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
a tool description or status line. This is the [presentation flow](../../analytics-build/references/setup/presentation.md) used
throughout setup.

## The welcome: right after the demo is chosen

Read the chosen demo's file, then print its welcome block before any other tool call, file
search or copy: whole and as written, with `<folder>` replaced by the current working
folder's absolute path. The welcome names the demo's live tour page; keep that link exactly
as written, on its own line, so it can be clicked.

Then ask one native question, "Ready to build?":

| Option | Description |
| --- | --- |
| Start building | Begin step 1: download the demo files into this folder and read them. |
| Not now | Stop here; /atlanai-analytics:analytics-demo picks it up later. |

The person may open the tour while the build runs; do not wait for them to finish looking.
On "Not now", stop and repeat the tour link in one line, so they can explore meanwhile.
After "Start building", print the first step tracker, then do step 1's work as the skill
lists it.

## Say what and why at every moment

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
| Copy | Copying `<firm>`'s demo files into this folder. | The dashboard, past queries and data are in those files, and I read them on your machine. |
| Read sources | Reading the dashboard's tiles and the past queries. | They show how `<firm>` already measures itself, so the context matches the firm instead of my guess. |
| Load data | Loading `<firm>`'s tables into a small database on this machine. | Each metric gets run on real rows, so you see numbers and not only words. |
| Sources question | Asking what to build from. | More sources give fuller context; the dashboard alone is faster. |
| Metrics found | Listing the metrics your sources hold. | You see what the context will contain before I write each rule. |
| Build | Writing the rule for each metric and query from the sources you chose. | `<why build them>` |
| Check | Running every metric and query on `<firm>`'s data. | One that returns nothing or nonsense would give wrong answers later; this catches it now. |
| Sign-in | Opening the Atlan Trident sign-in in your browser. | Saving needs to know who you are and which workspaces you can save to. |
| Workspace question | Asking which workspace to save to. | A workspace is where your team, and the agents they use, find this context. |
| Save | Saving the context to the workspace you chose and releasing it. | Agents only find released metrics and queries, so releasing is what lets the agent and your colleagues use them. |
| Ready | Your `<agent>` is ready. Showing questions this context can answer. | Pick a question to see an answer and the rules behind it. |

After a moment ends, give its result in one line with counts, in the past tense. Print the
first tracker right after "Start building", before reading setup's references or cloning.
Never use file names, helper names or query-shape counts in these lines. After reading
sources, add one sentence on what they revealed, such as which questions the analysts
asked most. Never leave a minute without a line; follow [progress](../../analytics-build/references/setup/progress.md).

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

## Step 2: ask what to build from

The demo's focus is already chosen, so skip both [intent](../../analytics-build/references/setup/intent.md)
questions and its source summary; step 1 recorded the preset. This replaces intent's rule
against recording an unselected answer, for the demo only.

After loading the data, ask one native question. Its visible prompt, with actual counts and
the demo's facts:

"I read `<firm>`'s sources: `<dashboard>`, `<N>` past queries and `<N>` data tables. Next I
turn them into metrics. A metric is `<what a metric is, by example>`. Metrics are how the
context is stored, so the agent uses the same rule every time. I build and check the
context from your selected sources. What should I build it from?"

This is the first time the person hears the word metric, so the prompt keeps all of it.

| Option | Description |
| --- | --- |
| Dashboard and past queries (recommended) | The dashboard's 4 numbers, plus what `<firm>`'s analysts actually ask. More metrics and queries, and a few minutes longer. |
| Dashboard only | The dashboard's 4 numbers as metrics. Fastest. |

Scope the run to the answer through [inspect and scope](../../source-survey/references/inspect-scope.md). With "Dashboard
only", author from the dashboard's measures alone and leave the past queries out. The data
tables are used either way, to check the context. Continue through the complete build and
checks after this choice, with factual progress updates and no intermediate metric-list
celebration or build confirmation. The next decision is where to keep the completed
context. Ask during the build only for a genuine missing input or blocker.
