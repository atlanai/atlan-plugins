# Analytics setup: implementation and verification

## Preview follow-up: 0.6.1

A participant's 0.6.0 run combined dashboard definitions and query history correctly, but
the generated preview stayed in tool output and a linked HTML file. The workspace question
arrived before the preview link; two prose examples did not show the extracted definitions
or SQL. Reusing existing releases then allowed the run to finish without a visible preview.

The setup route now requires actual content in an assistant message before destination
selection or publication/reuse, including resumed runs. `project.py preview --format chat`
renders up to five metrics with their meaning, calculation, source and check status, plus
an actual history-derived saved SQL example when available. Existing detailed Markdown and
HTML views remain available. Large HTML results retain their snapshot path and point back
to the chat preview instead of only returning an opaque result-file reference.

The presentation rules also require stage/remaining-step updates in chat, explain changes
between draft and released counts, and preserve candidates retained only for validation.
Question suggestions must use evidenced dimensions and date semantics; a reuse checkpoint
test caught unsupported time-based suggestions, so the handoff now prefers saved questions.
Before CSV preparation, test the selected runtime with the same isolated import used by the
worker. Runtime discovery stays within the active/supplied context, not unrelated projects
or home caches. These are workflow instructions; the host still controls execution and UI.

Verification includes 252 unit/integration tests, 116 analytics helper checks, 52 metrics
helper checks and strict local marketplace, Analytics manifest and skill validation. The
new tests cover combined model/history text, exact saved SQL, read-only preview behavior,
empty/query-only candidates, source-text escaping and large HTML result handling.
Claude checkpoint cases cover a missing visible preview before workspace selection and a
reuse-only handoff with changed query counts. These use synthetic supplied checkpoint
evidence, not a new full interactive setup or live Trident publication rehearsal.
The final focused run passed the initial preview and missing-run cases. The reuse-only
case passed skill invocation, reconciliation and reply-style checks but failed its visible
preview ordering grader. Its reply included both metric definitions and saved SQL, yet
still opened with Ready before that preview. Keep this follow-up draft until that ordering
is stable; the focused suite is not fully green.

## Earlier version: 0.6.0

The current implementation uses the existing Analytics plugin for links, ordinary local
folders and BI exports. It follows Inspect, Scope, Build, Check, Preview, Publish and Ready
to ask. The harness plus Trident MCP answers questions; this route creates no separate agent
or product eval. The accelerator is an input fixture, with no special runtime path.

Generic context state now lives in `context_state.py`, independent of dashboard-agent jobs.
`project.py` keeps the existing CLI and run format compatible while delegating discovery,
page scope, previews, progress and release validation to focused helpers. Existing explicit
dashboard, metrics, agent-build and eval modes remain available.

The revised flow adds page selection and All with separate page/visual filters, retained
supporting-measure dependencies, and held unresolved references. Report-only exports retain
page choices while missing model definitions stay held. Acquisitions reuse matching
revision/access-scope/hash receipts; host tools still own downloads and permission prompts.
Run storage uses an explicit or already-permitted location, with an in-workspace fallback.

Local candidate previews precede an unknown workspace choice. Show five and Show all render
actual metric/SQL content immediately, including an explicit zero/fewer outcome. Rich
rendering failures return text. Publication reconciles against the selected workspace before
reporting create/revise/reuse counts or obtaining missing authorization. No local candidate
is described as an authoritative Trident definition.

Progress uses seven durable stages, factual counters, bounded events and an optional escaped
HTML snapshot. The snapshot has real details/refresh controls; it is not a live custom pane.
Inspect/check work checkpoints its results. Ordinary helper output is bounded, with large
results in private artifacts; the parent retains semantic decisions and authenticated calls.

Released saved queries can execute locally or produce a validated warehouse plan for the
existing read-only host tool. An ordinary Ask request requires no local setup run or receipt.
DuckDB now handles identifier case and CTE column lists correctly. Failed parameter samples
remain held across later checks and the publication planner excludes them. Metric example
execution is revision-bound and is labeled separately from formula equivalence and parity.

