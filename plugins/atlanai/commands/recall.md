---
description: Read what your AI has kept in your Atlan memory, from any AI tool you use.
argument-hint: "[a topic]"
disable-model-invocation: true
---

Load this plugin's `atlan-memory` skill and follow its Recall step (in Claude Code, the Skill
tool's `atlanai:atlan-memory`; elsewhere, the skill named `atlan-memory`). Do not look for its
file in the working directory: it ships with the plugin, not the project. The topic:

> $ARGUMENTS

With no topic, read `memory/ME.md` and say what you know about the user in five lines or fewer.
