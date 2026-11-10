# Ready for questions

This moment is shared by all setup through [the presentation flow](presentation.md).
For a demo, use its facts file. For own data, use the actual source, selected business
focus, metric definition and checks in the saved run. Preserve the block's structure;
adapt source-specific prose to the evidence, and omit unsupported tours or example rows.
Never claim model/history agreement or disagreement without source evidence.

**Why this moment matters.** It is where the person learns what they made and what it can
do, and takes over. The ready block and supported questions hand them the agent; answering
for them takes that away.

**Two rules for this moment.** Your reasoning is never shown to the person: a note in
your thinking is not a reply, however it is worded. So send the block below as reply text,
in the same message as the question and before it. A question sent without the block above
it is a failure: send the block first.

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Do not answer any question the person has not
chosen. When the question step starts, show what they made, then let them pick a supported question.

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

**First show questions, for both demo and own-data setup.** Immediately after the ready
block, ask "What would you like to ask?" with two or three questions supported by the
checked context (fewer if needed) and the host's free-text entry. Use the demo's facts
file only for a demo; own-data questions come from its saved context. Omit any question
that depends on an unchecked or unavailable metric. Do not show an Ask AI menu, dashboard
choice or real-data transition before this first question list.

A question already supplied goes directly to the ask route. Answer only the question or
questions the person selects or types; listing suggestions never authorizes answering
all of them. If they selected several, finish those answers before offering next steps.
An explicit dashboard or real-data request still takes precedence over this default flow.

**After the selected questions are answered**, ask "What would you like to do next?":
At this moment the question and its choices are the complete handoff. Address the person
directly. Do not preface it with what they "would see" or append explanations of the flow,
the option rules, sign-in requirements or which tool runs next. End at the choices and wait.

| Option | Description |
| --- | --- |
| Ask more questions | Choose another supported question, or type your own. |
| Design a dashboard | I propose a layout from your metrics, you adjust it, and I build it as a page you can open. |
| Continue building on real data | Start with your own dashboard link, local folder or BI export, here in this chat. |
| I'm done | Finish the demo here. |

Show this choice after answering the person's request and after a dashboard is built.
Ask more questions returns to the supported question list. Own-data setup omits the
real-data transition option. A supplied question or explicit next action takes precedence
over asking again. Never start a flow until chosen.

Answer only the question the person picks or types, through the ask route, with real
numbers. Every answer has three parts, in this order:

1. **The answer**, led by the number or the name, then what explains it.
2. **Proof in Atlan Trident**: one line per metric and query the answer used, each a link
   to its page, as [definition links](../../../analytics-ask/references/definition-links.md) says. Read that file before
   the first answer. Never print metric or release ids, and never ask for a Trident address
   before answering: discover the active organization through the authenticated
   self-profile endpoint, as the definition-links reference describes.
   With the context only on this machine, the list says there is nothing to open yet and
   that saving to Atlan Trident gives each answer its links.
3. After the first answer, record `PROJECT journey asked`. Finish all questions in the
   selected request before showing the next-action choice above. After an own-data answer, offer Design a
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
