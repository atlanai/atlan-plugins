# Build a dashboard

The last step of both guided flows. Read after the first chosen question is answered, from
[ready for questions](ready.md). For a demo, open its facts file again for the dashboard's
file name ([Fixit](../../../analytics-demo/references/yard.md),
[Ticketfuddle](../../../analytics-demo/references/box-office.md)). For own data, name the file
after the business focus, such as `<focus>-dashboard.html`.

After the answer's proof, ask one native question: "Want to turn this context into a
dashboard?"

| Option | Description |
| --- | --- |
| Design a dashboard | I propose a layout from your metrics, you adjust it, and I build it as a page you can open. |
| Ask another question | The questions not asked yet, or your own. |
| I'm done | Wrap up. |

"Ask another question" returns to [ready for questions](ready.md) and offers this again after
its answer. Record `PROJECT journey dashboard` once a dashboard is built or the person picks
"I'm done"; nothing else ticks this step.

## Design a dashboard

Keep it real and small:

- Propose the layout in words first, at most six lines: a tile for each of the
  context's metrics, one breakdown, and one chart.
  Ask one native question to confirm it or change it.
- Build one self-contained HTML file in the working folder, with the name above: inline CSS and SVG, no external scripts or fonts, and
  readable in light and dark.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
- Each tile names its metric and links to its page in Atlan Trident when one exists.
- Say where the file is and how to open it, in one line. Do not publish it anywhere.

## Wrap up

After a demo, on "I'm done" or once the dashboard is built, add once: "With your own data,
the same steps turn your dashboards and your warehouse's query history into your team's
context. Run /atlanai-analytics:analytics-build."
