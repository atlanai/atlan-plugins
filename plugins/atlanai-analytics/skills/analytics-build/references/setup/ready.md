# Ready for questions

This moment is shared by all setup through [the presentation flow](presentation.md). The
helper composes the question from the run's own files, for a demo or for own data. Never
claim model/history agreement or disagreement without source evidence.

**Why this moment matters.** It is where the person learns what they made and what it can
do, and takes over. The ready block and supported questions hand them the agent; answering
for them takes that away.

**Two rules for this moment.** Your reasoning is never shown to the person: a note in
your thinking is not a reply, however it is worded. And the moment follows
[text before a native question](../conventions.md#text-before-a-native-question-is-lost):
the ready block is the question's own text, as the celebration is in [context built](save.md),
and an answer ends its turn.

The agent is this chat with the saved context; do not register, deploy or evaluate a
separate agent, and say so only if asked. Do not answer any question the person has not
chosen. When the question step starts, show what they made, then let them pick a supported question.

Run `PROJECT question ready`, adding `--data` with the demo's facts file for a demo
([Fixit](../../../analytics-demo/references/yard.json),
[Ticketfuddle](../../../analytics-demo/references/box-office.json)), and nothing for own data.
With `show_widget`, add `--widget` here, after a save to Atlan Trident first drawing
`PROJECT visual saved --widget`. Ask one
native question with the `header`, the `question` text and the `options` it prints, exactly as
given, and the host's free-text entry. Its text is the ready block: what was built, where it
lives, what it can answer, and on own data what needs a look. Its options are questions the
checked context answers, in the words the build wrote for them.

Own-data setup adds, before the last line of that text, only what the helper cannot read from
the run and [preview and publish](preview-publish.md) requires in the handoff: review warnings
and material limitations, as more rows labelled `Worth knowing`. Never label them "Needs a
look": that names checks that did not pass, and the helper already lists those. Nothing in
the handoff goes before the question.

Where the host has no question tool, print the same lines as the last thing in the message,
then the supported questions as plain numbered lines, and call no tool after it.

**First show questions, for both demo and own-data setup.** The helper's options are two or
three questions the checked context answers, each the question the build wrote for a metric
or saved query that passed its check. When it has fewer than two, it adds "Show what was
built" (run `PROJECT preview --all --format markdown`, show its text as the reply and end the
turn with the `Next:` line below) and "I'm done for now" (one line, then end the turn). No
demo ships a question list. Do not show an Ask AI menu, dashboard choice or real-data
transition before this first question list.

A question already supplied goes directly to the ask route. Answer only the question or
questions the person selects or types; listing suggestions never authorizes answering
all of them. If they selected several, finish those answers before offering next steps.
An explicit dashboard or real-data request still takes precedence over this default flow.

**After the selected questions are answered**, end the turn on the answer. Never call a
native question in a turn that answers. Close the answer with one plain `Next:` line naming
the options below that apply at this point, in the person's words, as one sentence and not
a list, then stop. Build it from
the table each time rather than copying an example: after a demo dashboard, Change a rule
and see what happens takes Design a dashboard's place; own-data setup leaves out Continue
building on real data. Treat the person's reply as the matching option. Ask the native
"What would you like to do next?" question only when the person replies without choosing,
and then with no answer text in that turn. The same rule ends the turn after a built
dashboard and after the edit-metric block.

| Option | Description |
| --- | --- |
| Ask more questions | Choose another supported question, or type your own. |
| Design a dashboard | I propose a layout from your metrics, you adjust it, and I build it as a page you can open. |
| Continue building on real data | Start with your own dashboard link, local folder or BI export, here in this chat. |
| I'm done | Finish the demo here. |

Offer these options in the `Next:` line after answering the person's request and after a
dashboard is built. Ask more questions returns to the supported question list. Own-data setup omits the
real-data transition option. A supplied question or explicit next action takes precedence
over asking again. Never start a flow until chosen.

Answer only the question the person picks or types, through the ask route, with real
numbers. Every answer has three parts, in this order:

1. **The answer**, led by the number or the name, then what explains it. In a setup or demo run,
   the `answer` visual's card or chip ([visuals](visuals.md)) is that lead, shown unchanged, as
   plain lines with no code fence around them.
2. **Proof in Atlan Trident**: one line per metric and query the answer used, each a link
   to its page, as [definition links](../../../analytics-ask/references/definition-links.md) says. Read that file before
   the first answer. Never print metric or release ids, and never ask for a Trident address
   before answering: discover the active organization through the authenticated
   self-profile endpoint, as the definition-links reference describes.
   With the context only on this machine, the list says there is nothing to open yet and
   that saving to Atlan Trident gives each answer its links.
3. After the first answer, record `PROJECT journey asked`. Finish all questions in the
   selected request before the `Next:` line. After an own-data answer, that line offers
   Design a dashboard, Ask another question and I'm done. Design a dashboard follows
   [the dashboard](../../../analytics-ask/references/dashboard.md).

Say once per conversation that answers come from the context built from the dashboard and
queries (the demo firm's on a demo, the person's own on their data), not from a guess at the tables. Names in the data, such as a crew member,
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
the dashboard's place in the `Next:` line. Follow the demo's
[edit a metric](../../../analytics-demo/references/edit-metric.md).

**Wrap up.** On "I'm done", record `PROJECT journey dashboard` if no dashboard was built,
then finish with the saved context or dashboard location. Do not ask another question or
require a restart. Building a demo dashboard ends with the `Next:` line above, including
the option to continue on real data.
