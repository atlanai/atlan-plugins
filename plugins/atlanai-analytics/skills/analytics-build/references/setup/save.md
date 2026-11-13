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
Ask it only when the collection was exported and holds at least one metric or query. When
the export was refused or the collection is empty, there is nothing to save: say what stopped
it and the one thing that fixes it, and do not offer a destination.

When every check has run, before any save, sign-in or workspace step, run
`PROJECT question save`, adding `--data` with the demo's facts file for a demo
([Fixit](../../../analytics-demo/references/yard.json),
[Ticketfuddle](../../../analytics-demo/references/box-office.json)), and nothing for own data.
With `show_widget`, first draw `PROJECT visual context --widget` and add `--widget` here. Ask
one native question with the `header`, the `question` text and the `options` it prints, exactly
as given: the text keeps the banner, the build's card and the checks chip in a terminal, and
the counts and the question come last, so they stay on screen if a short terminal scrolls the
top away. Every count is true: when some did not pass their check, the text says so, and the
celebration still stands. If it answers that items still wait for their sample check, do what
it says, then run it again; never ask this question without its text.

Where there is no question tool at all, print the same lines as the last thing in the
message, then the two choices in plain words, and call no tool after it: the last message
of a turn is shown in full.

The two reasons to save are retrieval (an agent finds the metric a question needs) and
consistency (one rule, one number, for everyone). Keep both in the copy, in those plain
words. Do not oversell: say nothing about speed, accuracy scores or governance, and never
suggest the local choice is wrong; it is the right one for a quick look.

Only "Save to Atlan Trident" starts sign-in. With "Keep it on this machine", run
`PROJECT journey keep-local`, then go straight to the question step ([ready](ready.md)); its
answers come from the local context.

**Sign-in.** People worry here about touching company data. Before the browser opens, say:
"A browser window opens; sign in to Atlan Trident with your work account. Signing in
changes nothing. Nothing is saved until you pick a workspace." After sign-in, name the
tenant the result shows, never a guess.

**Workspace.** Ask one native question, "Which workspace should hold this context?". List
the person's own workspace first (the `user_default` one from the workspace read):

| Option | Description |
| --- | --- |
| Your personal workspace (recommended) | Only you see it. Nothing changes for your team, and it cannot clash with a colleague saving the same metrics. |
| A shared workspace, by name | At most three, only ones the read returned. Everyone in it sees these metrics. |

Never offer to create a workspace. With many shared workspaces, show the count and let the
person name one; do not list them all.

**Saving.** Saving goes in batches and can take a few minutes. Say so once, then give a
count as it goes: "Saving to your personal workspace: 12 of 19 saved." Finish with the
`saved` visual, which says what was saved and where, and one line that it is now the context
the agent reads, with no number the visual already gives.

**The address for proof links, without asking.** Resolve the active organization's slug
from the authenticated `GET /registry/v1/users/me` response, matching `org_id` to an
`orgs[].id`. [Definition links](../../../analytics-ask/references/definition-links.md)
provides the helper, connection checks and exact metric/query routes. The person never
supplies a slug or URL. Workspace reads supply workspace ids, not organization slugs;
do not request a root workspace solely for links or read credentials.

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
| Counts differ from a colleague's run | "That is expected today: the number of queries built from the history can vary between runs. The metrics from the dashboard's tiles are the ones to compare." |
| Git or Python is missing | The one thing to install, then "run /atlanai-analytics:analytics-demo (or analytics-build for your own data) again and it picks up here". |

In every case the run can still end on the question list, answered from the context on this machine.

