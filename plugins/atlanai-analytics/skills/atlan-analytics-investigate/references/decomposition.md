# Decompose a change

Open this when step 6 of the skill needs the arithmetic. Subscript 0 is the baseline and 1
is the window. Every input is a figure a query returned.

## A sum or a count

For segment `i` with values `v0` and `v1`:

```
contribution_i = v1_i - v0_i
total change   = SUM(contribution_i)
share_i        = contribution_i / total change
```

Shares can exceed 100% or be negative when segments move in opposite directions. Report
them as they are, and say which segments offset the change.

## A rate or an average

For segment `i`, `w` is its share of the denominator (weights add up to 1 in each window)
and `r` is its rate. `R0` is the overall baseline rate.

```
mix_i   = (w1_i - w0_i) * (r0_i - R0)
rate_i  = w1_i * (r1_i - r0_i)
R1 - R0 = SUM(mix_i) + SUM(rate_i)
```

The identity is exact, so a remainder beyond rounding means a segment is missing, a filter
differs between windows or the weights do not add up to 1. Find it before reporting.

Worked example, on-time rate by station:

| Station | Departures 0 | On time 0 | Departures 1 | On time 1 |
| --- | --- | --- | --- | --- |
| BLR | 700 | 630 | 500 | 455 |
| DEL | 300 | 180 | 500 | 305 |

`R0` is 81.0% and `R1` is 76.0%, a fall of 5.0 points. BLR's rate rose from 90.0% to 91.0%
and DEL's from 60.0% to 61.0%. Mix is `(-0.2 * 0.09) + (0.2 * -0.21)`, which is -6.0
points. Rate is `(0.5 * 0.01) + (0.5 * 0.01)`, which is +1.0 point. They add up to -5.0.
The rate fell although every station improved, because departures moved to the station with
the lower rate.

## One statement for both windows

Return numerator and denominator per segment and window, and compute rates and weights from
those. Never average a rate column.

```sql
SELECT segment,
       SUM(CASE WHEN period = 'baseline' THEN numerator   END) AS n0,
       SUM(CASE WHEN period = 'baseline' THEN denominator END) AS d0,
       SUM(CASE WHEN period = 'window'   THEN numerator   END) AS n1,
       SUM(CASE WHEN period = 'window'   THEN denominator END) AS d1
FROM (<the definition's rows, with its population and mandatory filters, tagged by period>) t
GROUP BY segment
ORDER BY ABS(COALESCE(d1, 0) - COALESCE(d0, 0)) DESC
LIMIT 50
```

A segment present in only one window has a NULL side: treat its missing weight as 0. For a
new segment, use `R0` as its baseline rate, so its effect is `w1 * (r1 - R0)`, and report it
as a new segment, not as mix or rate. A segment that disappeared has a mix effect only.

## Checks before reporting

- Weights add up to 1 in each window, and segment numerators and denominators add up to the
  totals from step 3.
- The `other` bucket is included in every sum.
- A second dimension is cut inside one segment only, and its parts add up to that segment's
  effect.
- A change smaller than the swings of the earlier periods has no decomposition to report.
