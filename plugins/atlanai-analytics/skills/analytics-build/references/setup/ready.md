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

In every line, I build and test, you choose, and your agent answers. Say that your agent is
this chat only when asked. Do not register, deploy or evaluate a separate agent. Do not answer any question the person has not
chosen. When the question step starts, show what they made, then let them pick a supported question.
On own data, [check accuracy](accuracy.md) says whether this question offers the accuracy
check, and adds its result to the ready block once it has run.

Run `PROJECT question ready`, adding `--data` with the demo's facts file for a demo
([Fixit](../../../analytics-demo/references/yard.json),
[Ticketfuddle](../../../analytics-demo/references/box-office.json)), and nothing for own data.
On own data, add `--accuracy` when [check accuracy](accuracy.md) says the check is available; on a
demo, add it until the check has run, so the demo's ready question offers it too.
Add `--recap` on a resumed run, where the save question was not asked in this chat: only then
does the text say again what was built.
With `show_widget`, add `--widget` here, after a save to Atlan first drawing
`PROJECT visual saved --widget`. Ask one native question with the `header`, the `question` text
and the `options` it prints, exactly as given, and the host's free-text entry. When it prints no
options, ask no native question: print the text as the last thing in the reply and wait.

**The ready block** is the question's text, plain lines that read in any font. The save
question just said what was built, so it does not say it again (except with `--recap`):

- Where the context is stored and how to reach it later: "Stored on this machine, in
  `<folder>`. To use it later, open a chat in that folder and ask about `<topic>`."
- On own data, Needs your review and Held back when there are any, one sentence each.
- Then "What would you like to ask? Pick one, or type your own in Other." When no question is
  offered, "Type it in Other, or pick what's next."

Add nothing to it. Caveats, review warnings and material limitations that
[preview and publish](preview-publish.md) requires in the handoff go in the closing message,
once, under "Good to know". Nothing goes before the question.

Where the host has no question tool, print the same lines as the last thing in the message,
then the options as plain numbered lines, and call no tool after it.

**The options, for both demo and own-data setup.** At most four: questions the checked context
answers, each the question the build wrote for a metric or saved query that passed its check,
then, when they apply, "Check how accurate it is" and (own data only) "Build a dashboard", then
"I'm done". **Check how accurate it is** runs the check as [check accuracy](accuracy.md)
says. **Build a dashboard** follows [the dashboard](../../../analytics-ask/references/dashboard.md).
**I'm done** ends with the closing message, below. No demo ships a question list. Do not
show an Ask AI menu or real-data transition before this first question list.

**The closing message.** Every finished flow ends with it, once: after "I'm done",
after the accuracy check's last question on own data (a demo goes back to its
"What would you like to do next?", whose "I'm done" ends with it), and after a dashboard is built on own
data, the last step, right after its two lines. Run `PROJECT question close`, adding `--score <percent> --scored <count>`
when the accuracy check ran, and `--data` for a demo. Print its `text` as it is, as the last
thing in the reply: what was built, how to use it from now on, and how to share it. Then, only
when the build found caveats the person should know, add "Good to know" with one "- " line per
caveat, each said once in the conversation. No banner, no question, and never the ready block
again.

**The person's own question.** Whatever the person types in the free-text entry is the
question: answer it at once through the ask route, with no extra turn. When they say in words
that they want to ask their own question, reply with exactly one line, "What would you like to
know? For example: `<one suggested question>`", with no summary and no `Next:` line.

A question already supplied goes directly to the ask route. Answer only the question or
questions the person selects or types; listing suggestions never authorizes answering
all of them. If they selected several, finish those answers before offering next steps.
An explicit dashboard or real-data request still takes precedence over this default flow.

**After the selected questions are answered**, end the turn on the answer. Never call a
native question in a turn that answers. Close the answer with one plain `Next:` line naming
the options below that apply at this point, in the person's words, as one sentence and not
a list, then stop. Build it from
the table each time rather than copying an example: after a demo dashboard, Change a rule
and see what happens takes Build a dashboard's place; own-data setup leaves out Build one
on your own data. Treat the person's reply as the matching option. Ask the native
"What would you like to do next?" question only when the person replies without choosing,
and then with no answer text in that turn. The same rule ends the turn after a built
dashboard and after the edit-metric block.

| Option | Description |
| --- | --- |
| Ask more questions | Type another question, or pick one your agent answers. |
| Build a dashboard | I build a page from your metrics that you can open, and change it if you ask. |
| Build one on your own data | Start with your own dashboard link, local folder or BI export, here in this chat. |
| I'm done | Finish the demo here. |

Offer these options in the `Next:` line after answering the person's request and after a
dashboard is built. Ask more questions waits for the person's next question. Own-data setup omits the
real-data transition option, and offers the accuracy check only until it has run, through
[check accuracy](accuracy.md). A supplied question or explicit next action takes precedence
over asking again. Never start a flow until chosen.

Answer only the question the person picks or types, through the ask route, with real
numbers. Every answer has three parts, in this order:

1. **The answer**, led by the number or the name, then what explains it. In a setup or demo run,
   the `answer` visual's card or chip ([visuals](visuals.md)) is that lead, shown unchanged, as
   plain lines with no code fence around them.
2. **Based on**: one line per metric and saved query the answer used, each a link
   to its page, as [definition links](../../../analytics-ask/references/definition-links.md) says. Read that file before
   the first answer. Never print metric or release ids, and never ask for an Atlan address
   before answering: discover the active organization through the authenticated
   self-profile endpoint, as the definition-links reference describes.
   With the context only on this machine, the list names each one without a link and ends:
   "Nothing to open yet. After a save to Atlan, each metric named here has a page to open."
3. After the first answer, record `PROJECT journey asked`. Finish all questions in the
   selected request before the `Next:` line. After an own-data answer, that line offers
   Ask more questions, Check how accurate it is (until it has run), Build a dashboard and
   I'm done. Build a dashboard follows
   [the dashboard](../../../analytics-ask/references/dashboard.md).

With the first answer on own data, say once per conversation: "This answer came from the metrics
and saved queries built from your dashboard and past queries. Each one it used is named below."
Name only the sources the run used. A demo leaves this line out: its answer card's sources say it
([the tour](../../../analytics-demo/references/tour.md)). When the tests were skipped, add: "They aren't tested yet, so check this number
before you rely on it." Names in the data, such as a crew member, appear only when the
question asks who. Never answer a question the person has not asked.

**Build one on your own data.** Load the `analytics-build` skill in the same chat; do
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

**Wrap up.** On "I'm done", record `PROJECT journey dashboard --answer declined` if no dashboard was built,
then finish in three short lines, with no headings or lists, adding the dashboard's location
to the second when one was built:

```
Your <agent name> answers from <N> metrics and <N> saved queries.
They are kept in <workspace> in Atlan.
To ask again, open a chat with Atlan Analytics and <warehouse> connected, and ask a data question.
```

When it stayed on this machine, the last two lines are instead:

```
They are kept on this machine, in <folder>.
To ask again, open a chat in that folder and ask about your <use case> setup.
```

Use the plural that fits each count. The side page and the chat above hold the rest. Do not
ask another question or require a restart. Building a demo dashboard ends with the `Next:`
line above, including the option to continue on real data.

## On resume

When the person comes back and asks what is ready or what they can ask, or `PROJECT journey
status` gives `continuation.action` `ask`, the reply is the ready question, not a summary of
the run in your own words. Run `PROJECT question ready --recap` and give its text and options
as printed: no question of your own, no period or grouping the helper did not write, and no
list of next steps after them. The host's task list carries the steps, so this reply does not
repeat them in chat.
