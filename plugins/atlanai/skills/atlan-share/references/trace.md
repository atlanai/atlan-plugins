# Pushing this session's trace

`/atlanai:share-trace` uploads one session, this one, to one organization, after the person sees
exactly what leaves the computer and says yes. It is the opt-in alternative to the Atlan desktop
app's background sync, which uploads every local session to every organization whose traces job
is on. It never turns that sync on, off or changes it, and never uploads any session but this one.

The person's trust is the point. Say only what a command showed; where something cannot be read,
say so in those words and do not guess. Ambiguity (which organization, which session) is a
question, never a default. Use a shell; where there is none (Claude chat, Cowork), say pushing a
session needs Atlan's CLI on the person's computer, and stop.

## 1. Lead with the sync state, before anything else

1. Find the CLI: `command -v aireg || command -v brain || ls ~/.aireg/bin/aireg`. Its installer
   may name it `brain`; the two take the same arguments. Missing: reply "Atlan's CLI (aireg) is
   not on this computer, so nothing from it syncs to Atlan, and this session cannot be pushed from
   here. Installing the Atlan desktop app adds it." and stop.
2. `aireg auth list --json`: `data.accounts[]`, each `{id, registry, signedIn}`. `registry` is
   the organization's slug when the desktop app added it. The target is the organization the
   request names (match `registry`), else `org` in `.atlan/share.json`, else the only signed-in
   account. Signed out of the target: "Signed out of `<registry>`: run
   `aireg auth login <registry>`, then ask again." and stop.
3. `aireg job list --json`: `data.paused` and `data.jobs[]`. A command that fails or JSON
   without `data.jobs`: reply "Cannot read whether background sync is on for `<registry>`
   (`<the error>`), so nothing is pushed." and stop. The target's traces job has an `id` starting
   `job_traces_` and the target's `id` as `account`. It covers this session when all hold: the
   job exists, `schedule.enabled` is true, `data.paused` is false, its `traces` step's
   `extra.harnesses` (or `extra.targets`) is `all` or includes this tool (`claude` for Claude
   Code, `codex` for Codex), and its `select` is empty or one entry's `from` is this session's
   working folder or a parent of it.
4. Find this session and preview it, as [section 2](#2-find-this-session) says. The preview
   uploads nothing.
5. `aireg trace list --account <registry> --for <claude-code|codex> --json`: the `data.files[]`
   row whose `sessionId` is this session's. `state` `finalized` is "already uploaded", `partial`
   "partly uploaded", `unseen` "not uploaded yet". A failure, or no row for this session, is
   "cannot tell whether this session was uploaded already".
6. The reply's first line, before any other step or question, joins the two:

   | State | First line |
   |---|---|
   | Covered, `extra.content` `store` | "Background sync is ON for `<registry>`: every `<schedule.every / 60>` min it uploads this computer's sessions, with what was said. This session: `<already uploaded \| partly uploaded \| not uploaded yet>`." |
   | Covered, `extra.content` `drop` | The same, with "their structure, not what was said" |
   | No job, disabled, paused, or not covering this session | "Background sync is OFF for `<registry>`: nothing from this computer uploads unless you push it. This session: `<…>`." |

   With several signed-in accounts and none named, one such line each, then ask which to push
   to, and stop there. ON means a push only uploads it sooner; go on when the person still wants
   it, or to attach it as feedback (step 4).

## 2. Find this session

**Claude Code** gives this session's id where the command or skill says `${CLAUDE_SESSION_ID}`.
Pass that id to the preview and aireg finds the transcript itself; never pick a file by date in
its place:

```
aireg trace export <session id> --account <registry> --for claude-code --content <store|drop> --dry-run --json
```

Content is the job's `extra.content` when the target has a traces job, else `store`; the person
saying "structure only" makes it `drop`. `files[0].path` is the transcript, `sessions[0]` the
session. An error naming no transcript: say aireg cannot see this session's transcript, and stop.

**Elsewhere** (Codex, or the id still reads as a placeholder): the newest `rollout-*.jsonl` under
`~/.codex/sessions/` with `--for codex`, else ask which transcript, naming the first prompt of
each candidate. Pass that one path in place of the id.

The selector is always one session id or one path: with none, `trace export` uploads every
session it finds. Never pass `--all` or `--since`.

## 3. Show exactly what leaves the computer, then ask once

From the section 2 preview, which uploaded nothing and moved no cursor, and the transcript at
`files[0].path`, show in plain words, one line each:

- **Session:** its title (the transcript's last `custom-title` or `summary` record, else the first
  prompt, cut to 60 characters) and `sessions[0].sessionId`.
- **Size and time:** the file's size, and the first `timestamp` in the file to its last change.
- **What leaves:** for `store`, "what was said (prompts, replies, tool inputs and results, files
  read into the session, the title) and its structure"; for `drop`, "structure only: tool names,
  timing, token counts, nothing that was said". Add: "This computer cannot read `<registry>`'s
  content policy before the upload; if an admin set it to structure only, only structure is kept."
- **Where:** the organization `<registry>`, and the feedback note of step 4 with its workspace
  when there is one.
- **Who can see it:** "You, under History, and `<registry>`'s organization admins can read what
  was said; other members see usage totals only." For `drop`, "structure" in place of "what was
  said". A feedback note: everyone in its workspace.
- `files[0].disposition.kind` `partial`: "The last turns stay on this computer until the session
  ends."

End with one question, and offer nothing else (no feedback note the request did not name, no
sync change): "Push this session to `<registry>`?" Upload only on a clear yes in the
person's next message. Anything else (a change, a question, silence) uploads nothing.

## 4. Push, then attach it when asked

```
aireg trace export <the same session id or path> --account <registry> --for <claude-code|codex> --content <store|drop> --json
```

Read `sessions[0].state`, `traceId` and `policy`. `policy.content` `drop` after a `store` preview
means the organization keeps only structure: say so. `policy.source` `job` means the organization
set no policy and what was said is stored. A failed session: what `sessions[0]` or the error
names, and one step to fix it.

**Attaching it as feedback**, only when the request names a skill, agent or output, or a note:
a trace is not an artifact, so it is recorded as an `output` that points at it. Find the named
artifact with `registry__search_artifacts` (or read the handle given), then follow
[recording a share](write.md) in its workspace, one call to `extension__agent__publish_output`:

- `source_type` `local`, `source_id` `claude/trace-feedback-<traceId>`;
- `metadata.display_name` "Feedback: `<artifact or session title>`", and `file` a short Markdown
  note: the person's note in their words, the artifact's handle, the trace id and its link;
- `edges` `[{"rel_type": "used_skill", "to_id": "skill_…"}]` when the artifact is a skill, the one
  edge `output` declares. For an agent or an output, no edge: the note names it.

Step 3's yes covers this write only when the preview named it. The Atlan connection reaches one
organization; when the target is another, push the trace and say the note needs a session signed
in to `<registry>`.

## 5. Say what happened and how to undo it

At most five lines:

- what went up: the session, what was said or structure only, the organization;
- the trace: `https://app.atlan.com/<registry>/registry/traces/<traceId>`, and the feedback
  note's output link when one was written ([the link](link.md));
- undo: "To remove what was said, delete this trace's content:
  https://platform.atlan.com/registry/traces/references/limits-and-privacy#delete-trace-content.
  The structure (timing, tool names, token counts) cannot be deleted." A feedback note adds
  "Archive the note by asking to archive `output_…`.";
- the sync state, unchanged: "Background sync stays `<ON|OFF>` for `<registry>`."
