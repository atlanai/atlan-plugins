# Phase 2: tables and workspace

Goal: five or fewer warehouse tables that answer the brief's questions, and the registry
workspace the agent's context will live in.

## Tables

1. Find candidates with the warehouse query tool recorded in state, using its dialect's
   statements in `skills/analytics-survey/references/warehouses.md`. Read-only, with
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

1. Call `registry__list_workspaces`. Offer existing accessible workspaces suited to the
   requested audience, plus Keep local. Reuse a destination already selected. Do not silently
   select a personal or organization-default workspace for a shared agent.
2. Never offer or attempt workspace creation through an API/CLI. If no suitable workspace is
   accessible, preserve the local scope and explain the access needed. Only when the user
   explicitly asks for a new workspace, explain that an authorized person creates it in the
   Registry UI or obtains admin help. Refresh `registry__list_workspaces` afterward; never
   invent a workspace ID. Keep local pauses publication without discarding prepared work.
3. Check it's empty or intended: `registry__list_artifacts` for kinds `metric`, `sql_query` and
   `agent`, filtered to that workspace. If it already holds artifacts, say so and ask whether
   to build on them.
4. Save it as `ids.workspace_id` in state.

**STOP gate.** "Scope: N tables; workspace <display name> (M existing artifacts)." Set `phase`
to `context`.
