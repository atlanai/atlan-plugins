---
description: Keep a fact or preference in your Atlan memory, so every AI tool you use knows it.
argument-hint: "<what to remember>"
disable-model-invocation: true
---

Load this plugin's `atlan-memory` skill and follow its Remember step (in Claude Code, the Skill
tool's `atlanai:atlan-memory`; elsewhere, the skill named `atlan-memory`). Do not look for its
file in the working directory: it ships with the plugin, not the project. What to keep:

> $ARGUMENTS

With nothing given, ask in one line what to remember before calling any tool.
