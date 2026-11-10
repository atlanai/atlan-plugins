# After an answer: a dashboard, a missing metric, a wrong rule

Read when the person asks for a dashboard from their answers, when no published metric
covers the question, or when they say an answer's rule is wrong.

## Turn answers into a dashboard

There is no Atlan dashboard renderer. Propose the layout, then build it with the host's own
page tool.

1. Propose the layout in words, at most six lines: one tile per metric the conversation
   used, one breakdown and one chart. Ask one native question to confirm or change it.
2. Build the page with what the host offers: an Artifact in Claude, otherwise one
   self-contained HTML file in the working folder with inline CSS and SVG, no external
   scripts or fonts, readable in light and dark.
3. Every number comes from a metric or saved query, run through this skill's answer steps.
   Never write new SQL for a number a metric already defines, and never type a number in.
4. Each tile names its metric and links to its page in Atlan Trident when one exists
   ([definition links](../../analytics-build/references/definition-links.md)).
5. Say where the page is and how to open it, in one line. Publish it nowhere unless asked.

## Nothing published covers the question

Say so in the answer, as **Not managed**, with the formula and filters assumed. Then offer
one next step, not a menu:

- The person can build context here: offer to build the metric. On yes, load
  `analytics-build` with the question as the problem, so its focus question is already
  answered.
- The metric belongs to another team: offer a proposal for its owners, through
  `analytics-metrics` `edit`.

## The answer's rule is wrong

Ask what should change, in one question, unless they already said. Then call
`analytics-metrics` `edit` with the metric and the change. It revises a definition the
person owns, or proposes the change to its owners when they do not. Re-answer the question
with the changed rule only after the edit's check passes, and say which rule answered.
