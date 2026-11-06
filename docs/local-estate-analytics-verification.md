# Local project analytics: implementation and verification

6 November 2026. Implemented in the existing `atlanai-analytics` source in `atlan-plugins`.
Claude Code loaded the source plugins for this rehearsal without changing the globally
installed plugin versions. Synthetic metrics and SQL queries were published to the explicitly
approved live workspace. The PR isolates project setup from unrelated dashboard edits and
builds on the dashboard run infrastructure in PR #85. It bumps Analytics to 0.5.11; creating
the PR does not release or install that version.

## Starting experience

**Set up this project so I can ask questions about its data: <folder>**

The existing Analytics skill now routes this request to a source survey, combined model and
query-history authoring, existing Trident publication, supported question suggestions and
retrieval for subsequent questions. An initial business question continues straight to an
answer after publication. Otherwise the route waits for the user to choose or type a question.
No product eval dataset, experiment or agent is created by this route.

## Implemented changes

- Updated the Analytics starter card, routing and start command. The generated card was
  rebuilt from `plugin.toml`; no plugin was added.
- Added `project.py` under the existing skill. It surveys a selected root, preserves source
  identities and hashes, pairs matching BIM sidecars, mines available history through the
  existing normalizer/filter/miner, binds local data, and records released-artifact receipts.
  It uses the existing run storage; Registry calls remain in the authenticated host workflow.
- Fixed normalized JSONL intake, which previously returned zero records for the supplied log.
- Preserved table-associated Power BI partitions and M expressions as evidence. Text model
  import no longer needs PBIXRay; binary decoding still needs its approved runtime. Supported
  expression types derive from declared column types; unresolved measures stay visible.
- Added native DuckDB compilation and dollar-parameter bindings. The local adapter runs
  single read-only queries in a timed child process, with selected base tables, bounded
  results/memory and external access and extension installation/loading disabled. It returns
  typed columns, decimal/date values, truncation, query identity and execution duration.
- Extended the existing ask skill with project-specific workspace and data binding. Each
  question must search/fetch Trident releases, not read local authoring bodies. Receipt checks
  refuse changed definitions, mismatched workspaces and missing/different releases. Changed
  source inventory or data binding invalidates the recorded publication context.
- Added 18 deterministic/integration tests and a natural-language routing case for the
  existing host test suite. The standalone routing case was not run; the live Claude session
  exercised the natural starter prompt and the project route.
- The live rehearsal exposed an isolated-Python import failure. The helper now resolves its
  sibling modules from its own directory, including under `python -I`; a subprocess regression
  test covers it. Authentication recovery now directs users to the selected existing MCP
  connection instead of guessing REST routes or probing other registries.
- Publication instructions now explicitly distinguish same-named metrics and saved queries
  when resolving link endpoints. Receipt instructions require complete released definitions
  from MCP, including query SQL and parameters, rather than joining release IDs to local bodies.
  Answer citations must use verified UI links or artifact/release identifiers, never guessed
  REST version URLs.

## Verification performed

| Check | Result |
|---|---|
| `make check PYTHON=python3.12`, with an existing DuckDB runtime available | Passed on the isolated PR: 210 unit/integration tests, including the 18 new tests |
| Existing analytics helper self-checks | Passed: 116 |
| Existing metrics helper self-checks | Passed: 52 |
| Generated-file drift and repository lint | Passed |
| Claude strict validation: Analytics manifest, skills and commands | Passed |
| Full `make validate` | Blocked by installed Claude CLI: unsupported `--profile` option; the Makefile was not weakened |
| Natural prompt through Claude Code and live Trident | Passed: survey, authoring, checked publication and supported question suggestions |
| Follow-up metric question in Claude | Passed: fresh released context retrieval, receipt validation and DuckDB execution |
| Fresh-chat saved SQL and parameterized question | Passed: live search/get, matching release receipts, separately bound month, correct results |
| Independent released-readback audit | Passed: all 26 receipt bodies exactly match native MCP responses; all releases current on latest |

DuckDB integration exercised native parameter binding, decimal/date/NULL results, injection
as a bound value, truncation, table scope, forbidden writes/multiple statements, external
and dynamic SQL rejection, database-defined functions, schema changes, missing databases,
timeout handling, release receipts, fresh-run resumption and fresh data after an insertion.
Registry readbacks in these tests are explicit test doubles, not live service evidence.
The original shared rehearsal checkout passed 211 tests; its additional dashboard coverage
test and unrelated edits are not included in this PR. The isolated PR reran all 210 tests.

## Actual accelerator source rehearsal

Used the supplied repository's PBIT, BIM, rowsets, query history and five CSV tables. Built a
new test database in the local output folder from those CSVs, without executing the repo's
loader or overwriting an existing database. Used the already-cached DuckDB 1.4.5 package with
Python 3.12; no dependency was installed.

