# Analytics first-use experience

Help a first-time user understand the work, change its scope and inspect a useful result
before asking them to publish. Keep the existing metrics engine and Registry workflow.

Status: interaction contract and research evidence, prepared 8 November 2026. Participant
testing has not run. Implementation checks belong in the verification section below; they
do not establish that first-time users understand the experience.
The implementation baseline is Analytics 0.6.7 on main at `efeac77`; the proposed build is
0.6.8. Earlier recordings remain historical evidence, not a measured 0.6.7 comparison.

## Evidence and its limits

| Evidence | What was observed | What it does not establish |
| --- | --- | --- |
| Historical Codex setup chat using Analytics 0.6.6 | The run produced 7 metrics and 33 saved queries. It showed a five-metric table and SQL before workspace selection and repeated them in the final answer. Intermediate phase updates were lengthy. | No participant comprehension, reading-time or control score was collected. Counts from this run are not product defaults or a current-build benchmark. |
| Live inspection of Claude Dashboards onboarding | The empty starting view offered two routes, Use connector and Import file. | No authenticated data connection, generated chart, query inspection or refinement was exercised. |
| Current Cowork task | It loaded an older synced `atlan-analytics` plugin. | Its behavior cannot be attributed to the current `atlanai-analytics` build. Resolve the version confound before comparing hosts. |
| Earlier 0.6.1 synthetic checkpoints | Historical word counts and generated responses describe the earlier test inputs. | They are not the current 0.6.7 baseline, a live user study or a comparable timing measurement. |
| Public product docs and plugin source | The patterns below are documented or present in source. | Source inspection is not a hands-on usability test or evidence that an optional host capability is enabled here. |

Keep raw transcripts, screenshots and event timestamps in the approved private study folder.
Use anonymous evidence labels in this repository. Do not commit participant identities,
customer data, personal paths, workspace identifiers or authentication material.

The observed problem is narrower than "previews are missing": actual previews appeared in
the observed 0.6.6 chat. The unresolved design questions are when the user becomes oriented,
how much repeated text they must read, and whether choices feel available and reversible.

## Patterns worth testing

| Pattern | Source evidence | Proposed use here |
| --- | --- | --- |
| Start with an understandable entry choice | Claude Dashboards' observed empty view separates a connector from a file. Its official guide supports a natural-language question and subsequent refinement. | Explain what the supplied source enables. Reuse a known source rather than asking the user to choose it again. |
| Make results inspectable | Claude Dashboards documents per-chart SQL and refresh information. | Show an actual metric with meaning, source and check state early. Keep its SQL accessible, with a clear distinction between definition and executed value. |
| Keep progress in one place | `claude-dashboard` renders a persistent pane, changes tabs in place and refreshes from session measurements. | Keep current phase, scope and real counts together. Chat reports decisions, useful results and exceptions, not each helper invocation. |
| Keep outstanding choices visible | `side-pane` retains unanswered choices, supports copy/quote and respects a user's dismissal. | Repeat only the decision still needed. Preserve scope on resume. Do not reopen a surface the user dismissed. |
| Make detail optional | Claude HUD defaults to a compact display and offers additional task/tool information. | Keep the default summary small. Put detailed evidence behind actual working disclosure controls or an explicit request in chat. |

Primary sources, reviewed 8 November 2026:

