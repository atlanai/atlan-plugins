# Connections and discovery

Read for the `connections` and `discover` operations. Both look and report; neither asks the
person anything, changes anything, signs in to anything or installs anything. The caller owns
every question.

Discovery follows the method of a schema-first data interview: find the schemas in use, find
the tables that matter, read their columns, and work out entities, identifiers, the numbers
people ask for and what must always be filtered out. Where that method would ask an analyst,
discovery reads the answer from the warehouse and states it; it leaves a question for the
caller only where the warehouse cannot settle it.

## `connections`: what is already configured

Return the warehouse connections this machine has, without reading any data:

1. A warehouse record a saved run or this conversation already holds.
2. SQL tools behind Atlan, then the host's own connectors, then an installed vendor CLI with
   a saved connection, as [warehouse discovery](connect.md) steps 1 to 3 find them.

For each, return the product, how it is reached, and whether its check statement answered.
A connector that only offers sign-in is returned as "needs sign-in". Run at most one check
statement per connection. Never open a credential or connection config file.

## `discover`: suggest the tables

Inputs from the caller: the chosen connection, the domain (or "let discovery decide"), and
any BI source. Statements are in [warehouses](warehouses.md), the section for this warehouse.
Every statement is read-only with a limit.

### 1. With a BI source, read it first

`PROJECT survey` on the file, or the host's tools for a link. The tables it reads are the
first suggestions, and its measures are the numbers people ask for. Check that those tables
exist in the chosen warehouse with one metadata statement. A report whose data is packed
inside the file adds nothing to build from: say so in one clause and continue from the
warehouse.

### 2. Find the schemas in use

List the databases and schemas the connection can see (one statement). Keep those a person
works in: skip system, information and scratch schemas, and anything plainly a demo or test
area. With more than three left, keep the three most used in step 3.

### 3. Find the tables that matter

One usage statement for the last 30 days, where the warehouse has one: tables read, with how
many queries and how many distinct people.

| Warehouse | Usage comes from |
| --- | --- |
| Snowflake | `SNOWFLAKE.ACCOUNT_USAGE.ACCESS_HISTORY`, flattened on the objects accessed, grouped by table |
| Databricks | `system.access.table_lineage`, or `system.query.history` grouped by table |
| BigQuery | `INFORMATION_SCHEMA.JOBS`, the referenced tables |
| Another, or no grant | Table metadata only: row count and last change. Say that usage was not available; never show it as zero |

Then rank:

- **A named domain**: tables whose name, comment or columns hold the domain's words and
  their plain synonyms (for sales pipeline: opportunity, deal, stage, account), most used
  first.
- **Let discovery decide**: the most used tables overall, grouped by subject. The subject
  with the most use by the most people is the suggestion; name the runners-up in one line.

Prefer a modelled table (a mart, a fact or a dimension) over the raw table it was built
from, and a current table over a dated copy. Usage is context, not proof a table is right.

### 4. Read the columns of the top tables

One statement for the columns of at most eight tables. From them, work out and record:

| What | How to tell | If it cannot be told |
| --- | --- | --- |
| Entities | Tables that hold one row per thing: an account, a user, an order | Return the two candidates; the caller asks which the person means |
| Identifiers and joins | Key columns shared between tables | Leave the join out and say so |
| Numbers people ask for | The report's measures; otherwise the aggregates that recur in query history | Suggest none; generation mines them |
| What to leave out | Flag and status columns such as `IS_TEST`, `IS_INTERNAL`, `IS_DELETED`, a `status` with a deleted value | Apply none and say none was found |
| Time | The date column each table is filtered on, and whether it is a snapshot table | Say that the time basis is unknown |

Never sample rows to answer these, and never print a value that identifies a person.

### 5. Return the suggestion

- One line of what was found, products and counts only.
- Three to eight tables, each with one plain line of what it holds and why it was picked
  (read by the report, how much it is used, what it links).
- Two or three lines of what was worked out: the entities and how they link, and what will
  be left out.
- Anything the warehouse could not settle, as at most one question for the caller.

Bounds: one statement each for schemas, usage and columns; at most three schemas and eight
tables. Stop earlier when the suggestion is clear. Query history itself is pulled later, by
generation, scoped to the confirmed tables.

When no table fits, `readiness` is `none`: say which words were searched and that
`analytics-demo` shows the flow on made-up data.
