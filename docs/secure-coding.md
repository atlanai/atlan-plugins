# Secure coding in atlan-plugins

Read this before writing or changing code here. It lists the security patterns this repository
already follows and the anti-patterns to avoid. Match the pattern already in the code; flag
anything you cannot match. References name the file and the function or constant, not a line,
so they survive edits. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## What this code guards

Plugin content that installs into users' agents (Claude Code, Codex, Cursor, Copilot): skills and
commands that steer an agent's calls to the `atlan` MCP server at `https://api.atlan.com/mcp`,
the core's publish helper that reads skill folders off the user's disk and sends them inline, and
the `atlanai-mods` hooks that gate registry writes. The risks are a skill or hook that leaks local
files or credentials, sends them to a non-Atlan server, or lets a destructive registry call run
unconfirmed. There is no service code and no stored credential here; sign-in is the host's OAuth.

## Follow these patterns

- **One HTTPS endpoint, from one source.** The MCP URL is `[mcp] url` in `plugin.toml`, and
  `tools/build.py` refuses any URL that is not `https://`, including a variant's `[rewrite]`
  URLs (`validate` and `load_variant`).
- **Publish only to the Atlan server.** The command hook refuses `extension__skill__create` on any
  other server: `is_atlan_server` and `guard` in `plugins/atlanai/command-hooks/publish_hook.py`
  require HTTPS, an `atlan.com` host or subdomain, the `/mcp` path, no URL credentials or
  fragment, and only the optional `tools=full` query when the host supplies a URL. A stdio launch command is refused.
  Without URL metadata, command hooks retain the conventional Atlan namespace, including
  aliased names such as `atlan-personal`. Wildcard runner matching handles other aliases when
  the event supplies a valid origin. Claude Code exposes no server URL in its hook tool
  inventory: `gatewayRunners` recognizes conventional Atlan aliases only alongside their
  gateway search and describe tools. Ambiguous server names use the native fallback without
  reading local bytes, even if they expose similarly named discovery tools. These names identify the configured route, not a cryptographic server
  identity. The host's configured connection and permissions remain the trust boundary.
- **`files_from` stays inside the working tree.** Resolve the real path, require it under the
  session's roots, refuse `~`, UNC and drive-relative labels, and refuse a folder that is itself a
  link: `place` in `publish_hook.py` and in `register.ts`.
- **Never walk into credentials.** Skip `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.azure` and
  `gcloud`; refuse key files (`.env*`, `.pem`, `.key`, `.p12`, `.pfx`, `id_*`), login files
  (`.npmrc`, `.netrc`, `.pgpass`, `.pypirc`, `.git-credentials`), Terraform variables and state
  (`*.tfvars`, `*.tfstate`) and secret-shaped content, including passwords set to a literal or
  inside a URL, with placeholders let through: `CREDENTIAL_DIRS` in `publish_hook.py` and `claude-hooks/bundle.ts`, and `KEY_FILES`
  and `SECRETS` in `plugins/atlanai/skills/atlan-author-skills/scripts/check_bundle.py` and
  `bundle.ts`.
- **A shared output is scanned too.** `registry__publish_output` with inline `file.content` is
  held to the same `SECRETS`: a match denies the call and names the kind, line and column, never
  the value. The scan reads only the call's own text, and a fault sends the call as it was:
  `outputRefusal` in `register.ts`, `output_refusal` in `publish_hook.py`.
- **Bounded, no-follow reads.** 128 files, 256 KiB and depth 8 (`MAX_FILES`, `MAX_BYTES`,
  `MAX_DEPTH` in `check_bundle.py` and `bundle.ts`), checked before anything is read (`walk` in
  `publish_hook.py`); files open with `O_NOFOLLOW`, so a file swapped for a link fails.
- **Hooks fail open to the unchanged call, never to a wider one.** On any fault the call passes
  through as sent, and the server refuses an unexpanded `files_from` by name: `main` in
  `publish_hook.py` exits 0 with no output, and each handler in `register.ts` ends in `.catch`.
- **Destructive registry calls always ask.** Archive, approve, move, release and roll-back confirm with the blast
  radius, and are refused where nobody can answer: `ALWAYS_CONFIRM` in
  `plugins/atlanai-mods/hooks/registry.ts` and `NO_APPROVER` in `hooks/register.tsx`.
- **Subprocesses by argv list, with a timeout.** No shell strings in bundled scripts; see `run`
  in `plugins/atlanai-analytics/skills/analytics-build/scripts/registry_inspection.py`.
