# DAX to warehouse SQL

Target: users query the warehouse with portable definitions after migration. Source DAX and the BI model are evidence and a baseline, not the production execution dependency.

`dashboard_translate` compiles an AST, with separate value bindings and quoted physical identifiers. It supports Snowflake, Postgres and SQLite scalar SQL over one source-equivalent mapped rowset per measure. The host supplies `semantic_table`, a one-to-three-part physical `relation`, typed semantic/physical `columns`, `rowset_equivalent`, `security_preserved`, optional non-overridable `mandatory_filters`, and `evidence_ref`.

Supported: SUM/AVERAGE over numeric columns, MIN/MAX over supported non-boolean columns, COUNTROWS, DISTINCTCOUNT including blank, SELECTEDVALUE without alternate result on non-boolean columns, simple CALCULATE predicates, KEEPFILTERS, ISBLANK/NOT ISBLANK, literal ASCII CONTAINSSTRING, top-level scalar VAR/RETURN and addition/subtraction. Numeric literals with unsafe precision are refused. Text collation and relationship-generated blank members must be covered by source-equivalent mapping and validated; generated SQL alone does not prove this.

CALCULATE replaces filters on the same column unless KEEPFILTERS applies. Mandatory security filters cannot be replaced. COUNTROWS on an empty rowset is blank; DISTINCTCOUNT includes blank; `+ 0` is explicit coalescing. Top-level variables retain their original filter context. Values are never interpolated into SQL.

Unsupported: arbitrary DAX, iterators, time intelligence, relationship expansion, ALL/ALLSELECTED, SWITCH/MINX, unresolved measure references, boolean SELECTEDVALUE, complex coercion and wildcard/non-ASCII substring patterns. The tool reports a gap. Captured native SELECT rowsets with joins are supported through `rowset_sql`; unknown transformations still require authoring. There is no general host-authored SQL ingestion tool in this version; additional forms require compiler/authoring work before the plugin can migrate them.

Mapping must preserve source import transformations, row grain, relationships, calculated columns, calendar logic, timezone, source collation, refresh cutoff and row-level security. Do not map a flattened BI table to an arbitrary base table just because names resemble each other. This compiler consumes either an existing equivalent relation or an evidenced read-only `rowset_sql` SELECT. It does not invent multi-table M pipelines or create database objects.

Read `translation.sql`, `parameters`, `binding_style` and `query_hash`. Use driver binding; `:p0` is the canonical named-colon form. A driver adapter may translate placeholders without interpolating values, recording the canonical hash and actual execution receipt. If the available connector only accepts interpolated SQL, report the missing safe executor instead.

Compare source and warehouse against the same data cutoff and scope, including blanks, zero denominators, filters, duplicates and security boundaries. A warehouse observation must declare `executor:warehouse` and the exact query hash. Query hashes are host-supplied bindings, not cryptographic execution attestation. `warehouse_portable` means all submitted cases for that metric passed; it does not certify unseen scopes or general question accuracy. Use a separate isolated held-out benchmark for that.

Sources: [CALCULATE filter semantics](https://learn.microsoft.com/en-us/dax/calculate-function-dax), [DAX aggregation functions](https://learn.microsoft.com/en-us/dax/aggregation-functions-dax), [CONTAINSSTRING behavior](https://learn.microsoft.com/en-us/dax/containsstring-function-dax).
