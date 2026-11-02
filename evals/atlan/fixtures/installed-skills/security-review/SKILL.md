---
name: security-review
description: Use when running a pre-merge security review of a diff or PR against Acme's threat model.
metadata:
  plugin: acme-security
  related-agent: security-reviewer
---

# Security review

1. Ask what to review: the current diff, a file, or a PR number.
2. Scope check: only auth/, connectors/, ingestion/ and raw SQL are in scope. Otherwise reply
   "No security-relevant surfaces in this change" and stop.
3. Delegate to the `security-reviewer` subagent bundled with the acme-security plugin.
4. Rank findings by severity; see [reference.md](./reference.md).
