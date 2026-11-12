# Inputs still needed for the Claude submission

The bundle draft is saved, its GitHub source is readable and its package has no blocking
validation error. Policy holds may be submitted for human review. The fields below were
read directly from the saved Claude form on 12 November 2026.

## Required to submit the bundle

| Input | Exact form choice or confirmation needed |
|---|---|
| Personal data | Confirm Reads and stores. Registry content and audit records are stored remotely and may contain names, emails or other user-supplied personal data. |
| Services beyond declared connectors | Approve Yes, listed in README, or No after the service owner checks the published skills and declared connector boundary. Identify the relevant services and data if Yes so the README can accurately list them. Do not assume that one gateway declaration automatically covers every downstream service. |
| Retention | Confirm the applicable category, Under 30 days or Longer, and the actual policy for artifacts, session/trace content and deletion. The proposed answer cannot be Not retained while those records persist. |
| Under-18 users | Confirm whether the product is intended for users under 18: Yes or No. |
| Review contact | Confirm rohan@atlan.com, already filled by the form, or supply the monitored Atlan mailbox to use instead. This address is not public. |
| Legal and security attestations | An authorized Atlan representative must review and accept the four statements below at submission time. The agent has not checked any of them. |

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

Support, documentation and terms-of-service URLs are missing from the listing preview,
but the current Claude bundle form does not mark them as blocking requirements. Supply
approved pages for listing quality. Support and terms URLs are required by the OpenAI MCP
submission path, so that submission remains a separate pending step.
