# The socket spec

> **Status: normative, pre-1.0.** Every plug follows this page. The code form of it is
> `metrics_agent/sockets.py`; the conformance kit (`metrics_agent.testing.conformance`) tests a
> plug against it. Where this page and the code disagree, that is a bug in one of them.

**On this page:** [the principle](#the-principle) · [the common formats](#the-common-formats) ·
[registering a plug](#registering-a-plug) · [rules for every plug](#rules-for-every-plug) ·
[input socket](#the-input-socket) · [BI reader socket](#the-bi-reader-socket) ·
[output socket](#the-output-socket) · [observer socket](#the-observer-socket) ·
[what the core guarantees](#what-the-core-guarantees) · [changing the spec](#changing-the-spec)

## The principle

**The core is common; plugs convert.** The core reads and writes only the common formats on
this page and never knows which source or destination is attached. A **plug** is anything that
converts:

- an **input plug** turns a source (files, a warehouse, a catalogue, a BI tool) into a common
  format the core reads;
- a **BI reader** turns one BI tool's definition files into `bi_facts`, and compiles that tool's
  formula language into SQL;
- an **output plug** turns the core's outputs into a destination's format and delivers it;
- an **observer** records what a run did, to any backend.

A new source or destination is a new plug, never a core change. If a plug needs something this
spec does not give it, the spec is missing a field: extend the common format, for every plug
(see [changing the spec](#changing-the-spec)). Never teach the core a special case.

```text
 source ──► INPUT PLUG ──► query records · table schemas · BI files ─┐
 BI files ─► BI READER ───► bi_facts · compiled statements ──────────┤
                                                                     ▼
                                       CORE (common formats only: gates, merge, metrics)
                                                                     │
               verified queries · metric cards · relationships ◄─────┘
                         │
                         ▼
                    OUTPUT PLUG ──► the destination's own format
```

## The common formats

Each format has a JSON schema under `metrics_agent/data/schemas/`; a record is checked against
it at the boundary, before any stage sees it.

| Format | Written by | Read by | Contract |
|---|---|---|---|
| Query record | input plug (`query_history`) | H0 | `sockets.QueryRecord`, `query_record.schema.json` |
| Table schema | input plug (`table_schema`) | S0, the gates | `sockets.SchemaResult`, `table_schema.schema.json` |
| Scope | input plug (`resolve_scope`) | S0 | `sockets.Scope`: fully qualified `DB.SCHEMA.TABLE` names |
| BI definition files | input plug (`bi_definitions`) | the BI reader | paths on disk; nothing else crosses |
| `bi_facts` | BI reader (`parse`) | A1, A2 | `bi_facts.schema.json` |
| Compiled unit | BI reader (`compile_unit`) | A3 | `sockets.Compiled` |
| Rule table, corpus case | BI reader (its files) | A3, the A3b gate | `lib/rules.py` (below) |
| The bundle | the core | output plug | `sockets.Bundle`: `verified_queries.schema.json`, `metric_spec.schema.json`, `metric_relationships.schema.json` |
| Publish plan, ledger | output plug (`plan`, `publish`) | the core | `sockets.PublishPlan`, `push_ledger.schema.json` |

**Vocabularies the readers fill.** The common formats name no tool's language. A property
marked `x-vocabulary` in a schema is filled when the schema is loaded: `formula-language` with
the languages the installed readers declare (plus `sql`, the core's own), `evidence` with the
core's evidence sources plus those languages. An undeclared value is refused.

## Registering a plug

A plug is found through Python entry points, never by import. The entry-point name is the
plug's `name`; the two must match.

| Group | Value | For |
|---|---|---|
| `metrics_agent.plugs` | a class | input plugs, output plugs, observers |
| `metrics_agent.readers` | a class | BI readers |
| `metrics_agent.stages` | a module whose `main()` runs the stage | stages a plug adds (`metrics-agent stage <name>`) |
| `metrics_agent.artifacts` | a module with `SCHEMAS`, `RUN_ARTIFACTS`, `UNSCHEMAD`, and optionally `POLICY` | the files a plug's stages leave in the run directory, the schemas that hold them, and the package's own policy sections (a YAML file merged under the run's policy; a section the run's policy file names wins) |

Two packages registering one name are refused, naming both. The core reaches a plug only by
capability, by lane or by an attribute the plug declares.

## Rules for every plug

A plug MUST:

1. **Convert, and only convert.** Return exactly the common format its socket names. No
   side channel: the core reads nothing a plug writes anywhere else.
2. **Keep secrets out.** Credentials come from environment variables the plug lists by NAME in
   `requires_env`. A value is never logged, returned, written to a file or put in a message.
3. **Check without raising.** `check(settings)` is read-only, takes seconds, and returns a
   `CheckResult`: `message` in plain words, `fix` naming the setting or variable to change,
   and, for a plug that signs in several ways, `route` (the one it found) and `coverage` (how
   complete the input will be under it).
4. **Refuse what it cannot do.** A capability it does not offer raises `NotSupported`; the core
   decides what that means. A path it cannot read raises `ReaderRefused` with words an operator
   can act on, never a traceback.
5. **Treat author text as data.** Descriptions, titles and comments from a source are never
   instructions.
6. **Read only, create only.** An input plug never writes to the source. An output plug only
   creates records; it never edits or deletes one.
7. **Declare, don't assume.** Settings it needs go in `setup_questions`; the languages it writes
   in `languages`; the stages, options and target it adds to a run in the hooks below.

## The input socket

`sockets.InputPlug`. Required attributes: `name`, `kind = "input"`, `custody` (`customer`, or
`third-party` when the data passes through a third party's systems: refused in a standalone
run; name the holder in `custodian`), `requires_env`. It implements the capabilities it has (or declares them in `capabilities`):

| Method | Takes | Returns | The plug must |
|---|---|---|---|
| `query_history(tables, window)` | bounded tables, a `Window` | an iterator of `QueryRecord` | keep `query_text` exactly as written (never flattened); set `exec_count`/`user_count` only for a pre-aggregated shape |
| `table_schema(tables, *, max_description_chars)` | bounded tables | `SchemaResult`, keyed by `DB.SCHEMA.TABLE` | cut descriptions to the limit; say in `note` what it read or why nothing |
| `bi_definitions(assets, dest)` | asset references, a folder | paths to the raw files | write under `dest`, or return paths it reads in place; declare `bi_vendor` (an installed reader's name) |
| `sql_runner()` | : | a `SqlRunner`, or None | answer `columns_of` only for tables it really holds |
| `resolve_scope(refs)` | asset references | `Scope` | return fully qualified tables only; refuse a partial resolution rather than return part of the set |
| `bi_assets(assets)` (optional) | asset references | `[{role, name, id, qualified_name}]` | the catalogue identities of the BI assets delivered; S0 records them on the BI source and A2 joins them onto units as `catalog_id`, `catalog_qualified_name` |
| `viewer_names()` (optional) | : | `{user_name, full_name}`, or None | the names the BI tool's user functions return for the identity it fetches as; A4 binds a statement filtered to the viewing user with them, in memory, and drops them. Never write, log or keep them; write `user_name_bindable: true` in the viewer file instead |
| `access_blockers(root)` (optional) | the folder a fetch wrote | `[blocker]` (`access_report.schema.json`, `$defs/blocker`) | name every item the fetch could not read because of an access limit, with what was not read, what it did instead, and what to ask for, from whom; `s_fetch_bi` records them in the run's `access_report.json` |

Optional declarations an input plug may make: `credential_routes` (every way it signs in,
as `CredentialRoute(name, env, note)`: the interview lists them and never asks which one the
person has; `check` finds the one set), `check_sql_session(settings)` (a SQL-check
plug: whether a session opened with these settings resolves a statement that names
`SCHEMA.TABLE`, that is, has a current database, catalog or project; a `CheckResult` like
`check`, read-only, quick, never raising, with the fix named; `metrics-agent setup --check`
calls it for each plug `inputs.sql_check` lists once its `check` passes), `issued_markers`
(substrings marking the
statements its own system issues, which H0 never admits as analysis), `fetch_settings` and
`fetched_models(root)` (a BI plug that fetches its definitions before S0, and the model
folders a fetch wrote), `fetch_reads_all` (a fetch that brings several models is read whole,
one model each, because the plug's settings named each one; without it the run is refused
until `model` names one), `bi_vendor`, `custodian`, and the set-up declarations in
[Writing a plug](writing-a-plug.md#set-up-questions-and-the-other-declarations).

**An access limit is never a stop.** A plug that meets one (a sign-in refused, an item its
identity may not read, a seat that never downloads, a warehouse suspended or over its quota, a
table with no SELECT grant) raises `sockets.AccessBlocked(blockers)` where it would raise an
error, or, for a fetch that read part, returns the rest from `access_blockers`. A blocker is
built with `metrics_agent.lib.access.blocker(code, ...)`; the codes are neutral
(`data/access_codes.yml`), and the plug converts its tool's answer into one. The core records
each in the run's `access_report.json`, goes on with what is readable (a BI plug that read
nothing is left out at S0; a statement the warehouse refused is `skipped_no_substrate`), and
ends the run with the report's summary. A SQL-check plug passes
`Engine(..., access_blocker=fn)`, `fn(exc) -> blocker | None`, so its warehouse's refusals are
told apart from a failed statement. The custody and read-only checks are not limits: they
still refuse.

A `SqlRunner` offers `connect`, `explain(sql) -> int`, `fetch(sql, limit)`, `scan(sql, batch)`,
`columns_of(table)`, `columns_of_many(tables)`, `close`, and is a context manager. The core
wraps it so no statement that writes ever reaches it. The core opens no warehouse: a runner is
the plug's own, usually the core's `lib/engine.Engine` with a `connector` the plug passes and
`catalog_per_database` saying whether its `information_schema` lives in each database.

## The BI reader socket

`sockets.BIReader`. A reader is the plug that knows one BI tool: it reads the tool's files and
compiles the tool's formulas. Nothing after it reads the tool's syntax.

**Reading.** Required: `name` (the vendor a run names, and the `vendor` it writes), `suffixes`
(lower-case, with the dot), `languages`, `detect(path) -> bool`, `parse(path, **opts) ->
bi_facts`. `parse` returns ONE model with every vendor spelling resolved:

- `tables[]` with `columns[]` and `source_lineage`; a table whose source is a SQL statement
  carries it in `source_lineage.native_sql`, each column's output name in
  `source_lineage.native_columns`, and `statement_is_source` when the statement IS the table;
  `source_lineage.reads` (optional) lists every warehouse object the source reads (a join's
  tables, a statement's tables), which the run's bounded table set derived from BI lineage
  includes; `source_lineage.statement` is set when the tool's own query steps change a
  warehouse table's rows or values: one SQL statement in the run's dialect that returns the
  model table, each column under its `source_column`, read by A3 wherever the table is read
  (the table's `name` is then the reader's own, never a warehouse table's); and
  `source_lineage.refusal` names, by reason, a source with no faithful SQL;
- `measures[]` with `formula` and `formula_language`, one of the reader's `languages`;
- `relationships[]` (with `cardinality` when the tool states it), `usage[]` (one entry per
  widget: its measures, columns, filters, sort, top N, parameters), and `expression_language` on
  a widget whose columns may be calculated fields.
- On each relationship, what a join along it means, in the tool's own settings converted to
  common fields: `is_active` (an inactive relationship is never joined along), `cardinality`
  (`M:1`, `1:1`, `M:M`, `1:M` or `unknown`), `cross_filter` (`single` or `both`), and
  `join_type`: `left` keeps a many-end row whose key the other end lacks, under a NULL member
  (Power BI's blank member); `full` keeps an unmatched row of either end (a Tableau
  relationship); `inner` keeps neither (a limited or many-to-many relationship, or one that
  assumes referential integrity, `rely_on_referential_integrity`, which joins inner only
  where both tables are read by DirectQuery). A `blend` (`kind: blend`, one edge per linking
  column pair) is never a join: the `to_table`'s measures and filters are aggregated over the
  links whose `from_column` the widget groups by and joined to the widget's rows after
  aggregation, NULL matching NULL (an inner join when a filter narrows the `to_table`); a
  column only the `to_table` holds shows its one value per row, or `*`; with no such link,
  the `to_table`'s total is beside every row. On a table,
  `storage_mode` (`import`, `directquery` or `dual`): where the tool reads its rows, which
  decides whether text is compared as the model holds it or as the source does. Two model
  tables over one warehouse table are two tables: the reader gives the second a statement of
  its own (`source_lineage.statement`). A filter of the candidates that defines the rows of the
  table a statement reads (the WHERE of a derived table) is marked `in_source`, and X1 offers it
  as `mandatory_source_filters` and refuses a metric over the statement whose
  `mandatory_filters` does not name its column.
  On a widget, `column_tables` (parallel to `columns`) and a filter's `table` name the model
  table a column is on, so a column two tables share is read on the right one. On a column,
  `value_labels` ([{value, label}], an alias): the label a widget grouped by it shows for a
  value, its rows still one per stored value. At the top, `viewer` (its `domain` and `groups`,
  and `user_name_bindable`, never a name; from a `viewer.json` beside the input, schema
  `bi_viewer`): the identity a viewer-dependent expression is evaluated for. An expression
  of the viewing user's name or full name compiles to a bind placeholder the `viewer_bind`
  operator rule row spells per dialect (`:viewer_user_name`, `:viewer_full_name`); the
  candidate, the verified query and the metric card carry `viewer_binds` (the placeholders,
  and whether verification could bind them). A literal A1's scrub withheld (a person's
  address or name a formula or filter compares with) is the numbered marker `<redacted:N>`;
  A3 reads it as the bind placeholder the `withheld_bind` rule row spells (`:withheld_N`),
  the objects carry `withheld_binds`, and A4 binds it from the BI files read again in
  memory. At the top, `interactions`: each set action or parameter action the file saves
  (`kind`, its `target` set or parameter, `activation`, `sources`, `change`, `on_clear`);
  the statements are compiled for the saved state and say the target follows a selection.
- The model's own metadata, as tags that never change SQL: a measure's `description`,
  `folder` (a display or data source folder; nested ones joined with a backslash), `uses`
  (the other measures its formula names), `tags` (`annotations` and `perspectives`), `kpi`
  (its target and status, each also declared as a measure with `kpi_of`), `origin: report`
  and `report` (a report's own measure over a shared model), `shown_on` (the measure table it
  is shown on, its `home_table` then the table its formula reads); a column's `folder` and
  `tags`; a table's `measure_table`; an asset's `endorsement` (`certified` or `promoted`),
  `endorsement_note` and `description`. A fetch plug that learns an asset's endorsement or
  description from its API writes `bi_tags.json` beside the input (schema `bi_tags`), which
  the reader copies onto the matching asset. Who endorsed an asset is never written.
- On a widget, `table_calcs` (by measure name): a measure computed over the widget's
  aggregated rows (a table calculation). `addressing` lists the grouped columns it runs along,
  in order, and `partitioning` those it restarts on; `sort` (by column) the view's own order
  of an addressed column where it is not ascending (`direction`, a manual order's `members`,
  or the aggregate `by` a computed sort orders by, `within` the outer columns of a nested
  sort); `pad` the columns on a crosstab's rows and on its columns when the view lays every
  row out against every column, the empty cells included, before the calculation runs;
  `nested` (by calc name) the `addressing` and `partitioning` of each table calculation the
  measure reaches through another calc, which runs by its own compute-using; `refusal` says
  why it has no statement.
  A measure listed there is never compiled as its bare aggregate. A2 carries it onto the
  unit's measure as `table_calc`, its columns named as the unit's `group_by` names them.
- On each filter, its `stage` in the tool's order of operations: `datasource` (every row
  before anything else: a data source's own filter, an extract's), `context`, `dimension`,
  `measure` (after aggregation; with `measure` naming the measure it tests, a `HAVING`),
  `table-calc` or `display` (after every calculation: a hidden member still counts in a
  table calculation over the widget's rows). Its `op` beside the membership and range
  operators: `not_null` / `is_null`; a range `value` with `or_null: true` keeps the rows with
  no value; `top_n` (`value`: `n`, `by` an expression in the widget's `expression_language`,
  `direction`) and `condition` (`value`: `expression`, `exclude`) keep the members of the
  column that pass, computed over the rows the `datasource` and `context` filters leave;
  `relative_date` (`value`: `period`, `first`, `last`, optional `anchor`, `include_null`,
  `include_future`, and `week_start` for a `week`) keeps whole periods counted from the day the
  statement runs (or the anchor), up to today (or now, on a time column) when
  `include_future` is false; a `top_n`'s `n_parameter` names the parameter its count is the
  current value of; `top_rank` is
  the rank filter of a widget's `top_n`, already carried there. A `top_n` may also say how it
  ranks: `ties: true` keeps every member tied with the Nth, `population: filtered` ranks
  within every other filter of the widget (not only the `datasource` and `context` ones) and
  `empty_members: excluded` never ranks a member whose measure is empty.
- More filter tests: an `in` / `not_in` list may hold JSON null, the empty member;
  `contains`, `not_contains`, `starts_with`, `not_starts_with` (a text `value`); `any_of` /
  `all_of` (`value`: a list of `{op, value}` on the same column); `relative_range` (`value`:
  `lower` and `upper`, each null or `{steps: [["add", n, unit] | ["span", unit], ...]}`
  applied in order to the current UTC time; an upper bound ending in a span covers the whole
  span; a bound cut to a `week` needs `week_start` on the value); `tuple` tests several
  columns together (`value`: `columns` [{column, table}], `rows` of one value per column,
  `negated`); ops with no value are refused by name. A filter's `null_as`
  (`number`, `text`, `date`) is the value an empty cell takes in its comparisons, so a
  comparison the stand-in passes keeps the empty rows (`qty < 5` with an empty number read as
  0). A `measure`-stage filter with `measure` set tests that measure on each row of the
  widget (a HAVING). Scope `slicer` is a slicer's saved selection, applied to the other
  widgets on its page.
- On a widget, `blank_rows`: `hidden` when the tool leaves out a row whose every measure is
  empty (the statement keeps the rows where a value is not NULL, a `HAVING` of `<value> IS
  NOT NULL` terms over its own projections, which `sqlshape` reads as no filter), `shown`
  when it also lists members with no rows ("show items with no data": every member of the
  grouping columns joined to the rows' values).
- On a widget, `parameters` of `kind: member`: a value the widget takes when it is opened
  from another (a member of `column`); `variant_of` and `variant`: the widget answered another
  way it can show (a field it can be switched to, each member it can be opened with, the
  latest point of a trend). On a table, `hierarchies` (levels, each a column),
  `field_choices` (the fields a switch can show) and `calculation_group` (the column naming
  its items and each item's DAX, applied to the measures a visual shows with it; its
  `precedence`, the higher applied around the lower on a visual with two; an item's
  `format_string`, metadata only). A calculated
  table is `source_lineage.statement` like any table whose rows a statement returns; when the
  file itself fixes its rows (a number series, a table of literals) it also carries
  `fixed_rows` (`columns`, and `rows` or a `series` of start, step and count), so a table no
  relationship reaches is read as a parameter at compile time.
- On a widget, `totals`: the total rows it shows beside its leaf rows, one `{kind, group_by}`
  per grain (`grand_total` keeps no column; `subtotal` the columns listed). `distinct_rows:
  true`: a widget with no measure that lists the existing combinations of its columns; A2
  keeps it as a unit with no measure, read from the table its columns are reached from.
- On a widget, `visual_matrix`: the widget's own calculations over its matrix (Power BI's
  visual calculations): `language`, `axes` (`rows`, `columns`, `pages`: each a list of levels,
  a level the grouping columns it holds), `calcs` (`name`, `expression` as saved, `hidden`),
  `refs` (each name an expression reads: a `column`, a `measure` of the widget, or a `calc`)
  and `hidden` (measures in the matrix the widget does not show). A2 carries it to the unit,
  each calculation shown as a measure with `visual_calc: true` and each hidden measure with
  `hidden: true`; the compiler reads the matrix (`readers/visualcalc.py`).

A model that declares what each metric is (its dimensions, joins, keys and example questions)
also fills the optional declared-model fields: `primary_key`, `synonyms`, `is_hidden`,
`measures[].declared` and the relationship roles and key columns (see [declared
models](declared-models-contract.md)). A `formula_language: sql` measure is the core's own
language: it needs no declaring, and the shared engine places it by role.

`parse` receives the same options for every reader: the run's `dialect` (a model whose
formulas are SQL is read in it), the run's `policy` (an admission choice the reader makes
is policy, and a reader package may ship its own section as `POLICY` defaults) and `model`,
the BI input's `bi_model` setting (None when unset): the ONE model to read when a file holds
several. A reader whose file can hold several refuses an unset or ambiguous `model` by name,
listing what the file holds, and never chooses; a reader whose file holds one ignores it. A reader takes
what it needs and ignores the rest. What it read but the contract cannot carry it lists in
`_reader_notes` (a list of sentences), which A1 says out loud and does not keep.

A formula in a language the reader did not declare is refused at parse. Optional hooks:
`form(path)`, `refuse(path)`, `explain_empty_usage(path)`, `units_without_usage(facts) ->
[unit]` (when a model has no widgets: the units it offers instead, such as one per measure on
no dashboard; A1 still reports zero usage), `one_model(path) -> path | None`
(when the path is a container of models, such as a whole tenant's extract, that holds exactly
one a run could read: that model, which S0 then reads and names; with several, `refuse` says
why), and `models(path) -> [path]`: the models a path holds, one path each (a folder of model
files is several models, read one by one and merged like two BI plugs); without it, the path
is one model.

**Compiling.** A formula is in the tool's language, so the reader compiles it. A3 calls, for
each model the reader wrote:

| Hook | Contract |
|---|---|
| `prepare(facts) -> model` | once per model; whatever compiling its units needs from the whole model. Opaque to the core. |
| `compile_unit(unit, rules, dialect, *, model=None, columns=None) -> Compiled` | one question unit to one statement in `dialect`, or a named reason why not. With no `model` (a regression case) it uses only the `columns` it is given. Deterministic: the same unit and table give the same SQL. |
| `compiler_rules` | the path of its rule table |
| `compiler_corpus` | the path of its regression corpus |

`Compiled` carries `sql`, `coverage` (`translated`, `partial` or `refused`), `reason`,
`untranslatable` (`{construct, in_measure, why}`: a construct with no SQL form is NAMED, never
approximated), `rules_used`, `provisional_rules`, `unknown` (what a new rule row would have to
cover, as `language:FUNCTION`), `gotchas` (sentences every object compiled from the unit
carries, such as a parameter fixed at its default) and `totals` (one `{kind, group_by, sql,
reason}` per total grain the unit names, each its own statement; A3 writes them to the
candidate's `totals`, and a total that does not compile makes the unit `partial`). A `partial` result says what it leaves
out in `reason`; A3 carries it to the candidate (`compiler.reason`) and to the compile report
(`partial_units`), and states plainly when a reader named nothing. A reader with no
`compile_unit` compiles nothing, and A3 refuses its units by name.

**The rule table** is data, never code: `version` and `rules[]`, each row with a dotted `id`,
`match` (exactly one of `function` with its `language`, `aggregation` or `operator`; optional
`dialects`), `emit` (exactly one of `template`, with `{0}`-style arguments, or `untranslatable`),
`tier` (`stable` or `provisional`) and `evidence` (one source of the closed vocabulary). A
template row may also say how a compiler applies it; the shipped compiler reads `ignore_args`,
`extra_args_ok`, `list_arg`, `defaults` (`{index: SQL}` for an optional argument left out),
`literal_args` (`{index: characters}`: that argument must be a string literal without them,
such as a language's wildcards), `literal_values` (`{index: [values]}`: that argument must be a
string literal naming one of them, such as the date parts whose meaning SQL shares) and
`scalar_list` (the function iterates a table constructor of scalars, whose items fill `{0}`). The A3b
review proposes rows; a row is adopted only when every corpus case still compiles unchanged.

**A corpus case** is one JSON file: `name`, `dialect`, `unit`, `columns`, `expect_sql`,
`expect_coverage`. A case in a reader's language gates that reader's compiler; a case in no
tool's language (a plain aggregation, a join, a filter) gates every compiler. The tables and
cases of the shipped readers are under `metrics_agent/plugs/readers/data/`; an operator may put
their own beside the policy (`bi_compiler_rules.yml`, `compiler_corpus/`).

## The output socket

`sockets.OutputPlug`. Required: `name`, `kind = "output"`, `lane` (the publish target that
admits it: any word the plug chooses; `files` is the core's own, meaning nothing leaves the run
directory), `requires_env`, and:

| Method | Contract |
|---|---|
| `check(settings)` | as for every plug |
| `resolve(settings) -> dict` | the settings it will publish with (an id, not a name), recorded on the run |
| `plan(bundle) -> PublishPlan` | a dry run: sends nothing; one `creates` entry per record it would create, with its `kind` and name or id |
| `publish(bundle) -> Ledger` | creates the records; returns what actually changed, in `push_ledger.schema.json` |

The `Bundle` holds the core's formats only, and never a user name. The plug converts it into
its destination's format in its own code and stages. Optional declarations, so the core names
none of a destination:

| Declaration | What it does |
|---|---|
| `run_steps_before(options)`, `run_steps(options)`, `run_steps_last(options)` | the stages `metrics-agent run` executes for it, as `(label, stage, args)`: before the pipeline, after it (destinations in `run.yml` order), and after every destination |
| `run_options` | `(flag, argparse kwargs)` pairs added to `metrics-agent run` |
| `s0_settings`, `settle_target(settings, *, required)` | the target S0 records under `run_inputs.outputs.<plug>`, as `(setting, variable, help)`: each from `settings.<plug>.<setting>` in `run.yml`, else the variable; the plug checks and resolves it. A credential-named key is refused. |
| `run_start(options)`, `run_failed(options, env, why)`, `run_complete(options, env)` | the run's lifecycle, called by `metrics-agent run` (on observers too): before the first stage (return what to add to every stage's environment), when it stops on a failure, and when it completes |
| `destination` | the words the run prints for where this plug publishes |

## The observer socket

`sockets.Observer`. Required: `name`, `kind = "observer"`, `requires_env`, and `start_run`,
`stage_span`, `artifact`, `gate`, `decision`, `finish_run`. Optional: `human_args`, `update`,
`remember_said`, `ensure_experiment`, `close_pending`, and `traces_run = True` for one that
needs a trace opened and closed around the run. Each stage is its own process, so `start_run`
and `finish_run` run once per stage. An observer never stops a run on purpose, and lets a
stage's own failure through `stage_span` unchanged.

## What the core guarantees

These run around every plug, and no plug can opt out of them:

- **Read-only SQL.** Every statement passes the core's read-only check before a runner sees it.
- **Custody.** A standalone run refuses any plug whose custody is not `customer`, before
  anything is read; a `connected` run with no such plug is refused too.
- **Schema validation.** Every record a plug returns is checked against its schema.
- **The publish target.** The lanes of the output plugs `run.yml` publishes to. A write is
  refused unless the target holds its lane. A dry run is always allowed.
- **Closed evidence.** Every filter and literal cites a source of the closed vocabulary.
- **No user names in a bundle.** A bundle that holds one cannot be built.

## Changing the spec

A plug that needs more than this page gives is a change to this page, made for every plug:

1. add the field to the common format and its schema, optional, so existing plugs still pass;
2. fill it in the shipped plugs that have it;
3. make the core read the field, never the source it came from;
4. update this page, `sockets.py` and the conformance kit in the same change.

The core boundary forbids naming a plug, a vendor, a formula
language, a destination or a plug's file, in code, in a schema key or enum, or in its policy;
S0 must not take a source-specific input flag. A run is described by `run.yml` alone. [Writing a plug](writing-a-plug.md) is the how-to;
[Plugs and sockets](plugs.md) is the design behind it.
