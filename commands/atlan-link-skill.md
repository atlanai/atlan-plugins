---
description: Configure ongoing GitHub-to-Atlan sync for a repository's skills and agents
argument-hint: "[repository path or sync request]"
---

Follow the bundled skill at `skills/atlan-link-skill/SKILL.md`
for this request:

> $ARGUMENTS

If no repository path is supplied, use the current Git checkout. Inspect the
repository before asking questions, and ask only for decisions the repository,
the ai-registry Action, and the installed `aireg` cannot resolve. CI must be the
first publisher for newly Git-backed skills and agents; do not publish them
locally first.
