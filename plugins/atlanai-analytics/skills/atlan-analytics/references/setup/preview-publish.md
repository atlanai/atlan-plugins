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
Use the saved evidence's separate counts: execution checks, source reviews needing attention,
publication holds and released artifacts. Zero holds does not mean every query passed source
review, and a successful SQL run does not establish dashboard value parity. Do not rename
all generated or executed queries "verified". Mention any remaining source-review count in
the handoff even when publication holds are zero.
If a sample already appeared, show a material definition/check change before continuing.
A helper output or linked file is not a visible sample. Expand to five/all only on request.
Use a short contextual choice when a decision is missing: an existing destination or Keep
local. Mention Show details and Show more as available actions without repeating a menu after
every step. If publication was requested already, continue under its existing authorization.

**Show five:** run `PROJECT preview --limit 5 --format markdown` and display its text in the
same response. **Show all:** use `--all`. Neither action selects a workspace, writes to Atlan,
reruns extraction or restarts the workflow. Fewer/zero samples have explicit text outcomes.
**Keep local:** run `PROJECT journey keep-local` and preserve candidates. This is a
publication preference, not Pause. Continue requested local preparation and checks, then
hand off with an unshared status. Do not call drafts Atlan-ready or withdraw earlier releases.
With a local data binding, supported questions can be answered as explicitly labelled local
exploration without publication. Use the local route in [retrieval](../retrieval.md).
Only team answers from released Atlan definitions require publication first. Do not end
Keep local with a publication-only next step or imply the prepared dataset cannot be queried.
Do not claim that sign-in is needed unless this host actually returned an access failure.
Local-only completion needs no speculative authentication diagnosis or sign-in detour.

## 6. Choose the destination, reconcile and publish

Reuse an explicit existing destination or ask once by accessible workspace name, with Keep
local as an alternative. Explain it as where the team finds and reuses definitions. Never
offer or attempt API/CLI workspace creation. If none is accessible, retain the sample and
run, then explain how to request access. Only an explicit new-workspace request gets the
UI/admin route; refresh the list after the person resolves it. An explanation-only request
gets one short paragraph: the unavailable creation capability, preserved local drafts, and
the UI/admin action followed by refreshing accessible workspaces. Do not narrate a full
publication workflow, speculate about CLI endpoints, or add an alternative destination menu.
Fetch live kind schemas and existing definitions, then
run `MINE check` with that destination. Only now show actual create/revise/reuse/held counts.
If reconciliation changes a candidate, show the changed preview. Follow [publication](../metrics/publish.md)
for the concrete plan, missing authorization, idempotency, release policy and uncertain-write
recovery. Never retry an uncertain remote mutation blindly. Carry approved writes through
release and full MCP readback. Record receipts with `PROJECT publication` as the local
procedures describe. A draft or local plan is not publication.
When definitions already exist, say which, how many and where, and why reuse beats a copy:
"6 of the 8 metrics were already saved in Analytics Demo by an earlier run, so I reused them;
2 are new." Use actual counts and names, never only "everything was already created".
Reusing everything still requires the visible preview before marking Ready. Explain count
changes: name any candidate kept only for validation, deduplicated, held or replaced by a
released definition, with its reason. Preserve those local candidates and coverage decisions;
do not silently drop SQL merely to avoid a publication decision. Full coverage requires equivalent
meaning, population, dimensions and parameters, not just a similar metric name. For a narrower
existing definition, explicitly record and show the uncovered capabilities using
[reconciliation](../metrics/reconciliation.md); keep their original candidates local.

## 7. Ready to ask here

Structure the handoff around what the person can do now, what still needs attention and the
next supported action. Use `PROJECT experience --format json`'s `handoff` when available.
Local definitions are unshared; executed examples are saved checks; a published subset is
usable only for its released scope. Do not label a whole collection ready because one sample
worked. Mention remaining review warnings and uncovered reuse capabilities alongside holds.
When checks remain, lead with the prepared scope rather than an unqualified "setup complete"
or "ready for questions". If holds are zero but reviews remain, connect those facts explicitly:
"No publication holds; some queries still need source review." Substitute the actual count.
Execution and source review are independent dimensions; never subtract one from the other
or promise that reviewing two queries will make all definitions execution-checked.
Prefer three short paragraphs, under 160 words, over repeated status headings. For example:

"Prepared locally: 2 metrics and 8 saved queries. Six have recorded execution checks;
2 queries still need source review despite zero publication holds. Nothing is released
or available to the team in Atlan.

Shipped units is SUM(s.units) for status = 'shipped', from the shipping model and query
history. Its example ran earlier; dashboard value parity remains unmeasured.

You can explore local shipped-unit totals or units by region. Time comparisons are not
supported. Next, review the 2 outstanding queries."

Use actual evidence, not this example's numbers. Explain a held metric's reason briefly.
Do not append a new task to resolve it after the supported-question handoff. If the person
asks to resolve it, inspect its source definition; do not ask them to invent a denominator.
After displaying the sample, save `PROJECT progress --stage preview --status complete`
(or `complete-with-held-items`). Keep local then finishes at the local-question handoff,
not a stale Build/Check stage. Never mark Ready without matching release receipts.

Keep one real sample in the final answer with its calculation, mandatory population/filter,
source and check status, alongside actual released/local/held counts and verified links.
End with two or three supported questions (fewer if needed), including history-derived ones
where useful. For released answers, say once that definitions are retrieved before SQL runs.
For Keep local, label the questions as local exploration and cite their local definition.
Preserve data-connection and unmeasured-parity limitations. Do not invent a time basis or
dimension. Prefer the actual question stored on a released saved query. Add a period,
grouping or comparison only when its date basis, dimension and join are evidenced in the
retrieved definition; do not suggest an unsupported question with a caveat. Two supported
questions are better than filling the list with unverified ones.
Continue to a question already supplied; otherwise wait for the user's selection.
End with the supported questions and any material limitation; do not append a generic offer
or invite arbitrary questions whose coverage has not been checked.
Use [retrieval](../retrieval.md) for fresh Atlan context and fresh execution on every question.
