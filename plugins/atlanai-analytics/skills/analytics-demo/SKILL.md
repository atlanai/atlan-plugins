---
name: analytics-demo
description: Guided Atlan Analytics demo on made-up data: build an agent for a sample firm, save its metrics, ask it, then build a dashboard. Use when someone wants to try, see or learn Atlan Analytics. Not for their own data (analytics-build).
---

# Atlan Analytics demo: guided steps

A demo exists so the person understands Atlan Analytics by watching it work on a small,
made-up firm. The blocks printed at each moment are the product.

Read plugin files with the Read tool, one per call. Shell commands print no separators: zsh fails on `echo =====`.

Read [the tour rules](references/tour.md) before the welcome: what to say at every moment,
the two words for what gets built, and the rule against naming people. They apply to every
step below. Chips and cards follow [visuals](../analytics-build/references/setup/visuals.md).
The technical stages are the shared setup procedure in
[setup](../analytics-build/references/setup.md), run on the demo's working folder. `PROJECT`
means the helper named there, from the `analytics-build` skill's `scripts/`. `DEMO` means
`python3 "<skill-dir>/scripts/demo_data.py"`, where `<skill-dir>` is this skill's directory.

## The steps

Show this tracker in chat and in the host's task list when available, following
[the checklist](../analytics-build/references/setup/checklist.md) with `--flow demo`. Once
the survey creates the run, record the flow once with `PROJECT journey demo`, so a resumed run shows
these steps. Never count the steps aloud.
A folder holding a demo's run [picks up where it stopped](references/tour.md#coming-back-to-a-demo).

1. Load the demo data
2. Choose what to build your agent from
3. Build your context: metrics and saved queries
4. Choose where to store your context
5. Ask your agent a question

## 1. Load the demo data

Ask one native question first, with no words before it. Its visible prompt says what the person
will end with:

"Atlan Analytics builds an agent that answers your data questions in plain English. Try it
first on a made-up firm's data. Which demo should I build?"

| Option | Description |
| --- | --- |
| Fixit & Co. | I build a job profit agent for a made-up building firm from its Power BI dashboard. It tells you which jobs lost money and why. About 15 minutes. |
| Ticketfuddle | I build a ticket fees agent for a made-up ticketing firm from its Tableau dashboard. It tells you how much of what fans paid went on fees. About 15 to 20 minutes. |
| Use your own data | I build your agent from your own warehouse or dashboard instead. |

On "Use your own data", load the `analytics-build` skill instead and follow its steps.

