# The dashboard branch: make my dashboard agentic

Read when the person picks "Make my dashboard agentic" in [the interview](intent.md)'s card 1,
or names a dashboard link or file instead. For this run, this file replaces the seven steps in
`SKILL.md`, the shared checklist's step names and the interview's "Build it?" confirmation.
Where it is silent, the shared files apply. The discovery and known-tables branches and the
demo never read it, and nothing here changes how metrics are generated.

What the person should feel throughout: the build is moving, they can see each metric as it is
made, and they can trust what they see. Every rule below serves that.

## The checklist: nine items

Right after card 1, put these in the host's task list with item 1 in progress.
`PROJECT checklist --flow dashboard` returns them before a run exists. Once the run exists,
record the branch with `PROJECT journey agentic`; from then on `PROJECT checklist` and every
`PROJECT journey` result return this list from the run.

| # | Item | Waits for the person |
| --- | --- | --- |
| 1 | Pick report and pages | Yes |
| 2 | Check what can be built | Only when a chosen tile or table would be left out |
| 3 | Draft metrics from your sources | No |
| 4 | Merge drafts, one metric per number | No |
| 5 | Write each metric's rule | No |
| 6 | Check against the dashboard | Yes: whether to run the checks |
| 7 | Save or keep local | Yes |
| 8 | Ask a question | Yes |
| 9 | Build a page from the metrics (optional) | Yes |

Items 3 to 5 are the build, ticked one by one as the engine finishes each part; each shows its time
estimate once the count going into it is known ("about 4 min").

