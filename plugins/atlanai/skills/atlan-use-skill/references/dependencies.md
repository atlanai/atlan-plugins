# What a registry skill needs that its bundle does not carry

Open this when the skill you loaded relies on something outside `metadata.files`.

A registry skill is often cut from a larger plugin. Before following it, list anything it relies on that is not in
`metadata.files`: a subagent it delegates to, another skill or plugin, a command, a product's
tools (Linear, Slack, GitHub, a warehouse), a CLI. `metadata.frontmatter_metadata` often names
them (`plugin`, `related-agent`). Look for each where it costs least:

1. **This tool already has it.** A skill, plugin, command or subagent of that name in what
   your tool lists, or a connector or MCP server of its own whose tools do the step (Claude's
   Slack or GitHub connector, a server in the tool's MCP settings). What is listed costs no
   call; in Claude Code, a connector's tools may be deferred, so search for the product and the
   action with ToolSearch.
2. **Atlan has it.** A product's tools through the gateway: `mcp__search_tools` naming the
   product and the step (`atlan-use-mcp-servers`; a server listed under `connect` needs the
   user's sign-in first). Another registry skill it names: search with `kind: "skill"` and
   offer to load it.
3. **Neither.** Tell the user what is missing and what the skill does without it, and follow
   the skill's own fallback if it has one; do not pretend the missing piece ran.

Use the exact product or skill the bundle names, never a lookalike. When both your tool and
Atlan have it, use your tool's for a step where the user acts as themselves (a message, a
comment), and Atlan's when the step uses an organization credential, such as a warehouse, or
should be in Atlan's audit record; say in one line which one you used. Your tool's own
connectors act as the user and are not recorded in the Atlan session. Writes ask first either
way.
