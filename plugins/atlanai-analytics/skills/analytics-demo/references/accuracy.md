# Check how accurate it is

Read when the person picks **Check how accurate it is**, or asks how accurate the agent built in
this folder is. The demo has built the context; this shows how often it gets an answer right, on
questions the firm's analysts already ask, and fixes the rule behind a miss. The demo orchestrates
the check and owns acting on its result. It calls [eval-generation](../../eval-generation/SKILL.md)
to generate or save a set and [accuracy-check](../../accuracy-check/SKILL.md) to find the run, load
a set, run it, score it and test again. `<agent>` and `<firm>` come from the demo's facts file;
`CHECK`, `EVAL` and `PROJECT` are as accuracy-check's [run and score a set](../../accuracy-check/references/run.md)
names them, `ACC` is `python3 "<demo-dir>/scripts/accuracy.py"` and `EDIT` is
`python3 "<demo-dir>/scripts/edit_metric.py" --run-dir "<run>"`, with `<demo-dir>` this skill's
directory.

An own-data build runs steps 1 to 4 through `analytics-build`'s
[check accuracy](../../analytics-build/references/setup/accuracy.md), which offers the check before
the first question, asks before a release and replaces step 5's ending.

**When the person asks what will happen** before anything runs, answer in a few plain
sentences, in this order, naming only the decisions that are theirs: I pick 8 questions from
`<firm>`'s dashboard and the queries its analysts already run, and show each with where it
came from and its expected answer; you decide once whether to run them, change some or skip;
each question goes to a fresh agent with your context and again with column names only, so the score
shows beside that comparison; with a miss, you decide whether I find the fix; and only after
the score, whether to save the check to Atlan or store it on this machine. Nothing else.

When the host keeps a task list, its items are these, in the person's words, whichever questions
are picked: **Check your test questions**, **Run your agent on each question**, **Score
the answers**.
When the person replies "fix", add **Find what to fix**, **Apply the fix** and **Test again**
to the list before diagnosing, and mark each done as it finishes.

Every step follows [text before a native question is lost](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost):
a block to read before choosing is the question's own text, as plain lines with no Markdown, with
no Doing or Why lines. Every step that leads to a choice ends on a native question, except the
score with misses (step 3): it is the turn's last chat message, ending on a plain reply line, so
it prints whole. The result (step 5) is the text of its native question.

An eval set lives in the project (`.atlan-evals/sets/`), never fetched from Atlan: never
look up Trident, a workspace or saved evals for the questions, the run or the score. Only step
4's Trident fix and step 5's save touch a workspace.
`context_saved_to` (from `CHECK start` and `CHECK load`) says where the context lives; `null` means
this machine only.

## 1. Check your test questions

Run `CHECK start` at the start of every check, even a later one in the same session: where the
context lives can change, so never reuse an earlier answer. `CHECK start --project <project> --questions 8` returns the built run, `context_saved_to`, `execution`, the project's
`sets` and, when there is a set, `ask`: the native question to put as it is (its `question`,
`text` and `options`). Add `--run <run-dir>`, here and on `CHECK load` and `SCOPE inputs`,
whenever the run is known: the demo's own run, or the build's. With no sets, or the person asking for new questions, generate. Otherwise,
unless the request named a set, put `ask` as one native question, as it is, even when the folder
holds only one set.

Nothing else goes in it or before it: not a set's notes or expected answers, where its questions
came from, or what the test is likely to find. A brought set runs as it is; only generated
questions are shown with their expected answers, in step 2, because the person has not seen them.

- **A set:** picking it is the approval to run it. `CHECK load --project <project> --file <set>`,
  then step 3, with no list to confirm.
- **Generate:** run `eval-generation` `generate` (not its `save`) with 8 questions, passing
  `--limit 8` to its `SCOPE pick`, then `CHECK load` on the set it wrote, then step 2. A demo
  check stays at 8 questions, so it ends in about 4 minutes with both passes.

`CHECK load` drops a question whose trusted SQL fails or returns nothing; name how many.

## 2. Generated questions: show them, then ask to run them

The person has not seen these, so show them before running: the questions in the chat, then one
short native question to choose. The questions never go in the question's text, where a list this
long wraps and breaks.

