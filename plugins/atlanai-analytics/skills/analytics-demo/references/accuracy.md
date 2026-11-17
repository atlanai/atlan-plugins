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

When the host keeps a task list, its items are these, in the person's words, whichever questions
are picked: **Choose the questions**, **Ask each one to a fresh agent**, **Score the answers**.
When the person replies "fix", add **Find what to fix**, **Apply the fix** and **Test again**
to the list before diagnosing, and mark each done as it finishes.

Every step follows [text before a native question is lost](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost):
a block to read before choosing is the question's own text, as plain lines with no Markdown, with
no Doing or Why lines. Every step that leads to a choice ends on a native question, except the
score (step 3) and the result (step 5): each is the turn's last chat message, ending on a plain
reply line, so the before and after prints whole.

An eval set lives in the project (`.atlan-evals/sets/`), never fetched from Atlan Trident: never
look up Trident, a workspace or saved evals for the questions, the run or the score. Only step
4's Trident fix and step 5's save touch a workspace.
`context_saved_to` (from `CHECK start` and `CHECK load`) says where the context lives; `null` means
this machine only.

## 1. Choose the questions

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
<N> questions to test your <agent> on <the use case>. Each comes from <firm>'s dashboard or a
query its analysts already run; the expected answer is what that query returns.

<the table, as CHECK show printed it>

Each question goes to a fresh agent with your context, and again to one with only the raw tables (schema only, no additional context), about <4 or 8> minutes.
```

Never rebuild the table, add SQL, or shorten it.

**Then, in the same turn, one native question**, "Run these <N> questions?", whose text is that
one line and nothing else:

| Option | Description |
| --- | --- |
| Run them (Recommended) | Test all <N>, with your context and on the raw tables. |
| Edit questions | Change a question's wording, or leave some out, before anything runs. |
| Skip for now | Keep the questions on this machine and run them later. |

- **Run them** runs the set (step 3).
- **Edit questions:** ask in plain chat which questions to change and how: "reword 3 to: <new
  wording>" or "drop 2, 7". Run `CHECK edit --evals <evals-run> --question <n> --text "<new
  wording>"` for each reworded question and `CHECK drop --evals <evals-run> --questions
  <numbers>` for those left out. A reworded question keeps its trusted SQL and expected answer, so
  it must still ask for exactly what that SQL returns: when the new wording asks for something
  else (another period, another measure), say so in one line and keep the old wording. Then
  print `CHECK show` again in the chat and ask "Run these <N> questions?" again. The set file
  keeps the new wording and the dropped questions.
- **Skip for now** keeps the set on this machine and ends with "What would you like to do next?",
  whose text is one line: "The questions stay on this machine."

Where the host has no native question tool, end the turn on the table, with one last line:
Reply "run", "reword 3 to: <new wording>", "drop 2, 7" or "skip". Where no person can answer
(a scheduled or headless run), run them.

## 3. Run and score

Run the set with `accuracy-check`, as its [run it](../../accuracy-check/references/run.md#run-it) describes, with its
[before and after](../../accuracy-check/references/run.md#before-and-after) pass using schema only,
with no additional context or exploratory data queries; only its final answer SQL reads data.
The raw pass is started with the
context pass. With every question right, go to step 5: the result carries the comparison.

**With misses, the turn ends on the score, in the chat.** The before and after is the moment the
person sees what their context is worth, so it is the turn's last message, which always prints
whole, with no native question after it: text written just before a question can be cut to a
short note, and a question's own text is rewritten into one line. Send one message, filled in,
with nothing before it and nothing after it:

````
**<`headline`>**

```
<`bars`: the card below its headline, word for word>
```

<`changes`, the table as printed>

<the run report: the score line, the runs and the misses, as its [show the score](../../accuracy-check/references/run.md#show-the-score) describes>

<M> still wrong. Want me to work out what would fix them? Reply "fix" and I'll find the change behind each miss, or "keep" to keep this score as the bar to beat.
````

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
- **Write the fixes** with `ACC fixes --evals <evals-run> --file <a JSON list>`, which checks them
  and writes `<evals-run>/fixes.json`, the recommended one first (the one that turns the most
  misses): `[{"misses": ["<id>"], "why": "<reason>", "metric": "<name>", "rule_now": "<plain
  words>", "rule_proposed": "<plain words>", "changes": [{"path": "<definition field>", "value":
  <new value>}]}]`. A redraft (`"kind": "redraft"`) or a new metric (`"kind": "new_metric"`) has
  no `changes`.
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
  | Fix `<n>`: `<metric>` (one option per fix; Recommended on the one that turns the most misses, unless it carries a caution) | I change `<metric>` so `<rule proposed, plain words>`, and each other metric the fix names. With the context in Atlan Trident add: "This releases the new version in `<workspace>`." |
  | Reword question `<n>` (one option per redraft) | The question names a period that moves, or asks the opposite of its SQL. I leave the context alone and mark the question to reword in the set. |
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
  keeps the old rule, so say so and ask "Which fixes should I apply?" again with the fixes left. Change only that metric,
  never a file by hand, and do not raise saved queries with the person.
- **Context in Atlan Trident:** a ticked fix is the approval, so apply and release the fix here, with
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

The result block is the `text` that `ACC result --evals <evals-run> --agent <agent> --firm <firm>`
prints, put as it is: add `--changed "<what changed, plain words>"` after a fix, `--misses "<what
the misses share>"` with misses left, and `--generated` when `CHECK load` returned
`generated: true`. It holds these lines and nothing else:

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
4. The before-and-after card, word for word: the raw tables against your context on the same
   questions, a bar each. `ACC result` adds it from the check's `raw` run; it is never left out.
