# The dashboard branch: start from a dashboard

Read when the person picks "Start from a dashboard" in [the interview](intent.md)'s card 1,
or names a dashboard link or file instead. For this run, this file replaces the steps in
`SKILL.md`, the shared checklist's step names and the interview's "Build your agent?" confirmation.
Where it is silent, the shared files apply. The discovery and known-tables branches and the
demo never read it, and nothing here changes how metrics are generated.

What the person should feel throughout: the build is moving, they can see each metric as it is
made, and they can trust what they see. Every rule below serves that.

## The checklist: ten items

Right after card 1, put these in the host's task list: item 1 ticked, since card 1 answered it, and
item 2 in progress.
`PROJECT checklist --flow dashboard` returns them before a run exists. Once the run exists,
record the branch with `PROJECT journey agentic`; from then on `PROJECT checklist` and every
`PROJECT journey` result return this list from the run.

| # | Item | Waits for the person |
| --- | --- | --- |
| 1 | Choose what your agent answers: from your dashboard | No: answered in card 1, so it shows ticked |
| 2 | Pick report and pages | Yes |
| 3 | Check what your agent can use | Only when a chosen tile or table would be left out |
| 4 | Build your context: metrics and saved queries | No |
| 5 | Merge duplicate metrics | No |
| 6 | Add detailed information to each metric and saved query | No |
| 7 | Validate the context on your data | Yes: whether to run the tests |
| 8 | Choose where to store your context | Yes |
| 9 | Ask your agent a question | Yes |
| 10 | Build a dashboard from its answers (optional) | Yes |

Items 4 to 6 are the build, ticked one by one as the engine finishes each part; each shows its time
estimate once the count going into it is known ("about 4 min").

