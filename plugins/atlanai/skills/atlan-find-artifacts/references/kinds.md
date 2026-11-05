# Kinds you will meet most

Open this when a result carries a kind you need to explain, or the user asks what a kind holds.

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
