# Security policy

## Reporting a vulnerability

Please do not report security vulnerabilities through public GitHub issues, discussions or pull
requests.

Report them through Atlan's
[responsible disclosure program](https://atlan.com/responsible-disclosure-program/). Include:

- what the problem is and where it is (a file, a skill, a manifest or the MCP endpoint),
- the steps to reproduce it, and the host and plugin version you used,
- what an attacker could do with it.

You can also use GitHub's
[private vulnerability reporting](https://github.com/atlanai/atlan-plugins/security/advisories/new)
for this repository, if it is enabled.

## Scope

This repository holds plugin content: Markdown skills, commands and agents, the manifests that
describe them, and build tools. In scope here:

- a skill, command or agent that leads an agent to leak credentials or data, bypass a
  permission check, or run something the user did not ask for,
- a manifest that points at the wrong MCP endpoint or requests more than it needs,
- a problem in the tools under `tools/` or in the CI workflows.

Problems in Atlan's MCP gateway at `https://api.atlan.com/mcp`, the Atlan registry or the Atlan
product go through the same responsible disclosure program.

## Supported versions

Fixes ship in the latest version of each plugin. Claude Code and the other hosts pick up a new
version on their next marketplace refresh.
