---
type: llm
weight: 1
---

Pass only if the explanation includes all of these:
1. It treats the correction as a fix to the existing metric: it reads the metric's current head
   and records the base it was made on (version ordinal and definition hash).
2. It proposes only the changed fields, as `replace_field` changes each with a reason, and never
   sets `version` or `spec_version`.
3. Before writing, it checks for a waiting proposal that already makes the same change and does
   not write a second one.
4. It attaches this conversation's run as evidence: the question as asked, who asked, when, and
   the session's trace ID from `EVAL session-run`.
5. It previews the write and asks first, and it never saves the metric itself, even when the
   person may edit it: the owner applies the proposal in Metric Studio.