| Verification on 0.6.0 | Result |
| --- | --- |
| `make check` with Python 3.12 and the existing DuckDB runtime | 249 tests passed, plus 116 analytics helper checks and 52 metrics helper checks; generated files and lint passed |
| Claude Code 2.1.296, print mode with auto permissions, Show five choice | Five actual samples displayed without a destination or Registry writes |
| Same host, unrelated synthetic shipment folder | Surveyed model, history and CSV; authored two metrics and three queries; prepared DuckDB and executed SQL; inputs preserved |
| Metric-example follow-up | Both examples executed; all five candidates retained checked status |
| Accelerator intake | Report/model and history discovered together; four measures resolved; 110 history records became 108 privacy-filtered records and 84 query shapes, not 84 verified metrics |
| Preview browser checks | Details and refresh worked, source content escaped, no overflow at the tested desktop size |
| Strict Claude validation | Analytics manifest, skills, commands, agents and marketplace passed; the installed CLI lacks `--profile directory` |
| Manual-test installations | Claude Code and Codex enabled 0.6.0; all 92 plugin source files matched each installed copy |

The two new Claude rehearsals were non-interactive print-mode runs with auto permissions.
They do not prove a human-clicked interactive session. A complete interactive Claude/Codex
rehearsal through live publication and a fresh warehouse answer remains a release gate for
0.6.0. No new live Registry content was published in this revision's tests. The tester's
precise older version/transcript was unavailable, so no exact historical no-op reproduction
is claimed. The older live publication evidence below applies to 0.5.14, not this new flow.

Security-relevant boundaries remain explicit: selected read-only database bindings are
independent of input location, writes stay in owned private run files, path escapes and
symlinks are refused, SQL uses bound parameters and scoped tables, and no new dependency,
MCP server or credential is introduced. Format detection is not a parsing guarantee;
unsupported readers and translations retain visible held status.

Current entry: [shared setup](../plugins/atlanai-analytics/skills/atlan-analytics/references/setup.md).
New regression coverage: [context setup tests](../tools/tests/test_context_setup.py).

## Earlier 0.5.14 verification

6 November 2026. Implemented in the existing `atlanai-analytics` source in `atlan-plugins`.
Claude Code loaded the source plugins for this rehearsal without changing the globally
installed plugin versions. Synthetic metrics and SQL queries were published to the explicitly
approved live workspace. The PR isolates project setup from unrelated dashboard edits and
builds on the dashboard run infrastructure in PR #85. The corrected repository entry is
Analytics 0.5.14. Creating the PR does not release that version to marketplace main.

## Repository entry correction

The first rehearsal below started with a prepared local repository and detailed test scope.
It did not prove the participant's short GitHub setup prompt. A subsequent participant chat
loaded an older overlapping prototype, then selected hosted-dashboard capture for a PBIT in
the source repository. Repository workshop copy also redirected it into a local agent package
and skipped Trident publication. Local checks passing did not make that experience complete.

The correction makes GitHub/local repository setup take precedence, including a request to
"build an analytics agent from" the repo. The outcome is context in Trident and questions in
the current harness. Explicit separate-agent modes remain available. Setup keeps ownership
when source documentation describes a local-only workshop. The user still chooses the
workspace and approves a concrete publication scope; synthetic definitions retain provenance
and unreviewed status.

`project.py prepare` now creates a fresh DuckDB from explicit surveyed CSV-to-table mappings.
It checks source hashes, validates table identifiers, limits input size and execution time,
disables external access except for the exact selected paths, and creates the destination
atomically without replacing existing data. It executes no repository loader and installs
no dependency. CSV types are inferred and must be reviewed before binding. Tests cover fresh
preparation, overwrite refusal, changed/unselected/unsafe inputs, symlinks, duplicate tables
and timeout cleanup.

