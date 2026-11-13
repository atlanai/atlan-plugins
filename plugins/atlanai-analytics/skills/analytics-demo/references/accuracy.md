# Check how accurate it is

Read when the person picks **Check how accurate it is**, or asks how accurate the agent built in
this folder is. The demo has built the context; this shows how often it gets an answer right, on
questions the firm's analysts already ask, and fixes the rule behind a miss. The demo orchestrates
the check and owns acting on its result. It calls [eval-generation](../../eval-generation/SKILL.md)
to generate or save a set and [accuracy-check](../../accuracy-check/SKILL.md) to find the run, load
a set, run it, score it and test again. `<agent>` and `<firm>` come from the demo's facts file;
`CHECK`, `EVAL` and `PROJECT` are as accuracy-check's [run and score a set](../../accuracy-check/references/run.md)
names them, and `ACC` is `python3 "<demo-dir>/scripts/accuracy.py"`, with `<demo-dir>` this skill's
directory.

When the host keeps a task list, its items are these, in the person's words, whichever questions
are picked: **Choose the questions**, **Ask each one to a fresh agent**, **Score the answers**.
When the person picks Improve it, add **Find what to fix**, **Apply the fix** and **Test again**
to the list before diagnosing, and mark each done as it finishes.

Every step follows [text before a native question is lost](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost):
a block to read before choosing is the question's own text, as plain lines with no Markdown, with
no Doing or Why lines. Every step that leads to a choice ends on a native question.

An eval set lives in the project (`.atlan-evals/sets/`), never fetched from Atlan Trident: never
look up Trident, a workspace or saved evals for the questions, the run or the score. Only step
4's Trident fix and step 5's save touch a workspace.
`context_saved_to` (from `CHECK start` and `CHECK load`) says where the context lives; `null` means
this machine only.

## 1. Choose the questions

`CHECK start --project <project>` returns the built run, `context_saved_to`, the project's
`sets` and, when there is a set, `ask`: the native question to put as it is (its `question`,
`text` and `options`). With no sets, or the person asking for new questions, generate. Otherwise, unless the
request named a set, ask one native question, even when the folder holds only one set, whose text is this state line, as plain lines:

```
This folder has <N> eval set(s): <set name>, <N> questions with their trusted answers.
Next I ask each question to a fresh agent that sees only your context, and compare
its answer with the trusted one. Which questions should I test with?
```

Nothing else goes in it or before it: not a set's notes or expected answers, where its questions
came from, or what the test is likely to find. A brought set runs as it is; only generated
questions are shown with their expected answers, in step 2, because the person has not seen them.

| Option | Description |
| --- | --- |
| `<set name>` (`<N>` questions) | The eval set you already have, one option per set. |
| Generate 5 new questions | From your dashboard and the queries your analysts run most. |

- **A set:** picking it is the approval to run it. `CHECK load --project <project> --file <set>`,
  then step 3, with no list to confirm.
- **Generate:** run `eval-generation` `generate` (not its `save`), then `CHECK load` on the set it
  wrote, then step 2.

`CHECK load` drops a question whose trusted SQL fails or returns nothing; name how many.

## 2. Generated questions: show them, then ask to run them

The person has not seen these, so show them before running. Ask one native question whose text
is this block, as plain lines: each question from `<evals-run>/work/spec.json`, its metric, source
and expected answer from `<evals-run>/work/selection.json`, and the use case from `CHECK start`:

```
<N> questions to test your <agent>. They test the metrics it needs to answer
<the use case>, starting with <focus metric>. Each comes from <firm>'s dashboard or a
query its analysts already run, and the expected answer is what that query returns.

1. <question>
   Tests <metric>, from <where it came from>. Expected: <one short value>

Run these <N> questions?
```

The expected answer is one value or one short phrase, never a table, and never SQL unless asked.

| Option | Description |
| --- | --- |
| Run them (Recommended) | Each goes to a fresh agent that sees only your context, about 2 minutes. |
| Not now | Keep the questions on this machine and stop here. |

