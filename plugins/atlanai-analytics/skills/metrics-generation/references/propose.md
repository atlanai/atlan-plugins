# Propose a metric from this conversation

`/atlanai-analytics:metrics-generation propose` turns a gap this conversation found into a proposal that the
metric's owners review in Metric Studio:
- a **new metric** when an answer had no published metric for what was asked;
- a **fix** when the person said a published metric's number was wrong, and why.

It only proposes. It never creates or saves a metric, even for someone who could. It needs
Atlan Trident access; the warehouse query tool is used when one is recorded or connected.

## 1. Name the gap

From this conversation, write down:
- the kind: **new metric** (an answer said nothing in Trident covered the measure) or
  **fix** (the person corrected a published metric);
- the measure in a few words, the workspace the answer read, and for a fix the metric's id;
- each question as the person asked it, when, and who asked, by name (ask in the confirmation
  below when the conversation does not show it).

Confirm it with the person in one sentence: "I'll propose a new metric for <measure> to the
owners of <workspace>. Right?" or "I'll propose a fix to <metric> to its owners in
<workspace>. Right?" If the conversation shows neither kind, ask what
to propose rather than guess.

## 2. Check what exists

Open a Trident session as **Atlan Trident calls** in the conventions describes, then:
- **New metric:** search the workspace for the measure under its likely names
  (`registry__search_artifacts`, kind `metric`). If a metric already covers it, write nothing
  and point the person to it: "<metric> already covers this, so I didn't propose a new one. Your
  agent missed it when answering."
- **Both:** list the workspace's proposals and apply the duplicate rules in
  [the proposal record](propose-record.md). If one already covers it, write nothing and tell the person: "A
  proposal for this already exists, so I didn't add another." If it was dismissed, say "Its
  owners already decided against this, so I didn't propose it again."

## 3. Draft

- **New metric:** generate the definition through [the main metrics engine](metrics-engine.md)
  from source-backed model or SQL evidence, using related released metrics to establish
  population, filters and scope. Complete its gates and use the exported definition in the
  proposal. Without execution access, record that check limitation in the rationale.
- **Fix:** read the current head with `registry__get_artifact` (no release selector).
  Calculation, population or source changes use the same engine and source evidence. Propose
  its checked revision. Metadata-only corrections can retain the existing definition.


## 4. Attach this conversation as the run

Run `EVAL session-run`, that is
`python3 "<build-dir>/scripts/analytics_eval.py" session-run`, with `<build-dir>` the `analytics-build` skill directory
(from Codex, add `--harness codex --session-id <its session id>`). Its `trace_id` names the
trace this session becomes once the desktop app syncs it. Add one
`evidence.runs` entry per question: `{trace_id, agent, question, asked_by, at}`, where `agent`
is the talk-to-data agent that answered, or the harness when the person asked directly,
`asked_by` is the person's name when the conversation shows it, or `"unknown"` (don't ask just
for this), and `at` is when they asked, in UTC, or the time of this
propose run when the conversation does not show it. Tell
the person: "In Metric Studio, the owners see your question, who asked it and when. A link to
this chat works there once this session syncs to Atlan."

## 5. Preview, then write

Preview the write as the conventions require: the target workspace and the record. Write only
after a yes. Create it with `registry__create_artifact`, kind `file`, as [the proposal record](propose-record.md) describes:
`media_type` `application/vnd.atlan.improvement+json`, the record in `source_metadata`
`{"atlan_improvement": <record>}`, and `requested_by` `Atlan`. Name it for example
`propose-<metric>-<YYYYMMDDHHMM>` at `improvements/metric/<metric>-<YYYYMMDDHHMM>.json`.
For a new metric, put `new-` before `<metric>` in both. Its record leaves `subject_id` empty,
sets `subject_name` to the new metric's name, has no base or `changes`, and carries `proposed`
(`{name, display_name, description, definition}`) and a top-level `rationale`.

If Atlan Trident refuses the write because the person can only view the workspace, write the
proposal nowhere else: Metric Studio lists only the proposals in the metric's workspace. Say
"You can only view <workspace>, so I couldn't file this. Here it is in full." and give the
full record, so the draft is not lost. Then say who can file it: for a fix, "The metric's owner
can file it." (its `owner_id`); otherwise "Anyone who can edit <workspace> can file it."

Tell the person "Filed proposal <id>. Its owners review it in Metric Studio's metrics list in
Atlan." (`suggestion=<file id>` opens it there directly.) End naturally with the result and the owners' next action, as `conventions.md` describes.
