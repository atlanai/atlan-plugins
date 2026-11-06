# Getting started: running the agent on your own data

The [README](../README.md) gets you through a five-minute demo on synthetic data. This guide
takes you from there to your own estate: install, describe the run once in `run.yml`, test the
connections, run it, answer the authored gates with an AI assistant, and choose where the
results go.

You need Python 3.11 to 3.12, a clone of this repository, and (for the gates) an AI coding
assistant that can use the skills in `skills/`, for example Claude Code.

Running it on a customer's estate? Ask for the access first: [What access to ask the customer
for](guides/customer-access.md) lists it per source (Snowflake, Power BI, Tableau).

The steps, in order:

1. [Install](#1-install) the core and the plug packages for your systems.
2. [Describe the run](#3-describe-the-run-runyml) in `run.yml` (or [let your assistant do
   it](#2-the-fastest-path-let-your-assistant-drive)).
3. [Test the connections](#4-test-the-connections).
4. [Run](#5-run) and [answer the gates](#6-answer-the-gates).
5. [Read the results](#7-what-you-get) and [publish](#8-publish) them.

---

## 1. Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                                 # the core: local files, DuckDB, publish to files
pip install -e packages/metrics-agent-powerbi    # Power BI straight from Microsoft Fabric
pip install -e packages/metrics-agent-tableau    # Tableau straight from Tableau Cloud or Server
pip install -e packages/metrics-agent-snowflake  # Snowflake: SQL checks, schema, history
pip install -e packages/metrics-agent-atlan      # Atlan: catalogue metadata, Registry, glossary
metrics-agent plugs                              # what is installed, and the variables each reads
```

Install the core first, then only the packages for the systems you use, or everything at once
with `pip install -c constraints.txt -r requirements-all.txt` (what CI and contributors use).
Every package installs from your clone by path: none is published to a package index, so
`pip install <name>` without a path would not install this project. `metrics-agent plugs` lists every
installed plug, its capabilities and the environment variables it reads.

## 2. The fastest path: let your assistant drive

Open the repository in Claude Code and ask:

> Set up and run the metrics agent on my data.

The `setup-run` skill asks the set-up questions (where your query history and BI definitions
are, which system checks the SQL, where results go), writes `run.yml` and tests every
connection. The `run-pipeline` skill then runs the agent and answers each authored gate with
the matching skill, stopping where it needs you. The rest of this guide is what those skills
do, step by step, if you prefer to run it yourself.

No API credential for the BI tool? The `fetch-bi-in-browser` skill captures the definitions
from your own signed-in browser (the tool's own downloads), with the values each widget shows,
into one folder the run reads with `local-files`; see
[What access to ask the customer for](guides/customer-access.md#no-api-credential-the-browser-route).

Your assistant has the BI vendor's own MCP server (Tableau's `tableau-mcp`, Microsoft's Power
BI MCP servers) but the run has no plug credential? The `fetch-bi-over-mcp` skill calls its
read-only tools, saves the answers, and converts them into the same kind of capture folder; see
[the MCP route](guides/customer-access.md#no-plug-credential-but-an-mcp-server-the-mcp-route).

Every route, and what each needs and can verify:

| Route | Needs | Can verify |
|---|---|---|
| a plug (B: Power BI, E: Tableau) | a service principal, a token, a PAT, a connected app, or your own sign-in (device code, or in your browser) | the whole report or workbook and its model or data sources; values by the tool's API |
| browser capture (`fetch-bi-in-browser`) | your signed-in browser and a role that downloads | the tool's own downloads; values as displayed |
| MCP capture (`fetch-bi-over-mcp`) | the vendor's MCP server in your assistant | Tableau: the workbook (a published data source from another route) and values as displayed; Power BI: the model only, raw DAX values |
| files on disk (A) | the files | what the files hold |

A capture that lacks something (a report layout, a published data source, the displayed
values) says so: S0 prints each gap and `run_inputs.json` records it, with what the run cannot
verify because of it.

## 3. Describe the run: `run.yml`

`metrics-agent setup` asks the questions at a terminal and writes `run.yml` for you. You can also
write it by hand. Pick the starting point closest to yours.

**Secrets never go in `run.yml`.** Each plug reads its credentials from environment variables,
listed with each example. Export them in your shell, or keep them in a `.env` file that git
ignores and load it before a run (`set -a; . ./.env; set +a`).

Every `run.yml` has the same parts:

| Key | Meaning |
|---|---|
| `version` | always `1` |
| `scope` | a short name for this estate; every published id starts with it |
| `dialect` | the SQL dialect of your warehouse; never guessed |
| `mode` | `standalone` (nothing is read through a third party's systems) or `connected` (at least one input plug has custody `third-party`, such as the Atlan lakehouse) |
| `inputs` | which plug supplies each capability: `history`, `bi`, `schema`, `scope` (lists) and `sql_check` (one plug, a list, or none) |
| `publish` | where results go; never defaulted (`[files]` means nowhere) |
| `observers` | optional: tracing plugs, such as `atlan-trace` |
| `witness_mode` | only for a run with no `sql_check`: `parse` or `none`. `metrics-agent setup` writes `parse` when you decline the SQL check, so the choice is on record |
| `settings` | each plug's own settings, under its name; never a credential |
| `window` | optional: `start` and `end` dates (`YYYY-MM-DD`) bounding the query history |
| `tables` | optional: the tables in scope, `DB.SCHEMA.TABLE` |

A run that reads query history needs a table bound: `tables`, an asset scope, or BI definitions
whose lineage names tables. A statement is kept only when it reads a bounded table. A
capability may list several plugs: two history sources are read as one corpus, a Tableau
workbook and a Power BI model can be read in one run (see
[Several plugs per capability](design/plugs.md#several-plugs-per-capability)).

### A. Files on your machine

A query-log export and BI files, with SQL checked against a DuckDB copy of the tables:

```yaml
version: 1
scope: acme-sales            # a short name; every published id starts with it
dialect: duckdb              # the SQL dialect of your warehouse
mode: standalone
inputs:
  history: [local-files]
  bi: [local-files]
  schema: []
  sql_check: local-files
publish: [files]             # results stay in the run directory
settings:
  local-files:
    history: exports/querylog.jsonl.gz   # .jsonl, .jsonl.gz, or a .json list of statements
    bi: exports/powerbi_model            # a Power BI model folder, .pbix, .bim, or Tableau files
    bi_vendor: powerbi                   # powerbi | tableau | snowflake-semantic-view (Snowflake package)
                                         # also powerbi-scan | powerbi-paginated | powerbi-goals
    database: exports/warehouse.duckdb   # optional: the DuckDB file SQL is checked against
    witness_mode: explain                # parse | explain | execute
tables:                                  # the tables in scope (or let the BI lineage name them)
  - analytics.sales.fct_orders
```

No environment variables. Leave out `history` or `bi` if you have only one of them.

A Power BI scan result, paginated report (`.rdl`) or scorecard is read the same way, with its
own `bi_vendor`; a file holding several models takes `bi_model` to name the one to read. See
[Power BI service inputs](guides/power-bi-service-inputs.md).

#### Tableau files to supply

Set `bi_vendor: tableau` and point `bi` at one workbook file, or at a folder holding one
workbook and the files below. A run reads one workbook from disk; the `tableau-cloud` plug
reads every workbook its `workbooks` setting fetches, one model each (`model` narrows it to
one). What else to put beside a workbook depends on how its data sources connect:

| The data source | Put beside the workbook |
|---|---|
| is embedded in the workbook | nothing: the `.twb`/`.twbx` holds its connection |
| is published (its connection in the workbook is a `sqlproxy`) | the published data source's `.tds`/`.tdsx`, downloaded from Tableau. It is matched by name. Without it the data source is reported as an acquisition gap: it reads no warehouse table |
| is published and built on another published data source | every `.tds`/`.tdsx` along the chain, down to the one that holds the warehouse connection. A missing link, or a chain that loops back on itself, stops the run and names the chain |
| connects through a virtual connection | the virtual connection's `.tvcx` (see below) |
| uses custom SQL | nothing: the statement is in the file, and every table it reads (its joins, subqueries and set operations; not its CTE names) is recorded in the lineage, read in the run's `dialect` |
| connects to an Analysis Services cube, SAP BW, Essbase, Salesforce Data Cloud or Tableau Semantics | nothing will work: the model lives outside Tableau, and the run stops saying where it lives and what to supply instead (for example the query history and tables of the warehouse underneath) |

A table a data source names as `schema.table` or `table` is completed with the database (and
schema) its connection names, so the SQL reads `database.schema.table`; nothing is added when
the connection names none.

A data source on Tableau's relationships model (logical tables related in its data model)
is read as one table per logical table, each with its own columns, related by the fields its
relationships name, so every field is read from the table it lives on.

**How a join is written.** A column on another table is reached along the model's own
relationships, in as many hops as it takes (sales, then product, then category), and only along
an active one. A row whose key the other table lacks is kept under a NULL member, as Power BI
shows it under (Blank) and Tableau under Null; a one-to-one relationship keeps such rows from
either end. A table whose multiplicity the model does not state (a Tableau relationship) is read
as its distinct rows, so it never repeats the measure's rows. A filter on a dimension's column
joins that dimension. A join that only a measure reads through (its column on another table) is
always a `LEFT JOIN`, whatever the relationship's join type: a measure never changes which rows
the statement has. The rest follows what Power BI does, checked against it:

- **Many-to-many and both-ways relationships.** A path through an end that is not one row (a
  `*:*` relationship, or a bridge table behind a relationship that filters both ways) is read as
  one distinct set of (member, the columns needed beyond it), filtered inside, and joined on the
  member, NULL matching NULL. A fact row counts once per member and once under a filter on
  several members; a bridge key the dimension lacks is that dimension's blank member.
- **A table that does not filter the measure.** A column on a table the relationships reach only
  against their filter direction, or only along an inactive relationship, filters nothing: the
  statement lists every member of that column (its blank member included) beside the measure's
  value. A filter on such a table narrows only its members.
- **Measures that join differently.** A measure with `USERELATIONSHIP` or `CROSSFILTER` (Both,
  None, OneWay), or a measure of another table, beside measures that do not, is a statement of its
  own; the statements are joined on the visual's rows, keeping a row any of them has.
- **Role-playing tables.** Two model tables over one warehouse table (an order date and a ship
  date) are two tables: the second is read as a statement of its own under its own name.
- **Storage mode.** "Assume referential integrity" drops unmatched rows only where both tables
  are DirectQuery; an Import model ignores it. Text in an Import model is grouped and compared
  without case and trailing spaces (one row for 'Retail', 'retail' and 'Retail ', shown as the
  least spelling); a DirectQuery table's text is grouped by the source, case kept, and shown in
  one spelling per case-insensitive value.

A **data blend** (a worksheet on two data sources) is not a join. The secondary data source is
aggregated over the links the view holds (the fields both data sources hold under one name,
when the primary's is on the sheet), then joined to the primary's rows after aggregation, NULL
matching NULL: a member only the secondary has is not shown, and a filter on the secondary
also leaves out the primary's rows it leaves no secondary row for. A dimension of the
secondary shows its one value per row, or `*`; with no link in the view, every row shows the
secondary's total. A custom link (Edit Blend Relationships) pairs two fields of different
names; Tableau applies it only as its editor saves it (the two data sources' bare names, each
field qualified by its data source), and a secondary field renamed to the primary field's name
links too. A calc of the secondary is aggregated over the secondary's rows.

What has no faithful single statement is refused by name: two equally short paths to one
table, and a Top N beside a table that does not filter the measure.

**What a Power BI visual shows.** A column dragged to a visual's values with an aggregation
(Sum, Average, Count, Count (Distinct), Min, Max, Median, Standard deviation, Variance) is
that aggregate, named as the report labels it (`Sum of AMOUNT`); it is never a grouping
column. Count and Count (Distinct) skip blanks, and Standard deviation and Variance are the
population forms. A DAX calculated column used as a grouping column, as a filter or inside a
measure is compiled from its formula, with DAX's rules for a blank (`BLANK - 5` is -5,
`BLANK < 100` is true) and its case-insensitive text comparison; a calculated column of a
joined table is blank where the join finds no row. A level of a date column's auto date/time
hierarchy (Year, Quarter, Month, Day) groups by that part of the date, with Power BI's labels
(`Qtr 1`, `January`; English month names). A level of a hierarchy the model declares (a
level renamed too) is the column the model names for it. A drill saved at one level groups by
that level only. A calculated table reads no warehouse table: the reader writes the statement
of its rows in the run's dialect (`CALENDAR` and `GENERATESERIES` as a row series, `DATATABLE`
rows, `ADDCOLUMNS` columns, `DISTINCT` / `VALUES` / `SUMMARIZE` of one table's columns,
`UNION`, `FILTER` and `SELECTCOLUMNS` of one of them or of a table, a table itself), and a
visual grouped or filtered by its columns joins that statement. A calendar whose bounds the
warehouse holds (`CALENDAR(MIN(Sales[Date]), MAX(Sales[Date]))`, `CALENDAR` of `DATE` parts of
them, `CALENDARAUTO([fiscal year end month])`: the fiscal years holding the earliest and latest
date of every date column of the model's warehouse tables, never a calculated table's) is the
days between bounds the statement reads when it runs. A
`GENERATESERIES` with a decimal step has as many rows as DAX gives it: DAX adds the step in
binary floating point, so `GENERATESERIES(0, 1, 0.1)` has 11 rows but `(0, 0.3, 0.1)` has 3
(seen live). Another
table expression (`CROSSJOIN`, `GENERATE`) is refused by name on the table. A calculated column that reads another table (`RELATED`) is
evaluated on each row and reads the related row through the relationship. A report saved in the legacy format (a PBIP `report.json`, or a `.pbix` whose
report is `Report/Layout`) is read the same way as one saved in PBIR.

**Totals, "show value as" and tables of columns only.** A table's grand total and a
matrix's row and column subtotals (each row level but the innermost, the grand totals, every
row level crossed with every column level) are each their own statement, in the candidate's
`totals`: the measures over the total's own context, so a distinct count, an average or a
ratio total is what Power BI shows, never a sum of the rows. A table with totals off, a
matrix with its row or column subtotals off, or a level's subtotal off, has only the totals it
shows. The leaf statement keeps its plain shape; every later stage reads that one. "Show value
as" a percent of the grand total, the column total, the row total or the parent row total is
the value divided by itself with the visual's other grouping columns removed (`ALLSELECTED`),
named as the report labels it (`%GT Sum of AMOUNT`). A table with no measure lists the
combinations of its columns that exist: over one table its distinct rows (with the blank row
when a related table holds a key it does not have), over several the combinations in the
table they are all reached from. The blank row of one table is listed when any relationship
into it, active or not, has a key it lacks, or when the table on its many side has a blank row
of its own (a category lists a blank when a sale's product is unknown).

**Which rows a Power BI visual reads.** Every filter the report sets on the visual, its page
and the report applies, and so does a slicer's saved selection on the same page (unless the
visual's interaction with that slicer is set to none); a slicer itself is no question. A
basic filter that selects or excludes (Blank) keeps or drops the rows with no value. A
comparison compares an empty cell the way DAX does, as 0, the empty text or the zero date, so
"is less than 50" and "is on or before" keep the rows with no value, and "is greater than 500"
does not. "Contains" and "starts with" ignore case, and their negations keep the empty cells;
"is blank" is a null test; an `Or` of two tests is both. A filter on a measure (or on a column
aggregated there, "Sum of QTY is greater than 80") keeps the visual's rows where it holds, as a
`HAVING`; it is a filter on the visual's grouping columns, so `ALLSELECTED` beside it reads only
the rows it keeps and `ALL` of a grouped column reads the ones it leaves out too. A Top N keeps the members of its field ranked by its measure within the visual's other
filters, every member tied with the Nth included and a member whose measure is blank never
ranked, so a legend beside it shows each kept member's rows. A relative date filter counts
from the current date in UTC, as the service does ("in the last 7 months", "in the last 6
calendar months", "this year"); a week starts on Sunday, as Power BI's relative week options
do, and a relative time filter ("in the last 5 hours", "in the last 90 minutes") counts from
the current UTC time. A hierarchy slicer's selection (a filter over several columns at once)
keeps exactly the selected combinations, (2024, January) and (2025, March), never one
column's members alone. A slicer with no selection of its own that is synced to others (a
synced copy, often hidden) takes its sync group's selection, from whichever page holds it.
In a legacy report an edited interaction of "none" takes the slicer's selection off the
visual. A visual hides a row whose every measure is blank, and so does its statement (a
`HAVING` over its values that later stages read as hiding empty rows, not as a filter); a
visual that shows items with no data lists every member of its grouping columns (the
combinations one table holds, crossed between tables, the blank member included) with its
values where it has rows. Refused by name: a field of no known kind.

**Visuals that switch, drill and summarise.** A field parameter shows the field its page's
slicer chose (the first by order when nothing is chosen); each other choice is a unit of its
own, marked as a variant of the visual (`variant_of`). A drillthrough page shows its whole
population when opened directly; its field is a parameter of the unit, and a variant unit
answers it for every member at once, grouped by that field. A KPI's number is the value at
the latest point of its trend axis (a variant unit); its trend is the plain unit. Every other
role (a gauge's target, a scatter's X, Y and size, a combo's line values, a waterfall's
breakdown, a treemap's details, small multiples) is a measure or a grouping column by the
field it holds. The variants of one visual are capped by policy (`bi.max_variants_per_widget`);
one over the cap is listed in `dropped` with its reason.

**What a table's Power Query does.** A Power BI table is what its Power Query (M) leaves of the
warehouse table, so each step that changes rows or values becomes part of the table's own SQL,
and the agent reads the table as that SQL wherever it reads it (joins included). Checked
against Power BI's own answer on a real model, with the steps folded to Snowflake:

- `Table.SelectRows` keeps the rows M keeps: `[Region] <> "West"` keeps a null region (M's `=`
  and `<>` are never null), `[Amount] > 0` drops a null amount, text is compared with case.
  `List.Contains`, `and`, `or`, `not`, null tests, the `Text.Contains` / `StartsWith` /
  `EndsWith` tests and parameters are read.
- `Table.AddColumn` columns (arithmetic, `if … then … else`, where a null condition takes the
  `else` branch, `&`, `??`, `Text.*`, `Date.Year` / `Month` / `Day`, `Number.Round`) can be
  grouped by, filtered by and aggregated. `Table.TransformColumnTypes` to a whole number rounds
  half away from zero, and to a date drops the time. A rename is read back to the warehouse
  column, in every container and in a measure.
- `Table.Group`, a full-row `Table.Distinct`, a merge (`Table.NestedJoin` then
  `Table.ExpandTableColumn`, keys equal or both null) and an append (`Table.Combine`, columns
  matched by name) are written as SQL. `Table.Group` with `GroupKind.Local` is one group per
  run of adjacent rows with one key along the `Table.Sort` before it (without a sort the rows
  have no order, and it is refused by name).
- A table read through another query (`Source = #"Staging"`, any number of hops) reads what
  that query reads. Parameters naming the server, warehouse, database, schema or table are
  their values. A native query's SQL built from parameters is written with their values, and
  its two-part names are qualified from the database it is navigated to.
- Columns renamed by a function (`Table.TransformColumnNames`) are read from the warehouse
  column the function maps to each model column. A custom M function (let-bound or a shared
  query) is applied to a table or inlined per row. `try ... otherwise` takes the fallback on
  the rows where the body raises an M error, and `Table.ReplaceErrorValues` /
  `Table.RemoveRowsWithErrors` replace or drop them. Inside a `try`, Power Query evaluates the
  body itself, so an `if` over null raises there.
- Comparers: `Comparer.Ordinal` / `OrdinalIgnoreCase` (a null against text raises),
  `Comparer.Equals`, `List.Contains` with a comparer, and `Table.Distinct` / `Table.Group` /
  a merge with one. Distinct and Group keep the first row in the sort before them.
- `Table.Unpivot` and `Table.UnpivotOtherColumns` drop null values. Where the M does not list
  the columns, the warehouse's own unpivot is used. `Table.Pivot` makes one row per combination
  of the other columns. `Table.ReplaceValue` handles a whole value or text (case-sensitive), and
  old and new may be functions of the row. `Table.FillDown` / `FillUp` and
  `Table.AddIndexColumn` follow the sort before them.
- `Date.IsInPreviousNDays` and the other `Date.IsIn*` tests, `Date.Add*`, and
  `DateTime.LocalNow()` read the current UTC date. The model's rows are as of its last
  refresh, so the two agree while both fall on the same UTC day. A week starts on Sunday.
- A division by zero is infinity, minus infinity or NaN, as M divides
  (`Number.IntegerDivide` by zero is NaN). Folded to Snowflake, such a division makes the
  refresh fail, so a refreshed model has no such row. `Number.Round` takes each
  `RoundingMode`, and `Number.RoundUp`, `RoundDown`, `RoundTowardZero` and
  `RoundAwayFromZero` are read. Value.NativeQuery's parameters (a record for `@name`, a list
  for `?`) are written into its SQL as literals.
- `Table.Skip` of a folded sort makes a Snowflake refresh fail. After `Table.StopFolding` or
  `Table.Buffer`, Power Query skips the rows itself.
- Rows written in the M itself (`#table`, also typed by a table type, `Table.FromList`,
  `Table.FromRecords`, `Table.FromRows`, `Table.FromColumns`), including a list
  `List.Generate` makes while the model is read and the rows Desktop's Enter data saves
  compressed (`Json.Document(Binary.Decompress(Binary.FromText(...)))`), are the statement of
  those literal rows; a type conversion after them is made while the model is read (a whole
  number rounds half to even, seen live; text that is not a plain number is refused by name).
  An M error a column holds is carried through a `Table.Group` (an aggregate over it is an
  error), a `Table.Combine`, a merge, a `Table.FillDown` (the nulls below an error take it), a
  `Table.Unpivot` and a `Table.Pivot`, so a `Table.ReplaceErrorValues` or
  `Table.RemoveRowsWithErrors` after them reads it. A `Table.Distinct` comparing a column that
  holds one is refused by name: comparing it raises the error, and the refresh fails (seen
  live).
- Refused by name: a source that is no warehouse (an Excel workbook, a CSV or other file,
  SharePoint or OneDrive, a web address, an OData feed, a dataflow, a script, a
  `Table.FromValue`), the steps that keep rows by position with no sort before them (`Table.FirstN`
  or `Table.LastN` of a `Table.Sort` is `ORDER BY ... LIMIT n`, as the connector folds it, the
  warehouse's own NULL order kept; `Table.Skip`, `Table.RemoveFirstN`, `Table.RemoveLastN` and
  `Table.Range` of one keep the rows by their row number in that order; `Table.MaxN` and
  `Table.MinN` by a column need no sort), a position kept by a condition, a `Table.Distinct`
  on some columns with no sort, a pivot over values from the data when the other columns are
  not listed, conversions by culture, and native SQL built from an expression the reader
  cannot evaluate.

**What a DAX measure compiles to.** Each rule below was checked against Power BI's own answer
on a real model:

- Counts are BLANK, not 0, where nothing is counted (`COUNTROWS`, `COUNT`, `COUNTBLANK`).
  `DISTINCTCOUNT` counts a blank as one value and `DISTINCTCOUNTNOBLANK` does not. Text is
  compared and counted as the model holds it: without case, and a column's trailing spaces
  dropped (`'Retail'`, `'retail'` and `'Retail '` are one value; a literal keeps its spaces).
- `CALCULATE` filters may name a dimension's column: the dimension is joined to the fact along
  the active relationship (a LEFT JOIN, so a fact row with no match is the blank member).
  `&&`, `||`, `NOT`, `IN`, `ISBLANK` and `KEEPFILTERS` are read. A filter on a column the
  visual groups by replaces each row's own value of it, as DAX does; one on a column the
  visual filters replaces the visual's filter, so the aggregate reads the rows without it in
  a statement of its own, joined back.
- `ALL`, `REMOVEFILTERS`, `ALLEXCEPT` and `ALLSELECTED` (a percent of total) sum each aggregate
  across the visual's rows that differ only in the removed columns. Removing a table removes
  the tables its relationships reach too. An aggregate that is not additive (a distinct count,
  a median), and any aggregate whose removed column the visual filters, is computed again in a
  statement of its own over the rows that context holds, grouped by the columns it keeps and
  joined back to each row on them (NULL matching NULL). `ALLSELECTED` beside `ALL` in one
  `CALCULATE` keeps its own columns' filters and drops the others'; one `CALCULATE` that
  removes grouping inside another removes both. Under a Top N filter `ALLSELECTED` is the
  total of the members shown, `ALL` of another filtered column reads the same members without
  that filter, and `ALL` of the Top N's own column takes the Top N off.
- `SUMX`, `AVERAGEX`, `MINX`, `MAXX` and `COUNTX` over the measure's own table (or a `FILTER` of
  it) are row iterators, and `RELATED` inside one reads the row's dimension. Over the MEMBERS
  of another table or of a column's `VALUES` (`AVERAGEX(Customer, COUNTROWS(RELATEDTABLE(
  Sales)))`, `SUMX(VALUES(Date[Month]), [Sales])`, `COUNTROWS(FILTER(VALUES(Customer[Key]),
  [Sales] > 1000))`), the expression is computed per member, at the visual's grouping plus
  the member's key, and aggregated again; a blank value is skipped, a table's blank row is
  not a member and a column's blank value is. `CALCULATE(expression, FILTER(VALUES(column),
  test of a measure))` with an expression that sums its rows is the same sum over the members
  that pass. Over the rows of the fact itself (`SUMX(Sales, [Sales])`, `AVERAGEX(Sales,
  Sales[Qty] * [Margin])`, `COUNTROWS(FILTER(Sales, [Sales] > 1000))`), a measure is read in
  each row's context: every column of the fact filtered to the row's values, so rows alike in
  every column read their total together, each counted. A measure that reads no row (a
  constant, a parameter written in as its value) is the same in every row. A distinct count
  over the members a measure test keeps is still refused by name.
- `RANKX(ALL(column) | ALLSELECTED(column), expression[, value[, ASC | DESC[, SKIP |
  DENSE]]])` ranks the row's value among the expression for every member of the column (a
  blank as 0), keeping the visual's other grouping columns. RANKX is never blank, so the
  visual lists every member of its grouping columns. `RANKX(ALL(Customer), ...)` over a whole
  table ranks among its rows (each filter and grouping on the table removed). A RANKX, a
  window or a member iterator INSIDE another expression (`IF(ISBLANK([Sales]), BLANK(),
  RANKX(...))`, `DIVIDE(COUNTROWS(FILTER(VALUES(...), ...)), ...)`, or a measure naming one)
  is computed over its parts' values row by row, with DAX's blank rules. `CALCULATE(expression, OFFSET | INDEX |
  WINDOW(..., ALL | ALLSELECTED(columns), ORDERBY(...)))` reads the member before or after the
  row's, the n-th member, or the sum over a window of members (a running total), every member
  keeping its place; `CALCULATE(expression, TOPN(n, ALL | ALLSELECTED(columns), order[, ASC]))`
  sums the expression over the first n members (ties kept). WINDOW and TOPN need an additive
  expression (a sum or a count). `PARTITIONBY(...)` moves the window inside each partition (a
  row grouped by the partition only reads the union within it), and the blanks argument
  `DEFAULT` / `KEEP` sorts a blank lowest (`FIRST` / `LAST` were refused by the service, seen
  live).
- `ISINSCOPE`, `ISFILTERED`, `ISCROSSFILTERED` and `HASONEVALUE` are read from the statement's
  grouping and filters: a subtotal or a total is a statement of its own, so
  `IF(ISINSCOPE(Product[Name]), ...)` is what each row and total shows. ISCROSSFILTERED is
  TRUE where a filter or grouping column of the table, or of a table whose filters reach it,
  is in context. HASONEVALUE of a dimension's column reads that table's values under the
  visual's filters on its other columns. A card of a measure that reads no row (a constant)
  is one value. `TREATAS({values}, column)` is the filter
  `column IN {values}`, replacing the visual's own filter on the column. `TREATAS(VALUES(
  Targets[Channel]), Sales[Channel])` of a table no relationship reaches (a target table at
  another grain) reads the sales whose channel is one of the values the target table holds in
  the row's context (its grouping and its own filters; a blank matches a blank), replacing the
  visual's filter on the sales channel; two `TREATAS` of one table's columns in one
  `CALCULATE` are refused by name. A table no relationship links to the measure's table at all
  filters nothing: its columns list every member beside the measure's value, and a filter on
  it leaves the measure as it is.
- A member test that reads another table (the new-customers pattern,
  `COUNTROWS(FILTER(VALUES(Sales[Customer]), CALCULATE(MIN(Sales[Date]), ALL(Date)) >=
  MIN(Date[Date])))`) reads that table's aggregate in the row's context; the members are the
  ones the iterated table holds there. `SUMX`, `AVERAGEX` and `COUNTROWS` over
  `TOPN(n, VALUES(col) | table, order by[, ASC])` iterate the members ranked n or better in
  the row's context (ties kept, a BLANK as zero; ascending, or counted, every member of a
  dimension's column, one with no rows first).
- `IF`, `IF.EAGER`, `SWITCH` (a `BLANK()` case matches only a blank), `COALESCE`, `DIVIDE` and
  BLANK arithmetic follow DAX. `NOT` is read as a function and as an operator (`NOT ISBLANK(x)`,
  looser than a comparison; NOT of a blank is TRUE), and `DATEDIFF(start, end, DAY)` counts the
  interval's boundaries crossed (`WEEK` is refused: the model states no start of week). The `/` operator divides as DAX does: `5 / 0` is Infinity and
  `0 / 0` is NaN. Inside `IFERROR` and `ISERROR` a division by zero or BLANK under a non-blank
  dividend is an error (`IFERROR(5 / 0, -1)` is -1), and a BLANK is not (`IFERROR(BLANK(), -1)`
  is BLANK). `SQRT` of a negative, and `LN`, `LOG10` and `LOG` of zero, a negative or BLANK
  (or a base of 1), are errors there too, and so is `ERROR("text")`; an error inside an `IF`
  branch is one only where that branch is taken. `ERROR` outside them is refused by name (the
  visual shows the error). A comparison with `BLANK()` by `<`, `>`, `<=` or `>=` reads the blank
  as the other side's zero (0, the empty text, FALSE or the zero date 30 December 1899, by its
  type); where the type is not known it is refused by name. A variable holding a number can be
  compared in a `CALCULATE` filter (`VAR t = [Threshold] RETURN CALCULATE([Sales],
  Sales[Amount] > t)`; Power BI refuses a measure there). A text conversion inside them (`VALUE`,
  `DATEVALUE`) is refused by name: it parses by the model's culture (`VALUE("1,234")` is 1234
  and `VALUE("$5")` is 5, but `VALUE("5%")` is an error), which no warehouse cast repeats.
- A measure that is not blank on a row with no fact rows (`[Sales] + 0`, `COALESCE`, an `IF`
  with an else) makes the visual list every member of a dimension it groups or filters by: the
  statement lists them and gives each the measure's value on an empty row. A total over `ALL`
  there (or an `ALLEXCEPT` that removes a dimension's filter) is that context's own statement,
  joined to each listed row on the columns it keeps. A Top N filter narrows the members
  listed, also when the visual shows items with no data.
- A measure that reads only another table (`COUNTROWS(DIM_CUSTOMER)` on the fact) is computed
  over that table.
- `USERELATIONSHIP` joins along the named inactive relationship, and `CROSSFILTER` makes a
  relationship filter both ways, not at all or one way, for the measures that name them; beside
  measures that do not, each is a statement of its own, joined on the visual's rows.
- Time intelligence (`TOTALYTD`, `TOTALQTD`, `TOTALMTD`, `DATESYTD` with a fiscal year end such
  as `"6-30"`, `SAMEPERIODLASTYEAR`, `DATEADD`, `PARALLELPERIOD`, `PREVIOUSMONTH`, `DATESINPERIOD`,
  `DATESBETWEEN`) is compiled over a date table that an active relationship reaches on its
  date column. A running total written as pattern libraries write it,
  `CALCULATE([Sales], FILTER(ALL(Date[Date]), Date[Date] <= MAX(Date[Date])))` (or its
  shorthand `Date[Date] <= MAX(Date[Date])`), is every date up to the row's last;
  `DATESBETWEEN(Date[Date], BLANK(), MAX(Date[Date]))` is the same running total, and on the
  date table's blank row (seen live) its blank end reads as the last date, so that row shows
  the grand running total. Each row of the visual stands for the date table's dates under the visual's
  date filters, and the date set is derived from them; `DATEADD` moves each date and keeps a
  month's end at the month's end. A date set inside another date set's `CALCULATE` (last
  year's year to date: `CALCULATE([Sales YTD], SAMEPERIODLASTYEAR(...))` or
  `TOTALYTD([Sales LY], ...)`) reads the dates the outer one leaves; two date sets in one
  `CALCULATE` are intersected; `DATESINPERIOD` also counts from a `DATE(...)` literal (the same
  dates on every row, the date table's blank row too) or from the last date of a column of the
  measure's own table in the row's context (`MAX(Sales[OrderDate])`). Inside an `ALL` of a
  grouped column (`CALCULATE([Sales YTD], ALL(Sales[Channel]))`, also of a date table column)
  the date set is read for the columns it keeps, on every row of the columns it removes; under
  a Top N filter, also on a date table column, the members it keeps are the rows. A period
  back from the last fact date beside another date set reads the row's own dates, and inside
  one reads the dates the outer set leaves (the row's other grouping columns ride along). A
  Top N the statement keeps by LIMIT (a KPI's latest point, an item limit) is applied after
  the time intelligence. A date set over another column is refused by name.
- A `CALCULATE` filter that replaces the visual's filter on a column beside time
  intelligence (`CALCULATE([Sales], DATESYTD(...), Sales[Channel] = "ONLINE")` on a visual
  filtered to another channel) reads the date set's rows without the visual's filter;
  `TOTALYTD(expression, dates, filter)` is that CALCULATE.
- A calculation group's items on a visual (or one item kept by a filter) apply each item's
  DAX to every measure, `SELECTEDMEASURE()` standing for it: one statement per item, the
  item's name in its column; a total without the items' column has no item in context. Two
  calculation groups on one visual apply the one of lower precedence to the measure and the
  other around it (seen live: prior year of "per order" is prior-year sales per prior-year
  order); two of the same precedence are refused by name. An item's format string changes how
  a value is shown, not the value: it is kept in `bi_facts` and named in the gotcha.
- A what-if parameter (a calculated table of `GENERATESERIES` or `DATATABLE` that no
  relationship reaches, read through `SELECTEDVALUE(T[c], fallback)`) is compiled with its
  value written in: the one value the filters on the table leave (a slicer, a visual filter),
  else the fallback (BLANK without one), and every statement says which. `MAX`, `MIN`,
  `HASONEVALUE`, `ISFILTERED` and `COUNTROWS` of the table read the same rows. A visual grouped
  by the parameter's column is one statement per value, put together, for as many values as a
  widget may have variants (`bi.max_variants_per_widget`); the values over it are left out and
  named (`variant_cap`), and the unit is partial.
- A visual calculation (the visual's own formula, saved on the visual) is compiled over the
  visual's matrix: every cell at every level of its axes (leaf rows, subtotals, the grand
  total; on a matrix with rows and columns, the empty cells too). `RUNNINGSUM`,
  `MOVINGAVERAGE`, `PREVIOUS`, `NEXT`, `FIRST`, `LAST` and `RANGE` are windows along an axis
  (`ROWS`, `COLUMNS`, `ROWS COLUMNS`, `COLUMNS ROWS`), ordered by the axis' columns or an
  `ORDERBY`, restarting as `reset` says; `COLLAPSE`, `COLLAPSEALL`, `EXPAND`, `EXPANDALL`,
  `LOOKUP`, `LOOKUPWITHTOTALS` and `ISATLEVEL` read other cells. A calculation may read another
  and the visual's hidden fields. The leaf statement and each total statement read the matrix
  at their own level. The visual's sort does not change the order a calculation runs in.
A workbook needs no calculated field. A plain field aggregated on a shelf or on the Marks card
(text, colour, size, detail, tooltip), such as `SUM(Quantity)`, is a measure with the
aggregation the sheet uses, and a date pill such as `YEAR(Order Date)` groups by that part of
the date.

**Which rows a Tableau sheet reads.** Each filter is applied where Tableau's order of
operations puts it. Each rule below was compared live with the published sheet's own data and
with the VizQL Data Service:

- A data source's own filters (Data Source Filters, in the published `.tds` or in the
  workbook's embedded data source) and an extract's filters, while the extract is read, apply
  to every row of every sheet on it, before any context filter. They are the innermost tier
  of the statement.
- A range on a date or a number that is not aggregated (a dimension, or a measure filtered on
  "All values") is a row filter, also when the sheet has a Top N. "Include null values" keeps
  the rows with no value; "Non-null values" keeps the others. A range on an aggregated pill
  (`SUM(Sales)`, `AVG(Sales)` on Filters) tests that aggregate on each row of the view, after
  aggregation (a `HAVING`); on a sheet with no dimension, its one total.
- A hidden member (Hide) is left out of what the sheet shows only: it still counts in a
  calculation across the view's rows, such as a running sum. A range of members keeps both
  ends. Exclude ticked on a condition keeps the members that fail it.
- A Top N and a condition on an aggregate (`SUM(Sales) > 1000000`) keep the members of their
  field, computed over the rows the data source and context filters leave. The sheet's other
  dimension filters then apply to those members' rows, so a Top 10 beside a region filter can
  show fewer than ten; in context, the region narrows the ranking itself. A Top N on one of a
  sheet's two dimensions keeps members, never rows. A NULL member counts like any other.
- A condition with no aggregate (`NOT ISNULL([Region])`, `CONTAINS([Customer], "an")`) tests
  each row's value. `CONTAINS`, `STARTSWITH` and `ENDSWITH` compare as the warehouse does
  (case-sensitive on Snowflake).
- A relative date filter (this year, the last 6 months, the last 8 quarters, this or last
  week) is whole periods counted from the day the statement runs, or from the date it is
  anchored to. A week starts on the data source's start of week (Sunday when it states
  none). "To date" ends at today, or at the current time on a time column. Today and the
  current time are read in the time zone `viewer.json` states (`time_zone`, the Tableau
  plug's setting of the same name, for example `America/Los_Angeles`), else from the
  warehouse session's clock, which must then be in the site's time zone. A setting of one the reader does not know is refused by name.
- A Top N's count, a condition and a calculation that name a parameter read the parameter's
  current value (the one the workbook was saved with); every statement compiled through one
  says so.
- A date field's fiscal year start other than January makes its year or quarter pill and
  their filters the fiscal one: an April start makes 1 April 2024 to 31 March 2025 fiscal
  year 2025 (named by the year it ends in), and fiscal Q1 April to June. Tableau saves the
  start on the field (`fiscal-year-start` on the data source's column, the form Tableau Cloud
  applied live for April and July starts); a data source's Date Properties start is read when
  the field states none (a workbook with only that setting kept calendar years in Tableau
  Cloud, so the field's own setting is the one confirmed). A fiscal week number is refused by
  name.
- An aggregated extract ("Aggregate data for visible dimensions") holds one row per
  combination of the data source's visible dimensions (also those only its metadata records
  name, with the role their type gives them), each measure aggregated as the extract
  says (Sum, Min, Max, Avg, Count, Count Distinct): the statement reads those rows, so a
  sheet's average is the average of the aggregated values. An extract that also rolls dates
  up to a level is refused by name. Not confirmed live: refreshing one needs the warehouse.
- A union of tables is one statement: each table's rows with every column of the union (NULL
  where a table has no such column), and Table Name holding the table each row came from.
  Tables read through two connections of the same warehouse are each qualified from their
  own connection. Initial SQL that only tags the session, sets a timeout or picks the compute
  does not change a result and is left out; any other Initial SQL is refused by name.
- An alias is the label a member's row shows; the rows stay one per stored value, and a
  filter still names stored values.
- A user function of the viewer's domain or groups (`USERDOMAIN()`, `ISMEMBEROF("Sales")`) is
  compiled for the identity the fetch signed in as, when the Tableau plug's `viewer: true`
  records it. `USERNAME()`, `FULLNAME()`, `ISUSERNAME()` and `ISFULLNAME()` compile to a bind
  placeholder (`:viewer_user_name`, `:viewer_full_name` on Snowflake), never to a name: the
  statement is filtered to whoever runs it, and the verified query and the metric card say
  so. A row-level security filter such as `[Owner] = USERNAME()` is one of these. With
  `viewer: true` the check before publishing binds the fetching identity's name, read again
  from Tableau and held in memory only, runs the statement and keeps no name; without it the
  statement is parsed and not run.
- Refused by name, per data source, while the other data sources of the workbook still
  compile: a file, a spreadsheet, a web or SaaS connector or an extract with no live
  connection; a join or union across connections of different warehouses. A Tableau
  parameter inside custom SQL is its declared default, written as a literal of its declared
  type; one the workbook does not declare refuses the data source by name.

**What a Tableau calculation compiles to.** Each rule below was compared live with the
published sheet's own data (and, where it can run them, the VizQL Data Service):

- A quick table calculation (percent of total, running total, rank, percentile, moving
  calculation, difference, percent difference, year over year) and a calc that calls a
  table-calculation function (`RUNNING_SUM`, `WINDOW_SUM`, `TOTAL`, `INDEX`, `FIRST`, `LAST`,
  `SIZE`, `LOOKUP`, `RANK` and its forms, `PREVIOUS_VALUE`) runs over the sheet's aggregated
  rows: a window over the statement's grouped rows, along the dimensions its Compute Using
  names and restarting on the others, in each dimension's ascending order with Null first, or
  in the order the sheet sorts it (by a measure, by hand, descending). On a crosstab, Table
  runs across then down, Pane along the innermost dimension of each shelf, and Pane (across),
  Pane (down) and Cell as Tableau lays them out, every row against every column (the empty
  cells too, as Tableau densifies them) for all but Table and Cell; a dimension on the Marks
  card partitions; "at
  the level" keeps the addressed dimensions down to that one. A pill with no Compute Using
  runs Table (across). `TOTAL(AVG(x))` is the average of the underlying rows, and `TOTAL` of
  an expression (`TOTAL(SUM([Sales]) / SUM([Qty]))`) the expression of each aggregate's total.
  A Difference, Percent Difference or Percent From is from the previous member (the first
  one's is empty), or from a member the file fixes by name or by a parameter's current value
  (a member no row holds leaves every row empty), or relative to the first or last member
  (the editor's "Relative to First / Last"); Compound Growth Rate is
  (value / first) ^ (1 / (INDEX() - 1)) - 1. A YTD Total restarts every year (`level-break`
  names the first field after the restart), and a quick calculation addressed by a date field
  itself runs along every pill of it the sheet shows. `RANK_UNIQUE` numbers ties in the view's
  order. `PREVIOUS_VALUE` is a running sum, difference, greatest or least of itself where it
  is one, and otherwise the recursion itself (a recursive statement over the grouped rows,
  numbered along the Compute Using; any expression, a text value too; on T-SQL the
  recursion's CTEs stand at the top of the statement, which ends `OPTION (MAXRECURSION 0)`,
  and on Databricks it states its own `MAX RECURSION LEVEL`). A Top N keeps its
  members before a table calculation runs, which then runs over them only. A table
  calculation over another one (`RANK(RUNNING_SUM(…))`, a secondary
  calculation over a quick one) runs over the grouped rows the inner one computed; a calc
  another calc names runs by its own Compute Using, Table (across) when the pill names none
  for it; a sort by a measure beside a finer dimension orders by each member's total. None is
  ever compiled as the bare aggregate.
- A `FIXED` level of detail is computed before the sheet's dimension filters (after its data
  source and context filters), at its own dimensions. As a grouping field, or inside a row
  expression under an aggregate, it is joined to each row; under an aggregate
  (`AVG([Customer Sales])`) the sheet's rows are read once per combination of the sheet's
  dimensions and the level of detail's, then aggregated, and an average or a distinct count
  beside it is computed at the sheet's own grain. `INCLUDE` is computed after the dimension
  filters at both, `EXCLUDE` at the sheet's dimensions without its own, under an aggregate or
  per row; `{SUM([Sales])}` is one value for the whole table. A level of detail inside
  another one is read once per combination of the outer dimensions and its own, at any depth;
  inside an `INCLUDE` or `EXCLUDE` the outer one is at the sheet's dimensions with its own
  added or taken away, after the dimension filters, while a `FIXED` inside it still reads the
  rows before them. An aggregate of the rows' own fields beside an inner level of detail reads
  the rows (`SUM([Sales]) / MAX({FIXED : SUM([Sales])})`), and one aggregate over a row
  expression of both reads them row by row, each row carrying the inner value of its own
  dimensions (`SUM([Sales] * {FIXED [Region], [Status] : AVG([Discount])})`). A `FIXED` level
  of detail may be another one's dimension (a cohort: `{FIXED [Cohort Year] :
  COUNTD([Customer])}`), its value any expression of aggregates (a rounded spend band, a
  distinct count). An `INCLUDE` or `EXCLUDE` as a dimension is refused by name: Tableau
  rejects that formula itself (it is a measure).
- A context filter, or a data source filter beside a Top N or a level of detail, on a related
  table's column keeps the rows whose related row passes it before anything else is computed.
  A Top N or a condition filter on a related table's column ranks (or tests) its members over
  the rows that reach them through the relationships, before the dimension filters (the
  members are the related table's: a row that reaches none is not ranked). A table
  calculation on a crosstab over related tables' columns runs over its empty cells too, laid
  out from the members of the rows the view's own table holds; one
  beside a filter on an aggregate or a level of detail is refused by name.
- A set or parameter that a set action or parameter action changes is compiled for the
  members or value the workbook saved, which is what the dashboard shows until someone
  selects marks; every statement through it says so.
- A zero divisor gives NULL, as in Tableau. `IIF` with an unknown test gives its fourth
  argument (NULL without one). `MEDIAN`, `PERCENTILE`, `STDEV`, `STDEVP`, `VAR` and `VARP`
  compile on a shelf and inside a calc. A numeric bin is its lower bound (a bin whose size a
  parameter sets uses the parameter's current value); a group keeps the
  members it does not hold as themselves, or puts them (the Null member too) in its Other
  bin; a set is In where its members, its condition, its Top N or its combination of those
  hold (a condition or a Top N is computed per member before the dimension filters; a Top
  N's count may be a parameter's current value; a Top N of listed members ranks those
  members only; an `order` alone keeps every member; a set nests to any depth), Out
  elsewhere. A range of members runs from
  the lesser end to the greater in the warehouse's order, whichever end the file names first.
- A week starts on the data source's Date Properties day, Sunday when it states none
  (`DATETRUNC('week', …)`, `DATEPART('week', …)`, `DATENAME('week', …)`, `DATEDIFF('week',
  …)` and a week pill), never on the warehouse's. ISO week, year and weekday compile.
- `STR` is the warehouse's own text of a value, a date `YYYY-MM-DD` and a date and time
  `YYYY-MM-DD HH:MM:SS`; `DATENAME` the part as Tableau shows it; `DATEPARSE`, `SPLIT`,
  `MID`, the `REGEXP_` functions, `ATTR` (the one value or `*`), two-argument `MIN` / `MAX`,
  `DIV`, `^` and date literals (`#2025-01-01#`) compile. `TODAY()` and `NOW()` are the
  warehouse session's date and time: Tableau sends them to the connection (checked live). `MAKEDATE`,
  `MAKETIME`, `MAKEDATETIME`, `QUARTER`, `WEEK`, `PROPER`, `ISDATE` and `REGEXP_EXTRACT_NTH`
  compile from their documented meaning (Tableau does not offer them on a live Snowflake
  connection).
