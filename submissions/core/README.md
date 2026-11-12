# Prepare the Atlan Trident submissions

The core submission changes target version `0.5.55`, displayed as **Atlan Trident**,
on top of current main at `0.5.54`. The earlier local `0.3.21` preparation is superseded.
No legal attestation or review submission has been made.

## Build and review

Run `make build`, `make check`, `make validate` and `make codex-directory`.
The OpenAI upload draft is `dist/codex-directory/atlanai-0.5.55.zip`.
It contains the portable manifest, OAuth MCP configuration, skills, assets and licenses.
It omits lifecycle hooks and custom MCP headers. Repository installs keep their hooks and
client-identification header. Do not edit generated files.

The Claude portal reads GitHub, not a ZIP. Submit this repository with plugin path
`plugins/atlanai`, tracking the branch that contains these reviewed changes. Claude Code
also receives the core publish helper; chat and Cowork use the skills and remote connection.
The separate Analytics and Registry Mods plugins are outside this submission.

| Material | Purpose |
|---|---|
| `listing-and-disclosures.md` | Draft listing, release notes and data-handling answers |
| `review-cases.json` | Exactly five positive and three negative scenarios, unexecuted |
| `review-guide.md` | Sample fixtures, reviewer access, recording script and test record |
| `claude-review-notes.md` | Candidate scan findings and draft reviewer explanations |

## Needed from the publishing owner

- Approved support URL, terms URL and submission contact email. Set `plugin.support_url`
  and `plugin.terms_url` in `plugin.toml`, then rebuild; do not guess legal pages.
- Dedicated populated review tenant and account, with sign-in instructions entered privately
  in the portals. No real customer data. OpenAI reviewers must not need MFA or email codes.
- Confirmed retention, deletion, downstream data flows, hosting regions, age eligibility and
  launch-country availability.
- Atlan-owned publishing identities and portal permissions. Claude Owner access is verified;
  OpenAI business verification and submission access remain unchecked.
- An owner who can serve OpenAI's domain challenge at the exact URL the portal provides.

## Claude portal findings, 12 November 2026

The account is signed into the Atlan Claude organization, where Rohan has the Owner role.
The portal fetched current GitHub main and validated `plugins/atlanai`, but reports that
GitHub did not accept the connected account for this repository. The repository checker
specifically says atlanai SSO is hiding repositories and asks to reconnect GitHub.
This is not proof of push access. Complete the reconnection and rerun validation.

The current remote source still displays AI Registry, and its core includes a publish-helper
hooks module. The portal reported two warnings and fourteen policy holds. Several holds
concern that helper; others concern credentials and brand resemblance. These are review
findings, not a local test failure, and must be checked against the exact candidate commit.
Do not submit the old local core or silently remove current helper behavior to clear them.

Submit the MCP endpoint `https://api.atlan.com/mcp` as a connector too, under the same
Atlan organization, then pair it with the bundle. The existing Atlan directory connector
uses `https://mcp.atlan.com/mcp`, a different product. Do not overwrite it.

At [the Claude developer portal](https://claude.ai/directory/manage), validate the candidate
branch, inspect the listing, finish verified data-handling answers, and save the draft.
Legal attestations and Submit for review require the owner's final review. None of the
unknown data-handling answers should be filled just to advance the form.

## OpenAI handoff

At [the plugin portal](https://platform.openai.com/plugins), use Atlan's verified business
identity and an eligible project. Upload the hookless ZIP with MCP, verify the domain,
connect the dedicated reviewer account and resolve tool, skill and metadata findings.
Run the eight cases, record the walkthrough, then import the finalized cases and video
into the submission package's OpenAI review fields before requesting review.

Public OAuth discovery advertised CIMD, DCR, S256, openid, email and UserInfo on
12 November 2026. Authenticated callback behavior and verified-email claims are untested.
Neither portal validation nor publication has completed for the new candidate.

## Requirements

- [Anthropic plugin submission](https://claude.com/docs/plugins/submit)
- [Anthropic connector review](https://claude.com/docs/connectors/building/review-criteria)
- [OpenAI submission](https://developers.openai.com/plugins/deploy/submission)
- [OpenAI packaging and OAuth](https://developers.openai.com/plugins/build/plugins)
