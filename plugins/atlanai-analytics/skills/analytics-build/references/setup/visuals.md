# Chips, mini cards and the side page

Read before the opening or the welcome, and again when a moment below is reached. The flow,
its questions and its words do not change. At each moment the helper reads the run's own files
and prints what the chat shows: a chip saying what that step produced, each step with its own
mark, or a mini card at a big moment. Chips never show progress: the side page, a page on this
machine beside the chat, follows the whole build by itself and shows every metric with how it
was reached. Never draw, design or retype a visual, and never put a number in
one by hand.

## At each moment

Run `PROJECT visual <kind>`, or `PROJECT visual next` for the next moment the run has reached
but not shown. Any other `PROJECT` result with a `visual` field names such a moment: run it
before going on. While the engine builds, its result after each gate says so too: run
`PROJECT visual next`, and again while it says more is due. A demo adds `--data` with its facts file,
[Fixit](../../../analytics-demo/references/yard.json) or
[Ticketfuddle](../../../analytics-demo/references/box-office.json), on every call. It prints:

1. **What the chat shows**: a chip, one line such as `◆ **9 metrics** · Rework cost, Gross
   margin, Material waste +6`, or at the four big moments a mini card: a bold headline over a few
   indented lines.
   Show it at once, as the moment's result; a command's own output is folded away, so the person
   never sees it there.
   - **With a `show_widget` tool** (the Claude desktop app): add `--widget` to every `visual`
     call and pass the HTML it prints to that tool, chips and cards alike, never in a
     question's text. Pass what a `visual` call prints before its `side view:` line as it is:
     never send it to a file, cut it with a script, or say that you need it. Two moments in a
     row come from one call (`visual build context`): each moment's HTML ends at its own
     `side view:` line. Your own words reach the person there too, so the widget is the moment's
     result.
   - **Otherwise** (a terminal): the helper prints plain lines and says where they go. A moment
     that comes right before a native question goes in that question's own text, which is always
     shown: text written just before a question folds into one summary line, and reply text in
     the middle of a run is often left out. Other moments open your very next message, unchanged,
     on their own lines, before any other words or tool call: a command's output is folded away, so
     only your message shows them. During a build that is right after each gate; an answer's card
     leads the answer. On a light terminal, Claude Code's dark theme draws
     every question's text in white, the flow's own included: the person switches Claude Code to
     its light theme (`/theme`).
   When the helper prints nothing here, the chat shows nothing for this moment. When it prints
   nothing at all, the run has nothing true to show yet: carry on; the helper names the moment
   again when it is due.
2. **A last line, `side view: {...}`**, a JSON object; never print it. Its `next` says what to do
   with the lines above it, and with the side page when it needs you. An `error` field means
   this moment could not be read: carry on.

