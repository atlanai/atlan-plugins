# Link a Git-backed repository agent

CI keeps this agent in sync: merged changes to its folder, its `AGENT.md` and its
skills publish to the Registry. Nothing is published or created from this
machine. Start from [repository linking](link-repository-agent.md): its
interview chose this mode, and its discovery, Registry target and verification
apply here too.

## Prepare the agent's folder

The agent's definition is a folder with `AGENT.md`, or a
single-file agent such as `.claude/agents/<name>.md` when that is how the
repository already defines it. See the Git-backed example in
[the manifest contract](agent-manifest.md). Give `AGENT.md` front matter with `name` and
`description`, and keep the real prompt as its body, which becomes the agent's
instructions. Put the small prompts, policies, schemas, and configuration that
define the agent inside the folder; CI publishes every file in it, so keep
secrets, local settings, and large data out, following the path rules in
[the local flow](link-local-agent.md) step 2.
For an agent that already exists in Atlan with a `.atlan/agents/<slug>.yaml`
declaration, give `AGENT.md` the same `name`: CI finds the agent by name and
updates it, and `atlan-connect-repo` handles its one-time version. Keep the YAML
until that first publish is verified, then offer to remove it.

Then hand the selected skill paths, agent folders, each agent's skills, and the
workspace to `atlan-connect-repo`, which prepares `.registry/manifest.yml` and
the sync workflow. A Git-backed agent needs no separate `repo` registration:
`aireg` records the repository with each version. Reuse Git-backed skills only
by the exact IDs verified after CI; never push them locally.

A Git-backed flow may pause after local preparation, PR creation, merge, or CI;
resume from the first unverified phase rather than switching to local
publication.

## Finish after the merge

After the setup pull request merges and its publish run succeeds:

1. Read back the agent by name in the workspace with `registry__list_artifacts`
   and `registry__get_artifact`. Exactly one active agent must match, with this
   repository as its source, its folder's files as its files, and one
   `uses_skill` edge per `depends_on` skill. Its active `sourced_from` edge
   names the folder; an agent handed over from a `.atlan/agents/<slug>.yaml`
   declaration keeps that path as its `source_id`, which is expected. More than
   one match is a blocker to report, never to resolve by archiving.
2. Set what CI leaves alone, from repository evidence only: `model_id`,
   `tools`, `agent_framework_id`, `agent_provider_id`, `max_step_count`, and
   `public_url`, with `registry__patch_artifact` (an RFC 7396 merge; see
   `skills/atlan-author-agents/references/update-an-agent.md`). Never patch the
   instructions, files, or skill pins of a Git-backed agent: the next merge
   publishes the repository's version over them.
3. Offer a runtime credential only when the agent authenticates as itself, for
   example to emit traces, and only issue it if the user agrees. CI creates the
   agent without one. Mint it as
   [Runtime credentials](link-repository-agent.md#runtime-credentials) says, into a
   private path that is ignored and untracked. Never read or print it.

Later changes to the agent's folder, its `AGENT.md`, or its skills merge through
pull requests, where the preview shows what each merge publishes.
