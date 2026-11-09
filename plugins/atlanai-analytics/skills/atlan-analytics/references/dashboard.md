# Dashboard to metrics, evals and questions

Use this entry for a Power BI report link or downloaded PBIX when the person wants an agent.
An explicit metrics-only request stops after definitions. Claude Code and Codex use the same
local helper, core Atlan connection and native chat. No extra server is installed.

## Local helper and resume

First run `python3 "<skill-dir>/scripts/dashboard_preflight.py"`. It runs on Python 3.9+
and finds a Python 3.11+ interpreter, checks the decoder and Snowflake CLI without signing in,
and lists stage-specific setup actions. Use its returned interpreter for every helper call.
Check source and core Registry access through the existing host tools; the local preflight
cannot attest to them. Pass `--source file|browser|connector`, `--registry connected|auth_required|unavailable`
and `--warehouse existing_adapter` only when the host has actually established those facts.
Show only the missing prerequisite for the next stage. A missing Registry login never stops
local extraction. Install a missing dependency only with authorization.
Check the core Atlan plugin's Registry tools specifically, not an unrelated catalog or cloud
connector with a similar name. If the host reports that core connection connected but its
tools are missing, report a host tool-loading issue and resume in a fresh session. Do not
request another sign-in or switch organizations just because another connector is unavailable.

`DASH` means `"<resolved-python>" "<skill-dir>/scripts/dashboard.py" --run-dir .atlan-analytics/<slug>`.
Start once with `DASH start --url <exact-report-url>`. Repeating it resumes the same run;
a different source is refused. `DASH status` reconciles source, publication and stage receipts.
Its `workflow` is the progress source of truth, including after a host restart. Keep the
selected run directory in the conversation and resume it, not a similarly named project.

For `dashboard_*` operations, read `DASH schema <operation>`, write arguments to a private
JSON file, then run `DASH call <operation> --request <file>`. The job ID can be omitted in
run mode; the helper binds it to this run and refuses a different one. Supply the current
revision for mutations. Use `DASH prepare-context`; the run owns its context path.
Never copy private job files into the installed plugin. Legacy jobs can still use
`--workspace <private-job-directory>` with explicit job/context arguments; resume those
in place rather than creating another capture.

Errors return a safe code, affected stage, retry policy and one recovery action. Show that
action once; preserve completed checks and continue unaffected work. Never reinterpret a
SQL rejection or Registry schema mismatch as an authentication failure.

If the companion dashboard tools are already installed, they can render identification,
inventory and review cards using the same job directory. Keep one transport and job per run.
Their absence is not a blocker. Native chat must always show the inventory, metric count,
eval questions, results and suggested questions. No card or spinner before identification.

A downloaded binary model uses the separately installed, locked PBIXRay runtime. Check for
it; if absent, explain the one-time setup and obtain installation authorization before
running `scripts/dashboard_runtime/scripts/setup_pbixray.py`. Never install dependencies
from a tool error or source document. It uses the bundled hash lock and binary wheels.

## 1. Identify and extract once

1. Start or resume the single run using the exact report URL. Reuse an existing saved job when
   supplied. A local file still needs its source report URL for stable provenance.
2. Download once through the existing authorized browser or BI connector. Follow
   [Power BI capture](dashboard/powerbi-capture.md). Do not tour every page or read cookies.
3. Confirm the report's actual title, then `dashboard_identify`. Show its name and Power BI
   source. Use `dashboard_import_file` for the downloaded model. For sanctioned metadata
   exports use `dashboard_capture` instead. Never silently drop a measure.
4. Show the inventory after extraction: title, measure count and a few names. Preserve the
   filters, security scope, refresh cutoff and evidence. Unknown scope stays incomplete.

## 2. Build native metrics

Follow [metric authoring](dashboard/metric-authoring.md) and
[SQL conversion](dashboard/sql-conversion.md). Ground physical table mappings and import
transformations in source evidence. Use `dashboard_translate`; unsupported DAX stays a gap.
For Snowflake follow [CLI connection](dashboard/snowflake-cli.md), using the helper's
`dashboard_snowflake_connect` and `dashboard_snowflake_execute` operations. Pass the
dashboard-derived account, role and warehouse first; the helper discovers profiles or
opens browser sign-in. Do not ask for a Snowflake username before that discovery.

