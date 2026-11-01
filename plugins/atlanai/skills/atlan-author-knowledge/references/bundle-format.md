# Knowledge bundle format

Atlan stores Open Knowledge Format (OKF) v0.2 bundles. This is what the create hook accepts.

## The tree

- A bundle is a directory tree inside one `.zip`. Paths are relative to the zip root: no
  leading `/`, no `.` or `..` segments, no drive letters.
- **A concept is any `.md` file that is not reserved.** Its concept ID is its path minus
  `.md`: `metrics/revenue.md` is `metrics/revenue`.
- **`index.md` and `log.md` are reserved** at every level, not only the root. `tables/index.md`
  is the listing for `tables/`, not a concept. Omit them and they are synthesized on read: an
  `index.md` grouped by `type`, and a `log.md` from the bundle's version history. Author one
  and yours is served instead.
- Other files - a `.py` attester, an `.html` visualization - are kept and served, and mint no
  concept.

## A concept document

UTF-8 markdown in two parts: a YAML frontmatter block between `---` lines, then the body.

```markdown
---
type: Metric
title: Revenue
description: Billed amount, net of refunds.
status: stable
tags: [finance]
resource: https://bi.example.com/metrics/revenue
verified: { by: 'human:jdoe', at: '2026-06-25T09:00:00Z' }
stale_after: 2027-01-01
---
Revenue is the billed amount ...
```

### Required

| Key | Rule |
|---|---|
| `type` | Non-empty string. A document without it refuses the **whole** bundle. |

A document with no frontmatter block, or one that does not parse as a YAML mapping, also
refuses the whole bundle. The refusal names the path.

### Indexed

These are promoted into queryable columns on the concept. Everything else is kept in the
document and in `frontmatter_extra`; nothing you write is dropped.

| Key | Notes |
|---|---|
| `title` | Becomes the concept's display name. |
| `description` | Shown in directory listings and search. |
| `tags` | A list of strings. |
| `status` | `draft`, `stable` or `deprecated` - the editorial lifecycle, stored as `okf_status`. |
| `resource` | The underlying asset. Absolute `http(s)`, no userinfo, no credential-bearing query parameter - otherwise the bundle is refused. |
| `verified` | One attestation `{by, at}` or a list of them. A `human:` verifier makes the concept human-reviewed; any other makes it machine-confirmed. |
| `sources` | What the concept derives from. |
| `stale_after`, `generated.at` | RFC 3339 instants, or a bare `YYYY-MM-DD`. Anything else is kept in the document and not indexed. |
| `usage_window` | Kept as written. |

`trust_tier` and `content_digest` are computed by the server. Do not set them.

## Limits

| Limit | Value |
|---|---|
| Uploaded `.zip` | 16 711 680 bytes (16 MiB less 64 KiB) |
| Decompressed tree | 64 MiB |
| Entries | 10 000 |
| Path length | 1024 bytes |
| Directory depth | 16 |
| Inline files / batch operations | 1–100 |
| Inline file / write content | 65,536 UTF-8 bytes |
| Total new inline / batch content | 1 MiB |
| Inline / edited path length | 512 bytes |
| Bundles per `extension__knowledge__create` call | 100 |

A breach is refused before the archive finishes inflating.

## Joining a concept to something else

A concept declares one edge: `documents`, pointing at an `output` it describes. Create it from
the concept with `registry__relate_artifact` and `rel_type: documents`. A bundle declares no
edges of its own.
