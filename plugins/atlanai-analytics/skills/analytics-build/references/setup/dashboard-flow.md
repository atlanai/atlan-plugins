# The dashboard branch: make my dashboard agentic

Read when the person picks "Make my dashboard agentic" in [the interview](intent.md)'s card 1,
or names a dashboard link or file instead. For this run, this file replaces the steps in
`SKILL.md`, the shared checklist's step names and the interview's "Build it?" confirmation.
Where it is silent, the shared files apply. The discovery and known-tables branches and the
demo never read it, and nothing here changes how metrics are generated.

What the person should feel throughout: the build is moving, they can see each metric as it is
made, and they can trust what they see. Every rule below serves that.

## The checklist: ten items

Right after card 1, put these in the host's task list with item 1 in progress.
`PROJECT checklist --flow dashboard` returns them before a run exists. Once the run exists,
record the branch with `PROJECT journey agentic`; from then on `PROJECT checklist` and every
`PROJECT journey` result return this list from the run.

| # | Item | Waits for the person |
| --- | --- | --- |
| 1 | Pick report and pages | Yes |
| 2 | Check what can be built | Only when a chosen tile or table would be left out |
| 3 | Find metrics in your sources | No |
| 4 | Combine duplicates, one metric per number | No |
| 5 | Define each metric | No |
| 6 | Check against the dashboard | Yes: whether to run the checks |
| 7 | Add past queries (optional) | Yes, when past queries on the tables can be read |
| 8 | Save or keep local | Yes |
| 9 | Ask a question | Yes |
| 10 | Build a page from the metrics (optional) | Yes |

Items 3 to 5 are the build, ticked one by one as the engine finishes each part; each shows its time
estimate once the count going into it is known ("about 4 min").

