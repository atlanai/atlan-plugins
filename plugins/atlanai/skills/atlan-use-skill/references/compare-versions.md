# Comparing versions and checking installed copies

Open this when the user asks what changed in a skill ("what changed in 1.3?", "what's new since
I installed it?"), whether installed skills are up to date, or to update an installed copy.
Every call is a read, through `mcp__call_read_tool`, and nothing is written unless the user
then asks to update.

## Compare the manifests before reading any file

Each version's `metadata.files` lists every path with its `file_id`, `file_version_ordinal`
and `source_digest`, the file's SHA-256. Two entries for the same path are the same bytes when
their `source_digest` values match or, on an older gateway that leaves it out, when both
`file_id` and `file_version_ordinal` match. Such a file never needs reading to know it did not
change.

1. **Find the two versions.** "What changed in 1.3" means 1.3 against the release before it.
   `registry__list_releases` with the `artifact_id` lists the releases, each with its
   `release_id`, `version_label` and `version_ordinal`. "What's new" for an installed copy means
   the version the install record pins (below) against `latest`.
2. **Read both manifests, without file content.** Two `registry__get_artifact` calls, sent
   together: one with the older `release_id`, one with the newer `release_id` or
   `"channel": "latest"`. A version with no release, such as one waiting for review, is read by
   its `version_ordinal` instead of a `release_id`. Leave out `include_file`: the manifests are
   all this step needs.
3. **Join the two file lists by `path`.**
   - Same bytes, as above: unchanged. Do not read it.
   - Only in the newer list: added. Only in the older: removed.
   - Anything else: changed. A file that then diffs as identical counts as unchanged.
4. **Read only the changed files, at both versions.** One `registry__get_artifact` call per
   version with `include_files` listing the changed paths (the added files only on the newer
   side). If `include_files` is refused, read them with `registry__get_file_contents`: one call
   whose `"files"` lists up to 8 `{file_id, version_ordinal}` entries, both versions' files
   together, each with that version's `file_version_ordinal` as `version_ordinal`. Only when
   that tool is not served either, read each with `registry__get_file_content` and the same two
   arguments, as parallel calls in one turn.
5. **Diff them.** With a shell, write each pair to a scratch folder outside the user's project
   and run `diff -u old new`. Without one, compare the two texts yourself.

Reply with the changelog first: counts of files added, removed, changed and unchanged, then
what the changes do (a new step, a changed `description` trigger, a new script, a rule
removed). Leave out whitespace and wording that does not change behaviour. Point out any
added or changed script and say you have not run it. When nothing changed, say the two
versions have identical files.

## The install record

An install through MCP writes one record beside the skill folders, in the same skills folder:
`.atlan-installed.json` (for example `.claude/skills/.atlan-installed.json`). It lives outside
each skill's own folder, so publishing that folder later never sends it. It holds one entry per
installed skill, keyed by the skill's folder name:

```json
{
  "security-review": {
    "artifact_id": "skill_01j…",
    "workspace_id": "workspace_01j…",
    "release_id": "rel_01j…",
    "version_label": "1.2.0",
    "version_ordinal": 3,
    "installed_at": "2026-11-08T10:00:00Z",
    "files": {
      "SKILL.md": { "file_id": "file_01j…", "file_version_ordinal": 2, "source_digest": "9f2c…" },
      "references/rules.md": { "file_id": "file_01j…", "file_version_ordinal": 1, "source_digest": "41ab…" }
    }
  }
}
```

`files` copies each manifest entry's `file_id`, `file_version_ordinal` and `source_digest`
(when the manifest has one) at install time. A record written by an earlier version of this
skill names the digest `sha256`: read it as `source_digest`, and write `source_digest` when you
next replace that entry. Read the record first and
merge your entry into it, keeping every other skill's entry. aireg keeps its own record, so an
aireg install writes none.

## Are installed skills up to date?

1. **List the installed skills.** Your tool lists them in this conversation. Leave out the Atlan
   plugin's own skills (`atlan-…`, `atlanai:…`): they update with the plugin, not from the
   registry. Find where the others live: the skills folders in `install.md`'s table, or the
   folder your tool's skill loader names for a skill (in Claude Code, the base directory the
   Skill tool prints). Read `.atlan-installed.json` in the folder that holds the skill folders;
   one read covers every skill it lists.
2. **A skill with a record:** one `registry__get_artifact` on its `artifact_id` with
   `"channel": "latest"`, all of them sent together, with no `include_file`. Compare the
   record's `files` with the manifest's, path by path, as in step 3 above:
   - Every path matches: up to date.
   - Anything differs: behind. Give the installed and latest `version_label` and the counts of
     files changed, added and removed, with no file read.
   - `artifact_status` other than `active`, or a `not found`: say so, and leave the copy alone.
3. **A skill with no record** (installed by hand, by aireg, or before records existed): read the
   registry skill as SKILL.md section 1 says and compare the two `SKILL.md` texts, saying that
   only `SKILL.md` was compared. A skill with no match in Atlan is a local skill; say so in one
   line and do not search further.

Answer with one line per skill that is behind and a count of those up to date. Offer to show
what changed (the comparison above, from the recorded version to `latest`) or to update.
Updating is a reinstall of `latest` through `install.md`, after the user says yes, that also
replaces the skill's entry in the record. Files the user edited in the installed copy are lost
on a reinstall. With a shell and a recorded digest, `sha256sum` each local file first and
name the ones that no longer match the record; without either, say local edits would be
replaced.
