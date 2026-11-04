# Snowflake CLI connectivity

Use the installed Snowflake CLI. Credentials, browser OAuth and secure storage remain
inside that CLI. Do not read credential files, tokens, callback URLs, browser storage or
Keychain. A dashboard supplies the destination and data scope; its author or service
identity does not identify the person running the agent.

## Discover before asking

1. Extract account, approved query role, warehouse and available database/schema from the
   downloaded model's source evidence. Keep secondary roles disabled; never select an
   administrator role because it is the CLI default. Do not guess a username from an email,
   operating-system account, report author, service principal or `CURRENT_USER()`.
2. Call `dashboard_snowflake_connect` with those destination fields. **Do not ask for a
   profile or username first.** The helper reuses this job's connection, then reads
   `snow connection list --all --format JSON` through a strict non-secret field allowlist.
   It matches the account and role/warehouse, uses an unambiguous matching profile or its
   matching default, and considers an account-matched environment identity. Unknown fields
   and credential material are never returned or persisted.
3. If no connection matches, the helper checks its private Analytics profile for that
   account and role. If needed, it invokes the installed CLI's `auth login` command using
   only the account and requested scope. Tell the user: **Connect to Snowflake to validate
   your dashboard. Your browser will open if sign-in is needed.** The user authenticates
   there; the CLI discovers and stores the identity. No typed username is required.
4. Browser-created profiles live under `~/.atlan/analytics/snowflake/<scope-hash>/`.
   Both `SNOWFLAKE_HOME` and `--config-file` are scoped to that directory for login and
   later queries, so the CLI's default-connection change does not affect the user's global
   configuration. The CLI owns its configuration and credential cache; helpers never read
   these files or include them in metric, agent or eval exports. Named existing profiles
   keep their configured authentication policy.
5. Ask a short profile-choice question only when multiple matching profiles remain and
   no matching default resolves them. Unsupported CLI login capabilities are a compatibility
   issue, not a reason to guess identity, install dependencies, enable hidden feature flags,
   or fall back to one new temporary login per query. Explicit user-supplied profiles or
   login names remain supported, but are not the default setup path.

The successful read-only probe checks the actual account, role and warehouse. Job state
stores only non-secret connection metadata. `CURRENT_USER()` is an observed user-object
name and can differ from a login name; never copy it into a guessed login configuration.
Repeated connects with unchanged scope reuse the successful probe.

## Execute and resume

`dashboard_snowflake_execute` executes stored metric SQL with its exact context. Parameters
are typed Snowflake Scripting variables used by `EXECUTE IMMEDIATE ... USING`; `-D`
substitution is disabled. Only bounded read-only SELECT statements and supported functions
are admitted. Use this adapter for supplemental queries too, with the resolved connection;
do not reconstruct ad hoc temporary-connection commands. Keep warehouse calls sequential.

A native import SELECT can be supplied as `rowset_sql` on a translation mapping; `relation`
names the logical mapped rowset. Preserve joins, transformations, aliases and source
security semantics. No view or warehouse object is created.

SQL failures are query or object-access issues, not evidence that login expired. Keep the
connection and fix the query. Connection failures and timeouts persist a pause that blocks
further warehouse checks. Report the issue once and wait for resolution before one explicit
reconnect. A failed browser-login attempt also persists across helper restarts: set
`retry_login: true` only after the user requests a retry or says sign-in is resolved.
Never put reconnect or `retry_login` in a per-metric loop.

Preserve frozen questions and completed results. Resume pending case IDs. Changing the
connection scope invalidates prior warehouse comparisons while retaining historical
receipts privately. If sign-in still repeats with caching enabled, stop the warehouse
phase and check CLI cache availability and account policy; never disable caching, switch
identities, extend server sessions or change account policy to make checks pass.

Warehouse values are fresh observations, not proof of the dashboard's source refresh time.
Establish the cutoff and historical availability independently. If the source snapshot is
absent, record `snapshot_mismatch`; keep historical parity unmeasured even when current-data
queries execute successfully.

References: [CLI connection options](https://docs.snowflake.com/en/developer-guide/snowflake-cli/connecting/configure-connections),
[SQL command](https://docs.snowflake.com/en/developer-guide/snowflake-cli/command-reference/sql-commands/sql),
[EXECUTE IMMEDIATE bindings](https://docs.snowflake.com/en/sql-reference/sql/execute-immediate).
