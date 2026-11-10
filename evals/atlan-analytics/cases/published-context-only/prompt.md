---
max_turns: 12
allowed_tools: [Read, Glob, Grep]
---

Read the analytics-build answering-agent template. Explain how the generated agent should handle
this synthetic scenario, without querying a registry or warehouse:

Search finds Revenue revision ver_draft_4 with a formula including cancelled orders. Reading
the same metric with channel latest returns ver_published_2, release rel_revenue on latest,
whose mandatory filter excludes cancelled orders. The first saved-query hit is rejected; the
next is unreviewed, imported from history, revision ver_example_3.

Which definition and examples can the agent use, which filters must remain, and what exact
citations belong in its final JSON? Then explain what changes if the requested published release
is unavailable. Ground your answer in the template and distinguish policy from proof of SQL execution.
