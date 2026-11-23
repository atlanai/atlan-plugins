# Build a dashboard

The last step of both guided flows, and a follow-up to any answer here. Reached from the
demo's next-action choice, the own-data follow-up in
[ready for questions](../../analytics-build/references/setup/ready.md), or a request after an
answer. For a demo, take the file name from its facts file
([Fixit](../../analytics-demo/references/yard.md),
[Ticketfuddle](../../analytics-demo/references/box-office.md)). For own data, name it after the
business focus, such as `<focus>-dashboard.html`.

Record `PROJECT journey dashboard --answer built` once a dashboard is built, or `--answer declined`
when the person picks "I'm done" without one; nothing else ticks this step, and the closing
message names the dashboard only after `built`. Both moments follow
[text before a native question](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost).

## Build the dashboard

A request for a dashboard is the go-ahead: build it in this turn, with no layout to approve
and no questions first. Only when the person asks to see a plan before anything is built,
give the layout in words and stop. Keep it real and small:

- A tile for each of the context's metrics, one breakdown and one chart, chosen from what the
  metrics and the person's questions so far say matters. When the context was built from a
  dashboard, look at it for ideas, not as a template to copy: its tile titles, chart types,
  sort and filters are in the engine's `fragments/` (a `*.widget.json` per tile with its
  `title`, `visual_type`, `sort` and `filters`). Use what helps; the new one can look and
  group things differently.
- A tile's headline is one number only where adding up makes sense; a metric over several
  periods shows its latest period, with the rest in the breakdown, never one total across
  them all.
- In Claude, build it as a Claude dashboard: where the `Artifact` tool is available, run its
  quickstart with intent `other` and start from the Dashboard type it names, filled with the
  numbers below; where it names none, a page published with that tool. In any other host, one
  self-contained HTML file in the working folder with the name above, inline CSS and SVG, no
  external scripts or fonts, readable in light and dark.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
- Each tile names its metric, linked to its page in Atlan when it has one.
- Do not publish it anywhere. Then say where it is in one line: "Your dashboard is at <path>.
  Open it in your browser." for a file, or "Your dashboard is the page beside this chat." for
  a Claude dashboard, and one line on what it shows ("Pod S1 target for the latest quarter,
  by pod, with every quarter in a table below.").
- **What comes after.** In own-data setup the dashboard is the last step: record `PROJECT
  journey dashboard --answer built`, then end with [the closing message](../../analytics-build/references/setup/ready.md)
  (`PROJECT question close`), printed as it is, as the last thing in the reply. In a demo,
  end on the demo's "What would you like to do next?" question instead, as
  [the demo](../../analytics-demo/SKILL.md) says. Outside a guided flow, end on the two lines
  above. Nothing else in that reply: no headings, no list of what the page can do, no "before
  you share it" notes. A caveat the person must know goes in the closing message's "Good to
  know", once. Outside the demo, no native question in that turn.
