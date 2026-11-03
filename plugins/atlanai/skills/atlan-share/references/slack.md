# Posting a share to Slack

A Slack post goes out as the user, to people outside this conversation, and cannot be unsent.

## Which Slack tool

Use the first that is reachable:

1. **A Slack connector this harness already has**, such as Claude's own Slack connector: its
   tools are listed by name in this session, such as `slack_send_message`,
   `slack_search_channels` and `slack_search_users`, often under a server prefix. It posts in the
   user's own Slack with no Atlan call.
2. **Slack registered in Atlan**: `mcp__search_tools { "query": "send a Slack message", "session_id": "mcp_session_…" }`.
   Run what it finds with the runner its `run_with` names (a post is a write:
   `mcp__call_write_tool`), by its exact name and schema. When Slack comes back
   under `connect` instead of as tools, ask whether to connect it and follow
   `atlan-use-mcp-servers`.
3. **Neither**: say in one line that Slack is not connected here, and give the post as text the
   user can paste.

Never post with a lookalike tool from another product.

## Where it goes

In order: the channel or person the user named, then `slack` in `.atlan/share.json` (with no
working folder, as in Claude chat, the channel remembered in the tool's memory or project
instructions). With none, suggest one channel whose name matches the workspace when a channel search tool is
available (`#platform-eng` for Platform Engineering), and ask.

- **A channel**: resolve the name with the tool's channel search when it has one. Read its topic
  or description; if it says this kind of post belongs elsewhere, tell the user instead of
  posting. A name that matches no channel is asked about, never guessed. A tool with no channel
  search takes the channel by the name the user gave.
- **A person**: resolve them with the tool's user search, by name or email. Several matches or
  none is a question to the user. Never guess an address or a user id.

## What the post says

At most 60 words, plain text, not counting the link:

```
Shared in Atlan: Q3 churn report (v2), in Platform Engineering.
Churn fell from 4.1% to 3.2%, mostly in the SMB tier. Two follow-ups are open.
Open it: https://app.atlan.com/?open=%2Facme%2Foutputs%2Foutput_…
In Claude or Codex: /atlanai:continue output_…
```

The title and version, the workspace, the two-sentence summary from step 3, the link from step 4
when there is one, and the hand-off prompt. Never the content itself, a file's bytes, or anything step 1 flagged.

## Approval

Show the post and its destination, and send only after a yes. A request that already names the
destination and says to go ahead is that yes. A destination saved in `.atlan/share.json` is not:
it saves asking where, not asking whether.

After sending, say where it went in one line, without quoting the post back. A refusal from Slack is reported as it came, once;
do not retry with a different channel or tool.
