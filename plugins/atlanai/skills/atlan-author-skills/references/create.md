# Creating a skill to share

A new skill for the team starts with a search, because the organization may already have one,
and ends with a publish. The drafting in between belongs to your tool's own skill creator when
it has one: it interviews the user, writes the files and tests them better than a one-shot
draft. Atlan adds the search before it and the governed copy after it.

## 1. Look for one that already exists

Search before drafting, with the user's own words for what the skill should do:

```
mcp__call_read_tool {
  "name": "registry__search_artifacts",
  "arguments": { "query": "review pull requests for security issues", "kind": "skill", "limit": 3 },
  "session_id": "mcp_session_…",
  "rationale": "Checking whether the organization already has a skill for this before drafting a new one."
}
```

The first call of the conversation sends `objective` instead of `session_id`.

- **An `active` skill that does the job:** name it, its workspace and what it does in one line,
  and offer to use it here (`atlan-use-skill`) or improve it as a new version
  ([update a skill](update-a-skill.md)). Draft a new one only when the user says it does not
  fit, and say how theirs will differ.
- **A `pending_review` one:** someone already submitted it. Say who owns it before drafting a
  second.
- **Nothing close:** say so in one line and go on. Judge by name and description. A hit with
  `match.keyword` false and `match.similarity` under 0.45 is noise: unrelated skills scored 0.19
  to 0.26 in a live search, so treat it as nothing close.

A skill of that name already installed here counts too: offer to publish that one rather than
writing another.

## 2. Draft it

A one-file skill the user described in a sentence or two needs no interview: write its
`SKILL.md` yourself, as the quick path in [SKILL.md](../SKILL.md) says, and show it in the
publish question. Use a skill creator for anything bigger, or when the user wants tests.

| Where you are | Draft with |
|---|---|
| A tool that lists a skill creator among its skills, such as Claude Code, Claude chat or Cowork with `skill-creator` (often listed as `anthropic-skills:skill-creator`) | That skill |
| Codex | `$skill-creator`, which Codex ships |
| Cursor, or any tool with no skill creator listed | Write it yourself: a `SKILL.md` whose frontmatter follows [bundle format](bundle-format.md), with a description that says when to use it, then publish it with [publishing](publish.md) |

Do not install a skill creator to get one; draft it yourself instead.

When handing off, load the skill creator and give it:

- what the skill is for and when it should fire, in the user's words, plus what the search
  found that it must not duplicate;
- where to write it: a project folder in a tool with files (`.claude/skills/<name>/` in Claude
  Code, `.agents/skills/<name>/` in Codex), or wherever it works in a tool without them;
- Atlan's rules, so the draft publishes cleanly: a lowercase, hyphenated `name` of at most 64
  characters; a `description` that says when to use the skill, with `Use when`; text files with
  relative paths; no secrets or keys.

Follow its steps, including asking the user before long test runs. Skip its packaging step:
text skills publish from their files, inline. Come back here when the user is happy with the
draft.

## 3. Publish it

Follow [publishing](publish.md) from step 1. Ask once: show the skill's name, its files and the
workspace you picked (or ask the team's or the user's own when nothing settles it), and create
only after a yes. In a tool without files, send the text the skill creator produced inline, as
the same call does.

Report the slot as publishing says. Then tell the user that the local folder is a copy: an edit
there reaches Atlan only as a new version.
