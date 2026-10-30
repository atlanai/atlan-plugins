---
type: llm
weight: 1
---

Pass only if the explanation includes all of these:
1. The fix is a new metric, drafted as a complete definition and tested, not created by the
   person running the loop.
2. It is proposed for the workspace's metric owners as a workspace `file` of media type
   `application/vnd.atlan.improvement+json`, with the record in its content and in
   `source_metadata.atlan_improvement`.
3. The record leaves `subject_id` empty, names the metric in `subject_name`, and carries
   `proposed` with the name, display name, description and complete definition, grounded in the
   train runs behind it (`evidence.runs`).
4. Before writing, it checks the workspace for an existing proposal of the same name and writes
   no second one if one is waiting or was dismissed.
5. Nothing exists until a person creates the metric; the round stays pending and records the
   proposal's id rather than reporting the metric as saved.
