# Context built, then where to keep it

This moment is shared by all setup through [the presentation flow](presentation.md). The
helper composes the question from the run's own files, for a demo or for own data, so no
count is typed by hand and a demo's words never reach own data.

**Why this moment matters.** It is the finish line of the hard part, and the person should
feel they reached it. Skipping the celebration turns an achievement into a status update,
and the choice that follows into a chore.

**The celebration is the question's own text.** On current models, text written before a
tool call reaches the person as a one-line progress note, and a native question is a tool
call. A block printed before the save question is lost, however firmly it is asked for.
What the question tool is handed arrives word for word, so the whole celebration goes in
the question's text. The terminal keeps its line breaks and leading spaces, shows it with
the options' descriptions, and leaves it in the transcript after the answer.

Do not put it in an option `preview`: a preview is cut to a few lines on a short terminal,
hides the descriptions and is gone once the person answers. Write nothing before the
question: its text already says the checks are done.
Without an Atlan connection this question is not asked: [preview and
publish](preview-publish.md#5-show-the-metrics-before-asking-where-to-publish) says what ends the turn instead.

**The discovery branch asks one thing first.** When the build came from discovery and no
dashboard was part of it, the question before this one is the dashboard offer: read
[it in the interview](intent.md#after-the-build-in-this-branch-only-offer-a-dashboard) and ask
it alone, in its words; this question follows its answer. Every other branch, and a demo, comes straight here.

Ask it only when the collection was exported and holds at least one metric or query. When
the export was refused or the collection is empty, there is nothing to save: say what stopped
it and the one thing that fixes it, and do not offer a destination.

When every check has run, before any save, sign-in or workspace step, run
`PROJECT question save`, adding `--data` with the demo's facts file for a demo
([Fixit](../../../analytics-demo/references/yard.json),
[Ticketfuddle](../../../analytics-demo/references/box-office.json)), and nothing for own data.
With `show_widget`, first draw the checks chip, `PROJECT visual context --widget` (the build's card
came at the build's end; when it did not, draw both with one call, `PROJECT visual build context
--widget`, each moment's HTML ending at its own `side view:` line). Then add `--widget` here. Ask
one native question with the `header`, the `question` text and the `options` it prints, exactly
as given. Without widgets the text says in sentences what the build card and the checks chip
would, each fact once and with no bars: what was built and in how long, what it answers from,
its validation, then the side page's address and the question. No "Next:" line goes before it. Every count is true: when some did not pass their check, the text says so, and the
celebration still stands. If it answers that items still wait for their sample check, do what
it says, then run it again; never ask this question without its text.

Where there is no question tool at all, print the same lines as the last thing in the
message, then the two choices in plain words, and call no tool after it: the last message
of a turn is shown in full.

The two reasons to save are retrieval (an agent finds the metric a question needs) and
consistency (one rule, one number, for everyone). Keep both in the copy, in those plain
words. Do not oversell: say nothing about speed, accuracy scores or governance, and never
suggest the local choice is wrong; it is the right one for a quick look.

Only "Save to Atlan" starts sign-in. With "Keep it on this machine", run
`PROJECT journey keep-local` and go straight to the question step ([ready](ready.md)), with no
line of your own: the ready question says where it is stored and how to reach it.
Own-data setup goes, after either choice, to [check accuracy](accuracy.md) first, which offers
the check and then opens the question step.

**Sign-in.** People worry here about touching company data. Before the browser opens, say:
"Sign in to Atlan with your work account in the browser. Signing in changes nothing, and
nothing is saved until you pick a workspace." After sign-in, name the
tenant the result shows, never a guess.

**Workspace.** Ask one native question, "Which workspace should hold your agent's metrics?". List
the person's own workspace first (the `user_default` one from the workspace read):

| Option | Description |
| --- | --- |
| Your personal workspace (Recommended) | Only you see it. Nothing changes for your team. |
| `<workspace name>` | Everyone in it sees these metrics. To ask from their own chat, each person needs Atlan Analytics and warehouse access. |

Offer at most three shared workspaces, and only ones the read returned. Never offer to create a workspace. With many shared workspaces, show the count and let the
person name one; do not list them all.

**Saving.** Saving goes in batches and can take a few minutes. Say so once, then give a
count as it goes: "Saving to your personal workspace: 12 of 19 saved." Finish with the
`saved` visual, which says what was saved and where, then this line, with no number the visual
already gives:

"Your agent's metrics are now in Atlan. Ask here, or from any chat with Atlan Analytics and
`<warehouse>` connected."

When the save result says they are not released yet, its second sentence is instead: "They're
saved in Atlan but not yet released, so other chats can't answer from them yet."

**The address for proof links, without asking.** Resolve the active organization's slug
from the authenticated `GET /registry/v1/users/me` response, matching `org_id` to an
`orgs[].id`. [Definition links](../../../analytics-ask/references/definition-links.md)
provides the helper, connection checks and exact metric/query routes. The person never
supplies a slug or URL. Workspace reads supply workspace ids, not organization slugs;
do not request a root workspace solely for links or read credentials.

**Already there.** When some of the context already exists in that workspace, say how much,
where, and why that is fine: "4 of the 7 metrics were already in your workspace, so I
reused them. 3 are new."

### 6a. When something does not go to plan

Say what happened, that nothing is lost, and the one way forward. Never leave the person
at an error.

| What happened | What to say |
| --- | --- |
| A metric or query fails its check | "2 of 19 didn't pass their test, so I held them back from the save. That is normal the first time, and the other 17 are fine." Name the two only when asked. |
| Sign-in is cancelled or times out | "Sign-in didn't finish. Your agent is still on this machine. Try again, or ask it a question now." |
| Signed in, but no workspace to save to | "You're signed in, but you have no workspace yet. Your Atlan admin can add you to one. Meanwhile, your agent works here." |
| A save is refused or stops part-way | "12 of 19 saved, and nothing saved is lost. Atlan refused the other 7." Then the one reason the result gave, and whether to retry. |
| Counts differ from a colleague's run | "Saved queries built from past queries can differ between builds. Compare the metrics from your dashboard's tiles instead." |
| Git or Python is missing | The one thing to install, followed by "Then ask me to set up Atlan Analytics again, and I'll pick up here." |

In every case the run can still end on the question list, answered from the context on this machine.

