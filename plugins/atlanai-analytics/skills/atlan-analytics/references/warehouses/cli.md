# Through a CLI

Only where step 3 of warehouse discovery (`connect.md`) allowed it, with the connection or
profile recorded in `warehouse.profile`. Send the same read-only statements as `warehouses.md`.

- **Snowflake:** `snow sql -c <connection> --format json -q "<statement>"`. The check is
  `select current_role(), current_warehouse()`.
- **Databricks:** the Statement Execution API through the CLI:
  `databricks api post /api/2.0/sql/statements -p <profile> --json '{"warehouse_id": "<id>",
  "statement": "<statement>", "wait_timeout": "50s", "format": "JSON_ARRAY", "disposition":
  "INLINE"}'`. List warehouse ids with `databricks warehouses list -p <profile>`. Poll a
  `PENDING` statement with `databricks api get /api/2.0/sql/statements/<statement_id>`.

For the history pull, redirect the output to `<context-dir>/querylog.raw.json`, then run
`CONTEXT querylog <context-dir>/querylog.raw.json <context-dir>/querylog.jsonl`. It prints counts,
never the rows.