- **Update the task list the moment an item ends**: in the same message as the call that ends
  it (`PROJECT journey report`, `PROJECT journey readiness`, the build's start, `PROJECT journey
  checks`, `PROJECT journey asked`), before any other tool call. A list that lags looks stuck.
  When item 3 ends without a question, that same message also starts item 4.
- **Keep the list on screen in every turn.** The Claude desktop app shows the list under the
  turn that sent it, so a turn with no list update shows none. Every turn in this branch,
  including one woken by a helper finishing or by the build's wait, sends the whole list from
  `PROJECT checklist` in its first message: the plan tool's whole list in the desktop app,
  TodoWrite (or TaskUpdate) with every item in Claude Code's terminal.
- **Never tick an item from memory.** Item 7 ticks only when the helper says so: the checks
  recorded their results, or the person chose to skip them.
- A finished item shows its result after a colon, as the helper returns it: "Check what your agent
  can use: 2 of 5 tiles, targets not set". Never write a result yourself.
- Item 10 ticks when a page is built or the person declines one, recorded as
  [the dashboard](../../../analytics-ask/references/dashboard.md) says.

## 1. Pick report and pages

The scope is chosen before anything is checked, so item 3 inspects only what the person cares
about.

1. **Start the reader install** the moment the link's host or the file's extension says Power
   BI, before any question: see [the reader install](#the-reader-install). For a Power BI
   report link, step 2's command installs it too: start that instead.
2. **Sign in** where the link needs it, as [the interview](intent.md#a-link-that-needs-sign-in)
   says. Signing in is a conversation moment, so this session runs it, not a background helper.
   For a Power BI report link it is one command that also installs the reader, fetches the
   report and translates its tiles: `ENGINE powerbi-report` with `--run <run-dir>/engine`, as
   [the metrics module](../../../metrics-generation/references/metrics-engine.md#power-bi-from-a-report-link)
   says. This session starts it as a background command that wakes it on exit (in Claude
   Code, Bash with `run_in_background`), never in a terminal tab and never followed with waits.
   Say the interview's line as it starts, then carry on with anything that does not need the
   report. Say the person is signed in only once its result's `sign_in` is `signed_in`.
3. **Fetch what can be picked.** A background helper fetches the link or reads the file and
   returns names and counts only: for a **workspace link**, its reports with their page and
   measure counts; for a **report link or file**, its pages with their tile counts. For a
   Power BI report link, step 2's command already fetched it. When it exits, the person has
   waited since they signed in, so the first thing is one chat line, before any other call:
   "Signed in, and your report is read. Here's what it holds." (with `sign_in` `saved`, "Your
   report is read. Here's what it holds."). Then `PROJECT tiles --engine-dir engine` gives the
   pages and their tile counts. A fetch of
   the same workspace from earlier is reused only when it is under an hour old, and the line
   says so: "Using your workspace as it was at 19:01." Otherwise fetch again: a report
   edited since would be built from old definitions.
4. **Ask.** A workspace link asks "Which report should I build your agent from?" first, the reports as options
   with their counts in each description. Then one multi-select question, "Which pages matter
   to you?": up to three pages as options, largest first, plus "All pages". With more pages,
   the question's own text lists them all and the free-text entry takes any page by name.
   Supporting pages (drillthrough targets, tooltips, hidden pages) go with the pages that use
   them.
5. **Tick item 2 at once.** In the turn the person answers the pages question, before any
   other work, set the task list: item 2 completed, reading "Pick report and pages: `<report>`:
   `<pages, or all pages>`", and item 3 in progress. When the run exists (for a link, `PROJECT survey` on the working folder makes it), record the warehouse
   card 1 found with `PROJECT warehouse`, as [connecting a warehouse](../../../source-survey/references/connect.md)
   step 5 says: the tests are refused without it. Then record
   `PROJECT journey report --answer "<report>: <pages, or all pages>"` first and set the list
   it returns. When it does not, set the list yourself and record the report the moment the
   run exists. Never leave item 2 open while other work runs.

The build reads the chosen report whole, because generation is unchanged. The chosen pages set
what item 3 inspects and which metrics the review leads with.

## 2. Check what your agent can use

Never guess which tiles can be built: the build's own translation says, in seconds. In
background helpers, several at once:

- **The real translation.** Prepare the build now, with the report as its source, as
  [the metrics module](../../../metrics-generation/references/metrics-engine.md) says, and
  advance it to its first written step. Reading the report and translating every tile runs
  without any answers and takes seconds. Then `PROJECT tiles --engine-dir engine --pages
  <chosen pages>` returns, page by page, how many tiles became queries and each that could not,
  with why. Item 4 carries on from this same build: nothing is done twice. For a Power BI
  report link, the sign-in command in section 1 already prepared and advanced it.
- **Table access**: each table behind the chosen pages, read on the warehouse with one
  read-only statement.
- **Query history**: whether it is readable for those tables. Say so here, never as a promise
  later.
- **Fixed value lists**: values the report writes into its measures, columns or filters (stage
  names, region lists, status codes) compared with the column's actual distinct values. A value
  in one and not the other is a finding: "Your report lists 6 stages. Your data has 7, including
  Closed Lost - Duplicate."

Then, in one line, the result: "Check what your agent can use: 2 of 5 tiles, targets not set".
Record it with `PROJECT journey readiness --answer "<the result after the colon>"`. With the
result, say:

- Always: "I can read `<N>` of the `<M>` tiles on your pages. A later check can still hold one
  back."
- Only when a tile stays out: "A tile stays out when I can't write SQL that keeps its meaning,
  for example when a filter would be lost."
- Only when past queries cannot be read: "I couldn't read your team's past queries on
  `<warehouse>`, so I'll build your agent from your dashboard alone."

When something they chose would be left out (below), these lines open the question's own text
instead, since text before a question is lost.

The person already chose the report and pages, so a question here is the exception. Ask only
when something they chose would be **left out of the build**: a tile that cannot be built, or a
table that cannot be read. Findings about the data (empty values such as unset targets, one
quarter of data, an out-of-date value list, no readable query history) leave every chosen tile
in the build: they are news, never a question.

- **Every chosen tile can be built**: record the result, say the line with each finding in a
  short sentence of what it means for the person, and start the build in the same message.
- **Something they chose would be left out**: record it with `--waits` added, then ask one
  question whose own text names each tile that stays out and why, in the helper's words. The
  side page is not open yet, so the one line before the question is: "The question below names
  each tile that stays out, and why."

  | Option | Description |
  | --- | --- |
  | Continue with `<N tiles>` (Recommended) | "The rest stay out. The side page will say why." |
  | Change the pages | "Pick other pages and I'll check again." |
  | Stop here | "Nothing is built. Your choices are saved for next time." |

  On their answer, record the same result again without `--waits`, then do what they chose.

Either way, as the build starts, say: "I'll build your `<report>` agent from the `<N>` tiles I
can read in your report. About `<time>`. I'll offer to test its metrics at the end." The time
is the sum of the rough times in item 4's table, scaled to the report.

## 3. Build your context: metrics and saved queries

[Build and check](build-check.md) and [the metrics module](../../../metrics-generation/references/metrics-engine.md)
run as they are, every step in a background helper (below). The side page is the same as in
every flow: what the run made, metrics first and then queries, each in its state (Found,
Defined, Tested, Not tested, Needs your review) and opening its profile, then the tiles that could not be built.
It opens once the queries are in plain words, when `visual next` names `connect`. When it opens,
say once: "A page has opened beside this chat. It lists each metric and saved query as it's
found, and why anything is left out."

**The chat is the same as in every flow**, as [drafts as they land](progress.md#drafts-as-they-land)
sets out: before the side page opens, each step's chip when it ends; once it is open, the task
list carries the build where you keep one, with the one line `visual next` gives at each step's
end, and with no task list each step's end shows its chip. Never a "still" line. A step that
starts with no chip naming it (the review of the translations, the questions for the tiles) gets
its start line from the table below, once.

As the build starts, start the helper and run `PROJECT drafts --wait --engine-dir engine` in the
foreground in the same turn, as the readiness step's result says. Helpers advance every step
here, so the wait names each chip as it becomes due. The review of the translations saves
nothing until it ends: its start line gives its rough time.

Rough times, from a 37-tile report; scale by the number of tiles:

| Step | Start line | Roughly |
| --- | --- | --- |
| Review of the translations | "Checking each tile's SQL against what your report calculates. About `<time>`." | 5 minutes |
| Questions for the tiles | "Writing the question each tile answers. Each becomes a saved query: the question, kept with the SQL that answers it." | 4 minutes |
| Finding metrics | "Now finding the metrics behind each question. Each is defined once. Your agent answers from each metric's definition, so the same question gets the same number and you can trace it." | 4 minutes |
| Merging | "Merging duplicate metrics across tiles into one metric each, so the count drops." | 3 minutes |
| Adding information | "Adding detailed information to each metric: what it counts and what it leaves out. About `<time>`." | 10 minutes |
| Links | "Linking metrics that build on each other." | 1 minute |

## 4. Validate the context on your data: the person's choice

When the build ends, ask one question; the checks are optional and theirs to choose:

"Test your agent's metrics on `<warehouse>`? Each is worked out once on your data, so you see
its real number before you choose where to keep it. About `<time>`."

| Option | Description |
| --- | --- |
| Test them (Recommended) | "Each metric and saved query is tested once, read-only." |
| Skip for now | "Nothing is tested. Each metric is first worked out when you ask for it. A save to Atlan would hold untested metrics." |

Record their answer with `PROJECT journey checks --answer run` or `--answer skip`. Never record
a skip they did not choose, and never say "as you asked" about anything they did not ask.

- **Run**: run every check as [build and check](build-check.md) says. On a warehouse,
  `PROJECT check-query` and `check-metric` answer `run_on_warehouse` with the one statement to
  run: send it through the chat's read-only SQL tool for that warehouse, the one item 3 already
  used, then run the same command again with `"result"`, the rows it returned or why it failed,
  so the side page shows each one tested. A failed statement is a result too, never a reason to
  stop checking.
- **Skip**: item 7 reads "skipped, as you chose"; go straight on.

Either way, item 7 ends with the build's card: `PROJECT visual build --widget`, drawn before
the save question every time, so the build always closes on a result the person can see.
When anything is held back, say once with the card: "Held back means it stays out of your
agent, so no answer uses it. To fix one, tell me here and I'll rebuild it."

## 5. Choose where to store your context

`PROJECT question save --widget` composes the save question for this branch: what was built,
whether it ran on the data, what was held back and why, and the tiles that could not be built,
one line each. Ask exactly that text, with its options. Add nothing to it: the side page holds
every detail.

## Background helpers do the work; this session talks

This session's own tool calls are only: the questions, the sign-in, the task list, starting and
following helpers and the build's wait, drawing chips and cards, and the `PROJECT journey` records.
Everything else runs in background helpers (in Claude Code, a background subagent per piece of
work), several at once where the work splits:

- installing the reader, fetching the workspace and staging the chosen report;
- the checks in item 3, the build's preparation and its translation included;
- every generation step: each written step as [drafts as they land](progress.md#drafts-as-they-land)
  describes, and also the translation review, the tile questions and the links between metrics;
- packing the finished build into the run, and every repair after a refused answer or a failed
  check;
- the checks on the warehouse. Where only this session can reach the warehouse (a chat SQL tool
  a helper cannot call), this session sends those statements and nothing else.

Each helper's brief is its whole prompt: what to do, the files it needs, that it has no chat of
its own, and what to return: one result line plus anything that needs the person. Name each
helper for what it does in plain words ("Reading your report", "Checking your tables"), since
the app shows the name.

**Large step files** (200 to 400 KB): the helper reads the step's file in parts (the Read tool
with an offset and a limit), answers a batch of items, saves that batch, then reads on. It never
needs the whole file in view. If it runs short of room, it returns what is saved, and this
session starts a fresh helper on the rest: the saved batches say where to pick up.

A host with no background helper does the same work in this session and still shows only the
lines above.

## The reader install

Start installing the Power BI reader as soon as the link is recognised as Power BI, in a
background helper, while item 2's questions are asked: `ENGINE setup` with `--feature powerbi`
and the warehouse's own feature, as [the metrics module](../../../metrics-generation/references/metrics-engine.md)
says. First reuse a compatible runtime: one under `.atlan-metrics/` built for this plugin
version with those features, or the folder a refusal's `fix` names. Say one line while it
installs: "Installing a Power BI reader on this machine so I can read your report. About 2
minutes. Meanwhile, I'll ask which pages matter."
For a Power BI report link, the sign-in command in section 1 installs the reader itself, so no
separate install starts.

## Written for a first-time builder

The person is a builder in another organization, using this for the first time. Every line they
see, at every moment:

- **No internal words**: engine, gate, helper, subagent, export, hold, alias, run directory,
  candidate, Registry, statement, staged copy, packing, draft, chip, so-far, or a file
  or command name. Say what it means: "the build", "a step", "held back", "needs your review",
  "your report's tiles", "a metric", "merge duplicate metrics", "its definition".
- **Before the build, whenever something is happening, say what it is and why, and roughly how
  long**, once: "Checking that I can read your 9 tables on Snowflake, so your agent only uses
  data you can read. Read-only, about a minute." A rough range from the work's size; never a percentage, and never that
  something is still going. From the build's start, the chips, the task list and the line
  `visual next` gives say it: add none of your own.
- **Only what the person needs**: results, waits, decisions, and problems that change something
  for them. A step that went as expected gets its start line and its chip, nothing more.
- **One line per problem**, with the count and what happens next, in the grammar the count
  needs: "`<N>` metrics name a specific person in their SQL. They're held back until you
  confirm the value is fine to keep." or "`<N>` metrics didn't pass their check. I'm rewriting
  them, and the other `<M>` are done."
