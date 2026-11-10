# Demo: the first metric

**Why this moment matters.** It is the first time the person sees value: their sources
became something real, with numbers. Without the block they are asked to build more of
something they have never seen. The block is the point of this step; the build was only
how to get here.

**Two rules for this moment.** Your reasoning is never shown to the person: a note in
your thinking is not a reply, however it is worded. So send the block below as reply text,
in the same message as the question and before it. A question sent without the block above
it is a failure: send the block first.

This is where people get lost: a metric appears, they do not know what it is, where it
came from or why it matters, and they are asked to build more. So the first metric gets
its own block, in the same form as the welcome, before anything is asked.

Open the chosen demo's file again for its facts ([Fixit](demo-yard.md),
[Ticketfuddle](demo-box-office.md)). Follow [build and check](build-check.md)'s
first-candidate rule with that demo's first metric: build it, run its check, and stop.
Then print the block below before the question, whole and as written, as an ordinary
reply, exactly as the welcome is printed. It is a fixed text: do not shorten it, summarize
it, or replace it with a line about the result or about what you will do next. The only
changes are the values in `<angle brackets>`: the named ones come from the demo's facts
table, word for word, and the numbers come from this run. Never invent a value, and never
claim a check that did not run; when a row has nothing true to say, drop that row.

> **Your first metric: `<first metric>`**
>
> `<first metric, in plain words>` It is the number behind the question `<firm>` keeps
> asking.
>
> **Why it needed writing down.** `<firm>`'s dashboard and its analysts' queries each
> worked it out their own way. A metric states the rule once, so the agent, and everyone
> who asks, gets the same number.
>
> **The rule**
>
> | | |
> | --- | --- |
> | What it measures | `<what it measures>` |
> | How it is worked out | `<how it is worked out>` |
> | What counts | `<which rows count, from the rule the run built, such as: all jobs except cancelled ones>` |
> | Where the rule came from | The dashboard's `<tile name>` tile`<, and N past queries that ask about it>` |
> | How I checked it | Ran it on `<firm>`'s data: `<the figure it returned and over what>``<, matching a separate sum from the raw tables>` |
>
> **On one example: `<the example the facts name, from this run>`**
>
> | | |
> | --- | --- |
> | `<first row label>` | `<amount>` |
> | `<next row label>` | `<amount>` |
> | `<next row label>` | `<amount>` |
> | **`<first metric>`** | **`<its value on this example>`** |
>
> That is the metric at work: one rule, one example, every number traceable to a row in
> `<firm>`'s data. `<after the example>`
>
> **What's next.** This is 1 of about `<N>` metrics. The others, such as `<the other
> tiles>`, are built and checked the same way.
>
> **Compare it with the live dashboard.** The tour page shows the same number on
> `<firm>`'s own dashboard: `<tour>`

Keep SQL out of the block. With "Dashboard only", the source row names the tile alone.
Then ask one native question. Its prompt carries the step and the result, so they are
seen whatever else happens: "Step 3 of 6. `<first metric>` is built and checked:
`<the figure it returned and over what>`. Build the rest?":

| Option | Description |
| --- | --- |
| Build the rest (recommended) | The other metrics and queries from the sources you chose, each checked the same way. A few minutes. |
| Ask a question now | Try this metric straight away with one question about it. Answered from this machine; this question comes back after. |
| Show how this one was built | The SQL and the source lines behind this metric, then this question again. |

A person who wants to stop says so in their own words: keep the one metric, save nothing
to Atlan Trident. On "Build the rest", finish steps 3 and 4, with a count as the work goes
("4 of 7 metrics built"). This block is the setup preview; do not show a second sample
before the save question unless the first metric changed.

