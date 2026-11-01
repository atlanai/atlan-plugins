# Compatible repository agent declaration

## Repository identity gate for skills

Choose the publishing authority before the first Registry write:

- **Git-backed:** `atlan-connect-repo` prepares repository tracking and CI performs
  the first publish from committed content, for skills and for agents with their
  source files. Do not pre-publish through the local
  CLI. After merge, require a successful publish run and exact read-back before
  using the returned skill IDs.
- **Local:** `aireg publish` publishes the skill without promising future Git
  synchronization. Explain that changing to Git-backed identity later requires a
  supported adoption/rebinding path.

In a checkout with a remote, `aireg` records the folder, remote and last commit as
source (without a remote it records `local` and the skill's name),
but nothing republishes on merge, so it is still local publication. A verified
repo record also does not bind a later upload by itself. An absent skill may be
created by CI. If an existing candidate has local, missing, foreign, or ambiguous
provenance, stop before writing. Never treat local publication as a temporary
first phase of Git synchronization.

Git actions retain their own authorization boundaries. Preparing files does not
authorize a commit, push, pull request, merge, or secret change. Preserve dirty
work and never stamp uncommitted bytes as a published Git revision.

Derive create, unchanged reuse, or version append from verified remote identity
and digest. New skills require creation; changed existing skills append to their
verified IDs. Ambiguous identity and name collisions remain blocked regardless
of publishing approval.

Resolve remote candidates with paginated documented reads. Independently check
`workspace_id`, `source_type` and `source_id` on each candidate, then read its
active `sourced_from` relationships and verify the exact repo target and
`attributes.path`. The legacy `repo_id` field is not proof of a unique source;
server-side filter flags are not proof of scope.
Missing fields require an exact detail read, not assumptions. Multiple matching
heads, an incomplete inventory, or inconsistent responses block writes.

An active same-digest skill is reusable as repository-owned only when its identity
also matches. A name/digest match with local, absent or foreign provenance is a
collision to resolve, not proof of absence and not a license to create a second
artifact. Preserve its ID. Use a documented, authorized adoption/rebinding path
only when it exists and preserves history/relationships; otherwise report the
capability gap. Renames, moves and destination changes need the same review.

After publish, fetch each returned exact ID and verify identity, active status,
digest and version. If identity is missing or wrong, report partial completion
and stop dependent linking. Never claim bound from digest alone.

## Conflict recovery

Classify each failed item by structured error and exact remote state. A generic
409 is not success, even when its prose mentions identical content. Confirm a
documented duplicate-content outcome and exact identity/digest before reporting
an item unchanged; a nonzero publish remains failed. Mixed batches can contain
successful writes, so retain per-item IDs/phases and re-read before any retry.

Never propose archiving, deleting, renaming, forcing, or recreating as an experiment
to free a uniqueness constraint. Soft deletion may retain that constraint, and
recreation can break references. If ownership repair is unsupported, retain the
artifacts and report the CLI/gateway blocker with sanitized evidence. Do not hide
it by ignoring errors or narrowing selection merely to avoid the failing items.
Fixing one collision does not resolve independent
unchanged-content failures. Retry only after the cause and safe retry scope are
established; do not re-drive the entire batch blindly.

## Git-backed agent

A Git-backed agent is a folder with `AGENT.md`, exported by `atlan-connect-repo` in
`.registry/manifest.yml`. CI publishes `AGENT.md` and every file beside it, at
the same paths:

```text
agents/reviewer/
├── AGENT.md                 → front matter: name, description; body: the instructions
├── review-policy.md         → agent file review-policy.md
└── scripts/
    └── check.sh             → agent file scripts/check.sh
```

`agents/reviewer/AGENT.md`, with the real prompt as its body:

```markdown
---
name: reviewer
description: Review repository changes.
---
Report actionable findings using review-policy.md, and run scripts/check.sh on
each changed file.
```

The manifest entry `atlan-connect-repo` writes for it, naming the skills it uses:

```yaml
exports:
  - skills/code-review
  - path: agents/reviewer
    depends_on: [./skills/code-review]
```

Files the agent needs go inside its folder; there is no `associations` list.
Model, tools, framework, provider, step limit, and public URL are not in the
folder: CI leaves them as they are, and the skill sets them afterwards.

To hand over the agent declared in the YAML below, keep `name: reviewer` in
`AGENT.md`. CI finds the agent by name and updates it, its `sourced_from` edge
moves to the folder, and it keeps its original `source_id`. If its current
version is a plain number such as `1`, `atlan-connect-repo` adds `version: 1.0.0`
to the entry for the first publish and removes it afterwards.

## Agent declarations

Commit .atlan/agents/<slug>.yaml, preserving existing source identity:

```yaml
name: reviewer
display_name: Code Reviewer
description: Review repository changes.
instructions: Report actionable findings using the attached guidance.
max_step_count: 20
tools: []
associations:
  skills:
    - path: skills/code-review
  files:
    - path: docs/review-policy.md
      as: references/review-policy.md
    - path: scripts/check.sh
      as: scripts/check.sh
```

Skills, files, tools, model_id, agent_framework_id, agent_provider_id,
max_step_count and public_url are optional; use the live `agent` kind schema for
required fields and structured tool shapes. Associations are link-agent metadata,
not API fields: create and update with a temporary non-secret projection holding
only the kind's fields.
Do not implicitly clear remote values or prune omitted associations.

## Field mapping

Derive the declaration from repository evidence rather than asking the user to
translate their code into Registry fields. Keep `name`, `display_name` and
`description` concise and repository-specific. For the optional fields:

| Field | Set when | Omit when |
|---|---|---|
| `instructions` | A real prompt or instruction source exists; preserve it rather than synthesizing one. | Only a product description or README summary can be found. |
| `agent_framework_id` | A live `agent_framework` artifact exactly matches the implementation. | The runtime is custom or unclear. |
| `agent_provider_id` | The repo explicitly uses a registered `agent_provider`. | Location is only inferred from deployment files. |
| `model_id` | A pinned model resolves to a live Registry model. | The model is inherited, environment-selected, or ambiguous. Never send `inherit`. |
| `tools` | Each value matches the live `agent` schema's tool reference shape. | They are ordinary application function names. |
| `max_step_count` | The runtime enforces a real limit. | The number would be invented for the Registry. |
| `public_url` | The deployed agent has a maintained `http` or `https` address. | The URL is local, temporary, or only a deployment guess. |

Blank strings are not placeholders: a present-but-blank `instructions` or
`model_id` is refused. Omit an unknown optional field. Never copy credentials,
environment values, customer data, or secret-bearing paths into the declaration.

Use .atlan/agents/reviewer.yaml as the stable source_id and verify workspace,
repo and source metadata when resolving an existing agent. Keep returned IDs
in the partial result for safe resume; do not invent binding fields in a
publisher schema. Never duplicate an agent because its declaration moved.

Before creation, ensure .atlan/agent-credentials/ is ignored and untracked.
Write it with `aireg atlan api ... --output-file
.atlan/agent-credentials/<workspace-id>/<slug>.json --output-field identity`,
a new private destination. Never overwrite, read, print or commit its
contents. Updates use the verified agent ID and never rotate credentials.

Inline agents in .atlan/package.json and credentials in .env are not part of
this contract. Use the YAML/private-JSON creation path above for a local agent.
A Git-backed agent is a folder with `AGENT.md` exported in
`.registry/manifest.yml` instead; see the skill's Git-backed sections.
