---
type: llm
weight: 1
---

Pass only if the explanation includes all of these:
1. Before drafting, it checks the workspace for a metric that already covers the measure and
   for an existing proposal of it (waiting or dismissed), and writes nothing new when one exists.
2. It drafts a complete metric definition and tests its SQL with the warehouse query tool, or
   says plainly in the proposal that the SQL is untested.
3. It attaches this conversation's run as evidence: the question as asked, who asked, when, and
   a trace ID computed from this session (`EVAL session-run`), noting that the trace shows in
   Atlan only once the session is synced.
4. It previews the write and asks before creating a workspace `file` of media type
   `application/vnd.atlan.improvement+json` whose record has an empty `subject_id` and
   `proposed` holding the definition.
5. It never creates or saves the metric itself; the owners create it in Metric Studio.
