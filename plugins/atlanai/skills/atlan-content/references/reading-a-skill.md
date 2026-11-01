# Reading a skill end to end

The loop, and the four ways it goes wrong.

## The loop

```
registry__search_artifacts { "query": "slide design", "kind": "skill", ... }
  → skill_01m1pb66fxek99t3fxjv0mqeg2

registry__get_artifact { "artifact_id": "skill_01m1pb66fxek99t3fxjv0mqeg2", ... }
  → metadata.files = [
      { "path": "SKILL.md",                    "file_id": "file_01A", "file_version_ordinal": 3, "size_bytes": 17966 },
      { "path": "LEARNINGS.md",                "file_id": "file_01B", "file_version_ordinal": 1, "size_bytes":  2876 },
      { "path": "references/anti-patterns.md", "file_id": "file_01C", "file_version_ordinal": 1, "size_bytes":  6165 },
      { "path": "references/frameworks.md",    "file_id": "file_01D", "file_version_ordinal": 1, "size_bytes": 12543 }
    ]

registry__get_file_content { "file_id": "file_01A", "version_ordinal": 3, ... }
  → the SKILL.md text

  ... then, only if SKILL.md sends you there:
registry__get_file_content { "file_id": "file_01C", "version_ordinal": 1, ... }
```

Four files, ~39 KB. Reading all four costs about ten thousand tokens for material the entry
point may never reference. Read `SKILL.md`, follow its instructions, and fetch a reference
when it names one.

## The mistakes

**Treating the manifest as the content.** `metadata.files` is paths and sizes. A skill called
`slide-design` whose bundle holds a file named `frameworks.md` could be about anything; names are
not a summary and guessing from them produces confident, wrong work. If the read fails, report
that you could not read it.

**Substituting a skill you already have.** A similarly named skill already loaded in your
harness is a different artifact with different instructions. Reading the one you were asked
about is the task.

**Dropping the ordinal.** `metadata.files[].file_version_ordinal` pins an exact version.
Omitting `version_ordinal` reads the file's latest instead, which for a skill whose bundle was
re-uploaded is a different file than the one this skill version is made of. Pass what the
manifest gave you.

**Reading `content_uri` as something you can fetch.** A manifest entry may carry a
`content_uri` such as `gs://…`. That is the gateway's own object-store pointer, not a URL you
have credentials for. `file_id` is the handle; `registry__get_file_content` is the door.

## When the skill is withdrawn

A skill that failed a security scan is `blocked`, and a blocked skill's manifest is withheld —
`metadata.files` is simply absent from the read. That is not a bug and not a permissions
problem: the bundle has been withdrawn and its content is no longer served through any door.
`artifact_status` on the artifact tells you which state it is in, and a skill's `security`
insight has the reason.

## Reading before you write

Publishing a new version of a skill means editing the bundle that exists, not writing a fresh
one from its description. Read every file in the current manifest first, apply the change, and
re-upload the whole bundle — `atlan-author-skills` has the upload half. A "new version" that
silently drops the references the previous one had is the usual result of skipping this.
