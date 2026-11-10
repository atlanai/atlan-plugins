---
name: merge-metrics
description: "X2 : merge metric proposals into one metric per thing the business measures. Filters split mandatory from optional, dimensions union, every source traced."
---

# Merge metrics (X2)

Before this task, read and apply the shared [metric consistency guidance](../references/metric-consistency.md).

Input: `$RUN/x2_prompt.json`. Output: JSON handed to `x2_merge.py --from <file>`.

Every proposal is one statement's reading of one number. Several statements measure the same
thing; the same statement's number appears again elsewhere with a different scope. **You decide
what is one metric.**

**Read the statements.** Each proposal carries the SQL it came from and the question that
statement answers. That context is the evidence : a formula is not. Two proposals are one
metric when the statements *mean* the same thing, and a merge decided without opening the SQL
is a guess about spelling.

The examples use invented tables. They illustrate the reasoning.

## The two failures this exists to prevent

**Fusing.** `COUNT(DISTINCT id)` over an accounts table is a sign-off eligible base in one
statement and a book size per manager in another. Same expression, same table, two metrics.
Merging them publishes a number under a name that describes neither.

**Splitting.** The same metric written `COUNT(DISTINCT a.ID)` here and `COUNT(DISTINCT acc.id)`
there is one thing to a person. Leaving them apart publishes the same metric twice, and they
compete with each other in search.

Neither is decidable from the expression. Read the definitions, the `why_a_metric`, and the
statements behind them.

## Output

```json
{"metrics": [
  {"name": "<business name>",
   "definition": "<what it measures, over what population>",
   "expression": "<canonical : copied from ONE of the sources, never synthesised>",
   "entity": "<the table it is computed over>",
   "aggregation": "count | count_distinct | sum | avg | min | max | ratio",
   "grain": "<what one value describes>",
   "unit": "<accounts, days, percent…>",
   "mandatory_filters": [
     {"filter": "<the metric is a different metric without it>", "why": "<one clause>"}],
   "optional_filters": [
     {"filter": "<a knob>", "from": ["<vq_id>", "…"]}],
   "dimensions": [
     {"dimension": "<what it can be broken down by>", "from": ["<vq_id>", "…"]}],
   "sources": [
     {"proposal_id": "<verbatim>", "vq_id": "<verbatim>",
      "name_as_proposed": "<what X1 called it>",
      "contributed": ["<what this source adds that others do not>"]}],
   "merge_reasoning": "<why these proposals are one metric : or, for a single source, why it stands alone>"}
]}
```

**Every `proposal_id` appears in exactly one metric's `sources`.** Nothing dropped, nothing
counted twice. A proposal nobody merged is a metric the estate measures and the layer does not
publish, and it fails silently : the validator refuses it.

A metric with one source is normal and correct. Most metrics are measured once.

---

# Deciding whether two proposals are one metric

Ask: **would a person be surprised to get one number when they asked for the other?**

Answer it from the statements. Two `COUNT(*)` proposals tell you nothing until you see that
one joins to sign-offs and filters to a quarter while the other groups by manager across the
whole book : at which point they are obviously not one metric, and no amount of comparing
formulas would have said so. Conversely two statements whose SQL differs completely can be one
metric: a staging query and the mart query built on it, restricted the same way, counting the
same population for the same purpose.

Things only the SQL tells you, and each of them can decide a merge:

* **what it joins to** : a count of accounts joined to sign-offs is about sign-offs, whatever
  the aggregate says;
* **what the population really is** : a filter buried in a CTE restricts the number as hard
  as one in the outer WHERE, and a proposal's filter list is a summary that may have missed it;
* **the grain** : the same expression grouped one way and not another may be two metrics;
* **what else the statement returns** : the columns beside the number say what it was FOR.

They are ONE metric when the difference is only:

* **spelling** : a different table alias, a rewritten but equivalent expression;
* **scope** : one statement ran it for a quarter, a segment or a campaign and the other did
  not. That is an optional filter, not a second metric;
* **grain of presentation** : one grouped by manager, one not. That is a dimension.

They are TWO metrics when the difference is:

* **the population it counts** : eligible accounts and engaged accounts are not the same
  number even over the same table;
* **what the number is FOR** : read `why_a_metric`. A base used as a denominator and a book
  size used to balance workload are different metrics that happen to count the same rows;
* **a restriction that is part of the identity** : "customer accounts" and "accounts" are two
  metrics, not one with an optional filter, when the name and the meaning turn on it.

## A declared metric is its own metric

A proposal whose statement carries `declared` comes from a metric a BI model declares. Two
DIFFERENT declared metrics are never one merged metric, whatever their formulas look like: the
model publishes both, and the validator refuses the merge. A proposal from query history that
computes the same thing as a declared metric does belong with it : that is how the history
statements become its sources. The one exception is the same declaration twice (two models
declaring one formula over one table): that is one metric with two sources.

## One BI number, several definitions

A BI model often spells one number several ways: a measure and its copy, a format-string
variant (`[Sales]` shown in dollars), the column aggregated on a visual (`Sum of AMOUNT`), a
thin report's own measure, a calculation item applied to a measure beside the copied measure
that does the same, a workbook's calculated field beside the data source's. Each reaches you
as its own proposal. When their statements compute the same aggregate over the same table, or
their BI definitions are the same formula in one model (one visual may compile it beside time
intelligence, another alone), they are ONE metric: merge them, name each as a source, and make a
filter only some of them apply an optional filter. `same_number_hints` lists those groups;
read the statements before you follow one.

