# Kinds you will meet most

Open this when a result carries a kind you need to explain, the user asks what a kind holds, or
the user asks what a project holds.

| Kind | What it is |
|---|---|
| `skill` | An Agent Skill bundle (`SKILL.md` plus files), versioned and security-scanned |
| `agent`, `harness` | An agent's catalogue entry, and the coding tool or runtime it runs in |
| `output` | A deliverable an agent run produced, pinned to the file it published |
| `knowledge` | A bundle of curated Markdown concepts (glossary, tables, metrics documentation) |
| `mcp_server`, `mcp_tool` | A remote MCP server registered in the gateway, and each tool it serves |
| `file` | Uploaded bytes: bundles, documents, datasets |
| `repo` | A source repository that skills and agents are `sourced_from` |
| `dataset`, `scorer`, `experiment` | Evaluation material and results |

Other apps add kinds of their own (ontologies, objectives, models, metrics). When you meet an
unfamiliar kind, `registry__get_kind_schema { "kind": "…" }` describes it.

## Platform kinds

`app`, `app_action`, `harness_vendor`, `model` and `agent_framework` are the platform's own
records: the apps installed in the registry, their actions and the catalogues of vendors, models
and frameworks. They can outnumber everything people made (80 of 90 artifacts on a new
organization). Leave them out of a "what do we have?" answer and say they were left out; name
them only when the user asks about apps or models.

## Projects

A project is a container, not an artifact: it has no releases and no typed relationships.
`registry__get_project { "project_id": "project_…" }` returns its description, its
`knowledge_urls` and the workspace it is in. What is in it comes from
`registry__list_artifacts { "project_id": "project_…" }`, which lists artifacts homed in the
project or linked into it; add `kind` to include a kind hidden from cross-kind discovery, such
as `file`.
