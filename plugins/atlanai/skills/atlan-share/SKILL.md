---
name: atlan-share
description: Save, record or share a deliverable in Atlan as an output, and post it to Slack. Use when asked to share, send or hand this off, save or record a report in Atlan, or publish its new version. Not for skills (atlan-author-skills).
license: Apache-2.0
user-invocable: false
---

# Sharing and recording deliverables in Atlan

A report, document, dashboard or dataset goes into Atlan as an `output`, whether it is shared
with colleagues or only recorded. That is four steps: decide who it is for, record it where they
can open it, hand back how to open it, and tell them if the user wants. The user answers at most
one question and approves once, for all of step 3's writes.
Calls below are search-mode runner calls: `mcp__call_read_tool` for a read and
`mcp__call_write_tool` for a create. The first one opens the session by sending
`objective` beside `name` and returns `mcp_session_id`; pass that as `session_id`, and a
`rationale`, on every later call. Once the session is open, a `registry__` read this host also lists by name can be called directly, with `session_id` and `rationale` as its own arguments and no runner.
When the host lists `mcp__call_read_tools`, send up to 8 independent reads as one call,
`{ "calls": [{ "name", "arguments" }, …], "session_id", "rationale" }`; otherwise in parallel. Replies lead with the result. Check each before sending: no greeting, apology or closing offer; counts rather than lists, handles only when asked; one next step, not a menu; nothing about calls skipped or retried; under seven lines, not counting a table or a list the user asked for.
A read that fails with 502, 503, 504 or `retryable: true` is sent again unchanged, 3 tries
in all (waiting `retry_after_seconds` first when the error gives it), before you tell the user
Atlan is not answering. Never repeat a write: read it back first.

## 1. Know what is being shared

Name the thing in one line: a file path, or the content Claude wrote in this conversation. If
the request could mean several files, ask which. Then route by what it is:

| It is | Do |
|---|---|
| A skill folder (it has a `SKILL.md`) | Load `atlan-author-skills` and publish it; return here at step 4 with its handle |
| An agent definition | Load `atlan-author-agents`; return at step 4 |
| A glossary, runbook or reference notes meant for agents to read | Load `atlan-author-knowledge`; return at step 4 |
| Anything else: a report, document, notebook, CSV, HTML page, deck, diagram | Share it as an `output`, steps 2 to 5 |

Read the content before sharing it. Stop and show the user, with values masked, anything that
looks like a credential (a private key block, a token such as `sk-…`, `ghp_…`, `xox…` or
`AKIA…`, a `password=` or connection string) or a list of people's personal details. Share only
after they say so, or after they remove it. Where the publish helper runs, it refuses a
`registry__publish_output` whose text still matches a credential shape, naming the line: mask
the value or swap in a placeholder, then send again. Never route around the refusal.

## 2. Decide who it is for

**Recording with no audience** is the same output in a different workspace. When the user asks
only to save, record or keep it in Atlan, or an agent run records its own deliverable, use the
workspace the user names, else `.atlan/share.json`'s, else their `user_default` one from
`registry__list_workspaces`. Ask nothing, and skip step 5.

To share, remember Atlan shares by workspace, not by person. Default to the workspace the request names, then the
one in `.atlan/share.json`, then the only team workspace; never to the user's personal one, which
reaches nobody. With no default, ask one question. [Choosing the audience](references/audience.md)
has the settings file, the workspace kinds and how to say each one.

## 3. Record it, after one approval

Every share carries a source key (`source_type` `local`, `source_id` the file's
`<repository or folder>/<path>` or `claude/<title in kebab case>`), so sharing the same thing
again makes a new version of one output instead of a copy. Look for an earlier share first, show
the user what will be written in at most four lines, and write only on their yes. A request
that already names what to save and where ("record this in my personal workspace", "share
this report with the Data team") is that yes: write, then say what was written. Ask first when
anything is left to choose, when it would replace a different earlier share, or when step 1
found something to mask. The write is:

- **one call,** `registry__publish_output`, writes the bytes as a `file`, then the `output` that
  pins it and its edges;
- **a new version** is the same call with the same source key: it appends to the earlier share's
  file and makes the output's next version, and unchanged text writes no new file version.

[Recording a share](references/write.md) has the calls, the chat fallback for large or binary
files, and what each refusal means. For a deliverable an agent run produced, with its producer
fields, a hosted link instead of a file, or a gateway without `publish_output`, follow
[recording a run's output](references/record.md); [output modes](references/output-modes.md)
has the `upload` and `link` rules in full.

## 4. Hand back how to open it

Reply in at most three lines: what was shared and its version, who can open it, and how a
colleague picks it up:

- **the link**, which opens the output in the Atlan app, built as [the link](references/link.md)
  says from the organization's slug, the output's handle and its workspace. Leave it out when
  the slug is unknown;
- **the hand-off prompt**, in a code span, for a colleague who works in Claude or Codex:
  `/atlanai:continue output_…`.

Anyone in the workspace can also find it by searching Atlan for its title.

When the session has a working folder and `.atlan/share.json` is missing or names a different
workspace or Slack destination, end the reply with one short sentence offering to save this
share's choices there, so the next share asks nothing. When the slug was unknown, the same
sentence asks for the address of their Atlan dashboard, so later shares carry a link. Write it
only on a yes, with only the keys [Choosing the audience](references/audience.md) lists.
Committing it gives the whole team the same default.

When step 5 posts, the whole reply, post included, stays within four lines. Report what the tools
returned; do not add caveats about what they did not.

## 5. Tell people, when Slack is connected

Post when the user asked to. When `.atlan/share.json` names a `slack` destination, show the post
and that destination and send on a yes: a saved destination answers where, not whether. Otherwise
offer it in one line, and only if a Slack tool is reachable. Skip this step for a share only the
user can open: the post would point colleagues at something they cannot see.

[Posting to Slack](references/slack.md) says which Slack tool to use, how to resolve the channel
or person, what the post says, and when it needs the user's yes.
