---
name: atlan-author-skills
description: Publish a skill to Atlan as a versioned bundle, or a new version of one, from a skill folder on this computer. Use when the user wants to publish, upload, share, push or release a skill to Atlan or the registry, or update a skill already there. Not for loading or installing a registry skill here (atlan-use-skill), reading a skill's files (atlan-content) or publishing from GitHub CI (atlan-connect-repo).
license: Apache-2.0
---

# Author skill bundles

Pass a `session_id` on every tool call, and a first-person `rationale` on every write (on a read,
when the tool's schema requires one). In search mode, the default, the first
`mcp__search_tools` or `mcp__call_tool` opens the session by sending `objective`, and each
`registry__…` tool named here runs through `mcp__call_tool` with `session_id` and `rationale`
beside `name` and `arguments`. In full mode, call `mcp__initialise` first. Replies lead with the
result in a few lines, and ask before every write.

- **Publish a skill:** [publishing](references/publish.md). Text skills go inline in one call;
  read [the ZIP path](references/publish-zip.md) only for binaries, executables or large bundles.
- **Update a bundle:** [update a skill](references/update-a-skill.md).
- **Review relationships:** [skill edges](references/skill-edges.md).

Read the current entry point and referenced files before updating. Publish the complete
bundle through the skill-specific upload/create procedure and preserve its exact source
identity. Do not replace it with a generic artifact append, rename or recreate to bypass
a source conflict. Files belong inside the bundle and pin exact file ordinals.

For GitHub publication and repository provenance, use `atlan-connect-repo`;
CI publishes a newly Git-backed bundle first. Read file bytes with `atlan-content`.
