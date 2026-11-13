---
name: propose-metrics
description: "X1 : read each verified SQL and judge what, if anything, it measures. Zero, one or several metrics per statement. Judgment, not extraction."
---

# Propose metrics (X1)

Before this task, read and apply the shared [metric consistency guidance](../references/metric-consistency.md).

Input: `$RUN/x1_prompt.json`. Output: JSON handed to `x1_propose.py --from <file>`.

You are deciding **what this estate actually measures**. Everything downstream : the merge,
the cards, what a person can ask for : is built on this answer. A metric you invent here gets
published; a metric you miss does not exist.

The examples below use invented tables. They illustrate the reasoning; the metrics you propose
come from the statement in front of you.

## The one rule

**A metric is a number the business manages.** Not every aggregate is one, and not every
metric is an aggregate the statement names.

That is a judgment, and it is why this stage is not a rule. What follows is how to make the
judgment well : not a checklist to apply.

## Output

```json
{"statements": [
  {"vq_id": "<verbatim>",
   "verdict": "metrics",
   "metrics": [
     {"name": "<business name>",
      "expression": "<copied VERBATIM from the statement>",
      "entity": "<ONE table from the statement's tables, fully qualified, nothing else: no joins, no notes>",
      "why_a_metric": "<one sentence: what the business does with this number>",
      "definition": "<what it counts or measures, over what population>",
      "aggregation": "count | count_distinct | sum | avg | min | max | ratio, or the function it calls (median, percentile_cont…): the card records those as custom",
      "grain": "<what one value describes>",
      "unit": "<accounts, days, percent, currency…>",
      "mandatory_filters": ["<restrictions without which the number means something else>"],
      "optional_filters": ["<restrictions a person would reasonably drop>"],
      "dimensions": ["<what it can legitimately be broken down by>"]}
   ]},
  {"vq_id": "<verbatim>", "verdict": "none", "metrics": []}
]}
```

`verdict` is `metrics` or `none`, and **every statement gets one.** `none` is a real answer.
`expression` must appear verbatim in the statement : you are reading what runs, never
rewriting it.

## Save in batches of about five statements

Save the answer as several files in the gate's answers folder, not one at the end: judge about
five statements in the prompt's task order, save them as the next numbered file (`01.json`,
`02.json`, …) with the same `{"statements": [...]}` shape, then go on. Each statement appears
in exactly one file. A person watches the drafts appear as each file lands; the gate still
reads all the files together, so the judgment is the same.

---

# Deciding what is a metric

## Start from what the statement is FOR

The authored `question` and `description` tell you what a person wanted. The aggregates tell
you what the SQL computes. **The metric is where those two meet.**

A statement whose question is *"who are the contacts at this account?"* computes
`MAX(contact_name)` : and measures nothing. A statement whose question is *"how many customers
signed off, and what share of the eligible base is that?"* holds two numbers a person manages,
even though one of its columns is only there to print.

The prose can also tell you a number is NOT managed: a question that calls the statement a
check or a guard is saying so plainly. A *lookup* is different : see **A value list with a
count is the measure, cut** below : because the prose describes what a person wanted, and a
statement can count a managed population while being described as a list of values.

Read `aggregates_found` as evidence of what is computed. It is not the answer, and three
things follow from that:

## A statement with `declared` measures the metric its model declares

Some statements were compiled from a metric a BI model DECLARES (a governed model such as a
semantic view). They carry a `declared` block: the model, the metric's name, its formula, its
comment and its synonyms. That statement measures exactly that metric, and nothing else:

* answer `metrics`, with **one** metric : never `none`, and never two: the validator refuses
  both, because the model governs that number;
* `expression` is the statement's own (as always), `entity` is the table it is computed over;
* name it as the model does, in words (`total_amount` -> `Total Amount`); its comment is the
  `definition`'s best source, and its synonyms say how people ask for it.

Everything else in this skill is for the statements that carry no `declared`.

## A statement compiled from a BI widget

