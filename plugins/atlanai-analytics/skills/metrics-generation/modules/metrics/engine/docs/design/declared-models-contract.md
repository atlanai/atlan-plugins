# Design: declared models in the shared contract

> **Status: implemented, pre-1.0.** What a BI model *declares* about each of its metrics travels in
> `bi_facts`, and the metric gates (X1 to X3) read it from there, whatever the model's vendor.
> A Snowflake semantic view was the first model that fills it and a Databricks metric view is
> the second; Power BI and Tableau leave it empty and are read the usual way.

**On this page:** [the problem](#the-problem) · [the contract](#the-contract) ·
[how a declaration travels](#how-a-declaration-travels) · [A3](#a3-places-a-sql-measure) ·
[X1 to X3](#x1-to-x3-read-the-declaration) · [the Snowflake plug](#the-snowflake-plug) ·
[the reader](#the-reader) · [Databricks metric views](#databricks-metric-views) ·
[invariants](#invariants-and-the-failures-they-prevent) · [open questions](#open-questions)

## The problem

Some BI models are governed models. A semantic view declares, for every metric, its formula,
the dimensions it can be cut by (each with its SQL, comment and synonyms), the relationships
that reach them, the key of every table, and example questions with the SQL that answers them.

Without a place for it in the shared contract, the BI lane would drop all of that. A metric
compiles to a one-number statement, passes through the translate and judge stages as a
statement, and reaches the card author with none of its dimensions, joins or comments. Putting
them back afterwards would need a vendor-specific step in X3 that re-reads the view, with its
own guards, and that round trip is where the failures listed under
[invariants](#invariants-and-the-failures-they-prevent) come from.

This design has no round trip. The declaration is part of the shared contract, and it
travels with the statement compiled from the metric, so nothing has to be put back.

## The contract

Every field below is **optional**. A reader that has no such facts omits them, and every stage
reads a model that omits them exactly as it does today.

| Where in `bi_facts` | Field | Holds |
|---|---|---|
| `tables[]` | `primary_key` | the key columns the model declares for the table |
| `tables[]` | `synonyms` | the model's other names for the table |
| `tables[].columns[]` | `synonyms` | the model's other names for the column |
| `tables[].columns[]` | `is_hidden` | the column is used inside formulas only and is never offered as a dimension |
| `measures[]` | `synonyms` | the model's other names for the metric |
| `measures[]` | `is_hidden` | the metric is used inside other metrics only: it gets no unit and is never a metric of its own |
| `measures[]` | `referable` | `false` when no formula names the metric as `table.name`: the model names a metric another way and its reader has already replaced each reference, so `table.<its name>` is a column. Omitted: a formula names it, and it is expanded there |
| `measures[]` | `declared` | the metric as the model declares it (below) |
| `relationships[]` | `name`, `from_role`, `to_role` | the relationship's name, and the model's own name for each end. A table used under two names keeps two roles |
| `relationships[]` | `columns` | every key column pair, `[{from_column, to_column}]`. `from_column` and `to_column` stay, as the first pair |
| `relationships[]` | `kind: inferred`, `range`, `asof`, and `condition` | a join derived by the reader rather than declared; a range or as-of relationship, with its condition verbatim |

`measures[].declared` is in the shape a metric card uses, so a consumer copies it rather than
interprets it:

| Field | Holds |
|---|---|
| `entity` | the physical table the metric is computed over |
| `formula` | the metric's formula with every fact and metric it names expanded, fully qualified |
| `grain` | the entity's declared key, fully qualified |
| `source` | the FROM clause: the entity, then a `LEFT JOIN` for every join below |
| `joins[]` | every join the formula and the dimensions need, each on all of its key columns, never one that fans out (many-to-one, or one-to-one) |
| `dimensions[]` | every dimension the model declares on a table the metric reaches: name, `kind`, `datatype`, `sql`, `required_join`, the model's comment as `description`, and `synonyms` |
| `examples[]` | the model's example questions that read the metric, each with its SQL over the base tables and the dimensions it groups by |
| `timeframe_column` | the date the metric's examples restrict or group by, when they name exactly one |

A table that plays two roles in one metric (a flight's departure and arrival airport) is
written as a derived table under its role name in `source`, and its role name qualifies every
reference to it. Every other reference is fully qualified, as the card contract requires.

How a reader computes `declared` from its own tables, relationships and keys is shared code
(`lib/declared.py`): any reader with declared relationships and keys can call it.

## How a declaration travels

```text
A1  reader: measures[].declared            (bi_facts)
A2  unit.declared = {model, metric, description, synonyms, ...measures[].declared}
A3  candidate.provenance.bi.declared        (copied; the compiled statement is unchanged)
C1..C5, M1  provenance.bi is carried, as it is for every BI statement
X1, X2, X3  read provenance.bi.declared off the statements a metric is built from
```

A declared metric is identified by `model::metric`, so two models that both declare `revenue`
are two metrics. C2 never folds a declared statement into another statement of the same shape:
it is that metric's only statement. Nothing after A1 asks which vendor a model came from.

## A3 places a SQL measure

A semantic view's metrics are already SQL. A3 places them; it never translates them.

- **Roles, not tables.** Each table qualifier in a formula is a role. The home role is the
  statement's `t`; every other role gets its own alias and its own join, even when two roles
  read the same physical table.
- **All key columns.** A join is written on every column pair of its relationship.
- **Expanded first.** A fact, dimension or metric named in a formula is replaced by what it
  stands for (`orders.fare` defined as `orders.FARE_USD`), recursively, before placement. A
  metric that names a metric of another table is refused by name: its aggregate belongs to that
  table's grain, not to one flat statement.
- **Paths that never fan out.** A role is reached along declared relationships only: many-to-one
  forwards, one-to-one either way, as `LEFT JOIN`s, any number of hops. A role no such path
  reaches is refused by name.
- **Range and as-of relationships are refused by name.** They are read as relationships, never
  as column names, and a formula or dimension that needs one is not placed.

The same functions write `declared.formula`, `declared.source` and `declared.joins` at A1, so
the statement A3 compiles and the card X3 accepts cannot disagree.

## X1 to X3 read the declaration

Every rule below is keyed on a statement carrying `provenance.bi.declared`, never on a vendor.

| Gate | Shows the author | Refuses |
|---|---|---|
| X1 | the declared metric's name, formula, comment and synonyms beside the statement | a declared statement answered `none`, or with other than one metric |
| X2 | the same, on each proposal's statement | one merged metric carrying two different declared metrics (the same declaration from two models, one formula over one table, is one metric) |
| X3 | the whole declaration | see below |

X3 holds a card built from a declared metric to its declaration, then completes it from the
declaration, before any other check runs on it:

- **Held to it.** The card's formula is the declared `formula` (layout and identifier case
  aside), and its entity is the declared `entity`.
- **Completed from it.** The declared joins, dimensions, examples, grain, synonyms and date
  column are added to what the author wrote; the FROM clause gains every declared join it does
  not already make; the comment becomes a gotcha. A declared dimension's SQL wins over the
  author's for the same column; its comment comes first in the description, then whatever the
  author adds, then its synonyms ("Also called: ...").
- **Named by it.** The card takes the declared metric's name in words, and the author's name
  becomes a synonym, unless another card already holds that name (compared case-insensitively,
  on `metric_ref`).
- **Coverage.** Every declared metric whose statement is in the verified layer reaches exactly
  one card, and no card carries two. The set is read from the layer, so it is the set the
  reader kept: a hidden metric has no statement, and a metric A3 or the witness refused is
  reported by name where it was refused. The gate can always be met by answering X1, X2 and X3.

Then every existing X3 check runs on the completed card, so the identity (`measure_id`) is
computed from the FROM clause the card actually publishes.

## The Snowflake plug

`snowflake-direct` gains `bi_definitions`: for each view named in
`settings.snowflake-direct.semantic_views`, one `SELECT GET_DDL('SEMANTIC_VIEW', '<view>')`
through its read-only runner, written to `<run>/inputs/bi/snowflake-direct/<view>.sql`. Each name
is checked as a plain `DB.SCHEMA.VIEW` identifier before it is put in the statement, and the
statement passes the read-only check like every other. `semantic_views` is a fetch setting, so
`metrics-agent run` fetches before S0 (`s_fetch_bi`) and S0 never reaches the network for BI. The
core imports no vendor driver.

A local file keeps working: `local-files` with `settings.local-files.bi` set to the file (or a
folder) and `bi_vendor: snowflake-semantic-view`. A folder of views is read as one model per
view: the reader's `models` hook names one path per view, and A1 reads each as its own model.

## Metric catalogue tables

Set `settings.snowflake-direct.metric_catalogue` to a fully qualified table and include
`snowflake-direct` in `inputs.bi`. Start with the catalogue when it already contains the
metric definitions you want. Query history can be added as a separate source.

Each row needs a metric name and one read-only SQL query that returns a single named value.
Use fully qualified table names and give expression outputs an alias. The plug recognizes
`metric_name` or `measure_name` and `sql`, `metric_sql`, `measure_sql` or `exact_sql`.
Optional columns include `mandatory_filters`, `expected_value` (also `verified_value`),
`expected_tolerance`, and `description`. Set `metric_catalogue_columns` to map these fields
to other column names. A required-filter column can hold a SQL predicate or a JSON array of
predicates. The saved query must apply those predicates in its WHERE clause.

The fetch produces a `.metric-catalogue.json` extract that can also be read from disk.
The reader also accepts `DIM_MEASURE.csv` or `*.metric-catalogue.csv` with the same column aliases.
The shared declaration carries `statement_sql`, `mandatory_filters`, and any expected
value and tolerance as internal evidence. The card reads the saved query as a derived table
using its existing `source.sql` field. The required predicates execute inside that query.
In execute witness mode, X3 checks the returned number against the declared value. The default
tolerance is zero.

## The reader

The reader ships with the Snowflake package (`metrics_agent_snowflake/semantic_view.py`),
registered under `metrics_agent.readers` as `snowflake-semantic-view`, like any reader: the
core names no vendor and knows no view syntax. It reads the three shapes it read before (native
YAML, a catalog reconstruction, and `CREATE SEMANTIC VIEW` DDL through `semantic_view_ddl.py`)
and emits the contract above: roles and all key columns on relationships, primary keys, synonyms,
comments, `PRIVATE` as `is_hidden`, and `declared` on every metric it can place. The view's
verified queries are compiled to base-table SQL by the reader (`semantic_view_queries.py`)
and become `declared.examples`. Its measures are SQL, the core's own language, so the reader
declares no other `languages`; they compile through the shared engine
(`plugs/readers/bicompile.py`), which places a SQL measure by role rather than translating it.
A1 hands it, like every reader, the run's `dialect` and `policy`. A metric the reader cannot place has no `declared` and a note
says why.

Joins are never inferred unless the policy asks (`semantic_view.infer_relationships_from_primary_keys`,
`false` by default: the Snowflake package's own policy default, overridden in the run's policy), and even then only from a key that identifies one table and is not the
joining table's own key.

## Databricks metric views

The reader ships with the Databricks package (`metrics_agent_databricks/metric_view.py`),
registered under `metrics_agent.readers` as `databricks-metric-view`. It reads a view's YAML
(specification 0.1 and 1.1), its `CREATE VIEW ... WITH METRICS` DDL, or the JSON that
`DESCRIBE TABLE EXTENDED <view> AS JSON` or Unity Catalog's table API returns, and emits the
contract above. Where a metric view differs from a semantic view, the reader settles it before
`lib/declared` sees the model:

- **One source, named `source`.** The view's table is the role `source`. Each join is a role
  under its own name, and a nested join under its path (`customer.nation` is
  `customer__nation`). A many-to-one join is one relationship on every key column pair. A
  one-to-many join is carried and never walked.
- **Names are checked.** A table is `catalog.schema.table`, each part a plain identifier, in
  backticks or not; a `using` column and a key column are one plain identifier. Nothing else
  is taken as a name, because a name is written into SQL as it stands. The definition cannot
  tell a table from another metric view, so a source that is a metric view is read as a table,
  and the run's live check is what catches it.
- **Columns are bound.** A metric view writes a source column bare (`SUM(o_totalprice)`). The
  reader parses each expression in the Databricks dialect and binds every column to its role.
  In a field or a measure, a bare name an earlier field defines is that field; any other is a
  column of the source. In the view's filter a bare name is always a column of the source,
  never a field. Both rules were observed on a Databricks workspace on 2026-11-26.
- **`MEASURE(x)` is replaced, and a measure is not a name.** A metric view names a measure only
  as `MEASURE(x)`, so the reader puts that measure's expression in its place and marks every
  measure `referable: false`. Without the mark, `revenue: SUM(revenue)` beside
  `avg_revenue: AVG(revenue)` compiled to `AVG(SUM(revenue))`.
- **The view's filter is part of every measure.** `declared` has no model-wide predicate, so the
  filter is written into each aggregate as `FILTER (WHERE ...)`, after `WITHIN GROUP (...)`
  where there is one. The number is the view's. A measure the filter cannot be shown to reach
  is refused: one that calls an unknown function, or reads a column outside every aggregate
  the reader recognises. A filter that cannot be read leaves no measure read.
- **A field that redefines a column the filter reads is not listed.** `lib/declared` reads
  `role.name` as the field wherever it appears, so one model cannot hold a column and a field
  of the same name. The filter keeps the column, and a measure that reads the field carries
  its expression. Listing such a field as a dimension needs a change to `lib/declared`.
- **String literals are plain.** `lib/declared` reads a formula as text and takes a literal to
  end at the next quote. Databricks writes a quote inside a literal with a backslash, so an
  expression whose literal holds a quote or a backslash is refused by name rather than
  misread.
- **A folder is its readable views.** `models` returns the files that read as a metric view
  with a measure, sorted. Any other file is left out and never stops the run; hidden folders
  and symbolic links are not read. A file is UTF-8 text of at most 1 MiB.
- **No key.** A metric view declares none. A description can carry its source table's key
  (`primary_keys`); otherwise the grain is the card author's.
- **A link back to the view.** The view is the model's one asset (`assets`, role
  `semantic_model`). An asset may carry `url`, its own page in the tool it lives in: https, no
  query string or fragment. The reader gives the view's Catalog Explorer page only when the
  saved description names its workspace (`workspace_host`) and the view has a plain three-part
  name. X3 copies the pages of a metric's statements' assets into the card's
  `provenance.observed_in`, which the Trident metric contract already has. Any reader may state
  `url`; none is ever built from a name alone.

Refused by name, in the reader's notes: a window measure and every measure built on one, an
expression that reads a `parameters` entry or holds a parameter marker (`:name`, `?`), a
wildcard field, a column that needs quoting, an expression that is not one expression or
holds a query or a lambda, an ordered aggregate the SQL writer would change (`PERCENTILE_CONT`
or `PERCENTILE_DISC` with `WITHIN GROUP`, `ARRAY_AGG(x ORDER BY y)`), a field or measure longer
than 20,000 characters once what it reads is written out, a join whose source is not a table
name or whose `using` is not plain column names, a join condition that is not equalities
between its two tables, and anything that reads a column a later field of the same name
redefines. A view whose own source is not a table name (a query, or a name that needs
quoting) is refused whole, and so is a view whose filter names a field that `source_columns`
shows is not a column of the source. Every refusal is a `ReaderRefused`: a malformed
definition names the file and the kind of error, never a traceback.

X3 copies an asset's `url` to a card only when it is `https` with no query string, fragment
or space, whatever a reader wrote.

`databricks-direct` declares the reader (`bi_vendor`) and reads the saved definition named by
`settings.databricks-direct.bi`. It fetches nothing: see the open question below.

## Invariants, and the failures they prevent

| # | Invariant | Failure it prevents |
|---|---|---|
| 1 | A table used under two role names is two aliases and two joins, in the compiled statement and on the card | a check between two roles comparing a column with itself, always false, and marked translated |
| 2 | A relationship joins on every key column pair | a composite key joined on its first column, fanning out and inflating a sum |
| 3 | A verified query naming a view-level metric (no table prefix) compiles or is reported not compiled with the reason | A1 crashing on the name |
| 4 | Facts and metrics named in a formula are expanded before compiling and before the formula guard compares | `SUM(orders.fare)` compiled against a column `fare` that does not exist, and the guard holding the card to the same mistake |
| 5 | Range and as-of relationships are recognised and refused by name | their condition read as column names |
| 6 | Joins are inferred only when the policy asks (off by default), never on a key that is the joining table's own key or the key of several tables | `ID = ID` joins between unrelated tables |
| 7 | The coverage guard checks the set of declared metrics present in the verified layer | a hidden metric the reader never emits holding the run blocked for ever |
| 8 | `metric_ref` collisions are case-insensitive; a declared metric is identified by `model::metric`; a column the model types as a number is never a date, and only the date argument of a date function marks a date | a rename colliding on `metric_ref`, two models' `revenue` colliding, an integer offset marked as a date |
| 9 | A metric no formula names (`referable: false`) is never an expansion target | a Databricks measure named after the column it sums compiling to an aggregate of an aggregate, in the statement and on the card |

Each has a test that failed before its fix, on synthetic views.

## Open questions

Each was settled the conservative way for now; reviewers may prefer another.

- **Agent instructions.** A view's agent instructions (`AI_SQL_GENERATION`,
  `AI_QUESTION_CATEGORIZATION`) are reported by A1 and not carried. The contract could carry
  them as model-level text and the glossary stage could read them generically; that is left
  for a follow-up.
- **Range and as-of joins** are refused rather than compiled. Compiling a range join is a
  `LEFT JOIN` on two inequalities, but the boundary semantics need a live check first.
- **Paths of several hops.** A SQL measure's roles, and a declared dimension's, are reached
  along any number of many-to-one or one-to-one hops (the earlier compiler took one). Such a
  path cannot fan out, so the number is unchanged; a path through a one-to-many step is refused.
- **Declared types.** A declared dimension's `datatype` is the type the model declares, mapped
  onto the card's vocabulary (`string`, `integer`, `decimal`, `boolean`, `date`, `timestamp`),
  `unknown` when it declares none: Atlan Trident does not require it, and it is never read from
  a warehouse.
- **Role names on the card.** A table that plays two roles is a derived table under each role
  name, and X3 accepts a name the card's own FROM clause gives a derived table as a qualifier.
  The alternative, fully qualified names, cannot tell the two roles apart.
- **Fetching a metric view.** Its definition is returned by `DESCRIBE TABLE EXTENDED` and by
  Unity Catalog's table API, and by no query (`information_schema.views` does not list one).
  The read-only gate admits queries alone, so `databricks-direct` reads a saved definition
  instead of fetching. A fetch needs one of two decisions: a closed `describe` operation on the
  read-only runner that builds its own statement from a checked name, or a table API read
  through the plug's sign-in. Either also brings the tables' columns and keys.
- **A metric view's filter** is written into each aggregate rather than carried as a required
  filter of the card. A grouped query then keeps a group the filter empties, with a null
  measure, where the view drops the group. A model-wide predicate in `declared`, compiled
  into the statement's `WHERE` and held on the card as a mandatory filter, would match the view
  exactly.
- **A folder of views** is one model per view, identified `<plug>/<view file>` the way several
  BI plugs are. One BI plug delivering several models is new; A2 onwards read them exactly as
  they read several plugs.
