---
name: atlan-record-outputs
description: Register and version file-backed or externally hosted deliverables produced by an Atlan agent run.
license: Apache-2.0
---

# Record outputs

Open one audited session with `mcp__initialise`; pass its `session_id` and a first-person
`rationale` on every later tool call. Resolve the workspace with `registry__list_workspaces`
and read `registry__get_kind_schema` before writing a kind for the first time.

- **Register a deliverable:** [recording](references/record.md).
- **Choose upload or link mode:** [mode rules](references/output-modes.md).
- **Publish another version:** follow the append procedure in [recording](references/record.md#publishing-a-new-version).

Append to the existing output ID with the observed head ETag. Repeating create makes a
second output. Pin exact file ordinals in upload mode; read the result back. If an edge
write fails after creation, repair that edge using the returned ID instead of creating again.
