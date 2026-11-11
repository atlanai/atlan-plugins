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

Two things, sent together: how much each table is used, and a search by name. The
statements are in [warehouses](warehouses.md) under **Table usage** and **Table search**.

**Usage: take the first rung that answers, and say which it was.**

| Rung | Snowflake | Databricks | Covers |
| --- | --- | --- | --- |
| 1. Account-wide access | `ACCOUNT_USAGE.ACCESS_HISTORY` | `system.access.table_lineage` | Every table read by anyone, 7 days to start |
| 2. Account-wide queries | `ACCOUNT_USAGE.QUERY_HISTORY`, table names read from the query text | `system.query.history`, the same | Everyone's queries, 7 days to start |
| 3. The person's own queries | `INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER`, table names read from the query text | Their own rows in `system.query.history`, or the Query History API | Only what this person ran, 7 days on Snowflake |
| 4. None readable | Row count and last change from table metadata | The same | Size and freshness, not use |

Move down a rung only when the one above is refused or returns no rows, and never ask for a
grant to stay on a higher one. One statement per rung tried, each with a limit of 50 tables. Do not
run a rung to "confirm" another, and do not count or size history first.

**Keep every usage statement small enough to finish.** A warehouse tool gives up after a
minute or two, and a timed-out statement returns nothing.

- Use the statements in [warehouses](warehouses.md) as written. They pull table names out
  of the query text. Never join query text against a list of tables, and never bring an
  account-wide table list into a usage statement: that multiplies every query by every
  table and does not finish.
- Start with 7 days and the 500 most run query patterns. Widen to 30 days only when fewer
  than ten tables come back.
- When a statement times out, halve the window and send it once more. Do not resend it
  unchanged, and after a second timeout move down a rung.

Say the rung in the suggestion, in plain words, because it changes what "most used" means:
"across your account, last 7 days", "from your own queries, last 7 days", or "by size and
last change; usage was not readable". Missing usage is never shown as zero.

**Search: always, whatever the rung.** One statement over table names and comments, in the
databases the usage statement's tables came from (or the connection's own, with no usage):

- a named domain searches its words and their plain synonyms (for sales pipeline:
  opportunity, deal, stage, account);
- "let discovery decide" searches the subject words of the most used tables, so their
  rarely queried siblings are found too;
- a BI source adds the names of the tables it reads.

A table found by search and never queried is still a candidate: usage ranks, it does not
exclude.

**Rank what both returned:**

1. Tables a shared BI source reads, then the tables those are built from when the warehouse
   shows it. Start from what reports consume and work upstream.
2. Modelled tables before raw ones: a view, a mart, a fact or a dimension before the staging
   or raw table it was built from, and a current table before a dated copy.
3. Within those, most used by the most people first. A table used heavily by one service
   account ranks below one used by many people.
4. For a named domain, a match on the table's name before a match on a comment or a column.

For "let discovery decide", group the ranked tables by subject. The subject with the most use
by the most people is the suggestion; name the runners-up in one line. Usage is context, not
proof a table is right.

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
- Where the usage came from, once, in plain words.
- Two or three lines of what was worked out: the entities and how they link, and what will
  be left out.
- Anything the warehouse could not settle, as at most one question for the caller.

Bounds: one statement each for schemas, search and columns, and one per usage rung tried; at
most three schemas and eight tables. Stop earlier when the suggestion is clear. Query history itself is pulled later, by
generation, scoped to the confirmed tables.

When no table fits, `readiness` is `none`: say which words were searched and that
`analytics-demo` shows the flow on made-up data.
