# Demo: ready for questions

This moment is shared by all setup through [the presentation flow](presentation.md).
For a demo, use its facts file. For own data, use the actual source, selected business
focus, metric definition and checks in the saved run. Preserve the block's structure;
adapt source-specific prose to the evidence, and omit unsupported tours or example rows.
Never claim model/history agreement or disagreement without source evidence.

**Why this moment matters.** It is where the person learns what they made and what it can
do, and takes over. The ready block and the question list hand them the agent; answering
for them takes that away.

**Two rules for this moment.** Your reasoning is never shown to the person: a note in
your thinking is not a reply, however it is worded. So send the block below as reply text,
in the same message as the question and before it. A question sent without the block above
it is a failure: send the block first.

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Do not answer any question the person has not
chosen. When step 6 starts, show what they made, then let them pick what to ask.

Open the chosen demo's file again for its facts and its questions
([Fixit](demo-yard.md), [Ticketfuddle](demo-box-office.md)). First, print this block whole
and as written, with the values in `<angle brackets>` from the facts and this run:

> **Your `<agent>` is ready**
>
> | | |
> | --- | --- |
> | What you built | `<N>` metrics and `<N>` queries, from `<1 dashboard and N past queries>`, checked on `<firm>`'s data |
> | Where it lives | `<workspace name>` in Atlan Trident, or: on this machine |
> | What it can answer | `<what the agent can answer>` |
> | See the environment | `<tour>` |

Then ask one native question, "What do you want to ask your agent?", with three or four
of the demo's questions that this run's metrics and queries can answer, and the host's
free-text entry for anything else. Offer only questions the built context supports.

Answer only the question the person picks or types, through the ask route, with real
numbers. Every answer has three parts, in this order:

1. **The answer**, led by the number or the name, then what explains it.
2. **Proof in Atlan Trident**: one line per metric and query the answer used, each a link
   to its page, as [definition links](../definition-links.md) says. Read that file before
   the first answer. Never print metric or release ids, and never ask for a Trident address
   before answering: the link's address comes from the workspace read at the save step.
   With the context only on this machine, the list says there is nothing to open yet and
   that saving to Atlan Trident gives each answer its links.
3. **One follow-up**, as a native question: "Want to turn this context into a dashboard?"

| Option | Description |
| --- | --- |
| Design a dashboard | I propose a layout from your metrics, you adjust it, and I build it as a page you can open. |
| Ask another question | The questions not asked yet, or your own. |
| I'm done | Wrap up. |

Say once per conversation that answers come from the context built from `<firm>`'s dashboard
and queries, not from a guess at the tables. Names in the data, such as a crew member,
appear only when the question asks who. Never answer a question the person has not asked.

**Design a dashboard.** Keep it real and small:

- Propose the layout in words first, at most six lines: a tile for each of the demo's
  metrics, one breakdown, and one chart.
  Ask one native question to confirm it or change it.
- Build one self-contained HTML file in the working folder, named
  the name the demo's file gives: inline CSS and SVG, no external scripts or fonts, and
  readable in light and dark.
- Every number comes from the same metrics and queries, run through the ask route. Do not
  write new SQL for a number a metric already defines, and never type a number in by hand.
- Each tile names its metric and links to its page in Atlan Trident when one exists.
- Say where the file is and how to open it, in one line. Do not publish it anywhere.

**Wrap up.** On "I'm done", or after the dashboard is built, add once: "With your own
data, the same six steps turn your dashboards and your warehouse's query history into
your team's context. Run /atlanai-analytics:start and choose Use my own data."
