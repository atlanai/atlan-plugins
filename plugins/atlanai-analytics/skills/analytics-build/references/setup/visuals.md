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

1. **What the chat shows**: a chip, one line such as `● **4/4** metrics and queries ran · on
   your data`, or at the four big moments a mini card: a bold headline over a few indented lines.
   Show it at once, as the moment's result; a command's own output is folded away, so the person
   never sees it there.
   - **With a `show_widget` tool** (the Claude desktop app): add `--widget` to every `visual`
     call and pass the HTML it prints to that tool, chips and cards alike. There, text between
     tool calls or inside a question never reaches the person.
   - **Otherwise** (a terminal): copy the lines unchanged into your reply, on their own lines.
     Never put them in a native question's text: a terminal draws question text in one fixed
     colour that a light background hides, and these lines take the reply's own colours. A
     terminal shows six lines or fewer before a question whole, and every card that can come
     right before one keeps to that, so when a question follows, these lines are the text
     before it.
   When the helper prints nothing here, the chat shows nothing for this moment. When it prints
   nothing at all, the run has nothing true to show yet: run it again once the step's work is done.
2. **A last line, `side view: {...}`**, a JSON object; never print it. `next` says what to do
   with the lines above it. The other fields say what to publish or push, as the next section
   says. An `error` field means the side view missed this update: carry on.

| Moment | Kind | The chat shows | Data |
| --- | --- | --- | --- |
| The run folder exists, right after the first survey | `connect` | Chip: the sources found. It opens the side view | |
| Sources read and the data loaded: the end of a demo's step 1; on own data, right after `PROJECT bind` | `sources` | Chip | |
| Metrics generated: the engine run is complete, before the checks | `build` | Mini card | |
| Every check has run | `context` | Chip, before the save question | |
| Saved to Atlan Trident, or kept on this machine | `saved` | Mini card, or a chip when kept, before the ready question | `saved.json` |
| A question answered | `answer` | Mini card the first time, then a chip | `answer.json` |
| Dashboard layout proposed | `layout` | Chip | `layout.json` |
| A rule changed | `edit` | Mini card | `edit.json` |

The helper names the first four when they are due. A chip or card is the moment's result line:
add no other line that repeats its numbers.

## The side view

A private claude.ai page that shows every step, what came out of it and how. It needs the
`Artifact` and `ArtifactData` tools: Claude Code and the Claude apps, signed in with claude.ai.

- `page=<path>`, on the first update only: publish the page once with the `Artifact` tool,
  `file_path` the path, `capabilities` `{"db": {}}`, `icon` `chart` and a one-sentence
  `description`, reading the file in full first as that tool asks. Then run
  `PROJECT view --url "<its address>"`, and say in one clause that the run's progress is in the
  page beside the chat, with its address.
- `doc=<path>`: push it with `ArtifactData`: `action` `set`, the page's `url`, `collection`
  `steps`, `doc_id` the file's name without `.json` (such as `s02`) and `file_path` the path.
  Never read or retype it.
- `url=<address>`: the page this run already published. Use it; never publish another.
- A `side_view` field on any `PROJECT` or engine result names a newer document as one line: push
  it as the line says before going on. These updates tick each piece of work while its step runs.

Without those tools (Codex, Cursor, or Claude Code signed in with an API key, Bedrock or
Vertex), `local=<path>` is the same page as a file that refreshes itself. Say once, in your
reply: "Follow the build: open `<path>` in a browser." Then ignore `doc` paths. A failed publish or push leaves the run unchanged: carry on without the side
view, and try a failed step once more at most.

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
