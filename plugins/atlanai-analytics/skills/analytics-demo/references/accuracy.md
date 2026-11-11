# Check how accurate it is

Read when the person picks **Check how accurate it is**. The demo has answered a question;
this shows how often the context gets an answer right, on questions the firm's analysts
already ask. Run [eval-generation](../../eval-generation/SKILL.md) as a flow, through its
[check](../../eval-generation/references/check.md), with `<agent>` and `<firm>` from the demo's
facts file. That skill asks nothing beyond the check's moments. The demo's say-what-and-why
rule applies at every moment:

| Moment | Doing | Why |
| --- | --- | --- |
| Pick | Picking 5 questions that test the metrics your `<agent>` needs, from `<firm>`'s dashboard and its analysts' queries. | The test should use questions people really ask, about what this agent is for. |
| Test | Asking each question to a fresh agent that only sees your context. | It answers as anyone would, so the score is the context's, not mine. |
| Propose | Sending one change to the rule behind a miss to Metric Studio. | The metric's owner approves it there, and it becomes a new version every agent reads. |
| Save | Saving the questions and the score to `<workspace>`. | Your team can rerun the same test after every change to a rule. |

On Not now, or at the end of the check, return to the demo's next-action choice.
