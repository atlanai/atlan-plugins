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
     call and pass the HTML it prints to that tool, chips and cards alike. There, text between
     tool calls or inside a question never reaches the person.
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
| The run folder exists, right after the first survey | `connect` | Nothing: it opens the side page | |
| Own data, right before choosing what to build from | `found` | Chip: the sources and the warehouse found | |
| Data read: a demo's or local tables loaded, or a warehouse's past queries read | `sources` | Chip: the rows and what was read (a demo's opens the next question in a terminal) | |
| The engine grouped the past queries | `group` | Chip: how many different questions | |
| The engine wrote up the dashboard's tiles | `tiles` | Chip: the tiles, each now a question | |
| The engine described each query | `described` | Chip: one question in plain words | |
| The engine merged the metrics | `metrics` | Chip: the metrics by name | |
| The engine wrote each metric's rule | `rules` | Chip: one metric's rule | |
| The engine linked the metrics | `links` | Chip: how one metric builds on others | |
| Metrics generated: the engine run is complete, before the checks | `build` | Mini card (in a terminal, it waits for the save question) | |
| Every check has run | `context` | Chip: what ran, and what needs a look; in a terminal, with the build card, in the save question | |
| Saved to Atlan Trident, or kept on this machine | `saved` | Mini card, or a chip when kept, before the ready question | `saved.json` |
| A question answered | `answer` | Mini card the first time, then a chip | `answer.json` |
| Dashboard layout proposed | `layout` | Chip | `layout.json` |
| Dashboard built | `built` | Chip: the file | `built.json` |
| A rule changed | `edit` | Mini card | `edit.json` |

The helper names the first eleven when they are due. A chip or card is the moment's result line:
add no other line that repeats its numbers. After an answer's card, the reply leads with what
answers (the name) and what explains it.

## The side page

`PROJECT visual connect` starts the run's side page on this machine, at an address only this
machine can open (`http://127.0.0.1:<port>/<key>/`), and its `next` says what to do with it:

- **The Claude desktop app:** open the address in the app's browser pane with your browser tool
  (its preview or navigate call, with the `url`). Do it right away; any helper result names it
  again (`side_page`) until the page has been opened.
- **A terminal:** the helper opens the address in the person's browser, and the next chip that
  goes in a question names it, so the person can open it again.
- **Anywhere else:** the next chip that goes in a question names the address the same way.

The page reads the run's files itself: never reload, rebuild or push to it, and never describe
what it shows. Nothing on it leaves this machine, so it shows the metrics, their sources, values
and the answer on own data as on a demo. A card's last line points to it for the detail. If
the page stops while nobody looks at it, the next helper result starts it again at the same
address. `PROJECT view` prints the address again; `PROJECT view --stop` stops it.

## Data files

Pass a moment's file on standard input as a here-document: `PROJECT visual <kind> --input -`
writes it as `<kind>.json` in the run directory and records the moment in one step. Copy values
from the result that produced them; leave out a field you do not have, never fill one in.

- `saved.json`: `workspace`, its display name. Write no counts: the helper reads what is live
  from the publish receipt `PROJECT publication` recorded. Kept on this machine, skip the file.
- `answer.json`: `question`; `headline`, what answers and its number, such as
  `Turbine base: -£6,360`; `summary`, the answer's first sentence, without the headline's number; `unit` from the metric;
  `rows`, `[label, value]` pairs from the query result in its order (at most 12); `highlight`,
  the label of the row that answers; `rule`, the metric's rule in one line; `proof`, `[{name}]`
  of the metrics and queries the answer used. For a question that asks why, add `breakdown`
  with `steps` as `[label, signed value]` from the query that splits the answering row.
- `layout.json`: `tiles` and `more` as lists of metric names, exactly as the context names
  them.
- `built.json`: `file`, the dashboard's file name as written in the working folder.
- `edit.json`: from the edit result: `metric`, `before` and `after` rules in plain words,
  `values` as `[name, before, after, unit]`, and `added` and `removed` as lists of short labels
  for the re-asked question. Add `note` when a dashboard was built before the change: it still
  shows the old rule until it is rebuilt.