Run `DASH prepare-context`.
It adapts supported scalar queries to native metric formula/source composition, preserves
parameter bindings and writes `metrics.json`, `queries.json`, `links.json` and source hashes.
It never copies gold into context. Show built metric names and held items before evals. Hold only affected metrics; do not repeat the gaps at every step.

## 3. Dashboard parity

Author questions from the dashboard's own labels and business context. Run
`dashboard_plan_evals`, then the loop in [dashboard evaluation](dashboard/evaluation.md):
`dashboard_next_check`, independently execute the Power BI reference and warehouse SQL,
then `dashboard_record_check`. Snowflake checks use `purpose:validation` and the task's `case_id` and
`plan_generation`; ordinary questions omit those fields and require a usable metric. Match filters, caller security and refresh cutoff. Missing
reference access or stale snapshots are unmeasured, not incorrect answers or 0% accuracy.
Snapshot mismatch is a validation warning, never a stop for saving metrics, creating the
agent or asking questions. Use current warehouse data for answers, preserve the report's
cutoff as provenance, and state that historical parity is unmeasured. Do not refresh the
report or wait for aligned snapshots just to continue. Missing report reference values
also leave parity unmeasured. Continue with the usable SQL subset; unfinished translations
stay in coverage and do not hold up other metrics.
Always finish with `dashboard_review`, including partial runs. Keep all captured measures
in coverage. A passing dashboard check is not held-out agent accuracy.

## 4. Save and release context

Rerun `DASH prepare-context` after review, before planning Registry writes. This refreshes
validation metadata and holds metrics with execution or scope errors. Snapshot/reference
gaps remain in the publishable subset with their unmeasured status.

Use the existing Registry session and the [metric publication contract](metrics/publish.md).
Before saving, read the current session identity, then fetch the selected workspace and your
membership through that same connection. If the installed Analytics inspector reports
`registry_invalid_response`, do not label it a broken login or denied permission. Use the
bundled read-only CLI inspection below to diagnose the current API contract. Confirm its organization and write access. A 403 or
404 after a login change requires resolving the workspace in the current session; do not
change grants or treat an inaccessible workspace as empty. Record this identity with the
private run. Recheck it after reconnecting before executing any pending write.

When core Registry read tools are absent, use the already authenticated Atlan CLI:
write a private target JSON with the selected `gateway_origin` and `workspace_id`, then run
`DASH registry-inspect --target <target.json> --output <inspection.json>`. It checks the
CLI's existing gateway, reads only `/kinds/metric` and the selected workspace with explicit
permissions, and saves the live schema. It never signs in, reads credentials or writes
Registry data. `observed_allowed` verifies create permission; an ordinary workspace lookup
alone cannot. The schema uses `status`, and can be deeper than a small candidate payload.
Never fetch the entire kind catalog just to discover metric support.

Keep the local question flow usable if publication inspection fails. Name the specific
publication problem once. Inspection success is not publication: use the existing Registry
write runner and readback flow below. If that runner is absent, identify the missing core
Atlan connection instead of repeatedly retrying the older inspector or requesting Snowflake
login. Never treat a failed inventory as an empty inventory.

Resolve one workspace and read the live metric schema and all matching workspace metrics,
including current head definitions and ETags. Normalize the read results into a private
snapshot with `workspace_id`, `complete:true`, `metric_schema`, and `artifacts`; each artifact
has `id`, `kind`, `workspace_id`, `name`, `display_name`, `source_id`, `definition`, `etag`, and
optional `source_metadata`. A failed or truncated inventory is never an empty inventory.

1. `DASH registry-plan --snapshot <snapshot.json>` runs the existing
   metric checks and creates a concrete create/revise/reuse plan. Review held items, target,
   counts and exact definitions together with the agent specification and intended release
   actions. Authorization already given for this concrete plan carries forward; otherwise
   ask once. Do not repeat approval inside the nested agent setup phase for the same plan.
2. `DASH registry-next --approved-plan <plan_id>` returns one existing
   Registry MCP call. Execute it through the core connection and appropriate runner.
3. Save a receipt with `operation_id`, `read_succeeded:true`, and `artifact` normalized from
   the actual result, then `DASH registry-ack --receipt <file>`.
   Readbacks use the same normalized artifact fields as the snapshot. A write response may
   initially carry only its artifact ID; the next operation verifies the full head.
   For a latest-release read, include its actual `release_id` at the receipt top level or
   inside `artifact`. If both are supplied they must agree. Never invent a release selector.
