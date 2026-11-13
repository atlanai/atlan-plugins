# Atlan trident listing and disclosures

Use the listing copy below for the core plugin. Data-handling answers are prepared from published sources and package behavior.
The owner reviews the privacy and security attestations before submission.

## Listing copy

| Field | Draft |
|---|---|
| Display name | Atlan trident |
| Installed identifier | `atlanai` |
| Publisher | Atlan, subject to verification in each portal |
| Short description | Skills, agents and tools |
| Category | Developer Tools in OpenAI; development in the Claude marketplace manifest |
| Website | https://atlan.com |
| Privacy notice | https://atlan.com/privacy-policy/ |
| Support | https://docs.atlan.com/support/submit-request |
| Terms | https://atlan.com/docs/atlan-saas-agreement.pdf |
| Submission contact | rohan@atlan.com (private review contact) |

Description:

> Connect your coding agent to the Atlan registry with one OAuth sign-in. Search your
> organization's skills, agents, knowledge and MCP servers, install a registry skill into
> the current project, reach every MCP server your organization registered through one
> gateway, and publish skills, agents and outputs back with the registry's own authoring
> recipes.

Starter prompts:

- Connect me to Atlan
- Find an Atlan skill for this task and install it
- Publish the skill in this folder to Atlan

Account prerequisites: an Atlan account, organization access and permissions for the
requested action. Downstream integrations require that the organization register and
authorize those servers. Local project installation requires a host with filesystem access;
chat-only hosts can read and follow a skill instead. This core package excludes Analytics
and Registry Mods. Do not promise those features in the listing.

## Data-handling answers for confirmation

| Portal topic | Proposed answer | Still needed |
|---|---|---|
| Personal data | Reads and stores account identity and user-supplied registry content, which can contain personal data. | Owner reviews privacy-notice coverage |
| Storage | Registry artifacts and session records are stored by Atlan. Trident documents no automatic trace expiry and separate content deletion. | No universal purge or backup deletion deadline claimed |
| Other services | Yes, listed in README. Authorized requests can reach registered downstream services; repository and installer workflows can contact GitHub, npm and Atlan downloads. | README lists the destination, trigger and data |
| Permissions | Access is controlled by the connected account and organization permissions. Write workflows ask for confirmation where the skill specifies it. | Live tests of role boundaries, tenant isolation and destructive-tool annotations |
| Local execution | Repository installs include the publish helper and its local receipts. The OpenAI directory package omits hooks. Skills can use project files and CLIs where the host supports them. | Confirm host-specific limitations in live tests |
| Credentials | OAuth credentials are managed by the authentication flow; the public package contains no account credentials. | Verify refresh, revocation, host callbacks and UserInfo verified-email claims |
| Retention period | Longer. Trident documents no automatic trace expiry. | Review privacy coverage; no universal deletion deadline is claimed |
| Regions and countries | Not yet confirmed. | Approved hosting/distribution regions and launch countries |
| Under-18 eligibility | No, based on the business audience and children section of the privacy notice. | Owner reviews this intended-audience inference |

Do not answer that the service stores no data. The package itself supplies instructions
and a connection, while the remote service stores registry content and audit records.
Do not claim read-only access: the core includes authoring and publication workflows.

## Release notes

Atlan trident 0.5.59 changes the core display name to Atlan trident while keeping the
installed identifier atlanai. The OpenAI directory package omits lifecycle hooks and
custom MCP headers and declares OAuth with CIMD preference. Support and terms links use
the published pages linked from Atlan's footer, subject to the owner's final applicability
review. Both Atlan action uses in the repository-sync template now name a verified full
commit, with signed runtime updates disclosed. The new behavior regression is pending
Claude Code authentication. The Claude helper always launches python3 and the bundled receipt writer, rather than a path returned by that writer. Its update request now exposes the fixed public URL at the call site. Receipt guards and write-confirmation behavior are preserved.
Analytics and Registry Mods remain separate. Review cases, data disclosures and a recording
script are supplied as drafts; live review execution is still pending.
