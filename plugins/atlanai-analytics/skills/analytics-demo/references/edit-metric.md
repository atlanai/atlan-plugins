# Demo flow: edit a metric

Read when the person picks "Change a rule and see what happens". It shows the reason
context matters: change one rule once, and every answer that uses it changes with it.

## 1. Offer one real change

Pick one metric from the built context that the demo's questions use. Propose one change that a person at the
firm could plausibly want, taken from the metric's own rule: a population it excludes, a
threshold it uses, or a cost it leaves out. Say the current rule in one sentence, then ask
one native question:

| Option | Description |
| --- | --- |
| The proposed change | One line: what the rule would count instead. |
| My own change | The person types it. |
| Leave it as it is | Return to the next-action choice. |

Never propose a change the demo's data cannot show: the tables must hold the column the
new rule needs.

## 2. Make the change

Call `metrics-generation` `edit` on that metric with the chosen change. It changes the
definition through the engine, re-runs the check and returns the old rule, the new rule and
both check results. The metric keeps its identity; this is a revision, not a new metric.

If the check on the new rule fails, say so in one line, keep the old rule, and offer one
other change. Never show a failed check as a result.

## 3. Show the difference

Fill this block with the values from the edit's result. How it reaches the person depends
on where the context lives (step 4): it is either the last reply text of the turn or the
text of the save question, never reply text followed by a native question:

> **`<metric name>`, changed**
>
> | | Before | After |
> | --- | --- | --- |
> | Rule | `<old rule, plain words>` | `<new rule, plain words>` |
> | `<the demo's worked example>` | `<old value>` | `<new value>` |
> | Check | `<old check status>` | `<new check status>` |

Then re-ask, through `analytics-ask`, one question the person already asked that uses this
metric, and show the new answer beside the old one in one sentence. With no earlier
question, use the demo's first question.

## 4. Where the change lives

- Context kept on this machine: the change is saved in the run. Print the block as reply
  text, say so in one line, close with the plain next-actions line from
  [ready](../../analytics-build/references/setup/ready.md) and end the turn.
- Context saved to Atlan Trident: ask one native question whose text is the block as plain
  lines, then "Save this change for everyone who uses the metric?" On yes, call
  `metrics-generation` `save`, which publishes a revision of the same metric after the
  person approves the plan. On no, the change stays on this machine and the published
  metric is unchanged. Then say in one line which rule answers from now on, close with the
  plain next-actions line and end the turn.
