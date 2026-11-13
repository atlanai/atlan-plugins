# Inputs still needed for the Claude submission

The bundle draft is saved, its GitHub source is readable and its package has no blocking
validation error. Policy holds may be submitted for human review. The fields below were
read directly from the saved Claude form on 12 November 2026.

## Prepared draft answers

| Field | Answer | Basis |
|---|---|---|
| Personal data | Reads and stores | Account identity, registry content and session records can include personal data. |
| Services beyond declared connectors | Yes, listed in README | The README lists authorized downstream services, GitHub, npm and Atlan download requests, with their data and triggers. |
| Retention | Longer | The live Trident trace reference states no automatic expiry. It distinguishes content deletion from retained structure. |
| Intended for under-18 users | No | Inferred from the business audience and the privacy notice's children section; not a claim of age verification. |
| Review contact | rohan@atlan.com | Explicitly selected by the submitting owner. |

Sources: [Trident trace privacy](https://platform.atlan.com/registry/traces/references/limits-and-privacy),
[Trident overview](https://platform.atlan.com/), and
[Atlan privacy notice](https://atlan.com/privacy-policy/) sections 4, 11, 14 and 15.
The privacy notice gives purpose/legal-need retention, not a numeric deletion deadline.
The Agentic Data Catalog's AI retention guidance belongs to a different product.

## Required for final owner review

Review and accept the legal and security attestations below. They remain unchecked.
The privacy notice is broad; the owner must confirm it adequately covers Trident's
stored content and its documented absence of automatic trace expiry.

The four required checkboxes say:

1. I have read and agree to the Software Directory Terms and the Directory Policy on behalf of Atlan.
2. The plugin's privacy policy accurately describes the data it handles.
3. The plugin does not exfiltrate credentials or execute code outside its declared MCP servers.
4. Anthropic may contact the selected email about this submission and future policy changes.

The third statement needs the security owner's explicit review of the declared local
helper and the directory's treatment of it. Do not tick it simply because the package
validator passes. The portal says policy-held versions can still be submitted, but a
feature limited to Verified organizations may be refused for another organization.
The publisher's eligibility for that feature is a reviewer question, not a claim made here.

## Separate launch and connector prerequisites

The service owner must provide a dedicated populated review tenant, test credentials and
sign-in instructions for the companion MCP connector at https://api.atlan.com/mcp. Enter
secrets privately in the portal, not in this repository or a public PR. The existing catalog
connector uses a different MCP URL and must not be overwritten. Execute the supplied
functional cases against that account before claiming they pass.

The repository requires a maintainer security review before the helper changes merge.
This is separate from accepting the directory terms. No merge has been performed.

Version 0.5.58 supplies Atlan's published Support Center and SaaS Agreement links for the
OpenAI metadata requirements. The publishing owner reviews their applicability to Trident
before accepting attestations. OpenAI remains a separate draft requiring MCP domain
verification, dedicated reviewer access, live test results and a walkthrough recording.