A definition with `calculation_items` is a calculation group's item applied to the measure:
the number is the item's formula with the measure in place of `SELECTEDMEASURE()`, not the
measure's own. One item applied to `[Sales]` whose formula is `CALCULATE(SELECTEDMEASURE(),
DATESYTD(...))` is the same number as a copied measure `CALCULATE([Sales], DATESYTD(...))`:
merge them. A statement whose items are `stacked` shows several numbers, one per row of the
item column: it is a source of each of them, never one metric of its own.

Each proposal carries `bi_definitions`: where the BI tool defines the number and how
(`style`), with the model's own metadata (description, display folder, annotations,
perspectives, whether the model or data source is certified or promoted, a KPI's target and
status). Use them as hints for the name and the definition: prefer the endorsed model's name
and description, and a display folder as the business area. They never change which
statements are one metric, and never a filter. A measure chain (`derived_from`: margin from
sales and cost) is several metrics, related, not one.

## A generic expression is almost no evidence at all

`COUNT(*)` and `COUNT(DISTINCT id)` carry no meaning of their own. Half the estate spells its
metrics that way, and two proposals that share one have told you nothing except that both
count something.

**When the expression is generic, the population IS the metric.** Merge on the population and
the purpose, never on the spelling:

```
A: COUNT(*) over accounts WHERE type='Customer'     -> "how many customers"
B: COUNT(*) over accounts WHERE tags LIKE '%ai ready%'  -> "how big is the AI-Ready base"

WRONG   one "Account Count" with type and tag as optional knobs
        -> its default, with no knob set, is a raw table count: a number nobody manages,
           published as the headline value of the metric.
RIGHT   two metrics : "Customer Count" and "AI-Ready Account Count"
        -> each name says which population, and each default is a number somebody owns.
```

The test that catches this: **look at what the merged metric returns with every optional
filter dropped.** If that default is not itself a number the business manages, the filters you
demoted were not optional : they were the identity, and you have merged two metrics into one
that means nothing.

A specific expression is different. A ratio, a conditional sum, a formula with the business
rule written into it : two statements arriving at the same one is real evidence, because
nobody writes that by coincidence.

The same expression over the same entity is evidence, not proof. So is a different expression
over a different table: a staging table and the mart built on it are one thing, and the entity
groups in the bundle are a reading order rather than a boundary : merge across them where
that is what they are, and say so in `merge_reasoning`.

---

# Splitting the filters

This is the part that makes a merged metric usable, and it is the reason a merge is not a
union.

Take every restriction the sources carried, and put each in one of three places:

**Mandatory** : the metric is a *different metric* without it. It holds for every source, and
it is part of what the name means.

```
Sources: A filters type='Customer' and created >= '2026-01-01'
         B filters type='Customer' and segment='Enterprise'
         C filters type='Customer'

mandatory: type = 'Customer'        -- all three; the metric is about customers
optional:  created >= '2026-01-01'  -- from A
           segment = 'Enterprise'   -- from B
```

A filter that only *some* sources carry is almost never mandatory : if the metric were
meaningless without it, the source that omitted it would be computing something else, and
then these are two metrics rather than one.

**Optional** : a knob. Carry `from` so a reader can see which statement wanted it, and so
somebody re-running that statement's number knows what to set.

**Neither** : hygiene. Soft-delete guards, loader soft-delete flags (such as `_deleted`), and joins-as-filters that every
query in the estate carries. Leave them out entirely; they are noise in a knob list.

## When mandatory filters genuinely disagree

If source A is restricted to customers and source B is not, you have a decision, not a
merge conflict:

* if the metric is named for customers, **B is proposing a different metric** : split them;
* if B simply forgot and the estate always means customers here, make it mandatory and say so
  in `merge_reasoning`;
* if both are legitimate populations, that is two metrics with two names.

Never resolve it by demoting a mandatory filter to optional to make the merge fit. That
publishes a metric whose default is a number no statement computes.

---

# Dimensions

Union them, carrying `from`. A dimension one statement used is a dimension the metric has,
even if only that statement used it : that is the whole value of merging.

But only where the breakdown still computes the same measure. A dimension that changes the
grain of the number is not a dimension, it is a different metric.

---

# Traceability

`sources` is how a reader gets from a published card back to the statements behind it, so
`contributed` earns its place: say what this source adds that the others do not : *"the only
source grouped by region"*, *"the only one scoped to the quarter"*, *"the canonical
expression"*. For a metric with one source, *"sole statement"* is the honest answer.

`merge_reasoning` is the judgment in one or two sentences. For a merge, why these are one
thing. For a single source, why it does not belong with anything else : that sentence is
what somebody will read when they later think it duplicates something.

---

# Naming

A business name, title case, spoken English. The proposals arrive with names; pick the best
one or write a better one, but the merged name must cover **every** source. If no single name
honestly covers them, that is the signal they are not one metric.

Every merged name is unique across the layer. Two metrics that want the same name either are
one metric, or need names that say how they differ : usually their population or their scope.


## The name is the measure, never its cut

Two statements that compute the same measure cut two ways are ONE metric with a dimension :
not two metrics. `COUNT(*)` of open opportunities grouped by stage in one statement and by
owner in another is `Open Opportunities`, with stage and owner as dimensions.

So the merged name carries no `by X` or `per X`, and does not lead with a word that is really
a grouping: `CSM Renewal ARR` is `Renewal ARR`. If dropping the qualifier makes two proposals
collide, that is the signal they were always one metric.