The regression prompt is now the exact short repository request that failed, without telling
the model which route to take. Its Claude routing test passed all five graders. The test has
no shell, so it verifies truthful capability reporting and routing, not data execution. The
full live rehearsal is separate. `make check` passed 220 unit/integration tests, 116 analytics
self-checks and 52 metrics self-checks; strict manifest, skill and command validation passed.
The full validation target still stops on this Claude CLI's unsupported `--profile` option.

A separate readback audit exposed another failure: the host had shortened metric definitions
and reused query-search results while claiming complete reads. The helper now accepts raw
released-artifact responses, normalizes them itself, validates the release state and existing
metric/query schemas, and rejects the old summarized shape. Tests cover rejected summaries,
withdrawn/non-current releases and preservation of optional definition evidence. The corrected
receipt was rebuilt from 32 complete current MCP artifact reads, copied directly from tool
results without model transcription.

An empty-population check also found an unguarded denominator in the newly published
on-time-rate query. Its existing artifact was revised to use `NULLIF(COUNT(*), 0)` and released
as 0.2.0. Every observed job type and a missing value was checked: commercial/missing returns
NULL, while populated rates are unchanged. NULL means no qualifying completed jobs, not zero
performance. The workflow now requires representative parameter and empty-population checks
before publishing; the executor continues refusing non-finite answers.

Saved-query answers now use `project.py answer` to validate complete released readbacks and
execute their SQL in one operation. It selects SQL from the retrieved artifact, binds user
parameters natively and returns the exact release plus `retrieval_check: passed`. This removes
the separate receipt-check step that a host had skipped while still returning a numerical
answer with a caveat. Additional tests cover changed definitions, unknown query IDs, native
bindings and template-like text inside quoted SQL literals.

The corrected plugin is installed in Claude Code and Codex for manual testing. All 83
installed plugin files matched source. The user approved disabling the older Codex prototype;
it remains installed with `enabled = false`. No other plugin was disabled.

## Corrected live rehearsal

A fresh clone of the supplied accelerator repository started from the original short GitHub
setup prompt. Claude selected the project workflow, surveyed one report/four measures,
110 history records and five CSV tables, and created DuckDB with `project.py prepare`.
It authored and checked seven metrics and 25 queries, reusing seven metrics and 19 queries
already released. The approved live continuation created six additional parameterized
queries and six relationships, then presented five follow-up questions. No agent package,
registration, product eval dataset or experiment was created.

The installed Codex plugin independently selected the same project route and authored the
same six-query addition. Its fresh sandbox could not access a compatible Python with DuckDB;
it preserved the survey/drafts and reported the blocked execution without claiming publication.
This is a routing/authoring test, not a successful full Codex execution test.

A fresh chat with no plugin mention also bypassed Analytics and read local drafts. The Ask
skill now explicitly covers prepared local projects and discovers their run binding. Continue
questions in the setup chat; in a new chat, explicitly select Atlan Analytics. Installation
alone cannot force every harness to invoke the skill. The final question test uses that
explicit plugin selection and fresh MCP reads. Earlier in-flight sessions carried the old
receipt format while the helper was being corrected; their receipt warnings are not passes.

The final fresh Claude chat explicitly selected Atlan Analytics and asked about P03 gross
margin and commercial-job on-time delivery. It fetched both complete released saved queries
through native MCP and used `project.py answer` for each. Both returned `retrieval_check:
passed`: gross margin was GBP -6,360 and the commercial rate was NULL. A deterministic audit
matched both input definitions and release envelopes exactly to their native MCP responses.
The first command used macOS's `/tmp` symlink and was refused; it retried the canonical
`/private/tmp` path successfully without weakening the file checks.

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

1. The original full live test used Claude Code with session-loaded source plugins. The
   corrected 0.5.14 is now installed locally in Claude Code and Codex. Marketplace main,
   customer installation and Claude Desktop/Cowork are not verified.
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
