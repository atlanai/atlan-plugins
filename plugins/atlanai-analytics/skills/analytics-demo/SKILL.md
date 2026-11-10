---
name: analytics-demo
description: Run a guided analytics demo on sample data, with one metric end to end, questions, a dashboard, an edit and where to save. Use when someone asks to try, see or demo Atlan Analytics. Not for their own sources (analytics-build).
user-invocable: false
---

# Demo: understand Atlan Analytics by watching it work

An orchestrator. It owns the demo conversation: which dataset, which flow runs next, what
the person sees. It authors nothing itself; it calls `analytics-survey`, `analytics-metrics`
and `analytics-ask` and presets the answers a real build would ask for.

Success is not that setup finished. Success is that the person can say what they built and
why it matters. The blocks printed at each moment are the product; a run that builds
everything but skips a block has failed.

Read [shared conventions](../analytics-build/references/conventions.md) first. The moment
files, the demo facts and the helper live in the `analytics-build` skill, which holds the
shared runtime. `PROJECT` is the helper named in [setup](../analytics-build/references/setup.md).

## 1. Choose a dataset

Ask the one native question in [the start choice](../analytics-build/references/setup/demo.md),
before any other tool call. If the request already names a demo, skip the question.

| Choice | Do |
| --- | --- |
| Fixit & Co. | Read [the builder's yard](../analytics-build/references/setup/demo-yard.md) |
| Ticketfuddle | Read [the box office](../analytics-build/references/setup/demo-box-office.md) |
| Use my own data | Load `analytics-build` and stop here. Carry nothing over. |

Then print the chosen demo's welcome whole and ask "Ready to build?", as the start choice
describes. A saved demo run resumes at its next flow; say the saved result and the next
step instead of repeating the welcome.

## 2. The flows

Each flow has the same contract: what it needs, what it shows, what it calls. Run
`first-metric` first. After any flow ends, section 3 picks what to offer.

| Flow | Needs | Shows | Calls |
| --- | --- | --- | --- |
| `first-metric` | A chosen dataset | The step tracker, the sources question, then [the first metric](../analytics-build/references/setup/demo-first-metric.md): its rule, population, source, check and worked example | `analytics-survey` `inventory`; `analytics-metrics` `generate` and `check` |
| `save` | `first-metric` done | [Context built and where to keep it](../analytics-build/references/setup/demo-save.md): the celebration, actual counts, this machine or Atlan Trident | `analytics-metrics` `save` |
| `questions` | `save` done | [Ready for questions](../analytics-build/references/setup/demo-ready.md): the ready block and the demo's questions. Answer only one the person picks or types | `analytics-ask` |
| `dashboard` | One answer given | A layout in words, one confirmation, then a page the person can open | `analytics-ask`, [turn answers into a dashboard](../analytics-ask/references/next-steps.md) |
| `edit-metric` | `first-metric` done | [Edit a metric](references/edit-metric.md): the rule before and after, and how one answer changes | `analytics-metrics` `edit`; `analytics-ask` |

The technical stages inside `first-metric` and `save` are the ones in
[the guided tour](../analytics-build/references/setup/demo.md), sections 2 to 7: the demo
repository, the tracker at every step, the "Doing" and "Why" lines, the preset focus and
purpose, and the rule to read each moment's file when it arrives. Follow them as written.
The capabilities run without asking the person anything, because the demo supplies the
focus, the purpose and the source choice.

A new flow is one reference file and one row in this table.

## 3. After each flow: one nudge

When a flow ends, ask one native question that offers what is now possible. Offer only
flows whose needs are met and that have not run, at most three, plus the way out:

| Option | When to offer | Does |
| --- | --- | --- |
| Ask a question | `save` done | `questions` |
| Design a dashboard | One answer given, no dashboard yet | `dashboard` |
| Change a rule and see what happens | `first-metric` done, no edit yet | `edit-metric` |
| Try the other demo | Any time after `questions` | Section 1, a new run |
| Do this on my data | Always, last | Load `analytics-build`. Carry nothing over |
| I'm done | Always | Wrap up in one line |

Keep the order the person would want next: after `first-metric`, lead with saving; after
`questions`, lead with the dashboard; after `dashboard`, lead with the edit. Never offer a
flow twice in one run, and never start one the person did not pick.

## 4. State

The demo's run records the dataset and what has been built; `PROJECT journey status` and
`PROJECT checklist` read it. Which flows ran is read from the run and the conversation:
a saved destination means `save` ran, a built page means `dashboard` ran. A demo run never
continues as a build of the person's own data, and a build never resumes as a demo.

## 5. Rules for every moment

- Reasoning is never shown to the person. Anything they must read is reply text or a
  question's visible prompt, sent before the question.
- Never name a person at the demo firm. Say metrics and queries; the whole is the context.
- Never answer a question the person has not chosen.
- Only "Save to Atlan Trident" starts sign-in.

Before sending: the first sentence is the result or the step; one next step, not a menu
beyond the nudge; no apology, no closing offer; no em dashes or exclamation marks.
