# Reading a query for a metric

The card asserts what a number is and how it is computed. Both claims are read off the SQL, and
both are easy to get wrong in ways that look right. This is what to look for.

---

## Find the answer, not the workings

A statement usually has layers: CTEs that prepare, a body that joins, an outer SELECT that
returns. **The metric lives in what is returned**, not in the deepest CTE.

Start at the outermost SELECT and work inwards only as far as you must:

- `SELECT * FROM (…)` returns whatever the inner query returned : descend.
- A derived table in `FROM (SELECT …) x` is not a CTE. It is often where the real aggregate
  lives, and it is easy to mistake for one.
- A CTE that nothing in the final SELECT references computes nothing. Ignore it.

**Trap:** taking the last aggregate the parser found. In a statement with six CTEs, the last
one is usually scaffolding, and the number the business reads is three layers above it.

---

## Trace a column back to its table

The card names tables; the query names aliases. Between them there may be several renames.

```
WITH base AS (SELECT o.id AS order_id FROM SALES.ORDERS.ORDER_HEADER o)
SELECT COUNT(DISTINCT order_id) FROM base
```

`order_id` is `SALES.ORDERS.ORDER_HEADER.ID`. Write that.

Follow every hop: outer alias → CTE column → CTE's own alias → real table. Where a column is
computed rather than selected, say what it is computed from.

**Trap:** two CTEs using the same alias letter for different tables. Check which one is in
scope at the line you are reading.

---

## Which joins change the answer

This decides `source.sql`, `joins`, and half of `filters`.

| shape | changes the number? |
|---|---|
| `INNER JOIN` | **Yes.** It drops rows with no match. Always keep it. |
| `LEFT JOIN`, nothing in WHERE about it | No. Decoration. Drop it from `source.sql`. |
| `LEFT JOIN` + a predicate on its table in `WHERE` | **Yes.** The predicate makes it an inner join. Keep both. |
| `LEFT JOIN` + a predicate in the `ON` clause | No. Filtering in `ON` keeps the outer rows. |
| `CROSS JOIN` to a one-row total | No rows dropped, but it carries a denominator : keep it if the metric is a share. |

**The test:** delete the join and ask whether the count changes. If it only removes a column
from the output, it was decoration.

That LEFT-JOIN-plus-WHERE case is the one most often got wrong, and it is invisible unless you
read the WHERE clause against the join list.

---

## Find the true population

`population` is what one row is, before the metric's filters narrow it.

- `COUNT(*)` over a joined result counts **join output rows**, not source rows. If a
  one-to-many join is in play, the number is inflated and the card must say so.
- `COUNT(DISTINCT x)` after a fan-out is usually the author correcting for exactly that.
- A `GROUP BY` in a CTE sets the grain everything above it inherits.
- `QUALIFY ROW_NUMBER() OVER (PARTITION BY k ORDER BY t DESC) = 1` reduces to one row per `k` :
  the population is *k*, not the underlying rows.

**Trap:** a statement whose CTE already grouped to one row per user, then counts `COUNT(*)`.
That is a count of users, not of the events underneath. The projection says `COUNT(*)` and
means something quite different.

---

## Window functions are not aggregates

`SUM(x) OVER ()` and `MAX(x) OVER (PARTITION BY …)` return a value per row, not per group. They
usually carry a total or a rank alongside the real metric.

If the metric *is* the windowed value, the aggregation is `custom` : the row carries a number
something else computed.

---

## Pivots that look like metrics

`MAX(CASE WHEN rn = 1 THEN some_column END)` is not a maximum. It is a pivot: it lifts one row's
value into the group. `MAX(CASE WHEN key = 'x' THEN value END)` is the same shape.

These are labels, not measures. Where such a value genuinely is the metric : a stored score,
a snapshot total : the aggregation is `custom`, and the card should say the value was computed
upstream.

---

## UNION changes what the metric is

A `UNION ALL` of two SELECTs with a literal label column is two populations stacked, and the
label is a dimension. The metric is computed over both branches; the card's population must
say so, and the label belongs in `dimensions`.

---

## Filters that hide in unusual places

Not every restriction is in a `WHERE`:

- `QUALIFY` : filters after windowing
- `HAVING` : filters after grouping, on the aggregate itself
- `ON` clauses on inner joins : filter exactly like a WHERE
- `IFF(...)` / `CASE WHEN ... END` inside the aggregate : filters *within* the measure, and
  usually the definitional one
- a `LIMIT` after `ORDER BY` : makes the metric a top-N, which is a different metric

The filter inside the aggregate is the one most often missed, and it is usually the mandatory
one. `COUNT(DISTINCT IFF(status = 'won', id, NULL))` is not a count of ids.

---

## Parameters and pinned literals

A statement written for one campaign, one quarter or one account carries that value as a
literal. The metric is not about that value.

- a pinned date, campaign name or account id → a `filter`, and often a parameter
- a value every source statement shares → likely `mandatory`
- a value only some sources carry → a `filter`, not mandatory

**Trap:** publishing a formula with one event's date inside it. The date belongs in `filters`,
not in the definition of the number.

---

## Reading the query's purpose

The last thing, and the one the SQL says least about.

- The `GROUP BY` says what it was sliced by. The `ORDER BY` often says what it was ranked on :
  that is usually the thing the reader cared about.
- Two numbers side by side are usually a comparison: a count and its denominator, this period
  and last, attended and no-show. The card should say which.
- A column named like a target, a tier or a status is what somebody acts on.
- The statement's own question and description were written by somebody who knew. Read them.

The purpose settles which columns are dimensions and which are decoration, and there is no way
to get it from the syntax alone.


When inlining a derived row column into a metric formula, resolve its defining projection in
the source subquery. Do not substitute the outer aggregate that reuses the same alias. The
replacement must preserve row grain. If it is itself aggregated, retain a separate derived
query scope instead of nesting it inside another regular aggregate. The card validator checks
this even in parse-only runs; a repair must pass the gate before publication.