It carries `bi_definitions`: each BI measure the widget shows, how the tool defines it
(`style`: a model measure, a column aggregated on the visual, a report's own measure, a KPI's
target or status), its formula, display folder, description, the measures it is built from
(`derived_from`, a measure chain) and the model's tags (annotations, perspectives,
endorsement). Use them to name the metric and say what it is for: the author's description
and folder are the estate's own words. A KPI's target or status is a metric of its own (the
target the KPI is measured against, the status band), never the KPI's value. They never
change which aggregates the statement computes. `conversion_quality` says how close the SQL
is to the BI tool's number; it does not decide whether something is a metric.

## An aggregate is not a metric when it is carrying a label

```sql
SELECT a.region, MAX(a.account_name) AS account_name, COUNT(*) AS accounts
FROM accounts a GROUP BY a.region
```

`MAX(account_name)` is an aggregate the grouping forced. Nobody manages it, nobody trends it,
nobody asks "what is our max account name". It exists so the row has something to print.
`COUNT(*)` here is the metric.

The tell is usually a text column under a MIN or MAX, or a pick-one wrapped around an
identifier : but do not reduce this to that pattern. Ask what a person would do with the
number.

## An aggregate is not a metric when it measures the DATA rather than the business

```sql
SELECT COUNT(*) FROM accounts                       -- how big is this table
SELECT COUNT(*) FROM accounts WHERE type='Customer' -- how many customers do we have
```

The first is not a metric. Nobody sets a target on it, nobody asks why it moved, and when it
does move the answer is "we loaded more rows". It is the size of a table, and the question
behind it is usually operational : is the feed working, did the load complete.

The second is. Customer count is managed: it is reported, it is a denominator, and a change
in it means something happened in the business.

The difference is not the expression : both are `COUNT(*)` : and it is not the presence of a
WHERE clause either. It is whether the number describes **something the business does** or
**something the warehouse holds**. Ask what a person would conclude if it dropped by ten
percent overnight. "We lost customers" is a metric. "The sync is behind" is not.

The same reading applies to a bare `COUNT(*)` over calls, tickets or events: a volume with no
population behind it is a data-freshness check wearing a metric's clothes.

## An aggregate is not a metric when it is scaffolding

A data quality check counts failures. `COUNT(*) AS failures` is real, and it is not a business
metric : its meaning is "the test passed or did not". A cardinality guard returns a boolean so
a picker knows whether to paginate. A lookup returns one row so an application can render it.

These statements usually get `verdict: "none"`. Their question already says what they are :
if the description calls it a check or a guard, believe it.

## A value list with a count is the measure, cut

```sql
SELECT LEFT(o.product_text, 60) AS product_text, COUNT(*) AS orders
FROM open_orders o
WHERE o.product_text ILIKE '%widget%'
GROUP BY 1 ORDER BY 2 DESC
```

Its description may well say *"which spellings of the widget appear on open orders"* : a
value list. Read what it computes: a count of **open orders**, a population the business
manages, cut by a column and narrowed to some of its values. That is the open-order count, and
this statement is one of its sources: propose it with the same name and expression you would
give any other count of open orders, with the column as a dimension and the narrowing as an
optional filter. The values it lists are exactly what a consumer needs to learn how that column
is written.

This is narrow, and it applies only when **both** hold:

* **the count is the answer.** Each row's number is what the statement is for : how many fall
  under each value. Where the count is printed beside fill rates, match counts or null rates,
  it is the denominator of a check on the data, and the statement is that check: `none`.
* **the layer already measures that population.** A metric a BI model declares counts it (a
  statement in this bundle carrying `declared`), or another statement here computes it for a
  business purpose. Then this statement is one more source of that measure, and no new metric
  is created.

A count of a reference table cut by one of its own attributes : accounts by channel, items by
category : is a profile of the catalogue, not a source: `none`, unless another statement here
already establishes that count as a metric. And anything that computes a property of the data :
a share of rows with a field filled in, a count of distinct spellings, a null rate : is a check,
and `none` is right.

## Propose only what the statement COMPUTES

```sql
SELECT COUNT(DISTINCT a.id) AS eligible,
       COUNT(DISTINCT CASE WHEN s.signed_at IS NOT NULL THEN a.id END) AS signed
FROM accounts a LEFT JOIN signoffs s ON …
```

