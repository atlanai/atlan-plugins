# Automatic skill checks in Cursor

A skill loads only when the model decides to use it, so nothing makes Cursor look in Atlan
before a task unless a rule says so. Offer that once, in Cursor, as the last line of the
connection reply:

> Want me to check Atlan for a fitting skill at the start of each task? Say yes and I will add
> a short rule to this project in `.cursor/rules/atlan-skill-checks.mdc`.

Do not offer it in other tools. Codex has its own note (`codex-skill-checks.md`), and in Claude
Code the Registry Mods plugin (`atlanai-mods`) does this on its own.

**On a yes**, with a workspace open:

1. Write `.cursor/rules/atlan-skill-checks.mdc` at the project root with exactly the file
   below, creating the folder when missing. The file is this rule's alone: when it exists,
   replace it whole. Change no other rule and no `AGENTS.md`.
2. Reply in three lines at most: the path written, that it applies to this project from the
   next chat and reaches teammates only if they commit it, that it uses only active, scanned
   skills and still asks before any credential use or irreversible step, and that "stop
   checking Atlan for skills" turns it off.

For every project, Cursor keeps user rules in its settings, which no file reaches: give the
block between the markers for the user to paste under Cursor Settings, Rules. Do the same when
no workspace is open. When the user asks to stop, delete the file and nothing else, or name
the pasted rule for them to remove.

```markdown
---
description: Check the Atlan registry for a fitting skill at the start of each task
alwaysApply: true
---
<!-- atlanai:skill-checks:start -->
## Atlan registry skills

At the start of each task, check whether my organization's Atlan registry has a skill for it,
with the atlan-use-skill skill. Use a single clear match without asking and name it in one
line. Ask me when more than one fits, and say nothing when none does. Skip quick questions and
tasks a skill installed here already covers.
<!-- atlanai:skill-checks:end -->
```