| Moment | Kind | The chat shows | Data |
| --- | --- | --- | --- |
| The queries are in plain words, before any metric | `connect` | One line once the side page shows: "A page has opened beside this chat…", as its `next` gives it | |
| Own data, right before choosing what to build from | `found` | Chip: the sources and the warehouse found | |
| Data read: a demo's or local tables loaded, or a warehouse's past queries read | `sources` | Chip: the rows and what was read (a demo's opens the next question in a terminal) | |
| The engine grouped the past queries | `group` | Chip: how many distinct past queries | |
| The engine wrote up the dashboard's tiles | `tiles` | Chip: the tiles, each now a question | |
| The engine described each query | `described` | Chip: one question in plain words | |
| The engine found the metrics in the queries | `drafted` | Chip: how many metrics were found, and in how many queries | |
| The engine merged the metrics | `metrics` | Chip: the metrics by name | |
| The engine wrote each metric's rule | `rules` | Chip: one metric's rule | |
| The engine linked the metrics | `links` | Chip: how one metric builds on others | |
| The build ends: its context is in the run and its structural check has run | `build` | Mini card, before any question: it closes the build, before the question about testing it. In a terminal it waits for the save question, which says it in sentences | |
| Every check has run, after the build card | `context` | Chip: what ran, and what needs your review; without widgets, a sentence in the save question | |
| Saved to Atlan Trident | `saved` | Mini card before the ready question; nothing when kept on this machine, which the person has just chosen | with `PROJECT publication` |
| A question answered | `answer` | Mini card for a chat's first answer, then a chip | with the query that answers; on a warehouse or with no run, with `visual answer` |
| A rule changed | `edit` | Mini card | with `EDIT` |
| Test questions written or loaded for a check | `questions` | Chip: how many, and where they came from; the side page lists each one | |
| A new chat's first moment, on an agent built earlier | `agent` | Chip: what the agent holds, and when it was built; inside that moment's own card, or alone when a check starts or a run resumes | |
| A check scored | `accuracy` | Card: both scores and every question's mark | with `EVAL before-after` or `ACC result` |

These moments come with the results you read: a helper result, the build's wait, `CHECK start`,
`CHECK load`, `SCOPE emit`, the query that answers the person, the save's receipt
(`PROJECT publication`), a rule change (`EDIT`) and a scored check (`EVAL before-after`) hand each one over in `show_first`, drawn and
marked shown. Show it
before any other words or call; never draw one of them a second time.

**One line of your own.** The build card takes one line in `build.json` (`aside`, through
`--input`), and the saved card one in `PROJECT publication`'s payload (`aside`): why this build, in the person's terms ("Built from the
dashboard you shared this morning."). One line, no number: every number comes from the run.

The helper hands the first twelve over when they are due. Once the side page is open, where you keep a
task list, `drafted`, `metrics`, `rules` and `links` hand back the task list and one line, `say`,
instead of a chip. A chip or card is the moment's result line:
add no other line that repeats its numbers. After an answer's card, the reply leads with what
answers (the name) and what explains it.

**Keep the drawing out of your words.** The person sees the chip or card, never how it got
there: never write that you are drawing, recording or checking for a visual, and never name a
moment, chip, card or the helper, before or after it ("Preparing the answer card"). A moment that shows nothing
gets no words either. While the
engine builds, the chip after each gate is the update: add no line of your own about the
engine's steps, its files or its terms (cards, gates, the export and its install, assembled
rules) unless one is blocked or needs the person. Within a step, its batches show on the side
page as they land, never in the chat ([drafts as they land](progress.md#drafts-as-they-land)),
and the step's chip closes it.

## The side page

`PROJECT visual connect` starts the run's side page on this machine, at an address only this
machine can open (`http://127.0.0.1:<port>/<key>/`). It starts only once the build has a
possible metric to show, so `visual next` names it then; called earlier, it says to carry on.
If the page cannot start, its `next` gives the one line to tell the person. Otherwise its
`next` says what to do with it:

- **The Claude desktop app:** open the address in the app's browser pane with your browser tool
  (its preview or navigate call, with the `url`), then show that pane beside the chat (the
  app's show-pane call with pane `preview`): loading a page leaves a hidden pane hidden, and the
  person expects the page open by default. Then check with the app's layout call that `preview`
  is among the open panes, and show it again if not: a page in a closed pane still reports itself
  visible, so only the layout can tell. Do it right away; helper results name it again
  (`side_page`) until the page has been opened.
- **A terminal:** the helper opens the address in the person's browser, and the next chip that
  goes in a question names it, so the person can open it again.
- **Anywhere else:** the next chip that goes in a question names the address the same way.

The page shows what the build made, in every flow: the metrics, each in its state (Found,
Defined, Tested, Not tested, Needs your review), then the queries, then the tiles that could not be built. A click
opens a metric's definition, formula and where it was found. The build's steps, the survey, the
choices, the save, the answers and the dashboard are the chat's and never appear on it; an
accuracy check's questions do, on their own list, each with its known answer and your agent's. The page reads the run's files itself: never reload,
rebuild or push to it, and never describe what it shows beyond the one line its `next` gives when it first opens. Nothing on it leaves this machine, so
it shows the metrics, their sources and values on own data as on a demo. A card's last line points to it for the detail. If
the page stops while nobody looks at it, the next `visual` call, `visual next` included,
starts it again at the same address. `PROJECT view` prints the address again;
`PROJECT view --stop` stops it.

## Data files

Pass a moment's file on standard input as a here-document: `PROJECT visual <kind> --input -`
writes it as `<kind>.json` in the run directory and records the moment in one step. Copy values
from the result that produced them; leave out a field you do not have, never fill one in.

- The save: `PROJECT publication`'s payload takes `workspace`, the workspace's display name,
  beside `workspace_id`, and its result hands the card over. Write no counts: the helper reads
  what is live from the receipt it records. Kept on this machine, there is no card.
- An answer: the request of the query that answers (`PROJECT execute`) takes `answer`:
  `question`, the person's question, and `metrics`, the name of each metric or saved query the
  answer used. The card leads with the metric and its value (or a breakdown's first row) and
  lists the result's rows. Add `headline`, what answers and its number such as
  `Turbine base: -£6,360`, when the answering row is not the first; `summary`, the answer's first
  sentence, without the headline's number and with no number the rows, headline or question do
  not show (the helper refuses one); `rule`, the metric's rule in one line; and for a question
  that asks why, `breakdown` with `steps` as `[label, signed value]` from the query that splits
  the answering row. On a warehouse, after the result, run `PROJECT visual answer --input -`
  with the same and `result`, the `columns` and `rows` the warehouse returned.
- A rule change: `EDIT`'s request takes `before` and `after`, each rule in plain words, and its
  result hands the card over with the metric's checked value before and after. Add `added` and
  `removed` as lists of short labels for the re-asked question, and `note` when a dashboard was
  built before the change: it still shows the old rule until it is rebuilt. On a warehouse, `EDIT`
  first returns `run_on_warehouse` with the new rule's `sql`; its card comes when `EDIT` gets the
  metric's `name` and the `result` that statement returned.
