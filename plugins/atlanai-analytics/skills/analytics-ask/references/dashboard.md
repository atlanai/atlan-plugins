# Build a dashboard

The last step of both guided flows, and a follow-up to any answer here. Reached from the
demo's next-action choice, the own-data follow-up in
[ready for questions](../../analytics-build/references/setup/ready.md), or a request after an
answer. For a demo, take the file name from its facts file
([Fixit](../../analytics-demo/references/yard.md),
[Ticketfuddle](../../analytics-demo/references/box-office.md)). For own data, name it after the
business focus, such as `<focus>-dashboard.html`.

Record `PROJECT journey dashboard` once a dashboard is built, or when the person picks
"I'm done" without one; nothing else ticks this step. Both moments follow
[text before a native question](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost).

## Build the dashboard

A request for a dashboard is the go-ahead: build it in this turn, with no layout to approve
and no questions first. Only when the person asks to see a plan before anything is built,
give the layout in words and stop. Keep it real and small:

- When the context was built from a dashboard, start from that dashboard: its pages, tile
  titles, chart types, sort and filters, as the run read them (the engine's `fragments/`: a
  `*.widget.json` per tile with its `title`, `visual_type`, `sort` and `filters`, and a
  `*.dashboard.json` per page), each tile now drawn from the metric made from it. Keep
  what the person knows, and fix only what misleads, such as a total added across periods.
- Otherwise, a tile for each of the context's metrics, one breakdown and one chart, chosen
  from what the metrics and the person's questions so far say matters. A tile's headline is one number only
  where adding up makes sense; a metric over several periods shows its latest period, with
  the rest in the breakdown, never one total across them all.
- In Claude, build it as a Claude dashboard: where the `Artifact` tool is available, run its
  quickstart with intent `other` and start from the Dashboard type it names, filled with the
  numbers below; where it names none, a page published with that tool. In any other host, one
  self-contained HTML file in the working folder with the name above, inline CSS and SVG, no
  external scripts or fonts, readable in light and dark.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
- Each tile names its metric, linked to its page in Atlan when it has one.
- Do not publish it anywhere. End the turn with one line: "Your dashboard is at <path>. Open it in
  your browser." for a file, or "Your dashboard is the page beside this chat." for an Artifact,
  then one line on what it shows ("Tiles for Pod S1 target, latest quarter; by pod and quarter
  below. Tell me what to add or drop."), then, in a guided flow, the `Next:` line from
  [ready](../../analytics-build/references/setup/ready.md). No native question in that turn.