- **Update the task list the moment an item ends**: in the same message as the call that ends
  it (`PROJECT journey report`, `PROJECT journey readiness`, the build's start, `PROJECT journey
  checks`, `PROJECT journey history`, `PROJECT journey asked`), before any other tool call. A list that lags looks stuck.
  When item 2 ends without a question, that same message also starts item 3.
- **Keep the list on screen in every turn.** The Claude desktop app shows the list under the
  turn that sent it, so a turn with no list update shows none. Every turn in this branch,
  including one woken by a helper finishing or by the build's wait, sends the whole list from
  `PROJECT checklist` in its first message: the plan tool's whole list in the desktop app,
  TodoWrite (or TaskUpdate) with every item in Claude Code's terminal.
- **Never tick an item from memory.** Item 6 ticks only when the helper says so: the checks
  recorded their results, or the person chose to skip them.
- A finished item shows its result after a colon, as the helper returns it: "Check what can be
  built: 2 of 5 tiles, targets not set". Never write a result yourself.
- Item 9 ticks when a question is answered, `PROJECT journey asked`, or when the person ends without
  one: `PROJECT journey asked --answer declined`.
- Item 10 ticks when a page is built or the person declines one:
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
   measure counts; for a **report link or file**, its pages with their tile counts. A link is
   fetched with the runtime's own Python, which has the reader installed:
   `<runtime>/venv/bin/python -m metrics_agent_powerbi.pbi_definition_extract <link> --with-model
   --out <run>/pbi`. Then `PROJECT survey <run>/pbi` creates the run (every `PROJECT journey`
   record needs it, so the survey comes first), and `PROJECT pages <run>/pbi` lists the report's
   pages in order, each with its tile count and whether it is hidden. The build's
   BI input is then `powerbi-fabric` with `"bi": "<run>/pbi"`, the copy already fetched. A fetch of
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
  later. The build does not read it: item 7 offers it once the dashboard's metrics are done.
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

The build reads the dashboard alone. Its configuration names no history input
(`"history": []`), even when item 2 found the past queries readable: they are the person's
choice in item 7, after the dashboard's metrics are done.

[Build and check](build-check.md) and [the metrics module](../../../metrics-generation/references/metrics-engine.md)
run as they are, every step in a background helper (below). The side page is the same as in
every flow: what the run made, metrics first and then queries, each in its state (Found,
Defined, Tested, Needs a look) and opening its profile, then the tiles that could not be built.
It opens once the queries are in plain words, when `visual next` names `connect`.

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
| Review of the translations | "Checking each translated tile against the report." | 5 minutes |
| Finding metrics | "Finding the metrics in each query. They show on the side page as they are found." | 4 minutes |
| Combining | "Combining duplicates: metrics that measure the same number become one." | 3 minutes |
| Defining | "Defining each metric: what it counts and what it leaves out." | 10 minutes |
| Links | "Linking metrics that build on each other." | 1 minute |

The metrics come first: the tiles' questions are drafted from the report itself, and the query
descriptions are written beside the metric steps, not before them. When the build pauses at
finding metrics, its result also names the describing step as open: start that step's helper
in the same message, with no start line of its own. Its answers are taken in after the links,
and the definitions then pick up their example questions from it.

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

- **Run**: run every check as [build and check](build-check.md) says. Record the warehouse
  tool once with `PROJECT bind-warehouse --dialect <dialect> --tool <the read-only SQL tool item
  2 used>`. Then `PROJECT checks-due --parts 3` returns the metrics and queries whose current
  definition has not run, in three shares: give each share to its own background helper, all
  at once. On a warehouse, `PROJECT check-query` and `check-metric` answer `run_on_warehouse`
  with the one statement to run: the helper sends it through that tool, then runs the same
  command again with `"result"`, the rows it returned or why it failed, so the side page shows
  each one tested. A failed statement is a result too, never a reason to stop checking. A
  helper whose statement the host refuses says so and goes on with the rest of its share.
- **Skip**: item 6 reads "skipped, your choice"; go straight on.

Either way, item 6 ends with the build's card: `PROJECT visual build --widget`, drawn before
the next question every time, so the build always closes on a result the person can see.

## 5. Add past queries: the person's choice

The dashboard's metrics are built and checked. The past queries on its tables can add metrics
the dashboard does not show and the ways people cut them. Offer them once, never before this
point, and never add them unasked.

**When item 2 found them readable**, ask one question after the build's card:

"Built `<N>` metrics and `<N>` queries from the dashboard. Past queries on its `<N>` tables can
add more. Add them before saving?"

| Option | Description |
| --- | --- |
| Add past queries | "Read the past queries on these tables, combine them with the dashboard's metrics, then save both." |
| Save the dashboard's metrics | "Save only what the dashboard built." |

Say nothing about how long it takes: that depends on how many past queries there are. Record
the answer with `PROJECT journey history --answer add` or `--answer skip`, never on the
person's behalf.

**When item 2 found none readable**, there is no question: record `--answer unavailable` and
say one line, "No past queries can be read for these tables, so the save has the dashboard's
metrics only.", then go on to item 8.

**Add**: a second build reads the dashboard and the past queries together, so a past query that
repeats a dashboard metric joins it and the dashboard's definition wins. Nothing the first build
settled is done again.

1. Write its configuration: the first build's, with the history input added for this
   warehouse as [the metrics module](../../../metrics-generation/references/metrics-engine.md)
   says, scoped to the same tables, and `"history_scope": "inside"`: a past query that also
   reads other tables is about something else, and stays out. Reuse the report the first build
   already read: point the BI input at its staged copy, so an expired sign-in asks nothing.
2. Read the past queries. When they come through the chat's read-only SQL tool, the helper
   saves them as a query log inside the run and `PROJECT history --pulled <that file>` filters
   them for privacy, as it does an export. Prepare the build in `engine-history/` with
   `--carry-from` naming the first build's `engine/`, and record it with `PROJECT use-engine
   engine-history`. The review of the translations, the
   descriptions and the metrics already found are reused, and only the
   past queries are described and searched for metrics.
3. Advance it as item 3 advances the first build, with the same helpers, chips and start lines,
   and `--engine-dir engine-history` on every `PROJECT` call. The combining, defining and
   linking steps run again over everything: that is where duplicates across the two sources
   are folded together.
4. When the person ran the checks in item 6, run them again as item 6 did: `PROJECT
   checks-due` returns only what this build added or changed, since a definition that already
   ran keeps its result.
5. Export it as the first build was exported. Its files replace the dashboard-only ones in the
   context.
6. Record `PROJECT journey history --answer added`, draw `PROJECT visual build --widget` again,
   and go on to item 8.

**Skip**: go straight to item 8. The save has the dashboard's metrics and queries only.

## 6. Save or keep local

`PROJECT question save --widget` composes the save question for this branch: what was built,
whether it ran on the data, what was kept back and why, and the tiles that could not be built,
one line each. Ask exactly that text, with its options. Add nothing to it: the side page holds
every detail.

## Background helpers do the work; this session talks

This session's own tool calls are only: the questions, the sign-in, the task list, starting and
following helpers and the build's wait, drawing chips and cards, and the `PROJECT journey` records.
Everything else runs in background helpers (in Claude Code, a background subagent per piece of
work), several at once where the work splits:

- installing the reader, fetching the workspace and staging the chosen report;
- the checks in item 2, the build's preparation and its translation included;
- every generation step: each written step as [drafts as they land](progress.md#drafts-as-they-land)
  describes. The review of the translations and the links between metrics are the two written
  steps this session writes itself, as the build's result says: both are short;
- packing the finished build into the run, and every repair after a refused answer or a failed
  check;
- the checks on the warehouse. Where only this session can reach the warehouse (a chat SQL tool
  a helper cannot call), this session sends those statements and nothing else.

Each helper's brief is its whole prompt: what to do, the files it needs, that it has no chat of
its own, and what to return: one result line plus anything that needs the person. A helper that
answers a step saves its answers and returns: it never runs `ENGINE advance`, whatever the
step's own instructions say. This session advances, once, when the build's wait says every
answer is saved or the helper reports, whichever comes first. Every `ENGINE advance` names the
model that wrote the answers: `--model <your model id>`. Name each
helper for what it does in plain words ("Reading your report", "Checking your tables"), since
the app shows the name.

**Long per-statement steps.** When the describing or finding-metrics step asks about more than 60
queries, `PROJECT shares --gate m2` (describing) or `--gate x1` (finding metrics), with the
step's `--engine-dir`, splits them into shares of about 60. Start one helper per share, all at
once, each answering only its ids into its own `p<k>-` files. When every share's answers are
saved, the build's wait says so: advance at once.

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
installs: "Installing the Power BI reader so I can read your report. About a minute; then I'll
list its pages."

## Written for a first-time builder

The person is a builder in another organization, using this for the first time. Every line they
see, at every moment:

- **No internal words**: engine, gate, helper, subagent, export, hold, alias, run directory,
  candidate, Registry, statement, staged copy, packing, draft, merge, rule, chip, so-far, or a file
  or command name. Say what it means: "the build", "a step", "kept back for a look", "your
  report's tiles", "a possible metric", "combine duplicates", "its definition".
- **Before the build, whenever something is happening, say what it is and why, and roughly how
  long**, once: "Checking your 9 tables on Snowflake, so the build only uses data you can read.
  About a minute." A rough range from the work's size; never a percentage, and never that
  something is still going. From the build's start, the chips, the task list and the line
  `visual next` gives say it: add none of your own.
- **Only what the person needs**: results, waits, decisions, and problems that change something
  for them. A step that went as expected gets its start line and its chip, nothing more.