5. Only when `CHECK load` returned `generated: true`: the source line.

No other line: not a significance test, an open question, a note on saved queries or past
queries that still follow the old rule, or an offer to change a saved query, another metric
or the set.

**The turn ends on the result, in the chat, with the save offer as its last line.** The result
is the moment to save, whether the context is in Atlan Trident or on this machine, and like the
score it is the turn's last message, never a native question's text. Send one message, filled
in, with nothing before it and nothing after it:

````
```
<the result block, `ACC result`'s text, word for word: banner, result line, score line, card>
```

<`changes` from the comparison, the table as printed>

<one line per version a fix released: "<metric> version <v> is released in <workspace>.">

Save this check to Atlan Trident? Reply "save" to put it in <workspace>: the questions, the raw-tables run and every run with your context, with their scores, so your team sees accuracy go from <r>% on the raw tables to <q>% with your context, and a digest of each trusted SQL, never the SQL. Reply "keep" to leave it on this machine.
````

When the person just replied "keep" to a score message, they have seen the score: the message
is only the released lines and the save offer. Nothing else in either form: no notes on
proposals, files or cleanup. Then stop and wait for the reply: "save", or any reply that asks
to save, saves; "keep", or any reply that declines, keeps. `<workspace>` is the one the context
was saved to (`context_saved_to`); for a context on this machine it is the person's personal
workspace, the `user_default` one `registry__list_workspaces` returns. When that read needs
sign-in, follow [connection recovery](../../analytics-build/references/auth-recovery.md) after
the person replies "save", never before: the result has already been shown.

On "save": have `accuracy-check`
[save the check](../../accuracy-check/references/run.md#save-to-atlan-trident) to that
workspace. No agent is registered: the runs save without one. Saving the check never saves the
context: a context kept on this machine stays there. `ACC saved --evals <evals-run> --agent <agent> --workspace <workspace>
--org <organization slug> --released "<metric>:<version>"` (once per released version; `--withdrawn`
for one the test again withdrew) prints the summary
below. End with the demo's "What would you like to do next?" ([step 5](../SKILL.md#5-ask-a-question-then-choose-whats-next)), whose text is what was saved and where,
as that `text`, put as it is, and no banner (the result was just shown):

```
Saved to <workspace>:
  <agent>, with <N> questions and <runs> runs scored from <p>% to <q>%
  Raw tables, no context: <r>%
  <metric> version <v>, released            (one line per released or withdrawn version)
See the runs in Atlan: <runs link>
```

On "keep": the same question, whose text is one line: "The check stays on this machine."

A run with no person to answer (a scheduled or headless check) saves nothing and says the
check stays on this machine.
