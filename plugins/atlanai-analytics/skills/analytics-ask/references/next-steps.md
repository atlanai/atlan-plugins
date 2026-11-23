# After an answer: a dashboard, a missing metric, a wrong rule

Read when the person asks for a dashboard from their answers, when no published metric
covers the question, or when they say an answer's rule is wrong.

## Turn answers into a dashboard

Follow [the dashboard](dashboard.md): build the page straight away with the host's own page
tool, every number from a metric or saved query, then say what it shows. A layout in words
first only when the person asks to see one before anything is built.

## Nothing published covers the question

Say so in the answer's **Based on**: "My own SQL. No metric in Atlan covers <measure>. I
assumed <formula and filters>." Then offer
one next step as the answer's last line, not a menu and not a native question, and treat
the person's reply as the choice
([text before a native question](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost)):

- The person can build context here: offer to build the metric as the answer's last line,
  "Next: I can build the metric for <measure>, so it's defined once. Say yes to start." On yes, load `analytics-build` with the question as its subject: its
  domain entry looks for what already exists before building. Offer this before a proposal
  whenever a warehouse query tool is in reach.
- The metric belongs to another team: offer a proposal for its owners, through
  `metrics-generation` `edit`: "Next: I can propose this metric to the owners of <workspace>.
  They review it in Atlan."

## The answer's rule is wrong

Ask what should change, in one question, unless they already said: "What should <metric> count
instead?" Then call
`metrics-generation` `edit` with the metric and the change. It revises a definition the
person owns, or proposes the change to its owners when they do not. Re-answer the question
with the changed rule only after the edit's check passes, and say "This answer uses your changed
rule for <metric>: <rule>."
