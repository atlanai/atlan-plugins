# Name the definitions behind an answer, as links

Read when an answer, a preview or a handoff cites a metric or saved query. Render each
name as a clickable Markdown link, not a bare artifact id or URL.

Every saved-context answer ends with **Proof in Atlan Trident**, one line per metric and
query used. Include the released version in plain words when the read returned one.
Use the artifact's name as the link label, such as Gross margin or Margin by job.
Keep artifact, workspace and release ids inside URLs only; do not repeat them in prose,
code spans or caveats. A proof line needs only the linked name and released version.
Show a workspace's display name only when it helps, never its handle. Do not append
hypothetical warnings about valid, successfully resolved links.

## Discover the organization automatically

Never ask the person for an organization slug or an Atlan URL, and never make either a
prerequisite. The authenticated self-profile provides the slug:

```
GET /registry/v1/users/me
```

The gateway proxies this to the control plane's `/v1/users/me`. The response contains
`org_id` for the active login and `orgs`, whose rows contain `id` and `slug`. Select exactly
the row where `orgs[].id == org_id`, then use its `slug`. Never take the first membership:
a person can belong to several organizations. Read only these fields; email, names,
roles, permissions and credentials are unnecessary for composing a link.

Use the host's existing authenticated API transport for this fixed GET when available.
Do not invent an MCP tool name: the current Registry MCP catalog does not expose this
self-profile read. In Codex and Claude Code with the Atlan CLI, the bundled helper uses:

```
atlanai api get registry:/users/me --jq '{org_id, orgs: [.orgs[] | {id, slug}]}'
```

The CLI handles authentication internally. The helper first checks its non-secret auth
status against the current MCP gateway, resolves the active organization, and reads the
exact artifact to verify its id and workspace against the MCP receipt. It checks auth
status again before returning a link, so an account switch cannot mix organizations.
A separate CLI login by itself is insufficient; the artifact/workspace check is required.

For `https://api.atlan.com`, use dashboard origin `https://app.atlan.com`. Other environments
must pass their configured gateway and dashboard origins to the helper. Resolve again
after a connection or account switch; never reuse another organization's slug.

Workspace names and `GET /registry/v1/organisations` do not provide this URL slug.
In particular, `org_default.name` can be `org-workspace`; it is not an organization slug.
Do not read credential files, call a token-printing command or decode tokens in the agent.
Do not rely on a pasted address or a saved user-supplied slug.

If neither the host's authenticated HTTP transport nor a matching signed-in CLI is
available, report that specific connection limitation and keep the answer with named
proof. Do not substitute a URL/slug question or pretend the proof has a working link.
Never offer Skip links or Paste later. A transport failure is a connection issue to fix,
not a request for information from the person.

## Build the Metrics app link

Use `/<org>/apps/metric` on the verified dashboard origin. The outer query holds `path`
(the encoded inner route) and `ws` (the artifact's workspace id from its read receipt).

- Metric inner route: `/m/<metric_id>?workspace=<workspace_id>`.
- Saved query inner route: `/queries/<sql_query_id>?workspace=<workspace_id>`.

Encode the inner query first, then encode the whole inner route as the outer `path`
parameter. Catalog/filter state is optional navigation context and is not required to
open a definition. Do not use the old `/brain/metric` or `/?open=` routes for these items.
Use each artifact's own workspace when an answer combines definitions from several.

Use the bundled standard-library helper to encode the route, passing non-secret values
as separate arguments. The helper discovers the slug itself:

```
python3 <analytics-ask>/scripts/definition_link.py --origin https://app.atlan.com --kind metric --handle metric_example --workspace workspace_example
```

This returns a URL of the form:

```
https://app.atlan.com/example-org/apps/metric?path=%2Fm%2Fmetric_example%3Fworkspace%3Dworkspace_example&ws=workspace_example
```

Render it as `[Gross margin](<returned URL>)`. For a saved query, use `--kind sql_query`
and the returned `sql_query_...` handle. The helper verifies the active organization and exact artifact workspace before composing
the link. Use the workspace returned with that artifact, not a guessed default.

## Context kept on this machine

Local metrics and queries have no Atlan page. Cite their local definition by name and
path, label them as local, and say that saving to Atlan makes direct proof links available.
Never fabricate a saved link for an unpublished item or let a missing link block an answer.
