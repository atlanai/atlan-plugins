---
type: llm
weight: 1
---

Pass only if the explanation includes all of these:
1. Metric and query definition changes use explicit full-definition revision appends, not PATCH.
2. Studio or a configured authenticated HTTP/SDK client uses the append endpoint, reviewed-head
   ETag / If-Match, preserved source provenance and auto_release false.
3. Metric execution changes require a higher positive-integer definition.version. Undo copies
   old content into another new revision with a version greater than the then-current head,
   never regressing to 2.
4. Conflicts require re-read/review; an uncertain append is inspected in history, never blindly replayed.
5. MCP-only access prepares a proposal and pauses before Run rather than inventing a tool or
   reporting the unchanged definition as an improvement. Publishing for the release-reading
   answering agent is explicit and separate from saving or undoing.
