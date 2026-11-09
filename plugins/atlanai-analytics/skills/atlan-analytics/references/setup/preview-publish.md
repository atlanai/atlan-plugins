# Setup stages 5 to 7: Preview, Publish and Ready to ask

`PROJECT` is the helper named in [setup](../setup.md). Read this file on entering Preview, including on resume.

## 5. Show the metrics before asking where to publish

Run `PROJECT experience --format chat` and display its one actual sample before the next
question. Include its name, meaning and honest check status; make SQL, source and detailed
checks available with `--detail`. With only queries, show one saved query as such. Preserve
SQL, filters and parameters in details; never invent a metric or an executed result.
Show one sample again in the final answer with factual published/local/held counts, even if
it appeared in commentary. This applies to success, Keep local and blocked sign-in outcomes.
The final answer must remain useful when intermediate progress collapses.
If a sample already appeared, show a material definition/check change before continuing.
A helper output or linked file is not a visible sample. Expand to five/all only on request.
Use a short contextual choice when a decision is missing: an existing destination or Keep
local. Mention Show details and Show more as available actions without repeating a menu after
every step. If publication was requested already, continue under its existing authorization.

**Show five:** run `PROJECT preview --limit 5 --format markdown` and display its text in the
same response. **Show all:** use `--all`. Neither action selects a workspace, writes to Atlan,
reruns extraction or restarts the workflow. Fewer/zero samples have explicit text outcomes.
**Keep local:** run `PROJECT journey keep-local`, preserve candidates and stop with an
unshared status. Do not call them Atlan-ready. This never withdraws earlier releases.
Draft question ideas must say that retrieval-based answers require publication first.
Do not claim that sign-in is needed unless this host actually returned an access failure.
Local-only completion needs no speculative authentication diagnosis or sign-in detour.

## 6. Choose the destination, reconcile and publish

Reuse an explicit existing destination or ask once by accessible workspace name, with Keep
local as an alternative. Explain it as where the team finds and reuses definitions. Never
offer or attempt API/CLI workspace creation. If none is accessible, retain the sample and
run, then explain how to request access. Only an explicit new-workspace request gets the
UI/admin route; refresh the list after the person resolves it. Fetch live kind schemas and existing definitions, then
run `MINE check` with that destination. Only now show actual create/revise/reuse/held counts.
If reconciliation changes a candidate, show the changed preview. Follow [publication](../metrics/publish.md)
for the concrete plan, missing authorization, idempotency, release policy and uncertain-write
recovery. Never retry an uncertain remote mutation blindly. Carry approved writes through
release and full MCP readback. Record receipts with `PROJECT publication` as the local
procedures describe. A draft or local plan is not publication.
Reusing everything still requires the visible preview before marking Ready. Explain count
changes: name any candidate kept only for validation, deduplicated, held or replaced by a
released definition, with its reason. Preserve those local candidates and coverage decisions;
do not silently drop SQL merely to avoid a publication decision. Reuse requires equivalent
meaning, population, dimensions and parameters, not just a similar metric name.

## 7. Ready to ask here

Keep one real sample in the final answer with its calculation, mandatory population/filter,
source and check status, alongside actual released/local/held counts and verified links.
End with two or three supported questions (fewer if needed), including history-derived ones
where useful. Say once that answers retrieve released definitions before running SQL.
Preserve data-connection and unmeasured-parity limitations. Do not invent a time basis or
dimension. Prefer the actual question stored on a released saved query. Add a period,
grouping or comparison only when its date basis, dimension and join are evidenced in the
retrieved definition; do not suggest an unsupported question with a caveat. Two supported
questions are better than filling the list with unverified ones.
Continue to a question already supplied; otherwise wait for the user's selection.
End with the supported questions and any material limitation; do not append a generic offer
or invite arbitrary questions whose coverage has not been checked.
Use [retrieval](../retrieval.md) for fresh Atlan context and fresh execution on every question.
