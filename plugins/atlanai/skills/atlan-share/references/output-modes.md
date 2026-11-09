# `output` modes in full

## The exclusivity table

`mode` is the only required property. Everything else is conditioned on it, and the create
and update hooks enforce it - the schema cannot.

| | `mode: "upload"` | `mode: "link"` |
|---|---|---|
| `file_artifact_id` | **required** | must be absent |
| `file_version_ordinal` | **required**, ≥ 1 | must be absent |
| `external_url` | must be absent | **required** |

Setting a field from the wrong column is a hook refusal naming the conflict. Setting only
half of an `upload` pair is also a refusal - both pin fields travel together.

## Why the pin is an ordinal, not just a file

A new version of an output is a new version of *the output row*, carrying its own pin. So
an old output version keeps resolving the bytes it was published with, even after the
underlying `file` has gained versions.

A relationship could not express that - an edge points at a line, not at a point on it -
which is the whole reason this is a column pair rather than a `pins_file` edge.

Get the ordinal from the `file` create response. It is handed to you there; reading it back
later costs a call and a chance to read the wrong one.

## The pin is resolved under your bearer

The hook calls the file read as **you**, against that exact ordinal. Two consequences:

- A file you cannot read is a refusal at create time, not a dangling reference discovered
  later.
- The error distinguishes the cases: `file <id> has no version <n>` is a real file with a
  wrong ordinal, and that is different from a handle you cannot see.

So upload first. Recording an output that points at bytes you have not uploaded yet cannot
succeed.

## `external_url` validation

Shape only, and **never dereferenced**. Fetching it would make the gateway a request
forwarder into whatever network it can reach.

Refused:

- a relative or non-`http(s)` URL
- **userinfo** in the authority (`https://user:pass@host/...`)
- **any credential-bearing query or fragment parameter** - which means a presigned URL is
  refused, and so is anything with a token in the query string

If your deliverable is behind a credential, upload it (`mode: "upload"`) rather than
linking to a URL that carries one. A URL with a secret in it is a secret in a catalogue
row.

## What an output deliberately lacks

No `media_type`, `size_bytes` or `content_uri` columns. All three belong to the pinned
`file` and are embedded into the read, so there is no second copy to drift out of step with
the bytes.

## Discoverable, and not create-only

Unlike `session` and `session_message`, an `output` is **discoverable**: it shows up in
cross-kind list and search, because it is the thing someone goes looking for. And it is
**not** create-only, because iterating on a deliverable is the use case - which is exactly
the property that makes the version rail worth having.
