---
name: atlan-author-skills
description: Create, update and publish Atlan skill bundles with exact source identity and pinned file versions.
license: Apache-2.0
---

# Author skill bundles

Open one audited session with `mcp__initialise`; pass its `session_id` and a first-person
`rationale` on every later tool call. Resolve the workspace with `registry__list_workspaces`
and read `registry__get_kind_schema` before writing a kind for the first time.

- **Create and publish a bundle:** [publishing](references/publish.md).
- **Update a bundle:** [update a skill](references/update-a-skill.md).
- **Review relationships:** [skill edges](references/skill-edges.md).

Read the current entry point and referenced files before updating. Publish the complete
bundle through the skill-specific upload/create procedure and preserve its exact source
identity. Do not replace it with a generic artifact append, rename or recreate to bypass
a source conflict. Files belong inside the bundle and pin exact file ordinals.

For GitHub publication and repository provenance, use `atlan-connect-repo`;
CI publishes a newly Git-backed bundle first. Read file bytes with `atlan-content`.
