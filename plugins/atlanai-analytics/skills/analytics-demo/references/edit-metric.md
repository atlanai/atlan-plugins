# Demo flow: edit a metric

Read when the person picks "Change a rule and see what happens". It shows the reason
context matters: change one rule once, and every answer that uses it changes with it.

## 1. Offer one real change

Pick one metric from the built context that this run's questions use. Propose one change that a person at the
firm could plausibly want, taken from the metric's own rule: a population it excludes, a
threshold it uses, or a cost it leaves out. Ask one native question whose text says the
current rule in one sentence, then asks which change to make (where there is no question
tool, print them last as plain lines, as
[text before a native question](../../analytics-build/references/conventions.md#text-before-a-native-question-is-lost) says):

| Option | Description |
| --- | --- |
| The proposed change | One line: what the rule would count instead. |
| My own change | The person types it. |
| Leave it as it is | End the turn with the `Next:` line from [ready](../../analytics-build/references/setup/ready.md). |

Never propose a change the demo's data cannot show: the tables must hold the column the
new rule needs.

## 2. Make the change

Call `metrics-generation` `edit` on that metric with the chosen change, following its three
steps: `ENGINE edit` returns the authored card, `ENGINE edit --card` revises it through the
engine's card, consistency and relationship gates, and `PROJECT revise-metric` checks the old
and the new rule on the data and installs the new one. Together they return the old rule, the
new rule and both check results. The metric keeps its identity; this is a revision, not a new
metric. Never change `context/metrics.json` or the engine's files by hand. The revised card's
description and notes say the new rule too: the gates check the rule, not the words around it.

If either step refuses the new rule, say why in one line, keep the old rule, and offer one
other change. A refused `ENGINE edit` has already restored it; after a refused
`revise-metric`, restore the engine card as its message says. Never show a failed check as a
result.

## 3. Show the difference

Fill this block with the values from the edit's result. How it reaches the person depends
on where the context lives (step 4): it is either the last reply text of the turn or the
text of the save question, never reply text followed by a native question:

> **`<metric name>`, changed**
>
> | | Before | After |
> | --- | --- | --- |
> | Rule | `<old rule, plain words>` | `<new rule, plain words>` |
> | `<one worked example from this run's data>` | `<old value>` | `<new value>` |
> | Check | `<old check status>` | `<new check status>` |

Before showing it, re-ask, through `analytics-ask`, one question the person already asked
that uses this metric. With no earlier question, use the first question offered in this run. Write one
sentence with the new answer beside the old one; it goes right after the block, wherever
the block goes.

## 4. Where the change lives

- Context kept on this machine: the change is saved in the run. Print the block and the
  comparison sentence as reply text, say so in one line, close with the plain `Next:` line
  from [ready](../../analytics-build/references/setup/ready.md) and end the turn.
- Context saved to Atlan Trident: ask one native question whose text is the block as plain
  lines, then the comparison sentence, then "Save this change for everyone who uses the
  metric?" On yes, call
  `metrics-generation` `save`, which publishes a revision of the same metric after the
  person approves the plan. On no, the change stays on this machine and the published
  metric is unchanged. Then say in one line which rule answers from now on, close with the
  plain next-actions line and end the turn.
