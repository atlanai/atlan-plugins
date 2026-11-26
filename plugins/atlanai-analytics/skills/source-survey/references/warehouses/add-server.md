# Adding a warehouse server to this tool

Recommend the Atlan route first: "The simplest way: an Atlan admin adds <warehouse> from the
Atlan Store once. Then everyone reaches it through Atlan, signed in as themselves." When the person wants it in this tool
only, give the steps for the harness you are running in:

- **Claude Code:** `claude mcp add --transport http <name> <url>`, then `/mcp` to sign in.
- **Claude desktop or claude.ai:** In Settings, open Connectors and add a custom connector with
  this URL: `<url>`.
- **Codex:** `codex mcp add <name> --url <url>`, or a `[mcp_servers.<name>]` table with `url` in
  `~/.codex/config.toml`, then `codex mcp login <name>`.
- **Cursor:** add `"<name>": {"url": "<url>"}` under `mcpServers` in `.cursor/mcp.json`.

Your warehouse admin also has to allow this app to sign in. Never put a password or token in
these files.
