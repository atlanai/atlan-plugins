---
description: Draft feedback for Atlan about the plugin, a skill or a tool call, and send it after you approve.
argument-hint: "[what went wrong or well]"
disable-model-invocation: true
---

Load this plugin's `atlan-troubleshoot` skill (in Claude Code, the Skill tool's
`atlanai:atlan-troubleshoot`; elsewhere, the skill named `atlan-troubleshoot`), then read its
`references/feedback.md` and follow it. Do not look for its files in the working directory: they
ship with the plugin, not the project. What the user said:

> $ARGUMENTS

With nothing said, draft it from this conversation's Atlan work. If there was none, ask what to
report before writing a draft.