- `RAWSQL_*` (SQL the author wrote) and `SCRIPT_*` (an analytics extension) are refused by
  name.

**Tableau Pulse metric definitions.** Save the response of the Pulse definitions endpoint of
the Tableau REST API (`GET /api/-/pulse/definitions`, signed in to the site) as it is, one file
per published data source, named after that data source: `<data source>.pulse.json` beside its
`<data source>.tds`. Point `bi` at the folder (or at the `.pulse.json` file). Each definition
becomes one question: its measure with its aggregation, and the filters of the definition and
of its default metric. A cumulative metric (`is_running_total: true`, what the Pulse page saves
for a sum) is the same aggregate, accumulated over the measurement period. The window Pulse
shows by default (the current day, month, quarter or year so far) is applied as a filter;
another window is said, not applied. Each breakdown the definition allows (its
`allowed_dimensions`) is one more question grouped by that field. The `tableau-cloud` plug
saves the definitions beside each data source it fetches. A definition with no basic measure
is reported and skipped. Pulse names fields by their
remote names (`EXTENDED_PRICE`); captions are read too. The file looks like this:

```json
{"definitions": [{
  "metadata": {"name": "Units sold", "id": "…"},
  "specification": {
    "datasource": {"id": "…"},
    "basic_specification": {
      "measure": {"field": "QUANTITY", "aggregation": "AGGREGATION_SUM"},
      "time_dimension": {"field": "ORDER_DATE"},
      "filters": []},
    "is_running_total": true},
  "metrics": [{"is_default": true, "specification": {
    "filters": [],
    "measurement_period": {"granularity": "GRANULARITY_BY_MONTH"}}}]}]}
```

