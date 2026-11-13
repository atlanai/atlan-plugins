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

`CHECK start --project <project>` returns the built run, `context_saved_to` and the project's
`sets`. With no sets, or the person asking for new questions, generate. Otherwise, unless the
request named a set, ask one native question, even when the folder holds only one set, whose text is this state line, as plain lines:

```
This folder has <N> eval set(s): <set name>, <N> questions with their trusted answers.
Next I ask each question to a fresh agent that sees only your context, and compare
its answer with the trusted one. Which questions should I test with?
```

Nothing else goes in it or before it: not a set's notes or expected answers, where its questions
came from, or what the test is likely to find. Those would answer the test before it runs.

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
  the old rule (the cost of rework saying rework is never billed, say) gets its own fix entry: an
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
  | Apply it (Recommended) | I change `<metric>` so `<rule proposed, plain words>`, and each other metric the fix names, then test the same questions again. |
  | Keep the score | Keep this score as the bar to beat. |

**Apply it** is the yes through the next score, so ask nothing more until the person must act in
Metric Studio. Keep the score goes to step 5.

- **Context on this machine:** write `{"name": "<metric>", "changes": <the fix's changes>}` to
  `<evals-run>/fix-request.json`, never in the run (an answering agent that reads the proposed
  rule there no longer tests the context), and run `PROJECT edit-metric <that file>`. It keeps the old version beside the run (never where answering agents read), raises the
  version, re-checks the metric and keeps the engine's own copies of it in step; a failing check
  keeps the old rule, so say so and ask "Apply this fix?" again with the next fix. Change only that metric,
  never a file by hand, and do not raise saved queries with the person.
- **Context in Atlan Trident:** read the metric's head with `registry__get_artifact` and write one
  proposal with `registry__create_artifact` as
  [the proposal record](../../metrics-generation/references/propose-record.md) describes: the
  fix's definition changes and the missed questions as `evidence.runs`. Metric Studio refuses a
  proposed change to a metric's top-level `description`; when the fix rewrites one, the owner
  edits it in Studio while approving, so the question's text gives the new description to paste.
  Ask one native question whose text is the Metric Studio link that opens the proposal
  (`suggestion=<file id>`), then "Approved it in Metric Studio?":

  | Option | Description |
  | --- | --- |
  | Approved, test again | I test the approved version on the same questions before anyone reads it. |
  | Not now | The proposal waits in Metric Studio; the context stays as it is. |

Then have `accuracy-check` [test again](../../accuracy-check/references/run.md#test-again) on the same eval folder, naming the
approved `version_ordinal` in Trident.
In Trident, release that version to `latest` with `registry__release_artifact` only when
`EVAL compare` shows the missed question fixed and no question that uses the changed metric in
`flipped_down`, and say so; otherwise leave it unreleased and say which question changed. A
workspace that releases new versions on its own has already put the approved version on
`latest`: when a question that uses the changed metric gets worse, withdraw that release with
`registry__withdraw_release` and say so. A question on another metric that gets worse is a miss
for the next fix, never a reason to undo this one.

With every question right after the test again, go to step 5. With a miss left, ask "Does the
score look good?" again as step 3 describes, with the new score; Improve it finds the next fix.

## 5. The result

Everything stays on this machine until here. Only when the context is in Atlan Trident **and** a
fix was applied and tested again, ask one native question, "Save this check to Atlan Trident?",
whose text is the score move, as plain lines:

| Option | Description |
| --- | --- |
| Save to `<workspace>` (Recommended) | The questions and both runs with their scores, so your team sees accuracy go from `<p>`% to 100%. A digest of each trusted SQL, never the SQL. |
| Keep on this machine | The set and its runs stay in this folder. |

On Save: register `<agent>` in the context's workspace once, with `registry__create_artifact`
(`kind` `agent`; `metadata` holds only `name`, `workspace_id` and a one-line `description`). A
refusal because that name exists means it is registered: find it with
`registry__search_artifacts` (`kind` `agent`) and use its id. Then have `accuracy-check`
[save the check](../../accuracy-check/references/run.md#save-to-atlan-trident) with that agent
id and workspace. In every other case ask nothing: the set and its runs stay in the project.

End with the demo's "What would you like to do next?" from
[ready](../../analytics-build/references/setup/ready.md). Its text is exactly these lines, as
plain lines, and nothing else:

1. Every question right: this banner. Otherwise no banner.

   ```
    *   .  '   *    .   '  *   .    '   *  .
    '   *   .   <N> OUT OF <N> RIGHT   .   *   '
    .   *    '   .    *   '    .   *    '  .
   ```

2. The result line, in percentages. After a fix: "Your <agent> has reached 100% accuracy, up
   from <p>%, now that <what changed, plain words>." First time: "Your <agent> is at 100%
   accuracy on the <N> questions <firm>'s analysts ask most." Some missed: "<p>% accuracy, and
   <what the misses share>."
3. The score line, word for word, which carries the range.
4. Only when `CHECK load` returned `generated: true`: the source line.
5. One line on where it lives: on this machine, or saved to `<workspace>`.

No other line: not a significance test, an open question, a note on saved queries or past
queries that still follow the old rule, or an offer to change a saved query, another metric
or the set.
