# Link a local repository agent

Published once from this machine; CI does not republish on merge, and enabling
Git sync later needs `atlan-connect-repo`. Start from
[repository linking](link-repository-agent.md): its interview chose this mode,
and its discovery, Registry target, credential rules and verification apply here
too. A local agent needs its `repo` registered and a `sourced_from` edge, as
**Resolve the Registry target** there says.

## Prepare the declaration

Use `.atlan/agents/<slug>.yaml` as the committed agent declaration, following
the reference. Preserve unrelated files and existing declarations. Keep optional
skills/files in associations; project only the `agent` kind's fields into a
temporary non-secret JSON payload for creation.
Use the declaration path as source identity. Do not migrate existing inline
configs or identities without an explicit reviewed plan.

Prepare the credential's private path before any write, as
[Runtime credentials](link-repository-agent.md#runtime-credentials) says.

## Apply and resume

These steps apply a local agent, and local skills for a skills-only request.

1. Check the definition against the `agent` kind schema. Its non-secret
   temporary JSON projection is the create payload; it is not a second
   committed declaration. Strip unset optional nulls on create.
2. Require repository-relative readable paths; reject traversal, escaping
   symlinks, secret files, Git/control-file uploads, duplicate/case-colliding
   destinations and unsupported media/size. Never execute attachments.
3. Publish the local skills: run `aireg publish <skill-path>... --to
   <registry>/<workspace-handle> --dry-run`, then without `--dry-run`. The
   handle is the selected workspace's slug from `registry__list_workspaces`,
   not its display name or ID. When aireg refuses a changed skill because its
   current version "isn't a SemVer version" (an artifact created through the API is numbered
   1, 2, ...; one published as a skill starts at 0.1.0),
   name the next one: `--to <registry>/<workspace-handle>/<skill>@<x.y.z>`. Use a registry
   `aireg registry list -g` already declares for the selected org; ask before
   declaring one. Publish only
   committed folders, since `aireg` records the last commit with each version.
   An `aireg` refusal for a same-named skill is a collision. Follow
   [the identity gate](agent-manifest.md),
   never overwrite reuse-only skills, and verify workspace, repo when applicable,
   source type/path, digest and active status. Digest equality alone is not a
   binding.
4. Create the absent agent, keeping its reveal-once identity in the private file:

   ```bash
   aireg atlan api post /agent/v1/agents --registry <registry> --input <payload.json> \
     --output-file <private-json-path> --output-field identity
   ```

   The payload carries `workspace_id`, the definition fields and the
   declaration path as `source_id`; omit the legacy `repo_id`. Record the printed non-secret ID
   immediately, then use
   `registry__relate_artifact` with that agent as `artifact_id`, the verified repo
   as `subject_id`, `rel_type: sourced_from`, and `attributes: {"path":
   ".atlan/agents/<slug>.yaml"}`. Its result is the edge. If linking fails, resume
   linking the recorded agent ID; never create another agent or credential.

   If the command printed an agent ID but exited non-zero, the agent exists and
   no file was written. Do not recreate it. Only for an agent this run just
   created, mint its credential into the same path with the rotate call in
   [Runtime credentials](link-repository-agent.md#runtime-credentials), then link
   it as above. If that fails too, report the agent ID.
5. Update a verified existing agent by its ID with `registry__patch_artifact`.
   The patch is an RFC 7396 merge: an
   omitted key is kept and an explicit `null` clears it, so send only the fields
   the declaration changed and a `null` only when the user means to clear one.
   Details: `skills/atlan-author-agents/references/update-an-agent.md`. An update
   does not mint or rotate credentials.
6. Upload owned files through `atlan-content`, creating each `file` with
   `parent_kind: agent`, `parent_id: <agent-id>` and the stable destination as
   `path`. A path that already holds a file is a conflict to resolve, not to
   retry. Verify file ID, digest, version, workspace and exact owner/path slot. Source code stays in Git; upload only the
   small prompts, policies, schemas and configuration that define the agent.
7. Pin the requested skills in the agent's `skills` manifest, in the create
   payload or as a patch:

   ```
   registry__patch_artifact {
     "artifact_id": "agent_01J...",
     "patch": { "skills": [ { "skill_id": "skill_01K..." } ] },
     "session_id": "mcp_session_01J...",
     "rationale": "pinning the review skill this repository's agent declares"
   }
   ```

   An entry without `version_ordinal` pins the skill's current release. The
   registry derives one `uses_skill` edge per pinned skill and archives the
   rest, so do not write those edges by hand. Changing the manifest of an
   existing agent publishes its next version. Details:
   `skills/atlan-get-started/references/edges.md`.

Use dry-runs where supported; a lookup is not a simulated write, and an upload
has no dry run. Never
force drift, bypass blocked scans or prune undeclared files/edges without
explicit approval.

Record completed phases and returned IDs after every operation. On a failure,
re-read state instead of blindly retrying creates. Credential
persistence failure after agent creation must report the ID without recreating
it. Apart from the step 4 recovery, a fresh credential is a separate, explicit
rotation decision.