- [Claude Dashboards guide](https://support.claude.com/en/articles/17454700-get-started-with-claude-dashboards).
- [Claude Dashboard pane implementation](https://github.com/uppinote20/claude-dashboard/blob/main/scripts/mod/register.tsx).
- [Side-pane implementation](https://github.com/xixiaofinland/side-pane/blob/main/plugins/side-pane/hooks/register.tsx).
- [Claude HUD behavior](https://github.com/jarrodwatts/claude-hud).

These are interaction references, not dependencies to install. Their unrelated telemetry,
configuration and provider features are outside this change.

Failure behavior was inspected in source, not fault-tested in either plugin:

- In [claude-dashboard at commit 2445873](https://github.com/uppinote20/claude-dashboard/blob/24458736e52f900a34f2fd000bc11c629ed77e7b/scripts/mod/register.tsx#L83),
  `runRenderer` retries a missing Node executable using one cached lookup. `renderLines`
  catches renderer errors; `keepOrWarn` retains the previous lines, showing a warning only
  when there are none. The inference for Atlan is to preserve useful output but visibly label
  saved evidence: retained content alone does not establish freshness.
- In [side-pane at commit 66fe8a1](https://github.com/xixiaofinland/side-pane/blob/66fe8a1ee05ff2288b174adcbec73bdc0ec8449c/plugins/side-pane/hooks/register.tsx#L54),
  `open` checks whether placement succeeded and offers a one-time fallback toast. Its tool
  handler rejects non-string content and reports truncation. `ui.close` records a user's
  dismissal so later updates do not reopen the pane; restored content carries a restored
  flag. Atlan should keep equivalent fallback/dismissal behavior and explicit truncation.
  The storage path has no catch, so this source does not establish storage-failure recovery.

## Interaction contract

| Moment | What the user sees | Control and evidence |
| --- | --- | --- |
| Start | A short explanation of the intended outcome, the supplied source and the next step. | Explain how to narrow the scope or keep work local. Reuse existing authorization; orientation is not a second permission gate. |
| Discovery | A compact inventory separating dashboard definitions, query history and executable data. | State which sources will contribute and which are held. Use actual counts; a query shape is not a verified metric. |
| Scope | The selected report/page or All selection, when relevant. | Honor an existing choice. Ask only for a missing decision that changes the work. A changed scope uses the existing invalidation rules. |
| Build and check | Current phase, real completed/held counts and the next meaningful event. | Keep the same saved run. Show an actual first result when available; do not promise a percentage, ETA or control the runtime cannot support. |
| Preview | A small sample of actual metrics and saved SQL in chat, with further detail available. | Name meaning, calculation, population, source and check state. A file link or hidden tool result is not the preview. |
| Destination | Accessible existing workspaces and the reason a destination is needed. | Select or reuse an existing workspace. Never attempt workspace creation through CLI, MCP or guessed API routes. |
| Publication | The destination and checked create/revise/reuse/held counts. | Preserve current authorization and release policy. Clearly distinguish local, published and verified readback states. |
| Ready to ask | Verified context counts and a few questions supported by that context. | A chosen question retrieves the published definition and executes through the selected data path. Do not invent unsupported dimensions or time bases. |
| Access failure or interruption | What remains saved, what is blocked and one next action. | Keep local preview work useful. Resume the saved stage after actual recovery; do not rebuild or publish again solely to recover the UI. |

An opening might read:

> I'll inspect this folder for dashboard definitions and query history, then show the
> metrics and SQL they support. You can narrow the scope or keep the results local.
> First, I'll check what is available.

Adapt that text to the user's actual request and known choices. Do not repeat the complete
plan on every phase transition. A source already selected by the user needs acknowledgement,
not another source picker. A request already authorizing publication retains that scope.

The useful reveal is a real, inspectable metric or answer. Avoid celebratory claims before
checks finish. Plain text must preserve failures and held coverage even when a richer view
looks complete. The final answer keeps enough actual content to stand alone when progress
messages collapse, without repeating the entire execution narrative.

## Surface contract for this iteration

| Host | Required baseline | Optional presentation | Limit |
| --- | --- | --- | --- |
| Claude Desktop Code tab | Native chat, available host choices, actual metric/SQL preview and saved-run recovery. | A self-contained HTML snapshot opened through supported local viewing tools. | The snapshot is not a live native pane. An open action must succeed before claiming it is visible. |
| Claude Code CLI | Short phase updates, native choices/task display where available, terminal-readable metric/SQL content. | The same HTML snapshot in an available browser, with text remaining sufficient. | Narrow terminals, headless runs and unavailable browser tools must not block the workflow. |
| Codex | Native chat and choice tools when available, with equivalent scope, preview and recovery behavior. | The same HTML snapshot through supported host viewing tools. | Claude mod APIs do not establish a Codex pane capability. |

The snapshot may provide local tabs, filtering, expansion or copying when implemented and
tested. It must show its snapshot status and must not imply live engine updates. It must not
offer a Publish, Cancel or Change scope button that only changes the page while leaving the
actual workflow untouched. Required decisions stay with the existing host workflow.

Native Claude panes are a separate implementation choice. Official mods support panes,
buttons and text fields in the terminal and Desktop Code tab, with different placement rules
for narrow terminals. Drawing does not appear in print mode, SDK or cloud sessions. Skills
alone do not provide this interface. See [mods overview](https://code.claude.com/docs/en/plugins/mods/overview)
and [interface behavior](https://code.claude.com/docs/en/plugins/mods/interface).

Repository rules prohibit hooks in the Analytics add-on. A native adapter would need an
explicit architecture decision using the existing mod exception, plus the required security
review for mod-hook changes. This iteration changes no hooks. All core behavior must remain
usable without a mod. Claude Artifacts also have account, provider and policy requirements;
do not infer availability from a local HTML file. See [artifact availability](https://code.claude.com/docs/en/artifacts#availability).

## Initial acceptance gates

These are proposed gates. Record observed results before claiming they passed.

| Gate | Evidence needed |
| --- | --- |
| Orientation precedes expensive generation | The visible first response names the source, outcome and next step before engine preparation/generation begins. |
| User choices survive | A scope change and interruption/resume test preserve the chosen scope and appropriate checkpoints. |
| Useful content is visible | Screen or terminal capture contains actual metric definitions and SQL before destination selection, not only a helper output or link. |
| Progress is truthful | Displayed counts and states match the saved run; no fabricated percentages, ETA or completion status. |
| Existing workspaces only | Tool traces contain workspace reads/selection and zero workspace-create attempts. Missing access preserves local results and gives one supported next step. |
| Rich views are optional | A narrow CLI or unavailable panel/browser still completes the same decisions in text. Every displayed control has an observed effect. |
| Publication remains correct | No unauthorized write; reported releases match complete Registry readbacks. Local/held items are not described as published. |
| First-time use improves | The prepared study measures comprehension, control, first useful output, reading load, interventions and completion against the current build. |

The [study protocol](analytics-first-use-study.md) defines the comparison and blank observation
sheet. No participant result is inferred from passing unit tests or a source review.

## Implementation verification

The working prototype is Analytics 0.6.8 on branch `codex/analytics-first-use-experience`,
based on 0.6.7 at `efeac77`. The engine algorithm and eligibility rules are unchanged. Runtime
setup now creates its virtual environment with interpreter symlinks, preserving standalone
Python library linkage. The read-only presentation adapter consumes saved project checks,
matching release receipts and engine checkpoints. It cannot export provisional cards or
advance a run.

### Reproduce the prototype

```sh
python3 tools/analytics_ux_prototype.py --out /absolute/new-output-directory
```

The generator refuses a nonempty destination. It creates four clearly labeled synthetic
replays: an early engine proposal, schema-checked local definitions, interruption, and denied
workspace access. Each has chat, expanded details, JSON and HTML. Participant inputs contain
a Power BI model, query history and CSV. Independent integer/Decimal reference values live
in an observer-only folder, outside those inputs. These replays seed state; they do not run
an engine or claim real SQL execution/publication.

From an existing real run, the portable adapter is:

```sh
python3 plugins/atlanai-analytics/skills/atlan-analytics/scripts/project.py \
  --run-dir /absolute/saved-run experience --format chat
```

Use `--detail` for SQL/source evidence, `--format html` for a timestamped snapshot, or
`--format json` for a supported host renderer. `--engine-dir engine` resolves inside the
selected run. An external legacy engine directory is outside this adapter's boundary.

### Observed hosts

| Host and mode | Observed result | Limitation |
| --- | --- | --- |
| Claude Desktop 4.23118.0, Code tab, Opus 5.5 Medium, Auto | A fresh synthetic folder survey found the model, five history entries and six CSV rows; Keep local and pause after survey were honored. A second prompt rendered the seeded checkpoint's actual calculation, population, source and schema-only status in chat. Tool evidence points to the current checkout's skill/helper. | Installed plugin metadata still says 0.6.6 while `readFromFolder` points to the 0.6.8 working tree. This is a development-path trial, not a clean install. The opening explanation was missed; first visible text was "Now the helper's location and survey." A shell separator caused one failed reference read, then a direct read recovered. |
| Claude Code 2.1.296, noninteractive plugin eval, Opus 5.5 | Earlier comparisons and a later matched six-case comparison loaded isolated 0.6.7 and 0.6.8 copies with the gateway emulator. The later comparison used self-contained grader references. Targeted candidate follow-ups tested opening, handoff and workspace routing. | These are presentation checkpoints, not an interactive terminal or a full generation/publication run. Follow-ups do not establish a fresh six-case pass. Native terminal pane and narrow-screen actions remain untested. |
| Codex CLI 0.154.0, default configured model, read-only execution | The current checkout's skill and experience helper rendered the seeded checkpoint as five metrics/five queries, zero published, with calculation, required population, source and schema-only status. | Explicit development skill path, not plugin discovery or a clean install. Configured MCP startup warnings appeared even though no Registry tool was called. The plugin does not control that host startup output. |
| Codex in-app browser | Opening the generated local HTML was attempted. | Browser policy rejected the `file:` URL. No alternate serving/browser route was attempted. Browser disclosure/filter/copy and narrow-layout behavior remain unverified; the later Node tests cover DOM-event logic only. |

The Desktop session used a requested pause after survey, not an injected process crash. The
access-failure replay and behavioral cases used synthetic state, not changed real credentials.
No fresh Registry writes, agent registration or workspace creation ran in this verification.
A separate fresh live Trident read retrieved a previously released synthetic query, whose
local execution returned `118681`. This verifies that retrieval and local answer path; it
does not verify publishing new context.

### Desktop generation follow-up

A subsequent Code-tab rehearsal using the same host/model/mode and working-tree plugin
completed generation from a separate synthetic model, five history queries and six CSV rows.
Its opening explained the local outcome before reading sources. Five metrics and five queries
passed local checks and exact SQL/example execution; nothing was published. Shipped units
returned 60, shipping cost 189 and cost per shipped unit 3.15. The model lacked physical
lineage, so the host used independently available history SQL and disclosed that limitation.

The raw session has a 12h39m idle/tool gap. From the resumed active segment, proposals were
available after 3m22s, numeric evidence appeared in chat after 6m06s, and the first complete
metric sample appeared only in the final reply after 6m45s. These milestones are not total
processing latency or a matched timing comparison. The final reply remained too long.

Two provisional previews failed because the adapter assumed C5's query-layer hash protocol
for valid M1 output. Producer-specific verification now accepts that evidence and rejects
tampering; replaying the actual run shows five provisional metrics with no evidence warning.
The source and engine files stayed unchanged. This replay proves the adapter repair, not a
newly observed shorter time to first metric. Guidance now shows an already discovered source
measure before installing the runtime, explicitly untested and without invented SQL.

The host also chose a session scratch runtime. Its local executor has been rebound through
the existing helper to a persistent tested interpreter; engine provenance remains intact.
Guidance now chooses durable runtime storage, uses direct argv/functions, exports to a fresh
directory, and avoids speculative sign-in claims in local-only completion. A distinct Desktop
Python 3.12 bootstrap failure remains undiagnosed because its stderr was truncated. Structured
recovery now reports the exit code and a supported next action without leaking child output;
the successful fresh runtime tests do not establish that failure's cause.

There has been no fresh full Desktop run after every change. The concrete new publication
plan contained five synthetic metrics, five queries and seven links, with no revisions or
deletions. It was subsequently approved and completed as described below; the unfamiliar-user
study remains deferred as requested.
A subsequent natural-language local question in Claude executed the saved query afresh and
displayed North 31, South 14 and West 15 (60 total), explicitly labeled as unpublished drafts.

### Approved live publication and released-context answer

On 9 November, the current Codex harness used its live Atlan MCP connection to complete the
approved shipping plan in the existing personal workspace. All five metrics and five queries
were created at version 1, automatically released under workspace policy, and individually
read back at their complete released revisions. Five source-of links were created; two
dependency links already derived by Trident were reused. All seven were verified by readback.
There were no revisions, deletions, new workspaces or agent registrations.

The regional shipped-units query ranked first for the test question. A separate fresh release
read supplied its full SQL to the existing answer helper, which validated the saved release
receipt and executed against the recorded read-only DuckDB binding. The result was North 31,
South 14 and West 15, totaling 60, with no truncation. Dashboard parity remains unmeasured.
The reviewed missing ratio-policy metadata warning remains visible; the SQL's NULLIF guard
and local empty-population check are separate evidence, not Registry certification.

This was a staged, cross-host rehearsal: generation and the first local question ran in Claude
Desktop; approved publication and the released-context answer ran in Codex. A separately active
Claude session still required sign-in. Codex success did not establish Claude access.
The installed Codex inventory reported Analytics 0.6.8 and core 0.5.24; its CLI was 0.154.0.
The helpers in this test came from the working tree, including unreleased repairs. Claude
Code 2.1.296 reported Analytics 0.6.8 through its development folder. File fingerprints are
retained in the measurement record; version labels alone do not identify these uncommitted edits.

The live run exposed that presentation could show ten released items alongside a different
host's connection warning. Successful Registry reads now have explicit host/server-scoped
observations, separate from recovery state. Experience and journey accept the same explicit
host/server selection. Newer failures and owned sign-in tasks cannot be hidden by an older
read, and a Codex observation leaves Claude recovery byte-for-byte unchanged. Release status
stays visible even when the selected host needs access. The live Codex view reached Ready with
ten released definitions; the Claude view retains its sign-in requirement. This changes only
local evidence and presentation, not credentials, access grants or publication eligibility.

### Measured comparison

[The measurement record](analytics-first-use-measurements.json) retains synthetic replies,
word counts, grader verdicts, tool names where retained and exact host/plugin metadata.
It contains no participant results. Counts below use whitespace splitting, including code;
they are not reading times or equivalent information-density measurements.

| Same presentation checkpoint | 0.6.7 baseline, round 2 | 0.6.8 candidate, round 2 |
| --- | ---: | ---: |
| Opening | 73 | 56 |
| Missing workspace access | 248 | 125 |
| Final handoff | 328 | 77 |
| Requested SQL detail | 160 | 130 |
| Disabled connection recovery | 228 | 109 |
| Explicit new-workspace explanation | 213 | 158 |

The 77-word final handoff omitted the calculation/filter. After correcting the instructions,
the targeted third run returned 106 words with those facts, source/check status, two supported
questions and retrieval before execution. Shorter output alone was not sufficient.

On the same five-metric/five-query fixture, the unchanged five-sample renderer emits 237
whitespace tokens; the compact renderer emits 57, and expanded one-sample detail 86. This
compares different default detail levels on identical input, not equal information density.

The five earlier runs remain unchanged in the measurement record. Their all-grader case
passes were 0/6 versus 1/6 in round 1 and 0/6 versus 3/6 in round 2; the earlier targeted
handoff passed 3/4 graders. Those runs cost $3.37 in reported model/judge usage.

Manual review found both real failures and grader disagreements. Round 2's SQL detail kept
the query tokens, bindings and scope, yet its SQL grader failed. The final targeted answer
contained exactly the two supplied questions and made no execution claim, yet its handoff
grader failed. The original byte-exact SQL requirement was corrected to allow whitespace
wrapping before round 2, equally for both builds.

A later audit found a separate grader defect: `last_message` judges see the reply, not the
case prompt. Our SQL and question rubrics referred to expected content without supplying it
to the judge. This boundary is documented in [Claude Code's grader reference](https://code.claude.com/docs/en/plugin-evals#what-a-grader-can-look-at).
The six rubrics now include the expected SQL, questions and checkpoint facts, distinguish
historical checks/publication from actions in this turn, and set `focus: last_message`
explicitly. SQL tokens, filters, separate bindings, provenance and publication requirements
remain. Deterministic checks also reject altered SQL and shell/Registry calls. The prompt
and skill-trigger assertions were preserved. Vote-only reports cannot prove why each old
judge failed, so those results have not been reclassified as passes.

| Corrected-grader comparison or follow-up | Result | Interpretation |
| --- | --- | --- |
| Matched six-case baseline 0.6.7 | 0/6 cases passed | Same corrected graders as the candidate. |
| Matched six-case candidate 0.6.8, attempt 1 | 3/6 cases passed | Detail, no-workspace preservation and interrupted recovery passed. |
| Candidate attempt 2, opening only | Passed, 47 words | Loaded the skill and produced the orientation without reference or Registry calls. |
| Candidate attempt 2, final handoff only | Failed 1 of 8 graders, 122 words | The real omission was retrieving the published definition before SQL; the sample and questions were correct. |
| Candidate attempt 2, new workspace only | Failed 2 of 7 graders | Skill routing and concise explanation still failed. The UI/admin route and no-creation boundary passed. |
| Candidate attempt 3, final handoff only | Passed all 8 graders, 142 words | Attributes checks to the earlier run and explicitly retrieves published definitions before SQL. |

The third handoff attempt was the last targeted retry. Workspace routing and verbosity remain
unresolved after two corrected candidate attempts. No workspace-create call occurred. There
was no fresh six-case sweep after the latest instruction changes, so this is not a 6/6 result.
Added deterministic graders change score denominators; raw mean scores are not comparable
with the earlier rounds. The 11 recorded eval runs total $5.24 in reported model/judge usage.
These runs had no execution error or partial result. Retained trace availability is recorded
per case; the first round retains only replies and grader evidence. A passing checkpoint is
not evidence of first-time user comprehension or a completed live task.

### Checks and remaining gates

- The latest full-runtime `make check` passed 375 tests with zero skips, plus 116 analytics
  and 52 history helper self-checks. Focused presentation tests cover stale/held evidence,
  exact receipt matching, privacy filtering, path boundaries, read-only behavior and fallback.
- After the final bootstrap-recovery and description-display edits, 29 focused tests passed.
  Detailed chat now preserves the complete metric description; a shortened compact
  description ends visibly rather than cutting a word without notice.
- Python 3.12 runtime acceptance passed 34 tests. These later runtime checks replace the
  earlier incomplete check that had 21 optional skips and six missing-runtime failures.
- Four Node 22.19.0 tests execute the actual generated inline script with a minimal parsed
  DOM. They cover combined search/status filtering, count updates and reset, empty/unmatched
  results, successful copying, and focus/whole-text selection when Clipboard is denied or
  unavailable. These are DOM-event logic tests, not browser layout or permissions tests.
- Strict Claude validation of the Analytics plugin passed. Generated manifests and lint
  passed; the candidate version is 0.6.8 and the core stays 0.5.29.
- Integration review fixed two additional cases: a missing run-relative engine path now
  gives a visible warning; mixed released/held completion directs questions to released
  definitions while preserving the held items. A blocked engine also remains blocked when
  prior release receipts exist. None of these fixes changes engine eligibility.
- Both the earlier existing-release answer (`118681`) and the new approved publication and
  released-query answer (60 shipped units) passed. A clean three-host install, uninterrupted
  single-host journey after all changes, participant study, matched first-use timing and
  browser/layout verification remain open. The current evidence does not meet every acceptance gate above.

### Smallest remaining implementation and validation plan

1. Keep this one adapter and the existing setup/publish/ask route. Pilot the prepared study
   after a clean install of the candidate; repeat the full Desktop run with all repairs loaded
   and retain the complete tool trace. Orientation now appeared in the observed follow-up.
2. Test the HTML controls in an allowed browser, including real Clipboard permissions,
   dismissal and narrow layout, then the same sequence in a narrow interactive CLI. Node
   DOM-event tests already cover filter/copy logic; chat must remain sufficient.
3. The approved new publication and fresh released-context question are complete. After
   Claude's own sign-in is resolved, repeat the uninterrupted journey there with all changes
   loaded. Do not use Codex's readiness evidence to mark Claude connected.
4. Run the five/six-person study and decide from task/control/comprehension evidence. A native
   Claude pane is a separate follow-up only if that evidence justifies it; it must use the
   repository's existing mod architecture and security review, not new Analytics hooks.
