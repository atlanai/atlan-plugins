# The bundle format

## Layout

```
SKILL.md                    required, at the archive ROOT
references/*.md             anything the body links to
scripts/*                   anything the body tells a caller to run
```

Nothing else is required and nothing else is special. Zip the *contents* of the directory,
not the directory - a bundle whose `SKILL.md` is one level down is refused with "skill
bundle missing SKILL.md at root", and that is the single most common rejection.

## Frontmatter

A terminated `---` block at the very start of `SKILL.md`. Real YAML, so `>` and `|` block
scalars work for a long `description` and `~` means null.

### Required

| Key | Rule |
|---|---|
| `name` | Lowercase `a-z`, `0-9`, hyphens. No leading or trailing hyphen. Max 64 chars |
| `description` | Non-empty, max 1024 chars |

Plus a non-empty Markdown body after the closing fence, max 500,000 characters.

### Optional, and promoted onto the artifact

| Key | Rule |
|---|---|
| `license` | Max 1024 chars |
| `allowed-tools` | Max 256 entries, each max 256 chars. A YAML list **or** a space/comma-separated string. Tokenised at paren depth 0, so `Bash(git add *)` stays one token |
| `metadata` | A map, max 64 keys / 16 KiB. `metadata.lifecycle` must be `draft`, `active`, or `deprecated` |
| `version` | Max 64 chars. Free-form; the registry's own version rail is separate and ordinal |
| `when_to_use` | Max 1024 chars |
| `compatibility` | Max 500 chars |
| `disable-model-invocation` | Boolean |
| `user-invocable` | Boolean |
| `argument-hint` | Max 256 chars |
| `arguments` | Max 64 entries, each max 128 chars. List or string form, as `allowed-tools` |
| `model` | Max 128 chars. Claude Code's per-turn model (`haiku`, `sonnet`, `opus`, a full id, or `inherit`). Stored as `model_id`, and read as a `model_tier` of `fast`, `balanced` or `frontier` by model family; any other value is kept with no tier. Advisory: the harness and the user still choose the model. Not in the agentskills.io spec, so `skills-ref validate` and claude.ai upload reject it |

Unrecognised top-level keys are kept verbatim rather than dropped, so a key another harness
reads is not lost - but nothing here promotes it to a queryable column.

## Write the description as a trigger

`description` is what a model reads to decide whether to reach for this skill at all. It is
the only field that gets read before the body.

- Bad: `Preview deployment skill.`
- Good: `Deploy a branch to a preview environment and report the URL. Use when asked to put
  a branch somewhere reviewable, or to share work in progress.`

Say what it does *and* when to use it. `when_to_use` exists for a longer version of the
second half.

## Size ceilings

| Limit | Value |
|---|---|
| Archive, compressed | 10 MiB |
| Archive, decompressed | 500 MiB |
| Decompressed across one batch | 1 GiB |
| Entries | 10,000 |
| Path length | 1024 |
| Directory depth | 8 |
| `SKILL.md` read cap | 16 KiB |

The `SKILL.md` cap is the one worth internalising: 16 KiB is the *frontmatter and body* the
registry reads. It is generous for a well-shaped skill and tight for one that inlined its
references, which is the shape it is meant to discourage.

## Batching, and why the item cap is not the real limit

`extension__skill__create` accepts up to 100 skills per call, but the cap that actually fires is
usually the ~16 MiB request ceiling - and one bundle may be 10 MiB by itself.

The whole call also shares a **single 15-second processing budget that does not grow with
the item count**. So a large batch does not run longer; it times out.

In practice:

- Chunks of 10-20 small bundles.
- A large bundle on its own.
- Never a hundred at once, even though the schema allows it.

## What is derived, and therefore what not to send

Everything about the content: `summary`, `license`, `skill_version`, `when_to_use`,
`compatibility`, `allowed_tools`, `argument_hint`, `arguments`, `is_model_invocable`,
`is_user_invocable`, `lifecycle`, `model_id`, `model_tier`, the `files` manifest, and every structural count
(`file_count`, `total_bytes`, `largest_file_bytes`, `max_depth`, `code_file_count`,
`doc_file_count`, `asset_file_count`, `has_executable_files`, `executable_file_count`).

`extension__skill__create` takes only the envelope: `name`, `workspace_id`, either
`bundle_file_id` or inline `files`, and optionally `display_name`, `description`, `project_id`,
`source_type`, `source_id`, `idempotency_key` (a retry key) and `sha256` (a map of path to the
hex sha256 of that inline file's text, checked on arrival); `wait_for_release` and `dry_run` sit
beside `skills` for the whole call. That is the whole writable surface. An older skill app refuses
an argument it does not take, by name, before it writes anything: without `wait_for_release`
(added in 3.6.0) or `idempotency_key`, `sha256` and `dry_run` (added in 4.2.0), send the call
again without them; without `files`, send the bundle as [a ZIP](publish-zip.md).
