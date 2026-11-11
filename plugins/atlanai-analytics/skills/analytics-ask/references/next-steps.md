# After an answer: a dashboard, a missing metric, a wrong rule

Read when the person asks for a dashboard from their answers, when no published metric
covers the question, or when they say an answer's rule is wrong.

## Turn answers into a dashboard

Follow [the dashboard](dashboard.md): a layout in words, one confirmation, then a page built
with the host's own page tool, every number from a metric or saved query.

## Nothing published covers the question

Say so in the answer, as **Not managed**, with the formula and filters assumed. Then offer
one next step as the answer's last line, not a menu and not a native question, and treat
the person's reply as the choice
([text before a native question](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost)):

- The person can build context here: offer to build the metric. On yes, load
  `analytics-build` with the question as the problem, so its focus question is already
  answered.
- The metric belongs to another team: offer a proposal for its owners, through
  `metrics-generation` `edit`.

## The answer's rule is wrong

Ask what should change, in one question, unless they already said. Then call
`metrics-generation` `edit` with the metric and the change. It revises a definition the
person owns, or proposes the change to its owners when they do not. Re-answer the question
with the changed rule only after the edit's check passes, and say which rule answered.
