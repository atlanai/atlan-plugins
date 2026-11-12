# Atlan Trident listing and disclosures

Use the listing copy below for the core plugin. Data-handling answers are drafts that
need the service owner's confirmation before any compliance attestation.

## Listing copy

| Field | Draft |
|---|---|
| Display name | Atlan Trident |
| Installed identifier | `atlanai` |
| Publisher | Atlan, subject to verification in each portal |
| Short description | Skills, agents and tools |
| Category | Developer Tools in OpenAI; development in the Claude marketplace manifest |
| Website | https://atlan.com |
| Privacy notice | https://atlan.com/privacy-policy/ |
| Support | Awaiting approved public URL |
| Terms | Awaiting approved public URL |
| Submission contact | Awaiting Atlan contact email |

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
| Personal data | The service may process account identity and user-supplied registry content, which can contain personal data. | Confirm all categories and how the privacy notice covers them |
| Storage | Registry artifacts and audited session records are stored by Atlan. User requests can create versioned skills, agents, knowledge, files and outputs. | Confirm retention, deletion, backups and trace/session content |
| Other services | Authorized requests can reach downstream MCP servers registered by the organization. Data sent depends on the selected tool and request. | Confirm subprocessors, disclosures and whether the portal treats these as declared connectors |
| Permissions | Access is controlled by the connected account and organization permissions. Write workflows ask for confirmation where the skill specifies it. | Live tests of role boundaries, tenant isolation and destructive-tool annotations |
| Local execution | Repository installs include the publish helper and its local receipts. The OpenAI directory package omits hooks. Skills can use project files and CLIs where the host supports them. | Confirm host-specific limitations in live tests |
| Credentials | OAuth credentials are managed by the authentication flow; the public package contains no account credentials. | Verify refresh, revocation, host callbacks and UserInfo verified-email claims |
| Retention period | Not yet confirmed. | Exact periods for artifacts, sessions, traces and deleted data |
| Regions and countries | Not yet confirmed. | Approved hosting/distribution regions and launch countries |
| Under-18 eligibility | Not yet confirmed. | Service owner's answer under Atlan's applicable terms |

Do not answer that the service stores no data. The package itself supplies instructions
and a connection, while the remote service stores registry content and audit records.
Do not claim read-only access: the core includes authoring and publication workflows.

## Release notes

Atlan Trident 0.5.56 changes the core display name to Atlan Trident while keeping the
installed identifier atlanai. The OpenAI directory package omits lifecycle hooks and
custom MCP headers and declares OAuth with CIMD preference. The build accepts an approved
support URL. Core skills are unchanged. The Claude helper always launches python3 and the bundled receipt writer, rather than a path returned by that writer. Its update request now exposes the fixed public URL at the call site. Receipt guards and write-confirmation behavior are preserved.
Analytics and Registry Mods remain separate. Review cases, data disclosures and a recording
script are supplied as drafts; live review execution is still pending.
