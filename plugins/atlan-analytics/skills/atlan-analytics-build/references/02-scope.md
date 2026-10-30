# Phase 2: tables and workspace

Goal: five or fewer warehouse tables that answer the brief's questions, and the registry
workspace the agent's context will live in.

## Tables

1. Find candidates with the warehouse query tool recorded in state, using its dialect's
   statements in `skills/atlan-analytics-connect/references/warehouses.md`. Read-only, with
   small limits:
   - list the tables in the likely schemas, or search `information_schema.tables` by name for
     the domain's nouns (ticket, rating, quality…);
   - for each candidate, read its columns, types and comments from
     `information_schema.columns`.
2. **Propose** the scope: at most 5 fully qualified tables (`DB.SCHEMA.TABLE` on Snowflake,
   `catalog.schema.table` on Databricks), each with one
   line on which sample questions it serves. Flag tables that look like duplicates (`_V2`,
   `_BACKUP`, `_DEV` copies) and prefer the one analysts actually query.
3. Ask:
   - `<slug> · scope · the tables`
   - **These N tables (Recommended)**
   - **Add or swap one**
   - **Start smaller** (the 2–3 that cover most sample questions)
4. Write `tables.txt` (one FQ name per line) and set `answers.scope.tables`.

## Workspace

1. Call `registry__list_workspaces`. Recommend, in this order:
   - an empty workspace the user already created for this domain;
   - otherwise a new one named after the domain.

   Never recommend the org default workspace or a personal one for a shared agent.
2. If a new workspace is needed, **the user creates it in the registry UI** (no tool here
   creates workspaces). Give them the name to use, wait, then call
   `registry__list_workspaces` again and pick it up.
3. Check it's empty or intended: `registry__list_artifacts` for kinds `metric`, `sql_query` and
   `agent`, filtered to that workspace. If it already holds artifacts, say so and ask whether
   to build on them.
4. Save it as `ids.workspace_id` in state.

**STOP gate.** "Scope: N tables; workspace <display name> (M existing artifacts)." Set `phase`
to `context`.
