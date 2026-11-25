# Reading a statement that has no description yet

Open this when a statement's `question` and `description` are null. The prose is written later
in this run, so nobody has read the statement for you: you read it, one statement at a time,
with the same rules the describer follows. The SQL holds everything the description would have
said.

## Work out what it answers before judging it

Answer three questions from the OUTER select, using `facts`:

1. **What is one row?** `facts.outer_select.group_by` is the grain. Empty with
   `is_population_summary: true` means one row about the whole population.
2. **What does each output mean?** Read `facts.columns`: each output column with the
   expression that actually produces it, resolved through scalar subqueries, CTEs and derived
   tables (`computes`, `from`, `where`, and `inputs` for an expression over CTE columns). A
   column called `c5` or `value` is named by its expression, not by its name.
3. **What is it restricted to?** `facts.restrictions` lists every filter at every level. One
   inside a CTE narrows the answer exactly as hard as one in the outer WHERE.

Only then decide whether any output is a number the business manages.

## Patterns that are easy to misjudge without prose

**A row of headline figures.** One row, many columns, each column a scalar subquery
(`c5, c9, c10 …`), written to compare figures side by side. Read each column on its own `computes` and
`where`. A column that is the headline number is a candidate metric; do not dismiss the whole
statement as a check because of its shape. A column that recomputes a sibling another way
(another source column, another join, with deleted rows) is a check on that sibling, not a
metric. A narrower business population or the coverage of a business list is not a check: see
**A column that checks a sibling column is not a metric** in the propose skill.

**Always-empty twins.** A column with `always_empty: true` has a WHERE forced false (`1 = 0`):
a placeholder for a prior period or a guard. It is never a metric. Its non-empty sibling still
can be.

**A list with counts computed inside it.** The outer select lists entities (one row per
account) but some columns are aggregates computed in a CTE (`inputs` shows them). The list is
not a metric; the inner aggregates usually are, counted per entity. Judge them.

**A wide table read by its dimensions.** Columns that repeat a pattern
(`weekly_ / prior_week_ / four_week_avg_`, `fg_ / mg_`, `new_business_ / expansion_`) are a few
measures crossed with a few cuts. Propose the measures once and treat the cuts as dimensions.

**A test.** A statement that counts failures, returns rows violating a rule, or compares the
same number computed two ways (left versus inner join, with and without deleted rows) to see
whether they agree is a test or a reconciliation. It measures nothing. Say which rule it tests.

**A lookup.** A literal id, email or domain in the WHERE fixes the subject to one entity. A
value for one fixed entity is not a metric.

## Say only what you can see

`why_a_metric` and every word you write rest on the SQL and the dashboard in front of you. Do
not name a tile, report, team or decision that neither shows: the card author will repeat it.

## One reason per statement

A `why` says what THIS statement does, in terms of its own outputs and tables, for example
"compares researched-account counts with and without deleted Salesforce accounts". A sentence
that fits a whole family of statements means they were not read one by one, and the gate
refuses an answer that gives the same `why` to more than five statements.
