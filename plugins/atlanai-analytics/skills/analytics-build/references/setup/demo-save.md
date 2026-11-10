# Demo: context built, then where to keep it

**Why this moment matters.** It is the finish line of the hard part, and the person should
feel they reached it. Skipping the celebration turns an achievement into a status update,
and the choice that follows into a chore.

**Two rules for this moment.** Your reasoning is never shown to the person: a note in
your thinking is not a reply, however it is worded. So send the block below as reply text,
in the same message as the question and before it. A question sent without the block above
it is a failure: send the block first.

Open the chosen demo's file again for its facts ([Fixit](demo-yard.md),
[Ticketfuddle](demo-box-office.md)).

This is the moment the person finished the hard part, and they should feel it. When step
4 ends, before any save, sign-in or workspace question, print the block below whole and
as written, as an ordinary reply, exactly as the welcome is printed. It is a fixed text:
never replace it with a line such as "the checks passed, moving on". Keep it in its code
fence so the confetti keeps its shape. The only changes are the values in
`<angle brackets>`, filled from this run and the sources the person chose. Every count is
true: when some did not pass their check, the last line says so ("17 of 19 checked on
`<firm>`'s data, 2 need a look") and the block still prints.

```
  *   .  '   *    .   '  *   .    '   *  .   '    *   .
    '    *   .    CONTEXT BUILT    .   *    '    .   *
  .   *    '   .    *   '    .   *    '   .   *    '   .

    You turned <firm>'s dashboard and queries into context.

      <N> metrics and <N> queries
      built from <1 dashboard and N past queries>
      <N> of <N> checked on <firm>'s data
```

Then one line, as written: "That was the hard part. `<firm>`'s numbers now each have one
agreed rule, and an agent can answer from them."

Under it, say why the next choice matters, as written: "Right now this context lives in
one folder on this machine, and only this chat can use it. In Atlan Trident it becomes the
one place your agents look: each question finds the right metric, and everyone gets the
same number from the same rule."

Then ask one native question. The prompt carries the reason, because the lines above can
collapse: "Step 5 of 6. Context built: `<N>` metrics and `<N>` queries, `<N>` of `<N>` checked.
It works here, in this folder. Saved to Atlan Trident, any agent can find it and every
answer uses the same rule. Where should it live?"

| Option | Description |
| --- | --- |
| Save to Atlan Trident (recommended) | Agents find the right metric for each question, from any chat or tool. Everyone gets the same number, and a change to a rule reaches all of them at once. You sign in once. |
| Keep it on this machine | Works in this folder only. Other chats, tools and colleagues cannot find it, so each would work the numbers out its own way. |

The two reasons to save are retrieval (an agent finds the metric a question needs) and
consistency (one rule, one number, for everyone). Keep both in the copy, in those plain
words. Do not oversell: say nothing about speed, accuracy scores or governance, and never
suggest the local choice is wrong; it is the right one for a quick look.

Only "Save to Atlan Trident" starts sign-in. With "Keep it on this machine", go straight to
step 6 ([ready](demo-ready.md)); its answers come from the local context.

**Sign-in.** People worry here about touching company data. Before the browser opens, say:
"A browser window opens; sign in to Atlan Trident with your work account. Signing in
changes nothing. Nothing is saved until you pick a workspace." After sign-in, name the
tenant the result shows, never a guess.

**Workspace.** Ask one native question, "Which workspace should hold this context?". List
the person's own workspace first (the `user_default` one from the workspace read):

| Option | Description |
| --- | --- |
| Your personal workspace (recommended) | Only you see it. Nothing changes for your team, and it cannot clash with a colleague running the same demo. |
| A shared workspace, by name | At most three, only ones the read returned. Everyone in it sees the demo metrics. |

Never offer to create a workspace. With many shared workspaces, show the count and let the
person name one; do not list them all.

**Saving.** Saving goes in batches and can take a few minutes. Say so once, then give a
count as it goes: "Saving to your personal workspace: 12 of 19 saved." Finish with one
line: what was saved, where, and that it is now the context the agent reads.

**The address for proof links.** Once the save finishes, ask for the Trident address as
[definition links](../definition-links.md) says, so the first answer already links to its
proof. Ask it as the last message of the turn, in plain words, and go on when it is given
or declined.

**Already there.** When some of the context already exists in that workspace, say how much,
where, and why that is fine: "4 of the 7 metrics were already in your workspace from an
earlier run, so I reused them instead of making copies; 3 are new."

### 6a. When something does not go to plan

Say what happened, that nothing is lost, and the one way forward. Never leave the person
at an error.

| What happened | What to say |
| --- | --- |
| A metric or query fails its check | "2 of 19 did not return sensible numbers, so I left them out of what gets saved. That is normal, and the other 17 are fine." Name the two only when asked. |
| Sign-in is cancelled or times out | "Sign-in did not finish. Nothing is lost: the context is still on this machine. Try again, or keep it here and ask your question now." |
| Signed in, but no workspace to save to | "You are signed in, but you have no workspace to save to yet. Your Atlan admin can add you to one. Meanwhile the context works from this machine." |
| A save is refused or stops part-way | "12 of 19 saved; the rest were refused. Nothing saved is lost." Then the one reason the result gave, and whether to retry. |
| Counts differ from a colleague's run | "That is expected today: the number of queries built from the history can vary between runs. The metrics from the dashboard's four tiles are the ones to compare." |
| Git or Python is missing | The one thing to install, then "run /atlanai-analytics:start again and it picks up here". |

In every case the demo can still end on the question list, answered from the context on this machine.

