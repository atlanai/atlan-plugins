---
description: Connect to Atlan, check the sign-in, and see what your organization has.
disable-model-invocation: true
---

Load this plugin's `atlan-get-started` skill and follow it (in Claude Code, the Skill tool's
`atlanai:atlan-get-started`; elsewhere, the skill named `atlan-get-started`). Do not look for its file in
the working directory: it ships with the plugin, not the project.

Check the `atlan` connection,
help the user sign in if it is missing, list the workspaces they can reach, and offer a first
task. $ARGUMENTS
