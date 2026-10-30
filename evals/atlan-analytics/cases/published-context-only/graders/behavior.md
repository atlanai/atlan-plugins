---
type: llm
weight: 1
---

Pass only if the explanation grounded in the template covers all of these:
1. Uses ver_published_2 from latest, not the search head ver_draft_4.
2. Keeps the mandatory cancellation exclusion when adding question-specific filters.
3. Skips the rejected query; the unreviewed example requires independent checks and a caveat,
   not a claim that it ran or that someone approved it.
4. Retains used_context_ids and cites exact artifact/version ids in used_context_refs, including
   rel_revenue and latest for the metric and ver_example_3 if the example is actually used.
5. A missing/unreadable/withdrawn release does not fall back to head or allow silently inventing
   the unavailable governed calculation; it explains the limitation and leaves that calculation unanswered.

Fail if any item is missing or if it claims these instructions independently enforce governance.