The survey found the report, its supporting model, rowsets, history, database and CSVs. All
four semantic table partitions were retained and the four missing measure types resolved.
The compiled measures executed against the real fixture rows:

| Measure | Executed value |
|---|---:|
| Gross Margin | 118,681 |
| Rework Cost | 33,800 |
| Material Waste | 7,646.50 |
| Late Jobs | 3 |

Material Waste is displayed to two decimals; the CSV-inferred floating result was
7646.500000000001. This is an execution check, not independent Power BI parity.

History intake retained all 110 source records. The existing privacy filter withheld two
records for name-like literals. The remaining 108 records produced 84 distinct query shapes
in 38 families. Every remaining shape was executed: 83 succeeded and one was held. The held
on-time-rate query divided by an empty completed-commercial-project population and returned
a non-finite value. Its denominator policy needs review before publication. The filter was
not weakened to increase coverage. Query shape counts are not metric counts.

## Live Claude and Trident rehearsal

Used the actual accelerator checkout at commit `cbeef2ffacf22eb6cf72abddc03f16f28812e80b`,
Claude Code 2.1.296 and the existing Atlan MCP identity. The user approved its OAuth access.
The session loaded the locally developed Analytics plugin and existing core plugin. No new
MCP server, global plugin installation or credential was created by the implementation.

The natural starter produced seven metrics (four Power BI measures and three additional
history-derived measures), 19 reusable saved queries and 16 relationships. All 26 artifacts
were released as 0.1.0. The 84 history shapes were accounted for as 19 usable representatives,
51 duplicates with different literals, and 14 held (one empty-denominator failure, five bare
table counts and eight exploratory/detail previews). Ten unrelated existing metrics remained
unchanged. All published definitions and metric dimensions executed successfully.

The first follow-up, "What is our total gross margin?", retrieved the released metric through
native MCP and executed its SQL against the bound database: GBP 118,681. Its reconciliation
was GBP 474,450 contract value less GBP 181,568 labour and GBP 174,201 delivered materials.

A separate fresh Claude chat asked which materials have the highest waste rate and how much
rework cost in September 2026. It searched saved queries, fetched both complete released
definitions through MCP, validated the receipts and executed the retrieved SQL. Cement was
highest at 18.6014%; September rework cost was GBP 8,841. The month used native `$month`
binding with a separate parameter value. An independent Decimal calculation over the CSVs
matched all six material rates and the monthly rework result. Neither answer was truncated.
The fresh chat did not read local metric/query authoring files or publish anything.

The original setup receipt combined live release identifiers with local definition bodies.
After tightening the workflow, all 26 complete definitions were fetched through native
`registry__get_artifact` calls on `latest`. A deterministic audit checked requested/returned
IDs, current releases and exact definition equality. The refreshed receipts were identical;
the issue was provenance, not incorrect published definitions. The host captures and raw
MCP readbacks were retained as the evidence. Manually transcribed copies were excluded.

Browser verification confirmed the published metric's Live 0.1.0 status, definition, SQL and
dimensions, and the workspace's 19 saved queries with zero unreleased items. Embedded Metric
Studio failed to load twice; its existing "Open in new tab" action loaded the standalone
studio successfully. This is a platform presentation issue, not a failed Registry write.

## Remaining gaps and limits

1. The tested host is Claude Code with session-loaded source plugins. The globally installed
   plugin is older; customer installation/release and Claude Desktop/Cowork are not verified.
   The local helper validates normalized MCP evidence but does not independently contact or
   authenticate Trident. The actual host MCP transcript supplies remote evidence.
2. Embedded Metric Studio has the load failure described above. The standalone studio is a
   working path to inspect the live artifacts; no platform configuration was changed.
3. An approved DuckDB Python runtime must be available. Missing dependencies produce an
   actionable stop for execution, without preventing source preparation or auto-installing.
4. The local SQL boundary intentionally supports selected qualified base tables and reviewed
   analytical functions. Views, arbitrary functions/macros, external files and unsupported DAX
   are held for separate support. Engine controls do not replace host OS sandboxing for
   hostile SQL or database files. This is a security-relevant addition to review.
5. PBIT source parity is unmeasured without independent reference values. SQL execution does
   not certify business meaning. The withheld history records and empty-denominator query
   require review if their questions are needed.
6. The installed Claude CLI does not support the repository's full directory-profile
   validation command. Supported strict component validation passed; the complete target
   remains unverified until a compatible approved CLI is available.

## Main implementation files

- [Project workflow](../plugins/atlanai-analytics/skills/atlan-analytics/references/project.md)
- [Local project helper](../plugins/atlanai-analytics/skills/atlan-analytics/scripts/project.py)
- [DuckDB executor](../plugins/atlanai-analytics/skills/atlan-analytics/scripts/local_data.py)
- [Integration tests](../tools/tests/test_local_project.py)
- [Natural prompt routing case](../evals/atlan-analytics/cases/routing-project-natural/case.yaml)
