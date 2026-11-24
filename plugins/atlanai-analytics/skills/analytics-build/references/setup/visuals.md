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
     row come from one call (`visual build context`), each under its own heading. Your own words reach the person there too, so the widget is the moment's
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
| The engine grouped the past queries | `group` | Chip: how many different questions | |
| The engine wrote up the dashboard's tiles | `tiles` | Chip: the tiles, each now a question | |
| The engine described each query | `described` | Chip: one question in plain words | |
| The engine found the metrics in the queries | `drafted` | Chip: how many metrics were found, and in how many queries | |
| The engine merged the metrics | `metrics` | Chip: the metrics by name | |
| The engine wrote each metric's rule | `rules` | Chip: one metric's rule | |
| The engine linked the metrics | `links` | Chip: how one metric builds on others | |
| Every check has run, right before the save question | `build` | Mini card (without widgets, the save question says it in sentences). It counts the checks, so it has nothing to show before them | |
| Every check has run, after the build card | `context` | Chip: what ran, and what needs your review; without widgets, a sentence in the save question | |
| Saved to Atlan | `saved` | Mini card before the ready question; nothing when kept on this machine, which the person has just chosen | `saved.json` |
| A question answered | `answer` | Mini card the first time, then a chip | `answer.json` |
| A rule changed | `edit` | Mini card | `edit.json` |

The helper names the first twelve when they are due. Once the side page is open, where you keep a
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
choices, the save, the answers and the dashboard are the chat's and never appear on it. The page reads the run's files itself: never reload,
rebuild or push to it, and never describe what it shows beyond the one line its `next` gives when it first opens. Nothing on it leaves this machine, so
it shows the metrics, their sources and values on own data as on a demo. A card's last line points to it for the detail. If
the page stops while nobody looks at it, the next `visual` call, `visual next` included,
starts it again at the same address. `PROJECT view` prints the address again;
`PROJECT view --stop` stops it.

## Data files

Pass a moment's file on standard input as a here-document: `PROJECT visual <kind> --input -`
writes it as `<kind>.json` in the run directory and records the moment in one step. Copy values
from the result that produced them; leave out a field you do not have, never fill one in.

- `saved.json`: `workspace`, its display name. Write no counts: the helper reads what is live
  from the publish receipt `PROJECT publication` recorded. Kept on this machine, skip the file.
- `answer.json`: `question`; `headline`, what answers and its number, such as
  `Turbine base: -£6,360`; `summary`, the answer's first sentence, without the headline's number
  and with no number the rows, headline or question do not show (the helper refuses one); `unit` from the metric;
  `rows`, `[label, value]` pairs from the query result in its order (at most 12); `highlight`,
  the label of the row that answers; `rule`, the metric's rule in one line; `proof`, `[{name}]`
  of the metrics and queries the answer used. For a question that asks why, add `breakdown`
  with `steps` as `[label, signed value]` from the query that splits the answering row.
- `edit.json`: from the edit result: `metric`, `before` and `after` rules in plain words,
  `values` as `[name, before, after, unit]`, and `added` and `removed` as lists of short labels
  for the re-asked question. Add `note` when a dashboard was built before the change: it still
  shows the old rule until it is rebuilt.