**Virtual connections.** A data source on a virtual connection names only the virtual
connection's tables; the virtual connection's own file says which warehouse object each table
is. Save it beside the data source as `<name>.tvcx`, the zip Tableau returns from
`GET /api/<version>/sites/<site-id>/virtualConnections/<id>/content` (it holds a JSON `.tvc`
listing each table's database, schema and table, or its custom SQL). The `tableau-cloud` plug
fetches it for you. A table whose name carries its location, as Tableau writes it
(`ORDERS (SHOP.SALES.ORDERS)`), resolves from the name even without the file.

### B. Power BI straight from Microsoft Fabric

The run fetches the report and its semantic model itself, as its first step:

```yaml
version: 1
scope: acme-sales
dialect: snowflake           # the dialect of the warehouse the model reads
mode: standalone
inputs:
  history: []
  schema: []
  bi: [powerbi-fabric]
publish: [files]
settings:
  powerbi-fabric:
    targets:
      - https://app.powerbi.com/groups/<workspace-id>/reports/<report-id>
    # workspace: <workspace-id>   # or every report in a workspace
    with_model: true              # also the models a composite model chains to
    # model: <model-id>           # only if the fetch brings several semantic models
```

Sign-in needs no setting, and nothing asks which: the run uses a service principal when
`PBI_TENANT_ID`, `PBI_CLIENT_ID` and `PBI_CLIENT_SECRET` are all set (in Fabric, the tenant
setting *Service principals can use Fabric APIs* must allow it, and it needs a role on the
workspace), else `PBI_ACCESS_TOKEN` when it is set, else your own sign-in with a device code
when you run it at a terminal (the token is cached, owner-only, for the next run). Where your
tenant blocks the device code, set `sign_in: browser` under `settings.powerbi-fabric`: you sign
in in your own browser instead (the OAuth authorization code with PKCE; the answer comes back
to a loopback address on your machine). A run that is not at a terminal and has neither is
refused by name. `metrics-agent setup --check` says
which it found. Whatever signed in, the fetch takes the richest route that identity's rights
allow: the definitions, else a report's `.pbix` export, plus the admin scanner's metadata for
an admin; the [Power BI plug's README](../packages/metrics-agent-powerbi/README.md) lists the
routes. The definitions are fetched into the run directory; a resumed run reuses them. If you
already have an extract on disk, set `bi:` to it instead of `targets` and nothing is fetched.
A run reads every model the fetch brought, one model each (`model` narrows it to one); a
model with no measure is said and left out.

### C. Snowflake

SQL checked on your own read-only role, columns from `INFORMATION_SCHEMA`, and query history
from native Account Usage history or an existing aggregated shape table:

```yaml
version: 1
scope: acme-sales
dialect: snowflake
mode: standalone
inputs:
  history: [snowflake-direct]
  schema: [snowflake-direct]
  bi: [local-files]          # optional: BI files on disk
  sql_check: snowflake-direct
publish: [files]
settings:
  snowflake-direct:
    shape_table: ANALYTICS.METRICS.QUERY_SHAPES
    catalog: ANALYTICS       # the session's database: SCHEMA.TABLE resolves against it
    witness_mode: explain
  local-files:
    bi: exports/powerbi_model
    bi_vendor: powerbi
```

Environment: `METRICS_AGENT_WAREHOUSE_DSN` as `k=v;k=v` (account, user, role, warehouse, authenticator,
private_key_file, …). In a `.env` file, **single-quote the whole value**: a shell loading the
file ends it at the first `;` otherwise. For key-pair sign-in:

```text
METRICS_AGENT_WAREHOUSE_DSN='account=<org>-<account>;user=<SERVICE_USER>;role=<READ_ONLY_ROLE>;warehouse=<WH>;authenticator=SNOWFLAKE_JWT;private_key_file=/absolute/path/to/key.p8;private_key_file_pwd=<passphrase>'
```

`account` is `<organisation>-<account name>` (`SELECT CURRENT_ORGANIZATION_NAME(),
CURRENT_ACCOUNT_NAME()`); a bare account locator works only in AWS US West. `private_key_file`
is the key **file**, by absolute path; drop `private_key_file_pwd` for an unencrypted key. Give
the service user `DEFAULT_SECONDARY_ROLES = ()` so no other role it holds joins the session.
Use a read-only role: the agent refuses any statement that writes before it is sent, but the
role is the first lock. For native history, replace `shape_table` above with
`history_source: account_usage`, give the role read access to
`SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY`, and set the run's bounded `tables` and absolute
`window.start` / `window.end` dates. The end is exclusive at midnight UTC. Without explicit
bounds, the native route uses the previous 90 days. It blocks on overflow instead of returning
a partial corpus; see the [Snowflake plug](../packages/metrics-agent-snowflake/README.md).

### D. An Atlan tenant

Your Atlan tenant is your organisation's Atlan site (`https://<name>.atlan.com`).
Power BI definitions crawled by Atlan, SQL checked through Atlan's query path (no warehouse
credential of your own), and column metadata from an Atlan connection:

```yaml
version: 1
scope: acme-sales
dialect: snowflake
mode: connected
inputs:
  history: []
  schema: [atlan-lakehouse]
  bi: [atlan-lakehouse]
  sql_check: atlan-lakehouse
publish: [files]
settings:
  atlan-lakehouse:
    bi: extract/semanticModels/<workspace-id>/<model-id>   # fetched first, see below
    bi_assets: [<the model's qualified name or Atlan URL>]
    connection: default/snowflake/<connection-id>
    witness_mode: execute
```

Fetch the crawled definitions first (the qualified name, GUID or Atlan URL of the model or
report):

```bash
metrics-agent stage s_fetch_powerbi_extract --assets "<qualified name or URL>" --out extract
```

Environment: `ATLAN_DOMAIN` with `ATLAN_CLIENT_ID` and `ATLAN_CLIENT_SECRET` (an OAuth client) or
`ATLAN_API_TOKEN`; `ATLAN_QUERY_DATASOURCE` and `ATLAN_QUERY_DEFAULT_SCHEMA` for the SQL check;
`POLARIS_CATALOG_ENDPOINT`, `POLARIS_WAREHOUSE_NAME`, `POLARIS_READER_ID` and
`POLARIS_READER_SECRET` for the column metadata. `metrics-agent setup --check run.yml` names
any that are missing.

Query history from Atlan's metadata lakehouse is pulled to a file first, with
`metrics-agent stage s_fetch_querylog_lakehouse`, and listed as `inputs.history:
[atlan-lakehouse]` with `settings.atlan-lakehouse.history` set to that file. A `standalone`
run refuses it: data a third party holds (here Atlan) is read only by a `connected` run.

### E. Tableau straight from Tableau Cloud or Tableau Server

The run fetches each workbook and every published data source it uses itself, as its first
step. Downloaded by hand, a forgotten data source leaves every worksheet resolving to no table;
the fetch reads the workbook's XML, downloads each published data source beside it, and
follows any published data source those are built on:

```yaml
version: 1
scope: acme-sales
dialect: snowflake           # the dialect of the warehouse the data sources read
mode: standalone
inputs:
  history: []
  schema: []
  bi: [tableau-cloud]
publish: [files]
settings:
  tableau-cloud:
    workbooks:
      - https://prod-useast-a.online.tableau.com/#/site/acme/views/SalesOverview/Summary
      # - <workbook LUID>, or Finance/Sales Overview (project/name)
    # model: Sales Overview       # only if you list several workbooks
```

Environment: `TABLEAU_SERVER_URL` (`https://` and the host only), `TABLEAU_SITE` (the site's
content URL; unset for the Default site of a Tableau Server), and one complete set of
credentials, whichever you have; the plug works out which from what is set:
`TABLEAU_PAT_NAME` and `TABLEAU_PAT_SECRET` (a Personal Access Token, the route for Tableau
Cloud with multi-factor authentication), or `TABLEAU_USERNAME` and `TABLEAU_PASSWORD`, or a
connected app (`TABLEAU_CONNECTED_APP_CLIENT_ID`, `TABLEAU_CONNECTED_APP_SECRET_ID`,
`TABLEAU_CONNECTED_APP_SECRET_VALUE` with `TABLEAU_USERNAME`). `metrics-agent setup --check`
says which route it signed in by and what that sign-in may do (download workbooks, download
data sources, the Metadata API). The signed-in user needs View and Download/Save a Copy on each
workbook and Download Data Source on each data source. Where a data source download is refused,
it is rebuilt from the Tableau Metadata API (fields, formulas and upstream tables, no join
logic) and marked as reduced fidelity in `inputs/bi/tableau-cloud/manifest.json`; where neither
is available, the data source is recorded as unavailable with the access to ask for, and the
run goes on with what was obtained and says so. The credentials go only to a host under
`tableau.allowed_host_suffixes` in the policy (`.online.tableau.com` by default): add your own
Tableau Server's domain there. If you already have the workbook and its data sources on disk,
set `bi:` to their folder instead of `workbooks` and nothing is fetched.

## 4. Test the connections

```bash
metrics-agent setup --check run.yml
```

Each plug signs in or opens its files and reads nothing else. A failure names the setting or
the variable to fix, never a value. A SQL-check plug is also asked whether its session can
resolve a statement that names `SCHEMA.TABLE`: a Snowflake session with no current database
fails here, naming `settings.snowflake-direct.catalog` or `database=` in the DSN, instead of
failing most SQL checks during the run.

**Keep the SQL check.** Without one nothing is compiled: every statement ships `parse_only`,
and SQL that parses but cannot run goes unnoticed. The interview offers it by default; a run
without one is allowed (some estates have no warehouse access), and records it in
`run_inputs.json` (`sql_check.warning`) and repeats it when the run pauses or ends.

## 5. Run

```bash
metrics-agent run --run output/acme
```

Run it from the directory that holds `run.yml`, or name it: `metrics-agent run --run
output/acme --config path/to/run.yml`. `run.yml` is the whole description of the run: there are
no input flags.

The run settles its inputs, builds the verified queries from both lanes, and **pauses** at the
first authored gate.

| Exit code | Meaning |
|---|---|
| `0` | complete |
| `1` | a stage failed; its reason is printed above the `STOPPED` line |
| `2` | refused before starting; the message says why (a missing setting or variable, a contradiction) |
| `3` | paused: at an authored gate, or where tracing failed while you require it (`METRICS_AGENT_TRACE_REQUIRED=1`) |

Useful options: `--fresh` starts over (the old run directory is moved aside, never deleted);
`--skip-a3b-review` skips the optional review of the BI compiler's output; `--c3-deterministic`
writes structural BI questions without an author (each one flagged for review).

## 6. Answer the gates

Judgement calls are not made by the agent: it holds no model key. At each gate it writes a
prompt file into the run directory and stops, naming the file, the skill and where the answer
goes:

| Gate | What is asked | Skill |
|---|---|---|
| a3b | review the SQL the BI compiler wrote (optional) | `review-compiler` |
| c3 | the business question each BI statement answers | `author-questions` |
| m2 | a name, question and description for each verified query | `describe-verified-sql` |
| x1 | what, if anything, each statement measures | `propose-metrics` |
| x2 | which proposals are the same metric | `merge-metrics` |
| x3 | the published definition of each metric | `author-metric-card` |
| x4 | which metrics are derived from which | `relate-metrics` |

In Claude Code: *"Answer the m2 gate for output/acme."* Then re-run **the same command**; it
resumes where it stopped and validates the answer before going on. A refused answer says which
item and which field; fix it and re-run. Any other assistant works the same way if it can read
the skill files and write JSON.

## 7. What you get

In the run directory, once the run completes:

| File | What it holds |
|---|---|
| `verified_queries.json` | every verified SQL statement, with its question, tables, evidence and check result |
| `metric_cards.json` | one definition per metric: formula, population, grain, dimensions, joins, examples |
| `metric_relationships.json` | which metrics are derived from which, and how |
| `consistency_report.json` | the metric layer's own consistency check |
| `manifest.json` | every stage that ran, its inputs and outputs |

### How close each number is to the BI tool's

Every statement compiled from a BI widget, and every metric built from one, carries a
conversion grade with its reason (`quality`):

| Grade | Meaning |
|---|---|
| `exact` | a complete translation that names no difference from the BI formula, and the warehouse accepted it |
| `equivalent_with_gotcha` | the same number under a condition the agent names (a parameter at its default, the viewing user's rows) |
| `approximate` | part of the formula has no SQL form, and the grade names it: the number can differ |
| `unverified` | no warehouse checked the statement (parse only, no warehouse, an access limit), and why |
| `refused` | no SQL at all, and why |

`a3_compile_report.json` grades each widget and each measure as compiled; the candidate and
the verified query carry the final grade, and a metric card the worst of its statements'. A
grade says which checks ran; it is not a comparison with the numbers the BI tool shows. The
order and the wording are policy (`quality` in `agent_policy.yml`).

### One metric per business number

A model often spells one number several ways: a measure and its copy, a format-string
variant, a column aggregated on a visual, a thin report's own measure, a measure on a
dedicated measure table, a calculation item beside a copied measure, a workbook's calculated
field beside the data source's. When their statements compute the same aggregate the metric
merge proposes them as one metric (`same_number_hints`), and the card lists every BI
definition behind it in `bi_definitions`: where and how it is defined, its formula, display
folder and description, the measures it is built from, and the model's own metadata
(annotations, perspectives, whether the model or the data source is certified or promoted, a
KPI's target and status). That metadata is context for naming and describing the metric; it
never changes the SQL. Tableau's certification and descriptions come from the REST API: the
Tableau plug writes them to `bi_tags.json` beside the workbook. Power BI's come from the admin
scanner's saved scan, when the fetch ran one.

## 8. Publish

`publish` in `run.yml` decides where results go. Nothing is published unless it is listed.

| `publish:` | Settings | Environment |
|---|---|---|
| `[files]` | none | none |
| `[registry-rest]` (the Atlan Agent Registry) | `registry-rest: {base_url, workspace_id}` | `METRICS_AGENT_REGISTRY_BASE_URL`, `METRICS_AGENT_REGISTRY_TOKEN` |
| `[atlan-glossary]` | `atlan-glossary: {glossary: "<name>"}` | the Atlan variables above |

List both to publish to both. A run that publishes is the same command: `publish` is the
answer. The first publish to a glossary creates it.

**Tracing (optional).** Add `observers: [atlan-trace]` to record a run that publishes to the
Registry as a trace, an experiment and a session there; it reads the `METRICS_AGENT_TRACE_*` variables
(see `metrics-agent plugs`), and `--workspace`/`--agent` name the agent it reports as.
`--no-trace` runs untraced for once. A tracing failure is said once and the run goes on, unless you set
`METRICS_AGENT_TRACE_REQUIRED=1`: then the run pauses at that stage until you fix tracing, or unset the
variable to go on untraced (the run records that choice). Any other installed observer plug,
such as the example `jsonl-trace`, can be listed too.
Publishing again : from the same run directory or a new one : applies only the difference: the
agent reads the glossary from your tenant and pairs each metric with its term by the keys
stored on the term (so a term a person renamed or moved is still found). It creates the terms
it lacks in an inbox category, always updates each metric's definition, rewrites a name,
description or README only where nobody has changed it since the agent wrote it (a person's
text is kept, and what the agent would have changed is listed in the publish report), never
re-files, re-owns or re-certifies a term, and deprecates (never deletes) the term of a metric
your layer no longer has.

## 9. When something goes wrong

- **`REFUSED : …`**: nothing ran. The line names what to change in `run.yml` or which variable
  is missing.
- **`PAUSED : waiting at the <gate> gate`**: expected. Answer it (§6) and re-run.
- **`NO SQL CHECK: nothing was compiled`** when the run ends: the run has no
  `inputs.sql_check`, so every statement is `parse_only`. Add a SQL-check plug if the warehouse
  can be reached ([what to ask for](guides/customer-access.md)).
- **A statement labelled `needs_review`**: the SQL check did not pass (a table the warehouse
  does not have, a permission). It stays in the layer, flagged, and is not published as verified.
- **The run changed under you**: a run directory remembers the `run.yml` it started from and
  refuses to resume if the file changed. Restore it, or start again with `--fresh`.

More: [`design/plugs.md`](design/plugs.md) for every plug and setting, and
[`PIPELINE.md`](../PIPELINE.md), the operator's reference, for every stage, flag, contract and
gate.
