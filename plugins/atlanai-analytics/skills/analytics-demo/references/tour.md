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

Every chat line is written for the person. Say what you're doing when it helps them follow
along or explains a wait. Never write your own working notes: tool names, files to read, what
to do silently or next. The test: would the person miss this line if it were gone? The full
rule is in [the conventions](../../analytics-build/references/conventions.md), under Words the
person reads.

Assume the person knows nothing: not the firm, the data, what Atlan does, or why they will
sign in. The demo is about one experience: watching existing sources become context. The
context is made of metrics, each explained in plain words when it first appears. Before anything runs, they know what will happen, why,
and what they will do. The moment files supply the full visible content and its delivery method. Use their
reply text or native question payload exactly as directed; never replace a moment with
a tool description or status line. This is the [presentation flow](../../analytics-build/references/setup/presentation.md) used
throughout setup.

A native question holds four options. When the demo's "What would you like to do next?" table offers
five, leave Build one on your own data out of the options and end the question's text with one
line: You can also type "Build one on your own data".

## After the first answer

The demo's "What would you like to do next?" question keeps to what the person can't already
see (the team's copy budget, 2026-11-19):

- With `show_widget`, the `answer` card is drawn above it, so the text is one line naming what
  the answer used, such as "Behind this answer: Rework cost (metric), Rework cost by job
  (saved query).", then the question. Do not restate the answer.
- In a terminal, the text is the `answer` visual's lines as it prints them, one sentence on
  what explains the number, then the question.
- On every host, leave out the line that answers come from the context (the card's sources
  say it) and, while the context is only on this machine, the Atlan proof lines and
  any nudge to save. Once it is saved, keep one proof line per metric and query, as
  [definition links](../../analytics-ask/references/definition-links.md) says.
- When Build one on your own data is left out of the options, end with its line, as above.

Check how accurate it is reads: "Test your agent on questions `<firm>`'s analysts already ask,
and fix the rule behind any miss. So far, the tests only checked that each metric returns a value."
Once a check has run, it gives that check's result instead: "Your agent got `<right>` of `<N>`
right on the last check. Run it again after you change a rule."

Change a rule and see what happens reads: "Change one metric's rule, and see your agent's
answer change with it."

### Without a question tool

End the message on the question itself: "What would you like to do next?" on its own line,
then one plain line per option (its name, a colon, its description), and nothing after them:
no table, no "pick one" line. List every option that applies, Build one on your own data
included: the four-option limit, and the line that stands in for a left-out option, are the
question tool's alone. Offer Check how accurate it is until the check has run. Never
tell the person about a helper you ran, skipped or could not run.

## Coming back to a demo

When the working folder already holds a demo's run (`.atlan-metrics/<run>/` whose
`interaction.json` has `"flow": "demo"`) and the person asks to continue it or what they can
do with it, carry on from where it stopped: never show the demo picker, open the demo again or
rebuild. Read `PROJECT journey status`, put its `checklist` in the host's task list, and go to
the step it names: once the context is saved or kept on this machine, that is [step 5](../SKILL.md#5-ask-a-question-then-choose-whats-next)'s ready
question, from `PROJECT question ready` with the demo's facts file as `--data`. Its counts and
questions come from the run, never from memory or the person's message. With two demo runs,
ask which one, naming each by its demo.

## The welcome: right after the demo is chosen

**The welcome is the question's own text**, as
[text before a native question](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost)
requires. Read the chosen demo's file, then, before any other tool call or file search, ask
one native question whose text is that file's welcome lines, whole and as written (they end
with "Ready to build?"). Keep the tour link exactly as written; it is the person's way to see
the firm while the build runs.

| Option | Description |
| --- | --- |
| Start building | I'll add the demo's files to this folder and read them. |
| Not now | Stop here. Ask for the Atlan Analytics demo any time to start again. |

Where there is no question tool at all, print the welcome lines as the last thing in the
message, then the two choices as plain lines, and call no tool after it.

The person may open the tour while the build runs; do not wait for them to finish looking.
On "Not now", stop and repeat the tour link in one line, so they can explore meanwhile.
After "Start building", print the first step tracker, then do step 1's work as the skill
lists it.

## Say what and why at every moment

The person should never wonder what is happening or why. Before each moment below, print
two short lines as an ordinary reply, at most 35 words together, in this shape:

```
**Doing:** Reading Fixit's dashboard and 110 past queries.
**Why:** They show how Fixit already measures a job, so your agent measures it the same way.
```

Fill in actual counts and names from the chosen demo. Use these, in order; the first of a
step goes under its tracker. Skip a moment that does not happen in this run. A moment that
opens with a native question (Workspace question) puts its two lines at the top of that
question's text instead of printing them before it, as plain `Doing:` and `Why:` with no `**`:
question text is shown as written, so Markdown markers show as stars. The Ready moment and
the Sources question have no pair: the ready block says it, and the Sources question keeps to
what the person can't already see (step 2).