Two metrics: the eligible base and the signed count. **Not three.** The sign-off rate is what
the question asks about and what the business manages : and this statement does not compute
it. A consumer divides one by the other.

Proposing it anyway would mean borrowing one of those two expressions to stand for a number
neither of them produces, and a metric whose expression does not compute it is a published
lie. Where a statement DOES compute the ratio, that ratio is a metric in its own right:

```sql
SELECT ROUND(COUNT(CASE WHEN score='good' THEN 1 END)*100.0
             / NULLIF(COUNT(CASE WHEN score IN ('good','bad') THEN 1 END),0), 1) AS csat_pct
```

That is a ratio metric, because the statement computes the ratio.

The rule generalises: **a metric is a number this statement can hand you.** If it would take
arithmetic the statement does not do, or a column it does not select, it is not in here : the
description already tells the reader the two numbers relate.

Equally, a statement returning six columns may hold one metric and five attributes describing
it. Count the numbers a person would put on a dashboard, not the columns.

## The same expression in two statements can be two metrics

`COUNT(DISTINCT account_id)` over an accounts table is not one metric. In a statement about
success-plan sign-offs it is the eligible base; in one grouped by CSM it is book size; in one
filtered to a campaign it is campaign reach. **The statement it sits in is part of its
identity** : say what this one is, in this statement, and let the merge stage decide whether
two of them are the same thing.

Do not try to predict the merge. Propose honestly and specifically.

---

# Mandatory versus optional filters

This is the distinction the merge depends on, so make it deliberately.

**Mandatory** : the number means something *else* without it. `type = 'Customer'` on a metric
called "customer accounts" is mandatory: drop it and you are counting prospects too, and the
name becomes a lie.

**Optional** : a person would reasonably ask for the metric without it. A date window, a
single region, one campaign, a segment. Dropping it widens the population but the metric is
still the same metric.

```
WHERE a.is_deleted = FALSE          -- neither: hygiene, not a restriction anyone manages
  AND a.type = 'Customer'           -- MANDATORY: the metric is about customers
  AND a.segment <> 'Growth'         -- mandatory IF the metric is named for that exclusion,
                                    --   optional if it is just this report's scope
  AND o.created_at >= '2026-01-01'  -- OPTIONAL: a period, and periods move
```

The test for the middle case: **would a person quoting this number say the restriction out
loud?** If the metric is "sign-off rate outside the Growth pod", it is mandatory. If Growth is
simply not in this report, it is optional.

Soft-delete guards and loader flags (such as `_deleted`) are neither : leave them out entirely.

**A statement's `mandatory_source_filters` are always mandatory.** Each is a predicate inside
the derived table the statement reads from: the BI tool's own query steps keep only those rows
of the table (a Power Query step that drops staff orders, say). No report chose it, and the
table the business sees has no other rows, so every metric over the statement keeps it in
`mandatory_filters`. X1 refuses an answer whose metric leaves one out.

## Dimensions

What the metric can legitimately be broken down by. Take them from the statement's `GROUP BY`
and from the columns it carries alongside the number : but only where the breakdown would
still compute the same metric. A dimension that changes the grain of the measure is not a
dimension, it is a different metric.

---

# Naming

A business name, as the `describe-verified-sql` skill defines one: title case, spoken English,
never an identifier, never a table name. *Success Plan Sign-Off Rate*, not
`signoff_count` and not *Accounts Count 2*.

Name what it measures, not what it is computed from. Two proposals that turn out to be the
same metric should ideally arrive with the same name : but do not force it, and never blur a
name to make a merge more likely. The merge stage reads definitions, not just names.

---

# What to refuse

* **Do not invent an expression.** Quote what runs. The validator refuses an expression that
  does not appear in the statement.
* **Do not propose a metric the statement cannot compute.** If the number would need a column
  the statement does not select or a join it does not make, it is not in here.
* **Do not pad.** A statement that measures nothing gets `verdict: "none"`, and that is a
  correct and useful answer. A layer of plausible non-metrics is worse than a smaller true one,
  because every one of them competes in search with something real.
* **Do not describe.** The prose is already authored; your job is the measure.
