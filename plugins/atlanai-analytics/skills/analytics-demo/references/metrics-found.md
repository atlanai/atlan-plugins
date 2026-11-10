# Demo: metrics found, before the cards are built

Read when the engine reaches its X3 card gate, before writing any card. Open the chosen
demo's facts file again ([Fixit](yard.md), [Ticketfuddle](box-office.md)).

**Why this moment matters.** The person sees, in plain names, what their sources turned
into before the work of writing each rule starts. It replaces the demo's old first-metric
preview: one look at the whole list, not one card in depth.

**The celebration is the question's own text**, for the reason
[context built](../../analytics-build/references/setup/save.md) gives: text written before a
tool call reaches the person as a one-line progress note, and a native question arrives word
for word. Do not put it in an option `preview`, and do not also print it before the question.

Take the names from the X3 gate's merged metric collection, in its order, each in plain
words (`gross margin`, not `gross_margin_pct`). List every metric when there are at most
twelve; otherwise the first ten and "and `<N>` more". Count queries separately. Never invent
a name the gate's evidence does not hold.

- **header:** `Step 3 of 6`
- **question:** the lines below, with their line breaks and leading spaces. The fence only
  marks the lines here.

```
 *   .  '   *    .   '  *   .    '   *  .
   '   *   .  METRICS FOUND  .   *   '
 .   *    '   .    *   '    .   *    '  .

<firm>'s <dashboard and past queries> hold <N> metrics and <N> queries:

  - <metric name>
  - <metric name>
  - ...

Next I write the rule for each one and check it on <firm>'s data.
Build them now?
```

| Option | Description |
| --- | --- |
| Build them (recommended) | Write each rule and check it on the data. A few minutes. |
| Not now | Stop here; /atlanai-analytics:analytics-demo picks it up later. |

Where there is no question tool, print the same lines as the last thing in the message,
then the two choices in plain words, and call no tool after it.

On "Build them", author every card and continue step 3. The tracker stays on step 3 until
every check has run; then [context built](../../analytics-build/references/setup/save.md)
celebrates the result with its true counts.
