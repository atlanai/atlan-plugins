# Publish an eval set to Atlan Trident

Read when saving a set to Atlan Trident.

Each `EVAL publish` prints the next `step` and its `calls`, each a `{tool, arguments}` pair.
Send every call in order with that exact name and arguments (in search mode, through
`mcp__call_write_tool` with the session id beside them). Save the tool results, in call
order, as one JSON list in `<gen>/hillclimb/baseline/publish-<step>.json` with the Write
tool (never under a temporary folder outside the project), and run
`EVAL publish-ack --variant baseline --step <step> --responses <that file>`.
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
