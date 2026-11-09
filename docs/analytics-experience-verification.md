# Analytics experience verification

9 November 2026 · Analytics 0.6.24 · Analytics View 0.1.9

The Claude Desktop view uses the host's Box, Text and Button components. It inherits
typography, theme and controls rather than rendering a scaled image. The default view
contains the business focus, purpose, three-stage progress, counts and one metric;
definitions and source details expand on demand. Discovery suppresses empty counters
and controls that require prepared data. CLI uses a compact dock; Codex uses its
browser presentation with an explicit chat fallback when local serving is unavailable.

## Requirement audit

| Requirement | Implementation and observed evidence |
| --- | --- |
| Live view | Bounded saved-state polling, stale recovery and dismissal tests; actual Desktop pane, CLI dock and Codex browser observed. Placement is read from `ui.panes()` after `ui.open()`; actual CLI tool returned displayed and following true. |
| Truthful progress | Waiting, paused and local states separated; resumed CLI handoff completed without changing saved definitions. |
| Early value | First execution-checked metric preceded bulk authoring in fresh runs on all three hosts. |
| Connection recovery | Current-host readiness, bounded recovery and stale/cross-host evidence regressions; successful current-host reads observed. |
| Definition reuse | Read-only reconciliation reused seven metrics and 33 queries without proposing creates or revisions. Complete definitions and scope differences are preserved. |
| Relationships | 25 typed relationships resolved; repeated definitions and links stayed byte-identical; second repeated plan was identical. |
| Check labels | Execution, review, held, parity and released counts remain separate; native view displayed nine executions and five pending reviews. |
| Readable evidence | Source and definition disclosures opened and closed in the actual Desktop application. |
| Quiet progress | Compact native tool summaries and concise workflow instructions. Workspace explanation requests have a dedicated route instead of loading the full publishing workflow. Behavioral caveats below remain visible. |
| PBIT support | Direct model/report reader with archive, encoding and malformed-input refusal; actual engine acceptance test passed. |
| Privacy preflight | Shared literal policy runs before local and engine authoring; fresh intake and engine-gate regression verified. |
| Accurate explanations | Each host answered the local numerical question; independent CSV and SQL calculations checked margin and population-exclusion impact. |
| Intent | Domain then purpose questions precede authoring; saved intent determines selected focus, including suppression of stale defaults. |
| Controls | Native disclosures, prompt-fill feedback, hide/reopen and dismissal tested. Action buttons prepare a chat request and explicitly require submission. |
| Useful finish | Local definitions support questions without a release; counts, check scope and remaining review are shown. Resumed CLI handoff preserved definitions. |

## Validation and limits

- Repository suite: 458 tests passed, 34 optional skips; 116 analytics and 55 mining self-checks passed.
- Native companion: 13 tests passed. Full strict plugin validation passed.
- Fresh accelerator runs reached a local answer in Claude Desktop Code, Claude CLI and
  Codex CLI across incremental versions. Final reload checks supplement those runs;
  these were not three clean runs performed entirely on the final version.
- Desktop visual/disclosure evidence is from View 0.1.7. View 0.1.9 retains that layout
  and adds correct placement reporting and discovery-state controls, verified by native
  tests and actual CLI placement.
- Latest full behavioral sweep: 7/12 cases, grader score 0.92. The dedicated workspace
  route subsequently loaded the skill in all three targeted runs; two passed every
  grader. The third correctly explained the UI/admin route but split the reply into
  two paragraphs, and its capability grader rejected it. Original verdicts are retained.
  These results do not establish perfectly consistent model wording.
- Independent same-rubric review accepted four earlier opening/handoff responses
  rejected by the suite. This is diagnostic evidence, not a replacement for the original results.
- Codex CLI setup used a workspace-write sandbox. Its local-server restriction meant
  browser rendering was tested through the desktop host's supported process. A visual
  pane is not guaranteed in every CLI environment.
- No real publication was performed solely to make testing green. Successful SQL
  execution does not prove dashboard value parity. Release paths are covered by tests;
  live reconciliation used read-only evidence.

The presentation feed is a bounded loopback read route. It has no Registry writes,
credentials or permission hooks. Source content renders as native text. No direct
dependencies were added for the view.
