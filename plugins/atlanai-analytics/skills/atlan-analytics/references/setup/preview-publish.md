# Setup stages 5 to 7: Preview, Publish and Ready to ask

`PROJECT` is the helper named in [setup](../setup.md). Read this file on entering Preview, including on resume.

## 5. Show the metrics before asking where to publish

Automatically run `PROJECT preview --limit 5 --format chat`. Display its metric table and
saved SQL in an assistant message before asking the next question. Include up to five actual
metrics (all if fewer): name, meaning, calculation, model/history source and check status.
The helper includes a history-derived SQL example when available. With only queries, show
those as saved queries. Preserve SQL, filters and parameters; do not invent a metric or result.
This preview may exceed the usual short reply limit. An HTML view is supplementary.
Repeat the actual metric table and saved SQL in the final assistant answer, even if they
were shown in commentary. This applies to success, Keep local and blocked sign-in outcomes.
The final answer must be useful when the host collapses all intermediate progress messages.
If a draft preview was already displayed, show changed definitions or check status before
continuing. If none was displayed, render the full first preview now. Never infer that the
user saw tool output. Rendering or linking a file does not complete this conversation step.
Offer Publish, Show five, Show all and Keep local, using native choices when available.
If publication was requested already, continue toward the destination and final plan without
another intent question. Available preview actions still work immediately.

**Show five:** run `PROJECT preview --limit 5 --format markdown` and display its text in the
same response. **Show all:** use `--all`. Neither action selects a workspace, writes to Atlan,
reruns extraction or restarts the workflow. Fewer/zero samples have explicit text outcomes.
**Keep local:** preserve candidates and stop with an unshared status. Do not call them Atlan-ready.
Draft question ideas must say that retrieval-based answers require publication first.

## 6. Choose the destination, reconcile and publish

Reuse an explicit destination or ask once by accessible workspace name. Explain it as where
the team finds and reuses definitions. Fetch live kind schemas and existing definitions, then
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

Show actual released metric/query counts and verified artifact links, then three to five
supported questions (fewer if needed), including history-derived questions where useful.
Preserve data-connection and unmeasured-parity limitations. Do not invent a time basis or
dimension. Prefer the actual question stored on a released saved query. Add a period,
grouping or comparison only when its date basis, dimension and join are evidenced in the
retrieved definition; do not suggest an unsupported question with a caveat. Two supported
questions are better than filling the list with unverified ones.
Continue to a question already supplied; otherwise wait for the user's selection.
End with the supported questions and any material limitation; do not append a generic offer
or invite arbitrary questions whose coverage has not been checked.
Use [retrieval](../retrieval.md) for fresh Atlan context and fresh execution on every question.
