---
description: Share what this session made with colleagues through Atlan, and post it to Slack.
argument-hint: "[what to share] [with whom] [#channel or @person]"
---

Load this plugin's `atlan-share` skill and follow it (in Claude Code, the Skill tool's
`atlanai:atlan-share`; elsewhere, the skill named `atlan-share`). Do not look for its file in
the working directory: it ships with the plugin, not the project. The request:

> $ARGUMENTS

With no request, share the main file or result this session produced; if there is more than one
candidate, ask which before calling any tool.