There is no edit option.

## 3. Run and score

Run the set with `accuracy-check`, as its [run it](../../accuracy-check/references/run.md#run-it) describes. With every question right, go to
step 5. With misses, ask one native question, "Does the score look good?", whose text is the
score and the misses as its [show the score](../../accuracy-check/references/run.md#show-the-score) describes, as plain lines:

| Option | Description |
| --- | --- |
| Improve it (Recommended) | I look at why each miss failed and what would fix it. |
| Looks good | Keep this score as the bar to beat. |

Name no fix in the question's text: the fixes come from the diagnosis.

## 4. Improve it: find the fix, apply it, test again

**Find what to fix.** The set is the source of truth for the rule: a miss is fixed in the context,
even when the dashboard or other queries follow the old rule. Never weigh the set against the
context's sources, question who wrote it, offer changing the test, or say that it cannot be
changed; the one exception is *question asks the opposite*, where the wording, not the rule, is
wrong. For each miss, from accuracy-check's why it failed and, when the set gave a note, the question's `question_spec`
in `<evals-run>/work/spec.json`, name the fix:

- **The change.** Each change names a definition field (`source.sql`, `mandatory_filters`,
  `population`, `exclusions`, `examples` and so on) or the metric's own `description`. A rule
  change covers every place the metric states the rule: its source SQL and filters, its
  population and exclusions, each example whose SQL holds the old rule, its `description`, **and**
  the engine card's own notes when they state it (`extensions.metrics_agent.card.gotchas` and
  `.description`). Another metric whose description or card states
  the old rule (a related metric's description repeating the rule the fix replaces) gets its own fix entry: an
  agent that reads the old rule in any metric still answers with it. A miss on wording is fixed by adding that
  question to `examples`, with the SQL that answers it. A question that asks the opposite is a
  redraft; a miss with no definition is a new metric.
- **Write the fixes** with `ACC fixes --evals <evals-run> --file <a JSON list>`, which checks them
  and writes `<evals-run>/fixes.json`, the recommended one first (the one that turns the most
  misses): `[{"misses": ["<id>"], "why": "<reason>", "metric": "<name>", "rule_now": "<plain
  words>", "rule_proposed": "<plain words>", "changes": [{"path": "<definition field>", "value":
  <new value>}]}]`. A redraft (`"kind": "redraft"`) or a new metric (`"kind": "new_metric"`) has
  no `changes`.
- **Ask "Apply this fix?"**, one native question whose text is the fixes as plain lines, and
  nothing after the options: not saved queries that follow the old rule, nor what else the fix
  might touch. One per
  miss, the question, why it failed, and the fix (rule now, rule proposed), the recommended one
  first.

  | Option | Description |
  | --- | --- |
  | Apply it (Recommended) | I change `<metric>` so `<rule proposed, plain words>`, and each other metric the fix names, then test the same questions again. In Atlan Trident this releases the new version. |
  | Keep the score | Keep this score as the bar to beat. |

**Apply it** is the yes through the next score, so ask nothing more until the score, unless a
Trident write is refused and the owner must act in Metric Studio. Keep the score goes to step 5.

- **Context on this machine:** write `{"name": "<metric>", "changes": <the fix's changes>}` to
  `<evals-run>/fix-request.json`, never in the run (an answering agent that reads the proposed
  rule there no longer tests the context), and run `PROJECT edit-metric <that file>`. It keeps the old version beside the run (never where answering agents read), raises the
  version, re-checks the metric and keeps the engine's own copies of it in step; a failing check
  keeps the old rule, so say so and ask "Apply this fix?" again with the next fix. Change only that metric,
  never a file by hand, and do not raise saved queries with the person.
- **Context in Atlan Trident:** Apply it is the approval, so apply and release the fix here, with
  no second approval in Metric Studio. For each metric the fix changes:
  1. Write the proposal with `registry__create_artifact` as
     [the proposal record](../../metrics-generation/references/propose-record.md) describes (the
     fix's definition changes and the missed questions as `evidence.runs`), so the change and its
     evidence are on record in Metric Studio.
  2. Read the metric's head (`registry__get_artifact`, `properties: []`) for its `etag` and
     `version_ordinal`, then `ACC version --evals <evals-run> --run <run> --metric <name>
     --head-version <version_ordinal>` gives the new version, the fix applied to the definition
     the save sent, description included.
  3. Send `registry__append_artifact_version` with that `metadata` exactly as printed, `if_match`
     the head's `etag`, and release the new version to `latest` with `registry__release_artifact`
     unless the workspace already released it.
  A refusal at step 2 or 3 (someone changed the metric since, or the person may not write to
  it) leaves the proposal for its owner: give its Metric Studio link (`suggestion=<file id>`) and
  ask "Approved it in Metric Studio?" (Approved, test again / Not now, which goes to the result).
  As on this machine, do not raise saved queries with the person.

Then have `accuracy-check` [test again](../../accuracy-check/references/run.md#test-again) on the
same eval folder, naming the new `version_ordinal`. When a question that uses the changed metric
gets worse, withdraw that release with `registry__withdraw_release`, which puts `latest` back on the
previous version, and say so. A question on another metric that gets worse is a miss for the next
fix, never a reason to undo this one.

With every question right after the test again, go to step 5. With a miss left, ask "Does the
score look good?" again as step 3 describes, with the new score; Improve it finds the next fix.

## 5. The result

The result block is these lines, as plain lines, and nothing else:

1. Every question right: this banner. Otherwise no banner.

   ```
    *   .  '   *    .   '  *   .    '   *  .
    '   *   .   <N> OUT OF <N> RIGHT   .   *   '
    .   *    '   .    *   '    .   *    '  .
   ```

2. The result line, in percentages. After a fix: "Your <agent> has reached 100% accuracy, up
   from <p>%, now that <what changed, plain words>." First time: "Your <agent> is at 100%
   accuracy on the <N> questions <firm>'s analysts ask most." Some missed: "Your <agent> misses
   <what the misses share>."
3. The score line, word for word, which carries the range.
4. Only when `CHECK load` returned `generated: true`: the source line.

No other line: not a significance test, an open question, a note on saved queries or past
queries that still follow the old rule, or an offer to change a saved query, another metric
or the set.

**Context in Atlan Trident, after a fix.** The result is the moment to save, so ask one native
question, "Save this check to Atlan Trident?", whose text is the result block, then the
question:

| Option | Description |
| --- | --- |
| Save to `<workspace>` (Recommended) | The questions and every run with its score, so your team sees accuracy go from `<p>`% to `<q>`%. A digest of each trusted SQL, never the SQL. |
| Keep on this machine | The set and its runs stay in this folder. |

On Save: register `<agent>` in the context's workspace once, with `registry__create_artifact`
(`kind` `agent`; `metadata` holds only `name`, `workspace_id` and a one-line `description`). A
refusal because that name exists means it is registered: find it with
`registry__search_artifacts` (`kind` `agent`) and use its id. Then have `accuracy-check`
[save the check](../../accuracy-check/references/run.md#save-to-atlan-trident) with that agent
id and workspace. End with the demo's "What would you like to do next?" from
[ready](../../analytics-build/references/setup/ready.md), whose text is what was saved and where,
as plain lines and no banner (the result was just shown):

```
Saved to <workspace>:
  <agent>, with <N> questions and <runs> runs scored from <p>% to <q>%
  <metric> version <v>, released            (one line per metric the fix released)
See the runs in Atlan: <runs link>
```

On Keep: the same question, whose text is one line: "The check stays on this machine."

**Every other case** (the context on this machine, or no fix applied): ask nothing about saving;
the set and its runs stay in the project. End with "What would you like to do next?", whose
text is the result block.
