---
name: relate-metrics
description: Decide which metrics in a published layer are derived from which : and say what each part contributes, whether the populations nest, and what does not line up when they do not.
---

# Relate the metrics

A metric layer is not a list. Some of its numbers are built out of others, some are one
question asked at two grains, and some are a narrower reading of a neighbour. A consumer who
cannot see those edges will divide one metric by another that does not nest inside it, or
compare a count of companies to a count of people, and the layer gave them no way to know.

Your job is to look at each metric and its candidates and say, for each real relationship:
**what the target contributes**, **whether it is exact**, and **if not, what differs**.

You will say nothing for most metrics. That is the normal answer. A layer where everything
relates to everything is a layer where the word stopped meaning anything.

---

## The bundle

`x4_prompt.json` carries one task per metric:

- the metric itself : description, formula, population, grain, filters, and its runnable SQL
- `candidates` : a shortlist, each with `offered_because`
- `all_metrics` : every metric in the layer, compactly

**`candidates` is structural and nothing more.** It is built from shared query bodies, shared
expressions, shared source statements and matching aggregates. Structure is evidence. It is
never the answer, and two metrics can share all of it and be unrelated : `COUNT(*)` over one
table is a different question in a sign-off report and in a per-rep breakdown.

**The shortlist does not bound your answer.** A derivation that crosses two tables has no
structure to find it by, so nothing will propose it. If you can see one, name it : the answer
is checked against `all_metrics`, not against the shortlist.

---

## The four relationships

You say what the relationship **is**. The stage maps it to the registry's own edge vocabulary.

| `relation` | means | `role` | published as an edge | on the card as |
|---|---|---|---|---|
| `derivation` | this metric is **computed from** the target | `numerator`, `denominator`, `component` | `depends_on` | `depends_on` |
| `restriction` | this metric is the target **with a further restriction** : every row it counts is a row the target counts | `base` | `depends_on` | `depends_on` |
| `grain_variant` | the same question, **counted at a different grain**. Not interchangeable: swapping them changes the number | *(none)* | *(no edge)* | `related` |
| `alternative` | two spellings of one measurement a reader may pick between | *(none)* | *(no edge)* | `related` |

**The `metric` kind declares exactly two edge types, and the gateway enforces them.**
`GET /registry/v1/kinds/metric` advertises `x-gateway-edges` = `depends_on` and `replaced_by`.
The relationships route checks `rel_type` against that list before it even looks up the target,
and anything else is a bare 422 naming no field.

If a workspace holds older edges of another type (such as `alternative`), they predate this
check: do not copy them.

**So a grain variant has no edge to be : and it is not dropped.** It goes on the metric's own
payload, in `definition.extensions.related`, an open object the manifest describes as
"experimental data preserved without assigning execution semantics". That is exactly its
standing: a true relation the vocabulary cannot type yet. It needs no change to the gateway,
and a reader that does not know the key ignores it rather than resolving a dangling neighbour.
Assert it **once**; the gate writes it onto both metrics, because a consumer holding one card
must see the sibling without already knowing to look at the other.

`depends_on` is still the same word for a ratio's numerator and for a narrowed population, so
your finer word travels with the edge in `attributes.relation` : and with the extension, in its
`relation` field.

`derivation` and `restriction` also reach the metric's own payload as `definition.depends_on`.
The other two do not: a consumer reading `depends_on` is asking what this number is built out
of, and a sibling at another grain is not an answer to that.

---

## Method

**1. Read the two SQL bodies before you read anything else.** The strongest evidence in the
bundle is two cards whose text from `FROM` onwards is identical and whose `SELECT` differs.
That is one query the estate asked two ways, and it is what a ratio and its halves look like.

**2. Say what the target contributes.** Not that it is related : what it *does*. "The ratio's
numerator is this metric's expression verbatim" is checkable. "These are similar" is not. If
you cannot write the checkable sentence, there is no edge.

**3. Then ask whether the populations nest.** This is the step that is skipped and the one that
costs. Two metrics can compose arithmetically and still not nest:

> `CSM AI-Signal ARR` describes itself as *"a subset of the CSM's total book"*. It is not one.
> `CSM Total ARR` applies `CUSTOMER_STATUS_C = 'Active'` and the AI-Signal variant does not, so
> an inactive customer with a qualifying call is in the numerator and not the denominator, and
> the share can exceed 100%.

When they nest, `exact: true`. When they do not, the edge still holds : say `exact: false` and
put the difference in `differs`. That sentence is the whole reason to record the edge, because
it is what a consumer needs before dividing one by the other.

**4. `label` is optional and worth writing when you can better it.** Every edge gets one
either way : composed from the relation and the role, with a warning appended when `exact` is
false : because the registry renders it per edge and a bare line between two names tells a
reader nothing. Write your own when you can say something the shape cannot: *"the replies,
before they are divided by the reached"* beats *"numerator of this ratio"*.

**5. Set `confidence` to what you actually believe.** An identical query body and a verbatim
expression is near certainty. A plausible reading of two different queries is not. Omit it
rather than writing a number you do not mean.

---

## The answer

```json
{"relationships": [
  {"metric": "Account Credit Utilisation",
   "depends_on": [
     {"metric": "Account Credits Burned",
      "relation": "derivation", "role": "numerator",
      "why": "the ratio's numerator is this metric's expression verbatim; the two share an identical query body",
      "exact": false,
      "differs": "Credits Burned applies TOTAL_CREDITS_BURNED > 0 and the ratio does not, so an account at zero burn is absent from it and sits at 0% in the ratio",
      "confidence": 0.95},
     {"metric": "Account Credits Allocated",
      "relation": "derivation", "role": "denominator",
      "why": "the ratio's denominator is this metric's expression verbatim, over the same query body",
      "exact": true, "confidence": 0.95}]}
]}
```

A metric with no relationships is **omitted**, not listed with an empty array.

---

## What gets refused

The gate refuses the whole file, names the entry and the rule, and writes nothing:

- a metric or target this layer does not hold, or a metric related to itself
- a `relation` outside the four, a `derivation` with no role, a `grain_variant` with one
- the same relation asserted twice, or a symmetric relation asserted in both directions
- **a `why` that is empty** : an edge nobody can check is worse than no edge
- **`exact: false` with no `differs`** : the difference is the reason the edge is worth recording
- the same `(metric, relation, target)` twice: the edge is its own key
- `confidence` outside 0..1, or any key the schema does not declare
- **a cycle**: a metric cannot be computed from itself, however many hops away

---

## Before you hand it over

- [ ] Every `why` names what the target **contributes**, in words checkable against the two SQL bodies.
- [ ] Every `exact: false` has a `differs` that says what does not line up.
- [ ] Nothing is related on a shared table alone. A shared table is where metrics live, not a derivation.
- [ ] Ratios: did you look for **both** halves? They are usually both published.
- [ ] Grain twins: same table, same aggregate, different counted column : `grain_variant`, not `derivation`.
- [ ] You said nothing for the metrics that derive from nothing.
