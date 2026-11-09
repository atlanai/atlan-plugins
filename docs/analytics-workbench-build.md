# Analytics native journey implementation

Updated 9 November 2026 for the Analytics 0.6.12 pull request. This supersedes the first
snapshot-only slice. The default product surface is the existing Claude/Codex conversation.
There is no custom chat shell, palette, font system, new dependency, server or hook.

The 0.6.12 candidate incorporates current main, including its metric relationship and batched
read changes. Final repository checks pass: 401 tests with 21 optional skips, plus 116 and 55
helper checks. Strict Analytics manifest and skill validation pass. Earlier version-specific
measurements below remain historical evidence; the installed 0.6.10 copies were not replaced
as part of preparing this pull request.

## Complete interaction path

| Requirement | Implementation | Evidence |
| --- | --- | --- |
| Outcome before setup | Short opening, then authorized work | Shared setup and skill guidance; behavior coverage in the first-use suite |
| Discover and narrow | Existing cached survey and source/page scope | Project survey/scope tests |
| Understand the use case | Source summary, domain question, then a separate tailored purpose question; free text and known-answer reuse | Sequential intent, restart, CLI and behavioral tests |
| First useful reveal | One actual provisional or checked metric, with population and honest status | Experience tests and native details behavior eval |
| Build and check | Existing pinned engine and recorded schema/SQL checks | Engine acceptance plus local first-answer rehearsal |
| Retain the metric | Exact identity persisted in interaction.json; missing/ambiguous identities require repair | Restart, changed-scope and beyond-first-page regressions |
| Inspect at any point | Native chat/details; more candidates on request | CLI, behavior and browser tests |
| Pause or keep local | Durable intent separate from engine/check/release evidence | Restart and no-mutation tests |
| Recover and resume | Existing auth flow plus saved checkpoint routing; holds remain | Auth/hold and canceled-checkpoint regressions |
| Choose destination and save | Existing workspace, reconciliation, release and readback workflow | Publisher acceptance and receipt validation |
| First answer | Fresh released context followed by existing read-only executor | Real DuckDB answers change after source data changes; mismatched release refused |
| Native presentation | Normal host messages, questions and task controls | Claude behavior eval; no custom UI theme in product files |

## How it is connected

`project.py journey status` returns a compact chat view, saved mode and one host continuation.
`domain` and `purpose` save the user's business focus and intended outcome. `select`, `pause`,
`keep-local` and `resume` persist explicit choices. `details` and `status`
are read-only. Presentation does not manufacture engine actions, authentication, publication
or successful SQL. The host executes the corresponding existing stage procedure.

`experience` automatically uses the saved metric identity. Selection is resolved against the
full bounded inventory before the first 100 items are rendered. A missing or ambiguous
selection is visible and cannot silently become a different metric. Changed definitions do
not inherit an older revision's execution checks or release receipt. Provisional-to-final
merges use the engine's lineage, with an explained successor selection by the host.

Pause records intent; the host also stops scheduling or safely cancels its running task before
claiming work stopped. Keep local never withdraws an existing release. Resume does not clear
holds, invent access, or authorize an unapproved destination. Completed local stages advance
to their next local stage, never to Ready for questions without release receipts.

Optional HTML is a manually refreshed evidence document, using system fonts/colors and
standard browser controls. It preserves SQL/source disclosure, search, status filters and
copy-to-chat feedback. It has no network access or workflow mutation buttons. It opens only
on request; chat remains useful if dismissed or unavailable.

## Validation and limits

- Repository `make check` passes: 381 tests, 21 optional skips, plus 116 and 52 helper
  self-checks. The final details-only adjustment was also verified by the focused suites.

- Nine new journey tests pass, including the joined local first-answer rehearsal.
- The joined rehearsal executes survey, history intake, CSV preparation, binding, schema
  checks, metric/query SQL checks, restart, receipt validation and fresh answers. Its input
  data and authored definitions are synthetic; Registry transport uses complete test responses.
- The existing pinned engine's all-gates acceptance test passes, including resume, export and
  the existing publication plan. Authored gate responses in that test are deterministic fixtures.
- Claude behavior eval `ux-native-local-details` passes all graders. It uses native chat, retains
  the chosen metric, shows requested evidence and respects local-only mode and panel dismissal.
- Browser checks pass for disclosure, search, status filters, copy feedback/fallback, mobile
  width, dark mode and early/paused/access-denied snapshots, with no page errors.
- Strict analytics manifest and skills validation pass. The full repository validation command
  remains incompatible with this machine's Claude CLI `--profile` flag; it is not counted as passed.

This is implementation and local/behavior validation, not a claim that an unfamiliar-user
study or a fresh production publication was completed. Version 0.6.10 was installed locally
in Claude Code and Codex, and both installed plugin trees matched the source files. The local
first-answer rehearsal uses simulated Registry transport; it does not establish live
publication readiness.

## 0.6.9 intent and native UX update

After lightweight inspection, fresh setup always establishes two independent answers:
business area first, intended outcome second. Questions render sequentially through an
available native question tool, with ordinary chat as the fallback. Clear page/file scope
never silently chooses a business focus. The second question uses the selected domain.
Existing explicit answers are retained, and both answers known means reflect and continue.

The local journey stores both answers and supplies the next missing question, including after
restart. Changing the domain clears the old purpose. Pause, local-only mode, details requests,
held evidence and existing releases remain intact. The source summary, goal reflection,
relevant metric preview, details, progress, save decision and answer all have explicit native
render points in setup/workbench.md. No custom UI dependency or visual theme was introduced.

Six intent regressions cover sequence, persistence, domain changes, local access recovery,
invalid answers and CLI behavior. The full SQL first-answer rehearsal now records both intent
answers after survey. Three conversational cases cover the initial domain question, tailored
purpose question and carrying forward already-known intent to the relevant source metric.

Validation for this update: repository checks passed with 381 tests (21 optional skips), plus
116 and 52 helper self-checks. The domain and purpose conversation cases passed against the
final skill text. The known-intent case also passed after its reply-only style grader was
grounded in the evidence-only stopping point; the earlier generic next-step grader incorrectly
rejected a concise explanation without a follow-up. Behavioral evidence is in
`evals/atlan-analytics/results/20261109T145923Z-intent-069-final/` (domain/purpose) and
`evals/atlan-analytics/results/20261109T150117Z-intent-069-known-grounded/` (known intent).

## 0.6.10 project understanding before the question

The post-inspection step now explains the observed source types and business subject, how
the material contributes, and two or three plausible analyses before asking what matters
most. Counts alone and generic analytics-coverage language do not meet this requirement.
The explanation belongs in the native question's visible prompt, so it remains available
when commentary collapses. A text fallback keeps the same explanation with the question.

The opening retains sample-data and incomplete-inspection limitations and presents analyses
as possibilities, never computed findings or validated joins. The second purpose question
stays short; it does not repeat the whole overview. Added a synthetic construction-project
behavior case and strengthened the shipping-domain case to check this understanding step.

The 0.6.10 construction-project and shipping-domain behavioral checks passed, as did the
known-intent and purpose regressions. Reply-length checks for the two summary cases now use
a deterministic word bound because the LLM style judge produced inconsistent length verdicts.
Repository checks passed (381 tests, 21 optional skips, plus 116 and 52 helper checks); strict
skill validation and lint passed after the final wording adjustment.
