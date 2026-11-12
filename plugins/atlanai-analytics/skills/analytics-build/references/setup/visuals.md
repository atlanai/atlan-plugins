# Chips, mini cards and the side view

Read before the opening or the welcome, and again when a moment below is reached. The flow,
its questions and its words do not change. At each moment the helper reads the run's own files
and prints what the chat shows, and the side view, a page beside the chat, gets an update each
time a piece of work finishes, so the person watches each step tick through. Never draw, design
or retype a visual, and never put a number in one by hand.

## At each moment

Run `PROJECT visual <kind>`. Any other `PROJECT` result with a `visual` field names a moment the
run has reached but not shown yet: run it before going on. A demo adds `--data` with its facts file,
[Fixit](../../../analytics-demo/references/yard.json) or
[Ticketfuddle](../../../analytics-demo/references/box-office.json), on every call. It prints:

1. **What the chat shows**: a chip, one line such as `● **4/4** metrics and queries checked · on
   your data`, or at the four big moments a mini card: a bold headline over a few indented lines.
   Show it at once, as the moment's result; a command's own output is folded away, so the person
   never sees it there.
   - **With a `show_widget` tool** (the Claude desktop app): add `--widget` to every `visual`
     call and pass the HTML it prints to that tool, chips and cards alike. There, text between
     tool calls or inside a question never reaches the person.
   - **Otherwise** (a terminal): the helper prints plain lines and says where they go. A moment
     that comes right before a native question goes in that question's own text, which is always
     shown: text written just before a question folds into one summary line, and reply text in
     the middle of a run is often left out. Other moments are reply text, unchanged, on their own
     lines; an answer's card leads the answer. On a light terminal, Claude Code's dark theme draws
     every question's text in white, the flow's own included: the person switches Claude Code to
     its light theme (`/theme`).
   When the helper prints nothing here, the chat shows nothing for this moment. When it prints
   nothing at all, the run has nothing true to show yet: carry on; the helper names the moment
   again when it is due.
2. **A last line, `side view: {...}`**, a JSON object; never print it. `next` says what to do
   with the lines above it. The other fields say what to publish or push, as the next section
   says. An `error` field means the side view missed this update: carry on.

| Moment | Kind | The chat shows | Data |
| --- | --- | --- | --- |
| The run folder exists, right after the first survey | `connect` | Nothing: it opens the side view | |
| Own data, right before choosing what to build from | `found` | Chip: the sources and the warehouse found | |
| Data read: a demo's or local tables loaded, or a warehouse's past queries read | `sources` | Chip (a demo's opens the next question in a terminal) | |
| Metrics generated: the engine run is complete, before the checks | `build` | Mini card (in a terminal, it waits for the save question) | |
| Every check has run | `context` | Chip: how many checked, and how many need a look; in a terminal, with the build card, in the save question | |
| Saved to Atlan Trident, or kept on this machine | `saved` | Mini card, or a chip when kept, before the ready question | `saved.json` |
| A question answered | `answer` | Mini card the first time, then a chip | `answer.json` |
| Dashboard layout proposed | `layout` | Chip | `layout.json` |
| A rule changed | `edit` | Mini card | `edit.json` |

The helper names the first five when they are due. A chip or card is the moment's result line:
add no other line that repeats its numbers. After an answer's card, the reply leads with what
answers (the name) and what explains it.

## The side view

A private claude.ai page that shows every step and how it got there. It uses the `Artifact` and
`ArtifactData` tools of Claude Code and the Claude apps signed in with claude.ai. `ArtifactData`
is often deferred: load it with ToolSearch before the first push; a deferred tool is not a missing
one. On own data the page carries progress only (steps, pieces of work, statuses and counts);
metric names, values, answers and workspace names stay in the chat.

- `page=<path>`, on every moment until the run has a page: publish it once with the `Artifact`
  tool, `file_path` the path, `capabilities` `{"db": {}}`, `icon` `chart` and a one-sentence
  `description` that names no metric, table or value, reading the file in full first as that
  tool asks. Then run `PROJECT view --url "<its address>"`: it records the page and names the
  update to push. Say in one clause that the run's progress is in the page beside the chat, with
  its address. If you published it but did not run that command, run it now; never publish twice.
- `doc=<path>`: push it with `ArtifactData`: `action` `set`, the page's `url`, `collection`
  `steps`, `doc_id` the file's name without `.json` (such as `s02`) and `file_path` the path.
  Never read or retype it.
- `url=<address>`: the page this run already published. Use it; never publish another.
- A `side_view` field on any `PROJECT` or engine result is one line: open the side view, while the
  run has no page yet, or push a newer document. Do as it says before going on. These updates tick
  each piece of work while its step runs.

Codex, Cursor, and Claude Code signed in with an API key, Bedrock or Vertex have no artifact
tools. There, the run's `view/progress.html` is the same page as a file that refreshes itself:
say once, in your reply, "Follow the build: open `<path>` in a browser.", and ignore `page`,
`doc` and push lines. A failed publish or push leaves the run unchanged: carry on without the
side view, and try a failed step once more at most.

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
- `edit.json`: from the edit result: `metric`, `before` and `after` rules in plain words,
  `values` as `[name, before, after, unit]`, and `added` and `removed` as lists of short labels
  for the re-asked question. Add `note` when a dashboard was built before the change: it still
  shows the old rule until it is rebuilt.
