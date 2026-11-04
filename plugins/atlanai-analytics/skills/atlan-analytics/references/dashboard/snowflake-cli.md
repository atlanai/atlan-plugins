# Snowflake CLI connectivity

Use the installed Snowflake CLI for this workflow. `dashboard_snowflake_connect` discovers `snow` on PATH, in the usual user bin, or in existing uv tool environments. It never installs a dependency, reads credential files, exports tokens or prints raw authentication diagnostics.

1. Resolve account, warehouse and approved query role from source evidence or the user's selected connection. Prefer an explicit non-admin role and disable secondary roles. Reuse the job's existing connection. Never assume ACCOUNTADMIN is acceptable because OAuth defaulted to it.
2. Call `dashboard_snowflake_connect` with either a named `connection` plus role/warehouse, or source `account`, role/warehouse and optional database/schema. Temporary OAuth is the default when there is no profile. For either temporary OAuth or `externalbrowser`, resolve the non-secret Snowflake login name once and pass it as `connection.user`; a display name may differ from the login name. A stable user is required for credential reuse. The adapter refuses a missing user before opening the browser. Empty `snow connection list` output does not prove lack of connectivity. If login is needed, the CLI opens its own browser; the user signs in there, never in chat. Do not create credentials or persistent connection profiles yourself. Temporary browser connections enable the official CLI temporary-credential cache; OAuth also enables silent refresh. The CLI owns secure storage and renewal, subject to the account policy. Never inspect, export or copy its tokens. Named profiles retain their existing authentication and cache policy.
3. A successful read-only probe stores only non-secret connection metadata in private job state. Reconnecting with unchanged settings reuses that state without another probe. Do not reconnect before each metric or eval case. `dashboard_snowflake_execute` then executes the stored metric SQL with its exact context. Parameters are typed Snowflake Scripting variables used by `EXECUTE IMMEDIATE ... USING`; `-D` text substitution is disabled. The query and values are encoded separately. Only bounded read-only SELECT statements and supported functions are admitted. SQL and raw auth output are not logged by the helper.
4. A captured native import SELECT can be supplied as `rowset_sql` on a translation mapping. `relation` supplies a logical name for that mapped rowset. Preserve joins, transformations, aliases and source security semantics; a table with a similar name is not a substitute. There is no CREATE VIEW or warehouse write.
5. Returned scalar values are fresh warehouse observations, not proof of matched source refresh time. Independently establish the warehouse data cutoff and historical availability. If the source's snapshot is absent, record `snapshot_mismatch` for parity. Current-data queries may still be executable; state their data date and keep historical parity unmeasured.

The helper supports existing CLI versions exposing `snow sql --stdin`, temporary connections, browser OAuth, secondary-role control and JSON output. Unknown/missing options are an explicit compatibility issue, not permission to install or silently use another credential path.

References: [Snowflake EXECUTE IMMEDIATE and USING](https://docs.snowflake.com/en/sql-reference/sql/execute-immediate), [Snowflake CLI SQL command](https://docs.snowflake.com/en/developer-guide/snowflake-cli/command-reference/sql-commands/sql).

Connecting or retrying authentication preserves the frozen plan generation. Retry unavailable cases by case ID. A change to the existing connection scope invalidates warehouse comparisons, preserves historical evidence privately, and retains the original questions.

## Stop repeated login prompts

Use this adapter for every Snowflake operation; ad hoc `snow sql --temporary-connection`
commands without the same user and reuse flags restart the login problem. Supplemental
read-only queries must use `snowflake_cli.execute` with the same approved connection scope.
Keep warehouse calls sequential so refresh-token renewal cannot race.

SQL failures are query or object-access issues, not evidence that login expired. Do not
ask for a new login to repair SQL. Connection failures and timeouts pause warehouse checks
persistently, so remaining cases cannot launch another login. Read the job's new revision,
report the issue once, and wait for the user to resolve it before one explicit reconnect.
Preserve completed checks and retry pending case IDs. A successful reconnect clears the pause.
Never put reconnect in a per-case retry loop.

If the browser still opens repeatedly after reuse is enabled, stop the warehouse phase.
Check whether the CLI supports these options and secure credential storage is available;
account policy can require reauthentication. Do not silently remove caching flags, switch
identities, extend server session lifetimes, or change Snowflake account policy. Do not claim
that saving connection metadata alone proves login reuse.

[Snowflake CLI connection options](https://docs.snowflake.com/en/developer-guide/snowflake-cli/connecting/configure-connections)
document temporary-credential storage and OAuth refresh.
