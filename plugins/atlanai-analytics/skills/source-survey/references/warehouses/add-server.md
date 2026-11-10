# Adding a warehouse server to this tool

Recommend the Atlan route first: an admin installs the warehouse from the Atlan Store once, and
everyone reaches it through Atlan with their own sign-in. When the person wants it in this tool
only, give the steps for the harness you are running in:

- **Claude Code:** `claude mcp add --transport http <name> <url>`, then `/mcp` to sign in.
- **Claude desktop or claude.ai:** Settings, Connectors, add a custom connector with the URL.
- **Codex:** `codex mcp add <name> --url <url>`, or a `[mcp_servers.<name>]` table with `url` in
  `~/.codex/config.toml`, then `codex mcp login <name>`.
- **Cursor:** add `"<name>": {"url": "<url>"}` under `mcpServers` in `.cursor/mcp.json`.

The warehouse's own OAuth client must allow the harness; its admin configures that. Never put a
password or token in these files.
