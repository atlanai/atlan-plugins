---
description: Use a skill from the Atlan registry in this task, or install it here.
argument-hint: "<skill handle or name> [install]"
---

Load this plugin's `atlan-use-skill` skill and follow it (in Claude Code, the Skill tool's
`atlan:atlan-use-skill`; elsewhere, the skill named `atlan-use-skill`). Do not look for its file in
the working directory: it ships with the plugin, not the project. The request:

> $ARGUMENTS

Use the skill in this conversation unless the request says to install it. If no skill is
named, ask which one before calling any tool.
