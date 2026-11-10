---
name: analytics-demo
description: Guided Atlan Analytics demo on made-up data: pick a demo, build its metrics, save them, ask a question and design a dashboard. Use when someone wants to try, see or learn Atlan Analytics. Not for their own data (analytics-build).
---

# Atlan Analytics demo: six guided steps

A demo exists so the person understands Atlan Analytics by watching it work on a small,
made-up firm. The blocks printed at each moment (the welcome, the
context built, the ready screen, the dashboard offer) are the product; the commands between
them are plumbing. A run that builds everything but skips a block has failed.

Read plugin files with the Read tool, one file per call. In a shell, never print a separator such as
`echo =====`: zsh reads a word that starts with `=` as a command, fails and skips the rest of the line.

Read [the tour rules](references/tour.md) before the welcome: what to say at every moment,
the two words for what gets built, and the rule against naming people. They apply to every
step below. The technical stages are the shared setup procedure in
[setup](../analytics-build/references/setup.md), run on the demo repository. `PROJECT`
means the helper named there, from the `analytics-build` skill's `scripts/`.

## The six steps

Show this tracker in chat and in the host's task list when available, following
[the checklist](../analytics-build/references/setup/checklist.md) with `--flow demo`. Once
the run exists, record the flow once with `PROJECT journey demo`, so a resumed run shows
these steps.

1. Choose and load demo data
2. Choose what to build from
3. Build and check metrics
4. Keep local or save to Atlan Trident
5. Ask a question
6. Build a dashboard

## 1. Choose and load demo data

Ask one native question before surveying anything. Its visible prompt says what the person
will end with:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on. Which demo should we build?"

| Option | Description |
| --- | --- |
| Fixit & Co. | Build a job profit agent for a made-up building firm, from its Power BI dashboard: which jobs lost money and why. About 15 minutes. |
| Ticketfuddle | Build a ticket fees agent for a made-up ticketing firm, from its Tableau dashboard: how much of what fans paid went on fees. More data, about 15 to 20 minutes. |
| Use my own data | Load the `analytics-build` skill instead and follow its steps. |

Each demo has its own facts file: [Fixit's builder's yard](references/yard.md),
[Ticketfuddle's box office](references/box-office.md). Read the chosen one now, print its
welcome block whole before any other tool call, then ask "Ready to build?" as
[the tour rules](references/tour.md) describe. On "Not now", stop and repeat the tour link.

After "Start building", show the tracker, then do all of step 1's work:

1. **Download.** The checkout is `<folder>/analytics-agent-accelerator`, the path the welcome
   named. Reuse it when its origin is https://github.com/atlanai/analytics-agent-accelerator,
   after `git -C <checkout> pull --ff-only` so the demo is the published one; if it cannot
   fast-forward, say so and ask before using it. Otherwise `git clone --depth 1` there. Never
   search other folders or the home directory for a copy. Without git, ask the person to
   download the repository into that path.
2. **Read the sources.** Start a new setup run and inspect only the demo's source folder,
   with [inspect and scope](../source-survey/references/inspect-scope.md) section 1, as if
   the person had asked to set up that folder from scratch.
3. **Load the data.** Initialize or reuse the metrics engine's pinned runtime
   ([the metrics module](../metrics-generation/references/metrics-engine.md), section 1), then
   use that runtime's Python with `PROJECT prepare` to load the demo's tables into a local
   `.sqlite` database named after their schema (`site.sqlite`, `tix.sqlite`), as
   [build and check](../analytics-build/references/setup/build-check.md) describes. Bind it once
   with `--schema`. Every later check runs on these rows.
4. **Record the preset intent.** `PROJECT journey domain --answer "<focus to record>"`, then
   `PROJECT journey purpose --answer "<purpose to record>"`, from the facts file. The demo asks
   neither intent question.

End the step with one line in the past tense with counts, and one sentence on what the
sources revealed.

## 2. Choose what to build from

Ask the sources question from [the tour rules](references/tour.md): dashboard and past
queries (recommended), or dashboard only. Scope the run to the answer through
[inspect and scope](../source-survey/references/inspect-scope.md) section 2. The data
tables are used either way, to check the context.

## 3. Build and check metrics

The demo has no first-metric preview and asks no build-the-rest question. Run the metrics
engine as
[the metrics module](../metrics-generation/references/metrics-engine.md) describes, up to its
X3 card gate. The source choice authorizes the complete local build. Author every
card without a metric-list checkpoint or another build confirmation. Keep proposal and
deduplication details internal; show brief progress about building and checking context.
Advance the same run through all its gates, export and run every check on the loaded
data, through [build and check](../analytics-build/references/setup/build-check.md).

## 4. Keep local or save to Atlan Trident

When every check has run, read [context built and where to keep it](../analytics-build/references/setup/save.md).
The celebration is the save question's own text. Saving reads the person's workspaces with
`registry__list_workspaces` (`user_default` for the personal destination) and writes
in batches with `registry__create_artifacts`, as [publication](../metrics-generation/references/metrics/publish.md)
describes.

## 5 and 6. Ask a question, build a dashboard

When the context is saved, or kept on this machine, read
[ready for questions](../analytics-build/references/setup/ready.md): print the ready block, then
show two or three questions supported by the checked context immediately. Do not put an
Ask AI menu before the question list. Answer the question or questions the person selects
or supplies. After answering that request, offer "What would you like to do next?":

| Option | Runs |
| --- | --- |
| Ask more questions | Step 5: a question the context supports, answered through `analytics-ask`, then `PROJECT journey asked` |
| Design a dashboard, until one is built; then Change a rule and see what happens | Step 6: [the dashboard](../analytics-ask/references/dashboard.md), then `PROJECT journey dashboard`. After it, [edit a metric](references/edit-metric.md) takes this slot |
| Continue building on real data | Load `analytics-build` in this chat. A separate run; the demo's intent, sources and approvals do not carry over |
| I'm done | Finish with where the context and any dashboard live |

A typed or supplied action takes precedence over asking again. Never start a flow the person
did not pick, and never answer a question they did not choose.

A failure at any step follows the save file's **When something does not go to plan**: say
what happened, that nothing is lost, and the one way forward. Every demo can still end on
the question list, answered from the context on this machine.
