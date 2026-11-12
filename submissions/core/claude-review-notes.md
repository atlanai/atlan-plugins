# Claude candidate review notes

Claude validated the candidate branch on 12 November 2026 with no blocking package errors,
two warnings and thirteen policy holds. This does not approve the plugin. GitHub account
verification still fails because atlanai SSO hides the repository from the linked account.
The form is open under Atlan; a server-saved submission draft is not yet confirmed.

## Responses to prepare for the reviewer

| Finding | Evidence and proposed explanation |
|---|---|
| Network and process capabilities in the core module | `claude-hooks/register.ts` runs the bundled `command-hooks/receipt_store.py` through Python to check and capture local receipts. `checkLatest` fetches the fixed public manifest URL and parses its version for an update notice. The manifest response is not executed as code. Review these paths and their guards; do not treat the presence of both capabilities as proof they are chained together. |
| Local data and conversation access with a network path | The README describes the selected-folder publish helper, checked receipt destinations and fixed-host version check. Confirm that outgoing publish content stays in the explicit Atlan call, and that the update request carries no conversation, files or credentials. Do not claim the helper has no filesystem access. |
| Tool-input changes | The helper expands guarded local file references into the existing publish call and captures receipts. The README already describes this behavior. It does not grant tool permission. |
| Credential finding in `claude-hooks/tests/fixtures.ts` | The flagged environment-variable text and package-registry URL occur in synthetic test fixture strings. They are not commands run during plugin installation. The fixture exercises handling of credential-like content. Keep the tests and explain their role. |
| Credential finding in `claude-hooks/tests/publish.test.ts` | The fake key is constructed for local tests of output refusal. Tests assert it is withheld from results. These tests are not registered as runtime hooks. They do not forward a user's key. |
| Aggregate credential finding in the manifest | The scanner describes a credential called `pass $$` in `atlan-find-artifacts`. The current skill describes tool arguments and passing filters; it does not read an environment variable with that name. Ask the reviewer to inspect the exact match rather than introducing a credential prompt for it. |
| Brand and publisher resemblance | Atlan owns this submission and the existing catalog connector/plugin. Trident uses `https://api.atlan.com/mcp`; the catalog uses `https://mcp.atlan.com/mcp`. They are distinct products from the same publisher. Keep the installed identifier `atlanai` and the display name Atlan Trident. |
| Download-and-run warning | The skill-install procedure includes a package-install path. Confirm that it is requested by the user and matches the documented workflow. A warning is not authorization to execute it during review preparation. |

These explanations are drafts for human review, not signed policy attestations. Do not remove
security tests, conceal capabilities or weaken the helper to suppress findings. A maintainer
should review any actual helper change. Test all claimed runtime behavior with the review
account before submission.

## Remaining form inputs

The data-handling step asks whether the plugin reads or stores personal data, whether skills
send to services beyond declared connectors, retention duration and under-18 eligibility.
No unconfirmed answers have been entered. The listing imports the candidate display name
and components from GitHub. Finish GitHub reconnection before saving or submitting.
