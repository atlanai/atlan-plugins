# Retrieve released context, then reach the data

The current harness owns authenticated Trident calls. Every answer searches and reads fresh
released definitions; local drafts, search snippets and remembered setup answers are not
the definition source. No new agent or eval is required.

For a selected setup run, read `PROJECT status` for its workspace, release IDs and executor.
Search relevant metrics and saved queries in Trident, fetch complete `registry__get_artifact`
responses at a release, and retain the full definition and release metadata. Prioritize the
selected estate. Use saved queries for detail questions or when they already answer the request.

For a saved-query answer, run `PROJECT answer "<request.json>"`:

```json
{"readbacks": {"workspace_id": "workspace_example", "artifacts": ["complete MCP response objects"]},
 "artifact_id": "sql_query_example", "parameters": {}, "limit": 100}
```

Replace the illustrative array with actual complete objects. The helper checks the released
revision against the setup receipt, uses its SQL, checks parameter names and selects execution:

- **DuckDB:** converts Registry placeholders outside literals/comments into native bindings,
  executes read-only against the recorded database and returns fresh results.
- **Warehouse:** returns `status: execute_in_host`, the SQL, separate parameters, limit,
  selected connection and release. This is a query plan, not an answer. Dispatch it through the
  existing recorded read-only MCP/CLI tool. Convert placeholder syntax to that tool's native
  bindings outside SQL literals/comments, never interpolate parameter values. If the tool
  cannot safely bind the required parameters, hold execution and name that capability gap.

Do not rewrite the released formula or change its dialect to fit a different connection.
A changed release requires source/check review and refreshed MCP receipts. Withdrawn,
rejected, incomplete or mismatched releases stop this path, with no head or local-draft fallback.

For a metric without suitable saved SQL, use `PROJECT retrieved "<readbacks.json>"`, construct
SQL preserving the retrieved formula, joins, population, grain and mandatory filters, then
use the recorded local or warehouse executor. Check denominators, join multiplication,
truncation and date basis before answering. Say what could not be measured.

For an ordinary question with no setup run, follow the Ask skill's released-context discovery
and connection steps directly. Do not require setup, a local receipt, local files or DuckDB.
The pure `retrieval.validate(payload)` and `retrieval.query_plan(request, execution)` functions
also support this case without a receipt; callers must supply actual fresh MCP responses.
They do not contact or authenticate to Trident themselves.

Lead with the result and cite the metric/query name, actual artifact ID, release ID and
verified UI link when available. Retain executed SQL and bounded results; show SQL when asked.
Never claim success from a query plan or failed execution. If no relevant release exists,
explain the missing definition and offer to extend context. Setup follow-ups do not silently
fall back to an ungoverned answer.
