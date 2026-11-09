# Feedback to Atlan

Atlan wants to hear when the plugin, a skill or a gateway tool falls short, and when it does
surprisingly well. Good feedback is ticket-ready: someone at Atlan can reproduce it and see what
the user was trying to do.

## When to offer

Offer once per conversation, and only in these cases:

- The user runs `/atlanai:feedback`, or asks to send Atlan feedback. Draft it now.
- An Atlan call failed and this skill's tables did not fix it, or the fix was a workaround.
- A skill led you wrong: it sent you to a tool that refused, or its steps did not match what the
  gateway did.
- A hook or the session notes asked you to assess the Atlan work in the last turn, and something
  stood out, good or bad. If nothing stood out, say nothing and carry on.

Finish or unblock the user's task first. Then offer in one sentence, such as "This looks like a
gap in the plugin. Want me to draft feedback for Atlan?" A no ends it for the conversation.

## Draft

Write the draft in the reply, under these headings, in plain sentences:

- **Goal**: what the user was trying to get done, and why, in one or two sentences.
- **Steps**: the skill, the tool names and the order you called them in.
- **Expected**: what should have happened.
- **Observed**: what happened, with the error's `code` and its message.
- **Surprising**: what was unexpectedly good or bad, if anything.
- **Environment**: the host (Claude Code, Codex, Cursor or another), the `atlanai` version if you
  know it, and the date.

Leave out what is not needed to reproduce it: no tokens, upload URLs, session ids, file contents,
customer data, or names of the user's people, workspaces and artifacts. Write "a skill in a team
workspace", not its name. Show the draft and ask the user to edit it or say yes.

## Send

Nothing is sent by a tool. On the user's yes, give them a link that opens the draft in their mail
app, addressed to ask@atlan.com:

```
mailto:ask@atlan.com?subject=<subject>&body=<draft>
```

The subject is `atlanai feedback: ` and five to eight words. Percent-encode both, with `%20` for a
space and `%0A` for a line break. If the link would pass about 1,800 characters, give the address
and the draft as text to paste instead. Say that Atlan may follow up by email, then go back to the
user's task.