- **Fence untrusted data in prompts.** The analytics runner labels approved instructions apart
  from `Untrusted input data` (`analytics_case.py`, same folder).
- **CI least privilege.** `permissions: contents: read`, `persist-credentials: false`, SHA-pinned
  actions and a pinned Claude Code CLI in `.github/workflows/ci.yml`.

## Avoid these anti-patterns

Metric and saved-query batches use the same helper through `registry__create_artifacts`.
`items_from` names one JSON file and `items_sha256` binds its bytes; the selected workspace
must match every item. The inline `items` list must be empty, so an unavailable hook cannot
write anything. The helper accepts no other kinds and retains the 256 KiB limit, no-link
reads, credential checks and Atlan-only routing. Batch outcomes are independent; never apply
the skill publisher's keyed retry rule to a bulk create.

Opt-in `receipt_dir` capture strips its local field before a call. The shared
`command-hooks/receipt_store.py` writer accepts only guarded metric/query creates, released
artifact reads and relationship reads. It checks the request and native response, bounds
each file to 256 KiB and uses directory descriptors, no-follow opens, private permissions,
fsync and an atomic create-only link. Incomplete temporary files are never accepted.
Command hooks save the pending destination under a hash of session/tool identity, because
post-call input can have already been rewritten. No recording failure retries a remote
mutation. The Analytics assembler checks hashes, exact definitions, release identity and
planned edges before invoking the existing publication validator. Conflicts remain pending.

- **Hand-editing generated manifests.** A changed URL or header in `.mcp.json` or `mcp.json`
  drifts from `plugin.toml` and fails `make check`; change `plugin.toml` and run `make build`.
- **A second MCP server, hooks or `bin/` in an add-on or mod.** `lint_addon` and `lint_mod` in
  `tools/lint.py` refuse them; two `atlan` servers show every tool twice.
- **Widening the publish helper.** No new read roots, no following links, no shrinking
  `CREDENTIAL_DIRS`, `KEY_FILES` or `SECRETS`, no limits raised past the server's. Keep the
  Python (`check_bundle.py`, `publish_hook.py`) and TypeScript (`bundle.ts`, `register.ts`)
  copies identical; `tools/tests/fixtures/publish-bundles.json` holds the cases both must pass.
- **Trusting another server's tool annotations.** The mod leaves non-Atlan tools to the user's
  permission rules (`plugins/atlanai-mods/README.md`); do not auto-allow them.
- **Skill text that asks the agent to read or send secrets, env vars or files outside the task.**
  Skills steer real agents that hold user credentials, so review skill text as code.
- **Credential-shaped strings anywhere.** The `SECRETS` scan in `lint_repository`
  (`tools/lint.py`) fails on them; use placeholders such as `registry__…`, never a real handle,
  token or tenant name.
- **Symlinks or paths outside a plugin root.** `lint_repository` refuses symlinks; they also break
  installs.

## Before you open a PR

- [ ] `make check` passes (build drift, lint with its secret and symlink scan, `tools/tests`,
      analytics selftests).
- [ ] `make validate` passes, or the change touches no manifest, skill, command or hooks.
- [ ] Generated files come from `make build`; the MCP URL is still `https://api.atlan.com/mcp`.
- [ ] Publish-helper changes keep the Atlan-only guard, tree containment, no-follow reads,
      credential skips and limits, in both `command-hooks/` and `claude-hooks/`, with tests.
- [ ] Mod changes keep archive, approve, move, release and roll-back confirming, and
      `claude plugin test plugins/atlanai-mods` passes.
- [ ] New skill text reads no files, env vars or credentials beyond its task and calls only the
      Atlan tools it names.
- [ ] Workflow edits keep `contents: read`, SHA pins and `persist-credentials: false`.

The Claude receipt hook reuses the absolute interpreter path returned by its successful local
helper process, avoiding repeated environment-launcher startup. It still validates the request
and destination before every call and validates and atomically records every response.

Copilot loads its command adapter from `com.github.copilot/hooks/hooks.json`. The adapter
accepts both documented payload spellings and returns `modifiedArgs` without an `allow`
decision. Since Copilot omits tool-use ids, its pending receipt key binds the current session
to the exact native request. A conflicting destination blocks before dispatch. Original and
rewritten post-call arguments resolve to the same checked request; a changed local batch
leaves recording pending. Post-call failures never dispatch another tool. Unsupported or
redacted outputs cannot establish a completed publication.
