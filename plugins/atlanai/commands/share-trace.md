---
description: Push this one session's trace to an Atlan organization, optionally as feedback on a skill or output. Says first whether background sync already uploads it.
argument-hint: "[artifact or note] [organization]"
disable-model-invocation: true
---

Load this plugin's `atlan-share` skill (in Claude Code, the Skill tool's `atlanai:atlan-share`;
elsewhere, the skill named `atlan-share`), then read its `references/trace.md` and follow it.
Do not look for its files in the working directory: they ship with the plugin, not the project.
The request:

> $ARGUMENTS

With no request, push this session to the organization `aireg` is signed in to; if it is signed
in to more than one, ask which after saying whether each one already syncs this session.