- **Update the task list the moment an item ends**: in the same message as the call that ends
  it (`PROJECT journey report`, `PROJECT journey readiness`, the build's start, `PROJECT journey
  checks`, `PROJECT journey asked`), before any other tool call. A list that lags looks stuck.
  When item 2 ends without a question, that same message also starts item 3.
- **Keep the list on screen in every turn.** The Claude desktop app shows the list under the
  turn that sent it, so a turn with no list update shows none. Every turn in this branch,
  including one woken by a helper finishing or by the build's watch, sends the whole list from
  `PROJECT checklist` in its first message: the plan tool's whole list in the desktop app,
  TodoWrite (or TaskUpdate) with every item in Claude Code's terminal.
- **Never tick an item from memory.** Item 4 ticks only when the helper says so: the checks
  recorded their results, or the person chose to skip them.
- A finished item shows its result after a colon, as the helper returns it: "Check what can be
  built: 2 of 5 tiles, targets not set". Never write a result yourself.
- Item 7 ticks when a page is built or the person declines one:
  `PROJECT journey dashboard --answer built` or `--answer declined`.

## 1. Pick report and pages

The scope is chosen before anything is checked, so item 2 inspects only what the person cares
about.

1. **Start the reader install** the moment the link's host or the file's extension says Power
   BI, before any question: see [the reader install](#the-reader-install).
2. **Sign in** where the link needs it, as [the interview](intent.md#a-link-that-needs-sign-in)
   says. Signing in is a conversation moment, so this session runs it, not a background helper.
3. **Fetch what can be picked.** A background helper fetches the link or reads the file and
   returns names and counts only: for a **workspace link**, its reports with their page and
   measure counts; for a **report link or file**, its pages with their tile counts. A fetch of
   the same workspace from earlier is reused only when it is under an hour old, and the line
   says so: "Using the copy of your workspace from 19:01." Otherwise fetch again: a report
   edited since would be built from old definitions.
4. **Ask.** A workspace link asks "Which report should I use?" first, the reports as options
   with their counts in each description. Then one multi-select question, "Which pages matter
   to you?": up to three pages as options, largest first, plus "All pages". With more pages,
   the question's own text lists them all and the free-text entry takes any page by name.
   Supporting pages (drillthrough targets, tooltips, hidden pages) go with the pages that use
   them.
5. **Record** `PROJECT journey report --answer "<report>: <pages, or all pages>"`.

The build reads the chosen report whole, because generation is unchanged. The chosen pages set
what item 2 inspects and which metrics the review leads with.

## 2. Check what can be built

Never guess which tiles can be built: the build's own translation says, in seconds. In
background helpers, several at once:

- **The real translation.** Prepare the build now, with the report as its source, as
  [the metrics module](../../../metrics-generation/references/metrics-engine.md) says, and
  advance it to its first written step. Reading the report and translating every tile runs
  without any answers and takes seconds. Then `PROJECT tiles --engine-dir engine --pages
  <chosen pages>` returns, page by page, how many tiles became queries and each that could not,
  with why. Item 3 carries on from this same build: nothing is done twice.
- **Table access**: each table behind the chosen pages, read on the warehouse with one
  read-only statement.
- **Query history**: whether it is readable for those tables. Say so here, never as a promise
  later.
- **Fixed value lists**: values the report writes into its measures, columns or filters (stage
  names, region lists, status codes) compared with the column's actual distinct values. A value
  in one and not the other is a finding: "the report lists 6 stages; the data has 7, including
  Closed Lost - Duplicate".

Then, in one line, the result: "Check what can be built: 2 of 5 tiles, targets not set".
Record it with `PROJECT journey readiness --answer "<the result after the colon>"`.

The person already chose the report and pages, so a question here is the exception. Ask only
when something they chose would be **left out of the build**: a tile that cannot be built, or a
table that cannot be read. Findings about the data (empty values such as unset targets, one
quarter of data, an out-of-date value list, no readable query history) leave every chosen tile
in the build: they are news, never a question.

- **Every chosen tile can be built**: record the result, say the line with each finding in a
  short sentence of what it means for the person, and start the build in the same message.
- **Something they chose would be left out**: record it with `--waits` added, then ask one
  question whose own text names each tile that stays out and why, in the helper's words:

  | Option | Description |
  | --- | --- |
  | Build what can be built (recommended) | "The `<N>` tiles that translated. The rest stay out, with their reasons on the side page." |
  | Change the pages | "Pick other pages and I'll check again." |
  | Stop here | "Nothing is built. Your choices are kept." |

  On their answer, record the same result again without `--waits`, then do what they chose.

## 3. Build metrics and queries

[Build and check](build-check.md) and [the metrics module](../../../metrics-generation/references/metrics-engine.md)
run as they are, every step in a background helper (below). The side page is the same as in
every flow: what the run made, metrics first and then queries, each in its state (Draft, Merged,
Written, Checked, Failed) and opening its profile, then the tiles that could not be built.

**The chat is the same as in every flow**, as [progress](progress.md) sets out: each step's start
on its Doing line with roughly how long, from its size; its chip when it ends; and while a step
runs, the so-far chip `visual progress` gives with its line, unchanged. Never a "still" line.

As the build starts, before the first helper, arm `PROJECT drafts --follow --every-chip
--engine-dir engine` as a background watch (in Claude Code, the Monitor tool), and re-arm it
whenever it stops before the build ends. It names each chip as it becomes due, since helpers
advance every step here, and replaces the watch that
[drafts as they land](progress.md#drafts-as-they-land) arms at the describe step. The tile
translation review saves nothing until it ends, so no so-far chip comes during it: its start
line gives its rough time.

Rough times, from a 37-tile report; scale by the number of tiles:

| Step | Start line | Roughly |
| --- | --- | --- |
| Review of the translations | "Checking each translated tile against the report." | 5 minutes |
| Questions for the tiles | "Writing the business question each tile answers." | 4 minutes |
| Describing | "Describing each query in plain words." | 2 minutes |
| Draft metrics | "Finding the metrics in each query. They show on the side page as they are found." | 4 minutes |
| Merging | "Merging drafts that measure the same number." | 3 minutes |
| Rules | "Writing each metric's rule: what it counts and what it leaves out." | 10 minutes |
| Links | "Linking metrics that build on each other." | 1 minute |

## 4. Check against the dashboard: the person's choice

When the build ends, ask one question; the checks are optional and theirs to choose:

"Run each metric and query on `<warehouse>`? It shows each one's real number and catches a rule
that returns nothing, and takes about `<N>` minutes."

| Option | Description |
| --- | --- |
| Run the checks (recommended) | "Each metric and query runs once, read-only." |
| Skip for now | "Save without running them. The numbers stay untested until you ask." |

Record their answer with `PROJECT journey checks --answer run` or `--answer skip`. Never record
a skip they did not choose, and never say "as you asked" about anything they did not ask.

- **Run**: run every check as [build and check](build-check.md) says. When `PROJECT check-query`
  or `check-metric` refuses a warehouse route ("use the recorded read-only warehouse tool"),
  that is the route: send the same statements through the chat's read-only SQL tool for that
  warehouse, the one item 2 already used, and record each result. A refusal is never a reason
  to stop checking.
- **Skip**: item 4 reads "skipped, your choice"; go straight on.

Either way, item 4 ends with the build's card: `PROJECT visual build --widget`, drawn before
the save question every time, so the build always closes on a result the person can see.

## 5. Save or keep local

`PROJECT question save --widget` composes the save question for this branch: what was built,
whether it ran on the data, what was kept back and why, and the tiles that could not be built,
one line each. Ask exactly that text, with its options. Add nothing to it: the side page holds
every detail.

## Background helpers do the work; this session talks

This session's own tool calls are only: the questions, the sign-in, the task list, starting and
following helpers and the watch, drawing chips and cards, and the `PROJECT journey` records.
Everything else runs in background helpers (in Claude Code, a background subagent per piece of
work), several at once where the work splits:

- installing the reader, fetching the workspace and staging the chosen report;
- the checks in item 2, the build's preparation and its translation included;
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
background helper, while item 1's questions are asked: `ENGINE setup` with `--feature powerbi`
and the warehouse's own feature, as [the metrics module](../../../metrics-generation/references/metrics-engine.md)
says. First reuse a compatible runtime: one under `.atlan-metrics/` built for this plugin
version with those features, or the folder a refusal's `fix` names. Say one line while it
installs: "Installing the Power BI reader so I can read your report. About two minutes; I'll
ask about pages meanwhile."

## Written for a first-time builder

The person is a builder in another organization, using this for the first time. Every line they
see, at every moment:

- **No internal words**: engine, gate, helper, subagent, export, hold, alias, run directory,
  candidate, Registry, statement, staged copy, packing, possible metric, chip, so-far, or a file
  or command name. Say what it means: "the build", "a step", "kept back for a look", "your
  report's tiles", "a draft metric".
- **Whenever something is happening, say what it is and why, and roughly how long**: "Checking
  your 9 tables on Snowflake, so the build only uses data you can read. About a minute." A
  rough range from the work's size; never a percentage.
- **Only what the person needs**: results, waits, decisions, and problems that change something
  for them. A step that went as expected gets its start line and its chip, nothing more.
