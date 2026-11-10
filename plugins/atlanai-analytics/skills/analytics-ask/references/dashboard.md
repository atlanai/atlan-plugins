# Build a dashboard

The last step of both guided flows, and a follow-up to any answer here. Reached from the
demo's next-action choice, the own-data follow-up in
[ready for questions](../../analytics-build/references/setup/ready.md), or a request after an
answer. For a demo, take the file name from its facts file
([Fixit](../../analytics-demo/references/yard.md),
[Ticketfuddle](../../analytics-demo/references/box-office.md)). For own data, name it after the
business focus, such as `<focus>-dashboard.html`.

Record `PROJECT journey dashboard` once a dashboard is built, or when the person picks
"I'm done" without one; nothing else ticks this step. After a demo dashboard, end the turn with
one line naming the file's full path, then the plain next-actions line from
[ready](../../analytics-build/references/setup/ready.md); no native question in that turn.

## Design a dashboard

Keep it real and small:

- Propose the layout as the text of the confirm question, at most six lines (reply text
  before a native question often never reaches the person): a tile for each of the
  context's metrics, one breakdown, and one chart.
  Ask one native question to confirm it or change it.
- Build the page with what the host offers: an Artifact in Claude, otherwise one
  self-contained HTML file in the working folder with the name above, inline CSS and SVG,
  no external scripts or fonts, readable in light and dark.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
- Each tile names its metric and links to its page in Atlan Trident when one exists.
- Say where the file is and how to open it, in one line. Do not publish it anywhere.