**First, the questions in the chat.** `CHECK show --evals <evals-run>` prints the set as one
markdown table: the number each question keeps in the score, the misses and any fix, the
question, the metric it tests, and the trusted answer (a single value, or the result's size and
first values). Send this as a chat message, filled in, with the table exactly as printed:

```
<N> questions to test your <agent> on <the use case>. Each comes from a tile on <firm>'s dashboard or a
past query its analysts wrote. The known answer is what that tile or past query returns.

<the table, as CHECK show printed it>

Each question is answered twice from a clean start: once with your agent's metrics, and once with column names only. About <4 or 8> minutes.
```

With one question, it reads: "1 question to test your <agent> on <the use case>. It comes from a
tile on <firm>'s dashboard or a past query its analysts wrote. The known answer is what that tile
or past query returns." and "It is answered twice from a clean start: once with your agent's
metrics, and once with column names only." Never rebuild the table, add SQL, or shorten it.

**Then, in the same turn, one native question**: `CHECK show`'s `ask`, its `question` and
`options` as they are. Its text is the question alone. For several questions:

| Option | Description |
| --- | --- |
| Test them (Recommended) | I test all <N>, with your agent's metrics and with column names only. |
| Edit questions | Reword a question or leave some out before testing. |
| Skip for now | Keep the questions on this machine and test later. |

For one, the question is "Test your agent on this question?", and the options read Test it
(Recommended), "I test it with your agent's metrics and with column names only."; Edit the
question; Skip for now.

- **Test them** (or **Test it**) runs the set (step 3).
- **Edit questions:** ask in plain chat which questions to change and how: "reword 3 to: <new
  wording>" or "drop 2, 7". Run `CHECK edit --evals <evals-run> --question <n> --text "<new
  wording>"` for each reworded question and `CHECK drop --evals <evals-run> --questions
  <numbers>` for those left out. A reworded question keeps its trusted SQL and expected answer, so
  it must still ask for exactly what that SQL returns: when the new wording asks for something
  else (another period, another measure), say so in one line and keep the old wording. Then
  print `CHECK show` again in the chat and ask its `ask` again. The set file
  keeps the new wording and the dropped questions.
- **Skip for now** keeps the set on this machine and ends with "What would you like to do next?",
  whose text is one line: "The questions stay on this machine."

Where the host has no native question tool, end the turn on the table, with one last line:
Reply "test", "reword 3 to: <new wording>", "drop 2, 7" or "skip". Where no person can answer
(a scheduled or headless run), run them.

## 3. Run your agent on each question, and score the answers

Run the set with `accuracy-check`, as its [run it](../../accuracy-check/references/run.md#run-it) describes, with its
[before and after](../../accuracy-check/references/run.md#before-and-after) pass using column names only,
with no additional context or exploratory data queries; only its final answer SQL reads data.
The raw pass is started with the
context pass. With every question right, go to step 5: the result carries the comparison.

**With misses, the turn ends on the score, in the chat.** It is the turn's last message, which
always prints whole, with no native question after it: text written just before a question can
be cut to a short note. Write it from `ACC result --evals <evals-run>` (add `--generated` when
`CHECK load` returned `generated: true`, and `--widget` where you have `show_widget`) as
accuracy-check's [show the score](../../accuracy-check/references/run.md#show-the-score) describes,
its card drawn first where there is one, then this last line. Send one message, filled in, with
nothing before it but the card and nothing after it:

```
<the result, as show the score describes>

