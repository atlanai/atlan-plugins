# Claude policy-hold audit

The v0.5.57 candidate passed the live Claude portal validation on 12 November 2026:
zero blocking errors, two warnings and thirteen policy holds. GitHub reconnection is
complete and the draft is saved under Atlan. No review submission or legal acceptance
has been made.

## Changes made

- The receipt helper now always launches `python3` and the bundled
  `command-hooks/receipt_store.py`. A `python_executable` value returned by that program
  is treated as data and never selects a later executable. The regression test supplies
  a different executable path and verifies that it is never run.
- The version check now spells the fixed public GitHub URL at its `$.http.fetch` call,
  with no body or authorization headers. That clears the unknown-network-destination
  finding. Capability calls remain in their original module, as the directory requires.
- The core README now explains the exact local program, receipt writes, network request
  and synthetic tests. The installed identifier stays `atlanai` and the display name
  stays Atlan trident.

The count remains thirteen: the unknown-destination hold cleared, and the explicit
`python3` executable received an interpreter/inline-script classification. This is a
changed set of thirteen findings, not thirteen cleared findings. No checks were removed,
no tests were hidden and no helper capability was removed to suppress a finding.

## Each remaining hold

| # | Portal finding | Disposition and reviewer explanation |
|---|---|---|
| 1 | Network plus command/tool execution | Reviewer explanation. The public update response is parsed only for its version. It is never executed. The local process is the bundled receipt writer, and the actual publish still uses the existing authorized Atlan tool call. |
| 2 | Program or arguments not fully fixed text | Reviewer explanation. The executable is fixed to python3. The script path is beneath the installed plugin root; the only mode values passed by the two internal callers are check and capture. Request and result data travel through stdin, not shell interpolation. |
| 3 | Starts other programs | Reviewer explanation. The declared Python receipt writer validates a selected directory and records a bounded native result. The five-second timeout, no-follow paths, atomic writes and failure behavior remain in place. |
| 4 | Starts a shell, network tool or inline script | Reviewer explanation of the interpreter classification. This is python3 running a bundled file. No shell, command-string execution, inline -c code or network utility is launched by this call. |
| 5 | Local data plus outbound network capability | Reviewer explanation. Receipt content stays in the checked working tree. The fixed public GitHub GET contains no file content, session data or credentials. Explicitly selected publish content remains in the existing authorized Atlan MCP request. |
| 6 | Conversation access plus outbound network capability | Reviewer explanation. Hook context is used for capability/update notices and permission-preserving publish handling. The update GET is independent of conversation content and sends no conversation body. No hook sends feedback. |
| 7 | Credential forwarding in tests/fixtures.ts | False-positive explanation. The DB_PASSWORD text and registry URL are strings in synthetic test cases. They do not read an installer's environment, and this module is not a runtime entry point. Keep the refusal tests for inspection. |
| 8 | Credential forwarding in tests/publish.test.ts | False-positive explanation. FAKE_AWS_KEY is constructed as test data; assertions check that credential-shaped output is refused and not echoed. Tests are not registered as installation hooks. |
| 9 | Aggregate pass $$ credential finding | False-positive explanation. The scanner attributes this to the atlan-find-artifacts skill. Its source contains no dollar-sign credential expression or environment read. It describes passing search/session arguments. Ask the reviewer to check the match. |
| 10 | Installed name atlanai resembles Atlar | Ownership/distinct-product explanation. Atlan trident is an Atlan product, with no claimed affiliation with Atlar. Existing installs depend on the atlanai identifier. The public label is Atlan trident. |
| 11 | Installed name atlanai resembles Atlas | Ownership/distinct-product explanation. The plugin makes no claim to represent Atlas. Its Atlan publisher, Trident display name and production MCP endpoint identify its own product. |
| 12 | GitHub publisher atlanai resembles Atlar | Publisher explanation. The repository is the Atlan-owned source for this product; the submitting Atlan organization already owns its catalog listings. A reviewer must resolve the fuzzy publisher comparison. |
| 13 | GitHub publisher atlanai resembles Atlas | Publisher explanation. Same ownership evidence; no claimed relationship with Atlas. Renaming or transferring the repository would change established distribution for a fuzzy match. |

The two warnings concern tool-input rewriting and a download-and-run install procedure.
Both are documented. Input expansion retains the original tool permission checks, and
installation is a user-requested skill workflow. Neither warning is a blocking error.

These are proposed reviewer responses supported by source inspection and local tests,
not an attestation that Anthropic has accepted them. Runtime security claims still need
review-account testing before launch. The maintainer security review required by
AGENTS.md applies before merging the helper changes.

## Validation

The repository check passed 618 tests (98 skipped) and both analytics selftests. Core
helper tests passed 88 cases, and make validate passed. After the portal-specific module
layout correction, the 88 helper tests, 97 tool/build tests, generated-file drift and lint
checks passed again. Live portal validation of the corrected code had zero blockers.
All reviewer-tenant functional scenarios remain pending.
