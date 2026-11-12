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

## Design a dashboard

Keep it real and small:

- Propose the layout in at most six lines: a tile for each of the dashboard's own metrics, six at
  most, with the context's other metrics named under them; one breakdown; and one chart. Record it as
  the `layout` moment ([visuals](../../analytics-build/references/setup/visuals.md)) first. In a turn with no
  answer, the layout is the text of one native question to confirm it or change it. When the
  same message also asked a question, answer it, put the layout as plain lines after the
  answer, end the turn with a `Next:` line asking to build it or change it, and ask no native
  question.
- Build the page with what the host offers: an Artifact in Claude, otherwise one
  self-contained HTML file in the working folder with the name above, inline CSS and SVG,
  no external scripts or fonts, readable in light and dark. Open a file in the host's own
  viewer when it has one (the Claude desktop app's browser pane, the Codex app's preview);
  otherwise give its path.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
  Round as the visuals do, so the page and the chat agree: money to the whole unit, rounding
  halves up; shares to one decimal, with the same number of decimals across one list.
- Each tile names its metric and links to its page in Atlan Trident when one exists.
- Do not publish it anywhere. End the turn with one line naming where the page is (its full
  file path, or the Artifact) and how to open it, then, in a guided flow, the `Next:` line
  from [ready](../../analytics-build/references/setup/ready.md). No native question in that turn.