<M> still wrong. Reply "fix" and I'll find the change behind each miss. Reply "keep" to keep this score as the bar to beat.
```

With one miss, the line reads: 1 still wrong. Reply "fix" and I'll find the change behind it.
Reply "keep" to keep this score as the bar to beat.

Then stop and wait for the reply. "fix", or any reply that asks to improve the score, is step 4.
"keep", or any reply that accepts the score, is step 5. Name no fix in this message: the fixes
come from the diagnosis.

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
- **A miss that repeats is one fix.** Read `<evals-run>/fixes-history.jsonl`, each earlier round's
  fixes. When a miss fails the way an earlier fix on another metric already fixed (an answer that
  added a total row, then another that adds one on a different metric), the cause is how answers
  are shaped, not one metric: propose one shared fix that states the rule once and changes every
  metric that answers that kind of question, the one fixed before included, instead of a fix for
  this metric alone.
- **Write the fixes** with `ACC fixes --evals <evals-run> --file <a JSON list>`, which checks them
  and writes `<evals-run>/fixes.json`, the recommended one first (the one that turns the most
  misses): `[{"misses": ["<id>"], "why": "<reason>", "metric": "<name>", "rule_now": "<plain
  words>", "rule_proposed": "<plain words>", "changes": [{"path": "<definition field>", "value":
  <new value>}]}]`. A shared fix is `{"kind": "shared", "misses": [...], "why": "<reason, naming the
  earlier fix it repeats>", "rule_now": "...", "rule_proposed": "<the rule once, in plain words>",
  "metrics": [{"metric": "<name>", "changes": [...]}, ...]}`, two or more metrics, each with its own
  changes. A redraft (`"kind": "redraft"`) or a new metric (`"kind": "new_metric"`) has no `changes`.
- **A period that moves is never tied to a date.** When a miss comes from "last quarter", "this
  month" or any other period that moves with the calendar, and the trusted SQL fixes its dates,
  the context is not wrong: the question is. Never propose a rule that says what that phrase
  means on a date ("last quarter means Q2 2026"): it would answer every later question with a
  stale period. `ACC fixes` refuses such a fix. Write it as a redraft whose why says the period
  is relative; it is shown with the fixes, as a question to reword, and changes nothing in the
  context.
- **Ask "Which fixes should I apply?"**, one native multiple-choice question whose text is the
  fixes as plain lines, and nothing after the options: not saved queries that follow the old
  rule, nor what else the fix might touch. One block per miss, under the miss's own number: the
  question, why it failed, and the fix (rule now, rule proposed). A person decides each fix on
  its own: there is one option per fix, and never an option that applies them all or a
  recommendation for a fix that carries a caution.

  | Option | Description |
  | --- | --- |
  | Fix `<n>`: `<metric>` (one option per fix; Recommended on the one that turns the most misses, unless it carries a caution) | I change `<metric>` so `<rule proposed, plain words>`, and any other metric the fix names. When the metrics are saved to Atlan, add: "This releases the new version in `<workspace>`." |
  | Fix `<n>`: every `<kind of answer>` (one option per shared fix) | I change `<k>` metrics, `<their names>`, so `<rule proposed, plain words>`. It is the second time an answer missed this way. |
  | Reword question `<n>` (one option per redraft) | The question names a period that moves, or asks the opposite of its SQL. I leave your agent's metrics alone and mark the question to reword. |
  | Keep the score | Apply nothing. Keep this score as the bar to beat. |

The fixes the person ticks are the yes through the next score, so apply only those, in order,
then test the same questions again and ask nothing more until the score, unless a Trident write
is refused and the owner must act in Metric Studio. A question marked to reword stays in the set
and in the score until its wording is changed; say so in one line beside the next score. With
nothing ticked, or Keep the score, go to step 5.

- **Context on this machine:** write `{"name": "<metric>", "changes": <the fix's changes>}` to
  `<evals-run>/fix-request.json`, never in the run (an answering agent that reads the proposed
  rule there no longer tests the context), and run `EDIT <that file>`. It keeps the old version beside the run (never where answering agents read), raises the
  version, re-checks the metric and keeps the engine's own copies of it in step; a failing check
  keeps the old rule, so say so and ask "Which fixes should I apply?" again with the fixes left. A
  shared fix is one `EDIT` per metric it names, each with that metric's changes. Change only that metric,
  never a file by hand, and do not raise saved queries with the person.
- **Context in Atlan:** a ticked fix is the approval, so apply and release the fix here, with
  no second approval in Metric Studio and no proposal record: the new version is the record. For
  each metric the fix changes:
  1. Read the metric's head (`registry__get_artifact`, `properties: []`) for its `etag` and
     `version_ordinal`, then `ACC version --evals <evals-run> --run <run> --metric <name>
     --head-version <version_ordinal>` gives the new version, the fix applied to the definition
     the save sent, description included.
  2. Send `registry__append_artifact_version` with that `metadata` exactly as printed, `if_match`
     the head's `etag`, and release the new version to `latest` with `registry__release_artifact`
     unless the workspace already released it.
  3. Make the same change on this machine, as for a local context (`EDIT`), so the
     run holds the version Atlan holds and a later fix builds on this one.
  A refusal (the person may not write this metric, or someone changed it since) leaves the change
  for its owner: write the proposal with `registry__create_artifact` as
  [the proposal record](../../metrics-generation/references/propose-record.md) describes (the fix's
  changes and the missed questions as `evidence.runs`), give its Metric Studio link
  (`suggestion=<file id>`) and ask "Approved it in Metric Studio?" (Approved, test again / Not now,
  which goes to the result). After the owner's approval, release that version as in step 2 and
  make the step 3 change.
  As on this machine, do not raise saved queries with the person.

Then have `accuracy-check` [test again](../../accuracy-check/references/run.md#test-again) on the
same eval folder, naming the new `version_ordinal`. When a question that uses the changed metric
gets worse, withdraw that release with `registry__withdraw_release`, which puts `latest` back on the
previous version, and say so. A question on another metric that gets worse is a miss for the next
fix, never a reason to undo this one.

With every question right after the test again, go to step 5. With a miss left, end the turn on
the score again as step 3 describes, with the new score; "fix" finds the next fix.

## 5. The result

The result is the win moment of the check. `ACC result --evals <evals-run>` (add `--generated`
when `CHECK load` returned `generated: true`) computes every number: `banner` when every question
is right (shown only where no card is drawn), `accuracy`, `baseline`, `lift`, `outcome`, `questions`, `misses`, `lines` (the number
lines, worded once, with `fixed`, "Up from <p>% before the fix.", after a fix) and `source`. Write
the result from them as accuracy-check's
[show the score](../../accuracy-check/references/run.md#show-the-score) describes, card first where
you have `show_widget`; after a fix, its line on what made the difference names the rule it
changed, in plain words. Then one
line per version a fix released: "<metric> version <v> is released in <workspace>." No other
line: not a significance test, a note on saved queries or past queries that still follow the old
rule, or an offer to change a saved query, another metric or the set.

**Then ask for more, in the same turn.** `CHECK next --evals <evals-run> --run <run>` returns
the question, as accuracy-check's [ask for more](../../accuracy-check/references/run.md#ask-for-more)
describes. Draw the result's card first where you have `show_widget` (`ACC result --evals <evals-run> --widget`), then ask one
native question whose text is the result, a blank line and its `lead`, as plain lines with no
Markdown, as [show the score](../../accuracy-check/references/run.md#show-the-score) says. When the person just replied "keep" to a score message, they have seen the score: the
text is the released lines, if any, and the `lead`. On a demo, its options are Add questions of
your own, Save this score to Atlan (5 questions or more) and I'm done.

`<workspace>` is the one the context was saved to (`context_saved_to`); for a context on this
machine it is the person's personal workspace, the `user_default` one
`registry__list_workspaces` returns. When that read needs sign-in, follow
[connection recovery](../../analytics-build/references/auth-recovery.md) after the person picks
Save this score to Atlan, never before: the result has already been shown.

On **Save this score to Atlan**: have `accuracy-check`
[save the check](../../accuracy-check/references/run.md#save-to-atlan) to that
workspace. No agent is registered: the runs save without one. Saving the check never saves the
context: a context kept on this machine stays there. `ACC saved --evals <evals-run> --agent <agent> --workspace <workspace>
--org <organization slug> --released "<metric>:<version>"` (once per released version; `--withdrawn`
for one the test again withdrew) prints the summary
below. End with the demo's "What would you like to do next?" ([step 5](../SKILL.md#5-ask-your-agent-a-question-then-choose-whats-next)), whose text is what was saved and where,
as that `text`, put as it is, and no banner (the result was just shown):

```
Saved to <workspace>:
  <agent>, with <N> questions and <runs> rounds scored from <p>% to <q>%
  column names only: <r>%
  <metric> version <v>, released            (one line per released or withdrawn version)
See the scores in Atlan: <runs link>
```

On **I'm done**: the closing message, as [ready](../../analytics-build/references/setup/ready.md)
says. On **Add questions of your own**, follow ask for more, then test again.

A run with no person to answer (a scheduled or headless check) saves nothing and says the
check stays on this machine.
