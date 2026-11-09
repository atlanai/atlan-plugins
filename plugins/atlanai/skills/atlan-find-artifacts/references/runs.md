# Runs: what agents ran and did

Every agent run is a trace. For "what ran" or "what did my agents do", with no handle:

1. `registry__list_traces { "from": <Unix seconds>, "to": <Unix seconds> }` for the window the
   user means ("yesterday" is their local day; ask the time zone only if the answer would
   change), with `user_id` when they ask about someone else's runs. Page with `cursor`.
2. Answer from the list first: how many runs, which agents or harnesses, and how many have
   `error_count` above zero. Group by agent rather than listing every run.
3. Read a run only when the user asks about it or a failure needs a reason:
   `registry__get_trace { "trace_id": "…", "include": ["content"] }` for its request and final
   reply, then `registry__get_trace_spans { "trace_id": "…", "limit": 20 }` for the failed step.
   Send several `get_trace` reads together in one `mcp__call_read_tools`.

An output or a session handed over by its handle belongs to `atlan-continue`, which reads that
artifact's own runs.
