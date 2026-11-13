# Publish an eval set to Atlan Trident

Read when saving a set to Atlan Trident.

Each `EVAL publish` prints the next `step` and its `calls`, each a `{tool, arguments}` pair.
Send every call in order with that exact name and arguments (in search mode, through
`mcp__call_write_tool` with the session id beside them). Then run `EVAL publish-ack
--variant baseline --step <step> --ids <id,id,...>` with the ids the calls returned, in item
order: do not retype the responses into a file. The `results` step is the exception: save its
tool result as one JSON list in `<dir>/hillclimb/<variant>/publish-results.json` with the Write
tool and pass it with `--responses`, so the saved scores are checked.
Repeat until `publish` prints `"step": "done"`.

- A failed item comes back under `failed` with the registry's message; the next `publish`
  resends only those. Fix a named field first; after three failures of one item, stop and report it.
- A `conflict` on the scorer or dataset means one by that name already lives in the workspace:
  list that kind with `registry__list_artifacts`, save the listing, and run
  `publish-ack --reconcile`, so the set reuses it rather than making a second one.
- A lost result blocks `publish`. For `scorer` and `dataset`, run `publish --resend` and send the
  same calls: their idempotency keys return what already landed. For `records`, list
  `dataset_record` in the workspace with `registry__list_artifacts`, save the listing and run
  `publish-ack --reconcile`. Never resend a batch blindly.