| Moment | Doing | Why |
| --- | --- | --- |
| Open the demo | Setting up `<firm>`'s demo in this folder. | Its dashboard, past queries and data are files. I read them here, on your machine. |
| Read sources | Reading the dashboard's tiles and the past queries. | They show how `<firm>` already measures its work, so your agent measures it the same way. |
| Load data | Loading `<firm>`'s tables into a small database on this machine. | Each metric gets tested on these rows, so you see what it comes to. |
| Metrics found | Listing the metrics found in `<firm>`'s sources. | You see what your agent will answer from before I write notes on each one. |
| Build | Adding detailed information to each metric: what it counts and what it leaves out. | `<why build them>` |
| Check | Testing every metric and saved query on `<firm>`'s data. | A test catches one that returns nothing or looks off, before your agent answers with it. |
| Sign-in | Opening Atlan sign-in in your browser. | Atlan needs to know who you are, and which workspaces you can save to. |
| Workspace question | Asking where to save your agent's metrics. | A workspace decides who sees them. Your personal one is only yours. |
| Save | Saving your agent's metrics and saved queries to `<workspace>`. | Once saved, each metric has a page in Atlan that shows how it's worked out. |

After a moment ends, give its result in one line with counts, in the past tense. Print the
first tracker right after "Start building", before reading setup's references or opening the demo, and record
whether you keep a task list, as progress says.
Never use file names, helper names or query-shape counts in these lines. What the sources
revealed goes in the Sources question (step 2). Never leave a minute with nothing new on screen (where you keep a task list,
its count is that something new, so write nothing then); follow [progress](../../analytics-build/references/setup/progress.md):
while subagents write the build, each step's chip (or, once the side page is open, the one line `visual next`
gives) and the side page are what is new: add no line of your own, never a Doing line that repeats what a chip
just said; never that a step is still running, or what will show and when. The Doing and Why pairs
are for the moments above; the metric steps between Build and Check have their chips. The app shows each
command's description: describe each as what it does for the person, never a chip, the engine or a gate. Once the
side page is open, the task list carries the build instead of chips where you keep one, as progress says.

**No named people.** Never name a person at the firm in the demo's own words: the brief,
the steps, a question or an example. Use the firm's name. A name
from the data may appear in an answer only when the person's question asks who, and never
a customer's personal details.

**Two words for what gets built: metrics and saved queries.** Your agent answers from them:
metrics (a number the firm cares about, with its rule) and saved queries (a ready-made question
about the data, such as margin by job). Call the whole your agent, never the context or the run. Count them that way every time: "Your agent answers
from 7 metrics and 12 saved queries", "saving 3 new metrics". Never call them pieces, calculations,
KPIs, measures, definitions, saved questions or saved SQL; the dashboard has tiles, and
what analysts ran is past queries. Say "what this answer is based on". Never say
candidate, engine, reconciliation, hold, Trident or Registry to the person.

## Step 2: ask what to build from

The demo's focus is already chosen, so skip both [intent](../../analytics-build/references/setup/intent.md)
questions and its source summary; step 1 recorded the preset. This replaces intent's rule
against recording an unselected answer, for the demo only.

After loading the data, ask one native question. Its text says only what the person can't
already see (the team's copy budget, 2026-11-19): no Doing or Why lines, and no recap of what
was read, since the `sources` chip says it. In order:

1. In a terminal only, the `sources` chip's lines, as its `visual` result directs.
2. One sentence on what the sources revealed that bears on this choice, with a count: what
   the past queries cover that the dashboard does not, such as "Most of Fixit's 110 past
   queries are about crew hours and labour cost on each job, and the dashboard shows none of
   that."
3. "Your agent answers from metrics. A metric is one number `<firm>` cares about, with one
   definition, so the same question gets the same number." This is where the person first meets the word metric, so it stays on every host.
4. "What should I build your agent from?"

| Option | Description |
| --- | --- |
| Dashboard and past queries (Recommended) | The dashboard's 4 metrics, plus what `<firm>`'s analysts actually ask. More metrics and saved queries, and a few minutes longer. |
| Dashboard only | The dashboard's 4 metrics. Fastest. |

Scope the run to the answer through [inspect and scope](../../source-survey/references/inspect-scope.md). With "Dashboard
only", author from the dashboard's measures alone and leave the past queries out. The data
tables are used either way, to check the context. Continue through the complete build and
checks after this choice, showing what each step produced (`PROJECT visual next`) and no intermediate metric-list
celebration or build confirmation. The next decision is where to keep the completed
context. Ask during the build only for a genuine missing input or blocker.