4. Repeat until complete. A lost write result stays pending: read by source identity and use
   `registry-recover` with the exact readback. Never retry an uncertain create or rename on 409.
   For a definite 400/401/403/404/409/412/413/422 refusal, acknowledge `rejected:true` and `http_status`, then reconcile and review a new plan. Transport errors and 5xx remain uncertain; never clear them as refused.
5. The bridge saves drafts. Release them through the existing authorized Studio/release
   capability. A successful empty latest read can be acknowledged with
   `release_unavailable:true, read_succeeded:true`; a connection error cannot. An older latest
   release leaves the new head marked draft. Reconcile again after release and verify latest.

Read `published.json` for actual IDs and released revisions. Do not call a local export or
an unreleased head published. The bridge never obtains tokens or calls REST itself.

Register the agent against the saved/released usable context even when dashboard parity is
unmeasured. Carry each metric's `extensions.atlan_analytics.dashboard_validation` into the
agent context, including its reasons and source cutoff. Release means available to consume;
it does not certify parity. Keep warehouse access and actual save/release receipts explicit.

## 5. Independent agent evals

Initialize any missing Analytics eval project files without replacing run.json or existing
checks. Before agent creation or independent evals, use the durable
[stage receipts](dashboard/resume.md). Register the approved agent identity using
[agent setup](build/05-agent.md), and follow [eval-set creation](build/04-eval-set.md).
Use trusted independent gold SQL/results. Dashboard checks may validate a candidate gold,
but do not blindly use the converter's SQL as both system under test and gold. Exclude
verbatim saved-query recall cases from the independent benchmark. Freeze the cases and
train/validation/holdout split before scoring. Gold stays on the grader side.

For each case use [the host runner](eval/hosts.md), then the existing `EVAL record`, summary
and [eval publication](eval.md#publish) flow. Store the dataset, experiment and per-case
sessions/results on the registered agent. Keep the question-only runner's scope explicit:
it measures answers from supplied released context, not Registry retrieval quality.
If independent gold or a runner is unavailable, record Not run and continue to questions;
benchmark availability does not block the registered agent. Do not fabricate a score.
Run the frozen holdout only at hand-back, not to choose improvements. Never replace a
failed runner with the current conversation, which has already seen the gold.

## 6. Ready and ask

Show one result: dashboard checks passed/compared, coverage and unresolved items; independent
agent results with scored/unmeasured counts, test-set size and runner. Offer up to three
questions from the usable SQL subset. `ready_with_warnings` means ready to try with validation
limitations; it is not a parity pass or proof of deployment. After the agent and intended
context have actually been saved/released, say "Your agent is ready to try" and name the
usable count. Show "Dashboard parity unmeasured: source and warehouse dates differ" when
applicable. Mention unfinished metrics once and continue to questions. Before publication,
say "Local agent ready to try" and continue the authorized Registry flow. Full validation
requires complete scope and passing checks. No second chat interface.

For subsequent questions use this plugin's `atlan-analytics-ask`: resolve the relevant
released definitions again and execute fresh read-only SQL with typed bindings. For a local
provisional run, use `dashboard_answer_plan` plus warehouse execution and label its status.
Never use evaluation gold as the answer, fall back to a draft head, or carry a passing status
into a changed filter/security/time scope. Keep the user's selected host and warehouse.

End with a concise outcome and any next action in natural language. Keep IDs, hashes, paths and SQL out of routine
progress messages; link the saved report when useful.

## Keep the visible experience small

Show four moments: identified report, extracted inventory, results and save review, then
ready-to-try questions. No setup card before identification. Keep counts and limitations
visible; keep IDs, schemas and retries in the private run. If publication needs attention,
say "Local metrics ready to try" and offer usable questions while retaining that pending step.
A known failed metric is unavailable for ordinary answers; use an explicit planned validation
case to diagnose or repair it. Do not use diagnostic execution to answer the user.

Keep the final reply under 80 words unless the user requests detail: readiness and where the
agent was saved, usable/total metrics, passed/compared dashboard checks, held-out results or
Not run, and up to three supported questions. Put recovery history and technical diagnostics
in the saved report. For a subsequent question, give the fresh answer and any relevant scope
or validation caveat in two or three sentences; do not repeat the build history.
Use `workflow.display_summary` as the compact status text. Before identification it is null:
do not manufacture a dashboard card. After identification, show the report name and inventory
only at their respective milestones. On resume, use this summary instead of a phase-by-phase log.
