# Ready for questions

This moment is shared by all setup through [the presentation flow](presentation.md).
For a demo, use its facts file. For own data, use the actual source, selected business
focus, metric definition and checks in the saved run. Preserve the block's structure;
adapt source-specific prose to the evidence, and omit unsupported tours or example rows.
Never claim model/history agreement or disagreement without source evidence.

**Why this moment matters.** It is where the person learns what they made and what it can
do, and takes over. The ready block and next-action choice hand them the agent; answering
for them takes that away.

**Two rules for this moment.** Your reasoning is never shown to the person: a note in
your thinking is not a reply, however it is worded. So send the block below as reply text,
in the same message as the question and before it. A question sent without the block above
it is a failure: send the block first.

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Do not answer any question the person has not
chosen. When the question step starts, show what they made, then let them pick what to do next.

Open the chosen demo's file again for its facts and its questions
([Fixit](../../../analytics-demo/references/yard.md), [Ticketfuddle](../../../analytics-demo/references/box-office.md)). First, print this block whole
and as written, with the values in `<angle brackets>` from the facts and this run:

> **Your `<agent>` is ready**
>
> | | |
> | --- | --- |
> | What you built | `<N>` metrics and `<N>` queries, from `<1 dashboard and N past queries>`, checked on `<firm>`'s data |
> | Where it lives | `<workspace name>` in Atlan Trident, or: on this machine |
> | What it can answer | `<what the agent can answer>` |
> | See the environment | `<tour>` |

**After a demo**, ask one native question, "What would you like to do next?":

| Option | Description |
| --- | --- |
| Ask AI | Choose a question the built context supports, or type your own. |
| Design a dashboard | I propose a layout from your metrics, you adjust it, and I build it as a page you can open. |
| Continue building on real data | Start with your own dashboard link, local folder or BI export, here in this chat. |
| I'm done | Finish the demo here. |

Show this choice after the ready block, after each demo answer and after a dashboard is
built. A supplied question or explicit next action takes precedence over asking again.
Do not build a dashboard, answer a suggested question or start real-data setup until chosen.

**Ask AI**, or a completed **own-data setup**, asks "What do you want to ask your agent?"
with two or three supported questions (fewer if needed) and the host's free-text entry.
Use the demo's questions only for a demo; own-data questions come from its saved context.
Offer only questions the built context supports. A question already supplied goes directly
to the ask route. Own-data setup does not show the demo's real-data transition option.

Answer only the question the person picks or types, through the ask route, with real
numbers. Every answer has three parts, in this order:

1. **The answer**, led by the number or the name, then what explains it.
2. **Proof in Atlan Trident**: one line per metric and query the answer used, each a link
   to its page, as [definition links](../../../analytics-ask/references/definition-links.md) says. Read that file before
   the first answer. Never print metric or release ids, and never ask for a Trident address
   before answering: the link's address comes from the workspace read at the save step.
   With the context only on this machine, the list says there is nothing to open yet and
   that saving to Atlan Trident gives each answer its links.
3. After the first answer, record `PROJECT journey asked`. Then, as a native question: after
   a demo answer, show the next-action choice above. After an own-data answer, offer Design a
   dashboard, Ask another question and I'm done. Design a dashboard follows
   [the dashboard](../../../analytics-ask/references/dashboard.md).

Say once per conversation that answers come from the context built from `<firm>`'s dashboard
and queries, not from a guess at the tables. Names in the data, such as a crew member,
appear only when the question asks who. Never answer a question the person has not asked.

**Continue building on real data.** Load the `analytics-build` skill in the same chat; do
not repeat the demo picker. Reuse a real source explicitly supplied for this transition.
Otherwise ask once for a dashboard link, local folder or BI export and wait for it before
inspection. Do not search for a source in unrelated folders or start another demo.

Create a separate run for that source and preserve the demo's context and dashboard.
Reuse the existing authenticated connection when available. Establish the real source's
scope, business focus and purpose through that skill; the demo's preset intent, source
selection, data binding, metrics and publication approval do not carry over. Selection of
this option authorizes setup, not publication. Use the normal destination and concrete-plan
authorization steps if the person later chooses to save the new context.

**Change a rule and see what happens.** After a demo dashboard is built, this option takes
the dashboard's slot in the next-action choice. Follow the demo's
[edit a metric](../../../analytics-demo/references/edit-metric.md).

**Wrap up.** On "I'm done", record `PROJECT journey dashboard` if no dashboard was built,
then finish with the saved context or dashboard location. Do not ask another question or
require a restart. Building a demo dashboard returns to the next-action choice above,
including the option to continue on real data.
