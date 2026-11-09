---
name: atlan-analytics-investigate
description: Investigate why a metric changed or what drives it, by planning, querying and reconciling several cuts. Use for root cause, deep dives, drivers and period comparisons. Not for one number or one breakdown (atlan-analytics-ask).
---

# Investigate a change in a governed metric

Read [shared conventions](../atlan-analytics/references/conventions.md) first; its **Source of
truth**, read-only SQL and safety rules apply. Work in the conversation: no project, eval,
agent or registry write, and no subagents. A question that one number or one breakdown
settles belongs to `atlan-analytics-ask`: load it instead and say nothing about the switch.

Every figure in the finding comes from a result returned in this conversation. A plan, a
hypothesis or an earlier report is never evidence.

## 1. Frame the investigation

Write down the measure, the window, the **baseline** it is compared with (the period before,
the same period a year earlier, a target), the population and the decision the person is
making. Ask only when a missing piece would change the finding, and then once; otherwise take
the narrowest reasonable reading and state it in the finding.

## 2. Gather context in two calls

1. Open the session and find the metric as steps 2.1 and 2.2 of `atlan-analytics-ask` say:
   `extension__metric__search_metrics` with `semantic: true`, `include_definition: true`,
   `definition_view: "compact"`, `definition_limit: 1` and no `workspace_id`. A hit is a
   candidate: keep the one whose formula measures what was asked. Send nothing else until it
   returns the session id.
2. Send these together, as one `mcp__call_read_tools` call when listed:
   - **The kept definition**, when it was not the first hit: the same search with its exact
     `name` and `workspace_id`.
   - **Its release history**: `registry__list_releases` with the metric's `artifact_id`. A
     release dated inside or between the two windows means the definition changed. That is
     the first hypothesis to test, and the finding says which release each window used.
   - **Earlier analyses**: `registry__search_artifacts` with kind `output`, the measure as
     `query` and the metric's `workspace_id`. A hit is a lead for the plan. Recompute any
     figure you take from it, and name it under **Sources** as prior work.
   - **The warehouse**, unless this conversation holds it: steps 0 to 2 of
     [warehouse discovery](../atlan-analytics/references/connect.md).

Use only released revisions, as step 2.4 of `atlan-analytics-ask` says. With no released
metric, continue with your own formula, spell out its assumptions, and label the whole
finding **Not governed**. For a ratio, read the numerator and denominator from the same
definition; never pair one metric's numerator with another's denominator.

## 3. Check that the change is real

Before explaining anything, run these, together where the tool allows:

- the measure for the window and the baseline, from the definition's formula, population and
  mandatory filters, in one statement that returns both;
- the measure for the six to eight periods before the baseline, at the same grain;
- the row count and the latest event date in each window.

Then decide, in this order:

1. **Incomplete data.** The window is still filling, or its latest date stops early. Say so
   and compare complete periods only.
2. **Definition change.** Recompute both windows under one release before comparing.
3. **Normal variation.** The change is no larger than the swings in the earlier periods.
   Report the range, say the change sits inside it, and stop. Name no driver.

Only a change that survives all three goes on to a plan.

## 4. Plan, then continue

List three to six hypotheses, each with the cut that would confirm or rule it out. Draw them
from these classes, in this order of cost to check:

| Class | What it looks like | Cut |
| --- | --- | --- |
| Data artefact | Duplicates, a late load, a new source | Row counts and distinct keys per day |
| Volume | The denominator or the population moved | Counts by period |
| Mix | Segment shares moved, segment rates did not | Share and rate per segment |
| Rate | The measure moved inside segments | Rate per segment, both windows |
| Concentration | One customer, product or day carries it | Top entities by change |
| Calendar | Working days, a holiday, seasonality | The same period a year earlier |

Show the plan in at most six lines and continue in the same turn. The plan is information,
not a request for approval.

## 5. Query

- One SELECT or WITH statement per cut, with a LIMIT. Aggregate in the warehouse: never
  pull raw rows to add up here.
- Return both windows from one statement, so a cut is compared under one set of filters.
- Keep the definition's formula, population and mandatory filters in every cut. A cut adds
  a GROUP BY, never a new filter that the baseline lacks.
- Rank a dimension's values by size of change, keep the top eight and fold the rest into
  `other`, so the parts still add up.
- Send independent cuts together. After twelve statements, report what you have and ask
  whether to go deeper.
- Check column names against the definition, the ontology's context mode when installed, or
  the table's columns. Never guess one.

With no warehouse tool, do not stop. Give the finding so far (the definition, its release
history, earlier analyses), then the statements for step 3 in the definition's dialect for
the person to run, and continue from what they paste back.

## 6. Decompose and reconcile

Follow [decomposition](references/decomposition.md) for the arithmetic. In short:

- **A sum or count:** each segment's contribution is its own change, and contributions add
  up to the total change.
- **A rate or average:** split the change into a **mix** effect (shares moved) and a
  **rate** effect (the measure moved inside segments). Never add or average segment rates.
- **Go one level deeper** only into the segment that carries the largest share, along one
  further dimension. Stop at three levels, or when the next cut explains under a tenth of
  the change.

Reconcile before writing: the parts add up to the total change within rounding. What does
not add up is reported as **unexplained**, with its size. Never spread it across the parts
or drop it.

Apply [explanation checks](../atlan-analytics/references/answer-checks.md) to every
numerical claim, and recompute the headline through a second route (the total against the
sum of its segments). A mismatch is fixed or reported, never averaged away.

## 7. Report the finding

Lead with the finding in one or two sentences: the measure, the change, the window and the
largest contributor with its share ("On-time departure rate fell 5.0 points in October
against September. A shift of departures towards DEL accounts for 6.0 points; rates inside
stations rose 1.0 point."). Then:

- **Evidence**: one table of at most six rows: contributor, effect, share of the change.
- **Ruled out**: each hypothesis the data did not support, in one line.
- **Unexplained**: the remainder and its size, or "none".
- **Source**: each metric or saved query used, by name, with its release, then its id and
  release id in code (`metric_… · rel_…`); or **Not governed**, with the formula assumed.
- **Caveats**: a partial window, an unreviewed query, a definition that changed.

Say "associated with", not "caused", unless an experiment shows it. Give SQL only when
asked. A recommendation is one sentence, and only when the evidence supports it.

## 8. Close the gap

For a missing or disputed definition, or a request to save or share the finding, follow
[answer follow-ups](../atlan-analytics/references/ask-followups.md).

Before sending, check: the first sentence is the finding, every figure has a result behind
it, the parts add up, and there is no closing offer.
