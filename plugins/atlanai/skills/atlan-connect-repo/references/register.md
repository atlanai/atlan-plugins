# Connect a repository to Atlan

A `repo` artifact is a repository in one workspace, identified by its remote. Skills, agents and
scorers name the repository they came from with a `sourced_from` relationship to it, which is
what lets someone ask "everything from this repository". This skill does two things, in order:
register the repository, then link the artifacts the user chooses.

## Tools

Your first call opens the session by sending `objective`; pass the id it returns as
`session_id`, with a concrete first-person `rationale`, on every later call.

- `registry__list_workspaces` to resolve the workspace the user names.
- `extension__repo__register_repo` to register the repository, or to get the repo already
  registered there for that remote. Each call appends a version to a repo that exists, so
  look for it first with `registry__list_artifacts` (`kind: "repo"`, the workspace) and call
  this only when no row carries the normalized remote.
- `registry__get_artifact` to read the repo back.
- `registry__search_artifacts`, `registry__list_artifacts` and `registry__get_artifact` to find
  the skills, agents and scorers to link.
- `registry__relate_artifact` to write each `sourced_from` relationship.
- `registry__get_related_artifacts` on the repo, only to see links that existed before.

Do not reach for `curl` or raw HTTP when one of these covers the step. If none does, stop and
name the missing capability.

## 1. Discover before asking

From the user's checkout, read:

- the remote: `git remote get-url origin` (ask which remote when there are several and none is
  `origin`; a checkout with no remote cannot be registered). With no shell or checkout, as in
  Claude chat, ask the user for the repository URL;
- the default branch: `git symbolic-ref --short refs/remotes/origin/HEAD`, without the `origin/`
  prefix, when it resolves;
- candidate artifacts: `SKILL.md` directories, `.atlan/agents/*.yaml` declarations and scorer
  definitions, with their repository-relative paths.

## 2. Ask only what discovery cannot answer

- **Which workspace.** Explain it as where their team will find the repository. Never guess
  from a name; ask one question when several writable workspaces are plausible.
- **Contributors.** Contributor email addresses become detected users of the workspace, which
  workspace members can see. Send them only when the user agrees; collect them with
  `git log --format=%ae` and deduplicate. Omit `contributors` otherwise.
- **Which artifacts to link.** Show names and paths; existing registry artifacts are the
  candidates, matched by workspace and name or by a `source_id` equal to their path. A name
  match alone is not identity, and several plausible matches block the link.

## 3. Register the repository

Look for the repo first: `registry__list_artifacts` with `kind: "repo"` and the workspace, and
match the normalized remote (`github.com/acme/demo`) against each row's `name`. A match is the
repo: read it back and go to step 4, because registering it again appends a version it does
not need. Otherwise call `extension__repo__register_repo` with `workspace_id`, `remote_url`
exactly as git printed it, and `default_branch` when known (`contributors` only with consent). The gateway normalizes
the remote and derives the repo's name, so `git@github.com:acme/demo.git` and
`https://github.com/acme/demo` are one repo named `github.com/acme/demo`. Do not send a `name`.

Read the returned `repo_…` id back with `registry__get_artifact` before using it, and confirm
its `workspace_id` and `remote_url`.

## 4. Link the artifacts

For each chosen skill, agent or scorer, call `registry__relate_artifact` with:

- `artifact_id`: the skill, agent or scorer;
- `subject_id`: the `repo_…` id;
- `rel_type`: `sourced_from`;
- `attributes`: `{"path": "<repository-relative path>"}`.

Only `skill`, `agent` and `scorer` declare `sourced_from`. Any other kind refuses it; report that
rather than choosing another relationship. Writing the same link again is harmless.

## 5. Report

Each `registry__relate_artifact` result is the link it stored, so report from those results
rather than reading the links back. Report the repo's name and id, the workspace, and each linked
artifact with its path. Say plainly which links failed and why.

## Continue with publication

For ongoing GitHub publication, follow [GitHub sync](github-sync.md). Registration or
provenance links alone are not evidence that CI published the selected artifacts.
