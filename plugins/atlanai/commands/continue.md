---
description: Catch up on and continue an Atlan artifact handed over from the Atlan dashboard.
argument-hint: "<artifact handle, such as output_…> [source session session_…] [what to do next]"
---

Load this plugin's `atlan-continue` skill and follow it (in Claude Code, the Skill tool's
`atlanai:atlan-continue`; elsewhere, the skill named `atlan-continue`). Do not look for its file in
the working directory: it ships with the plugin, not the project. The hand-off:

> $ARGUMENTS

If no handle is given, ask for the artifact's handle or title before calling any tool.
