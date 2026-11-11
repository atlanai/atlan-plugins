# Templates

These are starting points. Keep a repository's existing triggers, runners, pins,
required checks, and custom policy, and change only what the sync needs.

## Manifest

`.registry/manifest.yml` declares the registry, the skills and agents to
publish, and the destination workspace. Create it by hand when the repository has none;
`aireg registry add` only writes to a manifest that already exists, and writes
to the user's own scope otherwise.

```yaml
registries:
  atlan: { source: https://api.atlan.com, adapter: atlan }
exports:
  - skills/meeting-brief
  - .claude/skills/release-notes
  - path: agents/release-manager          # a folder with AGENT.md and its files
    depends_on: [./skills/meeting-brief, ./.claude/skills/release-notes]
  - path: .claude/agents/triage.md        # a single-file agent
    depends_on: [./skills/meeting-brief]
publish:
  to: atlan/release-tools
```

- `exports` lists skill folders, one per entry, relative to the repository root.
  A glob such as `skills/*` publishes every folder it matches, including ones
  added later.
- An agent entry names its folder or file with `path`, and the skills it uses
  with `depends_on`, each as `./<skill path>` and each exported too. CI pins the
  exact version of each skill it publishes in the same run.
- `publish.to` is `atlan/<workspace handle>`, as `aireg registry show atlan`
  lists the handle.
- Keep any other keys the manifest already has, such as `artifacts`.
- `version` on an entry is only for a skill or agent whose current version is
  a plain number such as `1`, for one publish; see the skill's Plain-number
  versions section.

## Agent with source files

A folder agent publishes `AGENT.md` and every file beside it, at the same paths:

```text
agents/release-manager/
├── AGENT.md               → front matter: name, description; body: the instructions
├── SOUL.md                → agent file SOUL.md
└── workspace/
    └── checklist.md       → agent file workspace/checklist.md
```

`agents/release-manager/AGENT.md`:

```markdown
---
name: release-manager
description: Prepares release notes and checks each release against the team checklist.
---
Draft the release notes from the merged pull requests, then work through
workspace/checklist.md before you mark the release ready.
```

Its manifest entry names the skills it uses, which are exported too:

```yaml
exports:
  - skills/meeting-brief
  - path: agents/release-manager
    depends_on: [./skills/meeting-brief]
```

A merge that edits, adds, or deletes any file in the folder publishes the agent's
next version, and the preview lists each file change first. A single-file agent,
such as `.claude/agents/triage.md`, is exported the same way with its file as
`path`; it has no other files.

To hand over an agent Atlan already holds, give `AGENT.md` its exact `name`. If
its current version is a plain number such as `1`, add a version for the first
publish only, as for any skill or agent with a plain-number version:

```yaml
  - path: agents/release-manager
    version: 1.0.0           # remove in a later pull request; versions then follow on
    depends_on: [./skills/meeting-brief]
```

## Workflow

`.github/workflows/atlan-skill-sync.yml`. Replace `main` with the default
branch. The secret is named `AIREG_TOKEN_ATLAN`, like the variable `aireg` reads.

```yaml
name: Sync skills and agents to Atlan

on:
  pull_request:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

concurrency:
  group: atlan-skill-sync-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  # Comments on the pull request with what merging publishes. aireg reads the files and never runs them.
  preview:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: atlanai/ai-registry-action@v1
        env:
          AIREG_TOKEN_ATLAN: ${{ secrets.AIREG_TOKEN_ATLAN }}

  # Publishes the manifest's exports from the default branch only.
  publish:
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    permissions:
      contents: read
      pull-requests: write # comments on the merged pull request when publishing fails
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: atlanai/ai-registry-action@v1
        env:
          AIREG_TOKEN_ATLAN: ${{ secrets.AIREG_TOKEN_ATLAN }}
```

What each part does, so you can keep it when adapting an existing workflow:

- **One workflow, two jobs.** The preview runs on `pull_request`, never on
  `pull_request_target`. The action withholds the key from a fork, from a pull
  request that changes `registries:`, and from one that merges into another
  branch.
- **The default branch publishes.** A push or manual run on any other branch
  does not publish, and a rerun of an older commit is refused.
- **Conflicts fail the preview** when a skill the pull request changes was also
  published since its base. To report them without failing, add
  `with: { fail-on-conflict: false }` to the preview step, only if the user
  chose that.
- **Approval gate.** To require a review before each publish, add
  `environment: <name>` to the publish job and keep the key as that
  Environment's secret. Keep a repository secret of the same name too, or the
  preview runs without a key.
- **No path filters.** The action compares every export with the workspace, so
  a filter only risks skipping a manifest edit.
- **Pins.** Keep `actions/checkout` on a full commit SHA. `@v1` follows the
  action's 1.x releases; pin a full commit SHA instead if the repository's
  policy requires it.

## Migrate from the older setup

A repository on the older setup has `.atlan/package.json` and the earlier
Atlan registry workflows, `atlan-preflight.yml` and `atlan-publish.yml`.

| Older setup | New setup |
| --- | --- |
| `publishConfig.skills.paths` in `.atlan/package.json` | one `exports` entry per skill folder under each path |
| `publishConfig.skills.exclude` | leave those folders out of `exports` |
| `publishConfig.workspaces` (workspace IDs) | `publish.to: atlan/<handle>`; resolve the ID to its handle. More than one workspace needs the user to choose one |
| `atlan-preflight.yml` | delete it; the new workflow's preview job replaces it |
| `atlan-publish.yml` | replace it with the new workflow, or rename it and keep its custom policy |
| `ATLANAI_TOKEN` secret | ask the user to add the same Atlan API key as `AIREG_TOKEN_ATLAN`, which the new workflow reads, then to delete `ATLANAI_TOKEN` |
| `workspace`, `artifacts`, `prune`, `mode`, `preflight` inputs | drop them; the manifest replaces them |

Check the setup pull request's preview before merging: skills the older setup
already published should show as unchanged or updated, not new. A skill shown as
new, or refused as a collision, is not the same Atlan skill; stop and report it
rather than merging. If the preview refuses one as "not a SemVer version",
follow the skill's Plain-number versions section. Leave
`.atlan/package.json` in place unless the user asks to remove it, since other
Atlan tools may still read it, and say that CI no longer reads it.
