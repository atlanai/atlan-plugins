# Prepare the Atlan trident submissions

The core submission changes target version `0.5.58`, displayed as **Atlan trident**,
on top of current main at `0.5.54`. The earlier local `0.3.21` preparation is superseded.
The owner submitted Claude separately. OpenAI remains an unpublished draft.

## Build and review

Run `make build`, `make check`, `make validate` and `make codex-directory`.
The OpenAI upload draft is `dist/codex-directory/atlanai-0.5.58.zip`.
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

- Review the Support Center and SaaS Agreement linked from Atlan's public footer,
  now set in `plugin.toml`. Confirm their applicability to Trident before accepting
  attestations. The review contact is rohan@atlan.com.
- Dedicated populated review tenant and account, with sign-in instructions entered privately
  in the portals. No real customer data. OpenAI reviewers must not need MFA or email codes.
- Confirmed retention, deletion, downstream data flows, hosting regions, age eligibility and
  launch-country availability.
- Atlan-owned publishing identities and portal permissions. Claude Owner access is verified;
  OpenAI submission access under Atlan Enterprise was verified on 13 November.
- An owner who can serve OpenAI's domain challenge at the exact URL the portal provides.

## Claude portal findings, 12 November 2026

The account is signed into the Atlan Claude organization, where Rohan has the Owner role.
GitHub reconnection succeeded and the portal confirmed repository access. The Atlan
Trident draft was saved. The candidate scan passes with zero blocking errors, two warnings
and thirteen policy holds. The exact findings and explanations are in
`claude-review-notes.md`; the remaining form inputs are in `submission-inputs.md`.

The 13 November branding candidate displays Atlan trident with the Registry PNG icon.
Its live scan passes with zero blocking errors, six warnings and fifteen policy holds.
The added findings concern non-executable image references and compressed image bytes;
all fifteen holds have explanations in the review notes. The core publish-helper module
is included. The remaining holds concern declared capabilities, synthetic tests and fuzzy name matches.
They require human review; package validation alone does not approve them.

Submit the MCP endpoint `https://api.atlan.com/mcp` as a connector too, under the same
Atlan organization, then pair it with the bundle. The existing Atlan directory connector
uses `https://mcp.atlan.com/mcp`, a different product. Do not overwrite it.

At [the Claude developer portal](https://claude.ai/directory/manage), validate the candidate
branch, inspect the listing, finish verified data-handling answers, and save the draft.
Legal attestations and Submit for review require the owner's final review. None of the
unknown data-handling answers should be filled just to advance the form.

## OpenAI handoff

The Atlan Enterprise draft is saved as Atlan trident. Its MCP setup requests a plain-text
challenge at `https://api.atlan.com/.well-known/openai-apps-challenge`. Keep the challenge
value and reviewer credentials out of this public repository. The initial upload reported
missing support and terms URLs; version 0.5.58 supplies Atlan's published pages. Portal
rechecks, domain verification, reviewer access and live cases remain necessary.

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

## Final branding review

The display name is Atlan trident. The package includes the latest matching Trident logo
found in the Registry Library on 13 November 2026, the 600 by 600 PNG updated on
10 November. Claude declares it with the directory icon field, alongside explicit privacy
and Trident documentation links. The installed identifier remains `atlanai`.