Each demo has its own facts file: [Fixit's builder's yard](references/yard.md),
[Ticketfuddle's box office](references/box-office.md). Read the chosen one now, then, before any other
tool call, ask "Ready to build?" with its welcome lines as the question's own text, as
[the tour rules](references/tour.md) describe. On "Not now", stop and repeat the tour link.

After "Start building", show the tracker, then do all of step 1's work:

1. **Open the demo.** Each demo lives in this skill, under `assets/<demo>/`. Run
   `DEMO open <demo> "<folder>"` with `<demo>` as named in its facts file and `<folder>` the
   current working folder. It sets up `<folder>/atlan-analytics-demo/<demo>/`, the working
   folder the run reads and writes, and prints it as `working_folder`. Run it again on a later
   demo: it reuses that folder and brings it up to date with this skill. Never clone, download
   or search other folders for the demo. In the same call, initialize or
   reuse the metrics engine's pinned runtime, the slowest part of this step:
   `python3 "<metrics-dir>/scripts/analytics_agent.py" metrics --runtime "<folder>/.atlan-metrics/runtime" setup`,
   with `<metrics-dir>` the `metrics-generation` skill's directory
   ([the metrics module](../metrics-generation/references/metrics-engine.md), section 1).
2. **Read the sources.** Start a new setup run with `PROJECT location --workspace "<folder>"`,
   so it lives in `<folder>/.atlan-metrics/`, outside the folder it reads. Inspect only `working_folder`, with
   [inspect and scope](../source-survey/references/inspect-scope.md) section 1, as if
   the person had asked to set up that folder from scratch. Then run
   `DEMO requests <demo> --run-dir "<run-dir>"`. It writes the demo's fixed choices as request
   files, `prepare` and, for a Power BI dashboard, `scope`, and prints the dashboard file (`bi`),
   `database` and `schema`. Use these as given; do not work them out again.
3. **Load the data.** With setup's `python`, run `PROJECT prepare "<prepare>"`, as
   [build and check](../analytics-build/references/setup/build-check.md) describes; `prepare`
   is null when the database is already loaded. Then bind it once with
   `PROJECT bind "<database>" --schema <schema>`. Every later check runs on these rows.
4. **Record the preset intent.** `PROJECT journey domain --answer "<focus to record>"`, then
   `PROJECT journey purpose --answer "<purpose to record>"`, from the facts file. The demo asks
   neither intent question.

The `sources` chip is the step's result: add no line. What the sources revealed goes in step 2.

## 2. Choose what to build your agent from

Ask the sources question from [the tour rules](references/tour.md): dashboard and past
queries (Recommended), or dashboard only. Scope the run to the answer through
[inspect and scope](../source-survey/references/inspect-scope.md) section 2, with
`PROJECT scope "<scope>"` for the whole dashboard when `requests` wrote one. The data
tables are used either way, to check the context.

## 3. Build your context: metrics and saved queries

The demo has no first-metric preview and asks no build-the-rest question. With past queries in
scope, run `PROJECT history` (no arguments). Then `PROJECT engine-config` with a request file
`{"sources": "all", "bi": "<bi>"}` (or `"dashboard"`) writes the engine's configuration and
prints `config` and `engine_run`; prepare the engine with them, never with a configuration
written by hand. Run the metrics engine as
[the metrics module](../metrics-generation/references/metrics-engine.md) describes, up to its
X3 card gate. The source choice authorizes the whole local build: no metric-list checkpoint or
other confirmation, and proposal and deduplication details stay internal.
Advance the same run through all its gates, export and run every check on the loaded
data, through [build and check](../analytics-build/references/setup/build-check.md).

## 4. Choose where to store your context

When every check has run, read [context built and where to keep it](../analytics-build/references/setup/save.md).
The celebration is the save question's own text. Saving follows
[publication](../metrics-generation/references/metrics/publish.md). Keeping it local, run
`PROJECT journey keep-local` first.

## 5. Ask your agent a question, then choose what's next

When the context is saved, or kept on this machine, read
[ready for questions](../analytics-build/references/setup/ready.md): ask "What would you like
to ask?" with the ready block as its text, offering up to three supported questions; one the
person types is answered at once. Do not put an Ask AI menu before the question list. Answer the question or
questions the person selects or supplies, then, in the same turn, ask the native "What would
you like to do next?", its text as [the tour](references/tour.md#after-the-first-answer) says.
This overrides ready's `Next:` line: a demo answer always ends on this question, as does a
dashboard or an edit:

| Option | Runs |
| --- | --- |
| Ask more questions | Step 5: a question the context supports, answered through `analytics-ask`, then `PROJECT journey asked` |
| Check how accurate it is | Test the context on questions the firm's analysts already ask, and fix the rule behind a miss, as [accuracy](references/accuracy.md) describes |
| Build a dashboard, until one is built; then Change a rule and see what happens | [The dashboard](../analytics-ask/references/dashboard.md), then `PROJECT journey dashboard --answer built`. After it, [edit a metric](references/edit-metric.md) takes this slot |
| Build one on your own data | Load `analytics-build` in this chat; it asks for the person's own source. A separate run: nothing of the demo carries over |
| I'm done | End with the closing message, `PROJECT question close` |

With no question tool, end as [the tour](references/tour.md#without-a-question-tool) says.

A typed or supplied action comes before asking again. Never start a flow the person did not
pick, or answer a question they did not choose.

A failure at any step follows the save file's **When something does not go to plan**: say
what happened, that nothing is lost, and the one way forward. Every demo can still end on
the question list, answered from the context on this machine.
