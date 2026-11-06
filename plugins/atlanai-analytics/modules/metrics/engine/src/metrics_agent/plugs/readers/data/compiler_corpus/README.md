# The compiler's regression corpus

**This is the gate, not a test fixture.** It lives beside `bi_compiler_rules.yml` because it
versions with it: every rule the review loop learns is admitted only if the whole corpus still
compiles byte-identically.

The loop is autonomous : no person reviews a proposed row : so this directory is what stands
between a plausible-sounding rule and a statement that runs while answering a different
question. A row added for widget 41 that quietly rewrites twelve widgets which were already
right is rejected here, by the run, with nobody watching.

Every case is **synthetic**. A case derived from a customer workbook would carry that estate's
structure into this repo, which the repo does not hold; promoting one is a deliberate step that
synthesises it first.

Each case pins the OUTPUT, not an intention:

    {"name", "dialect", "unit", "columns", "expect_sql", "expect_coverage", "rules_used"}

and, for a case whose measures are `formula_language: sql`, `logical`: the model's roles
(logical table names) to the warehouse tables they read. A case whose measures read another
table's columns (a CALCULATE filter on a dimension, RELATED, time intelligence over a date
table) also names `table_of` (model table -> warehouse table) and, where text or dates matter,
`types` (`[table, column, type]` rows).

A change that alters `expect_sql` must either be wrong or explain itself.
