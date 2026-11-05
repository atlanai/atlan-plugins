# Automatic skill checks in Codex

A skill loads only when the model decides to use it, so nothing makes Codex look in Atlan
before a task unless the user's own instructions say so. Offer that once, in Codex, as the last
line of the connection reply:

> Want me to check Atlan for a fitting skill at the start of each task? Say yes and I will add
> a short note to your Codex instructions in `~/.codex/AGENTS.md`.

Do not offer it in other tools. In Claude Code the Registry Mods plugin (`atlanai-mods`) does
this on its own; mention it only if the user asks for automatic checks there.

**On a yes**, with a shell on the user's computer:

1. Pick the file in the Codex home folder (`$CODEX_HOME`, by default `~/.codex`):
   `AGENTS.override.md` when it exists, because Codex then reads it in place of `AGENTS.md`,
   and `AGENTS.md` otherwise.
2. If the file already holds the `atlanai:skill-checks` markers, replace only what is between
   them. Otherwise add the block below at the end of the file, creating the file and the folder
   when missing. Change nothing else in the file.
3. Reply in three lines at most: the path written, that it applies from the next Codex session,
   that it uses only active, scanned skills and still asks before any credential use or
   irreversible step, and that "stop checking Atlan for skills" turns it off.

Without a shell, give the block for the user to paste into their instructions. When the user
asks to stop, remove the block with both markers and nothing else.

```markdown
<!-- atlanai:skill-checks:start -->
## Atlan registry skills

At the start of each task, check whether my organization's Atlan registry has a skill for it,
with the atlan-use-skill skill. Use a single clear match without asking and name it in one
line. Ask me when more than one fits, and say nothing when none does. Skip quick questions and
tasks a skill installed here already covers.
<!-- atlanai:skill-checks:end -->
```
