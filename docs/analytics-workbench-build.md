# Analytics UI implementation

Superseded in Analytics 0.6.25. The native pane, its companion mod and the live local preview were removed. Setup now shows progress as a six-step checklist in the host's task list, read from the saved run with `project.py checklist`. The rest of this page describes the removed design and is kept for history.

Updated 9 November 2026 for Analytics 0.6.15 and Analytics View 0.1.2. Setup now opens an
actual host view after inspecting the sources, before the business-focus question. It
explains the material and possible analyses, asks the business focus and intended outcome
separately, then retains one metric while its checks and progress change.

## Host surfaces

| Host | Actual surface | Verification |
| --- | --- | --- |
| Codex desktop | Live local Browser panel | Rendered source overview and selected metric; evidence disclosure, catalog search and a saved selection/pause update worked without reopening |
| Claude Desktop Code | Native Analytics plugin pane | Actual show tool returned displayed=true; pane visibly rendered; evidence expanded and Ask for details filled an editable prompt |
| Claude Code CLI, classic renderer | Native framed dock above the prompt | Actual interactive PTY rendered the pane and reported placement |
| Claude Code CLI, wide fullscreen | Native sidebar beside conversation | Interactive 180-column PTY with CLAUDE_CODE_NO_FLICKER=1 rendered the source summary, metric, checks and buttons beside the conversation |
| Unsupported/headless host | Concise native chat | Placement failure is explicit; a prepared HTML file is never reported as visible |

CLI layout follows [Claude's pane API](https://code.claude.com/docs/en/plugins/mods/interface).
For one fullscreen session use `CLAUDE_CODE_NO_FLICKER=1 claude`. `/tui fullscreen` also
works but saves the host preference; the plugin never changes that setting automatically.
The renderer is a research preview. Desktop Code is tested; Claude web chat and Cowork are
not claimed as tested native-mod surfaces.

## Implementation

`project.py experience --format panel` writes an escaped local HTML snapshot and returns a
bounded text payload for the native pane. It explicitly returns displayed=false until an
actual host tool opens it. Source-understanding prose is stored separately from verification
and bound to the current source signature. Changing surveyed sources invalidates stale prose.

The skill's setup/render.md connects this payload to the actual host. The new optional
Claude-only `atlanai-analytics-view` mod registers a presentation tool and a reopen command.
It keeps only bounded text in session memory, expands evidence locally and fills the native
prompt for requests. Submitting that prompt remains the user's action. Dismissal survives
subsequent updates within the session; `/analytics-view` explicitly reopens it.

Codex uses a standard-library loopback preview process owned by the host terminal task.
The page follows saved run evidence every 2.5 seconds, preserving open disclosures, search,
status filtering and focus across updates. A failed refresh labels the retained evidence as
stale. Copy-request buttons explicitly copy text for the user to submit in chat. They never
claim to pause, execute SQL, or publish themselves. Closing a panel does not discard the run.

The existing journey persists domain, purpose, selection, pause and local-only choices.
The selected definition retains its exact identity. Changed definitions cannot inherit older
checks or release receipts. Presentation never manufactures execution, authentication or
publication evidence. The host continues the existing engine and publication procedures.

## Security-relevant surface

The preview listens only on 127.0.0.1 with a selected unused port. It serves only `/` and
`/snapshot`, checks Host and Origin and rejects cross-site fetches. It exposes no directory,
raw source route, write endpoint, credentials or CORS access. Source text is escaped and the
page uses a hashed script CSP with no external assets. Requests and errors do not log source
content. Stop the owned preview through the host task controls when finished.

The companion mod adds no MCP server, dependency, network access, file access, process
execution, permission hook, auth change or Registry write. Its registered hooks concern only
its own tool, command and pane. Presentation failures retain chat fallback. The portable
Analytics add-on remains hookless. All manifests are generated from plugin.toml.

## Validation and installation

- `make check`: 406 tests, 21 optional skips, and 171 helper self-checks pass.
- Native mod tests: five pass, covering payload bounds, actual rendering, dismissal,
  same-pane updates, unplaced fallback, evidence expansion and editable prompt actions.
- Fresh installed-skill setup in Claude Desktop inspected synthetic Power BI model/history
  sources, opened the pane automatically, and displayed the first native business-focus
  question beside the source explanation. The test stopped there without running the engine
  or contacting Registry. This separately verifies skill-to-pane wiring, not only a direct
  call to the presentation tool.
- Read-only preview tests cover source-summary invalidation, escaping, Host/Origin/cross-site
  boundaries, unsupported routes and writes, symlink rejection, state updates and no mutation.
- Strict marketplace, Analytics manifest/skills and companion validation pass. Full
  `make validate` remains incompatible with this installed CLI's `--profile directory` flag;
  it is not counted as passed.
- Analytics 0.6.15 is installed in Claude Code and Codex; Analytics View 0.1.2 is installed
  in Claude Code. Runtime files match the repository source byte for byte. Start a fresh
  session to load the new versions. Existing sessions may retain the previous skill.

Host checks used synthetic shipping sources and local saved evidence. They establish
rendering and interaction, not new production SQL execution or publication. The earlier
DuckDB rehearsal below establishes changed answers against changed synthetic data, with
simulated Registry transport. No unfamiliar-user delight study is claimed.

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
