# The link to a shared output

The Atlan app's own Copy link builds this link for an output, and the share builds the same
one. It opens the output in the Atlan desktop app, or offers the download to someone who does
not have it yet. A colleague outside the output's workspace lands on a page that names the
workspace and offers to join it.

## What goes in it

- **The dashboard origin** of the environment this connection reaches: `https://app.atlan.com`
  when the `atlan` server is `api.atlan.com`, `https://app.dev.example` when it is
  `api.dev.example`. The server's URL names it, or the `uri` of any artifact read with
  `verbose: true`. A link on another environment's origin opens nothing, so never assume
  production. The examples below use production.
- **The organization's slug**, such as `acme`: the first segment of the user's Atlan dashboard
  address (`https://app.atlan.com/acme/…`). Take it from the request, a dashboard address the
  user pasted, or `org` in `.atlan/share.json`. The registry does not return it, and no
  workspace name is it: the `org_default` workspace's `name` is often a generic one such as
  `org-workspace`. The app sends a link with a slug the reader does not belong to back to its
  home page, so a guessed slug gives a link that opens nothing.
- **The output's handle**, from the create result: `output_…`.
- **Its workspace**, `workspace_…`, and its display name. The app uses these only to label the
  join page; they grant nothing.

## How to build it

1. The app path, with the query encoded as a form (a space becomes `+`, other characters
   outside letters, digits, `-`, `_`, `.` and `*` become `%XX`):

   ```
   /acme/outputs/output_01abc?ws=workspace_01xyz&n=Q3+churn+report&utm_campaign=output_share&utm_source=agent&utm_content=output_01abc
   ```

2. That whole path percent-encoded once more, after `<origin>/?open=`:

   ```
   https://app.atlan.com/?open=%2Facme%2Foutputs%2Foutput_01abc%3Fws%3Dworkspace_01xyz%26n%3DQ3%2Bchurn%2Breport%26utm_campaign%3Doutput_share%26utm_source%3Dagent%26utm_content%3Doutput_01abc
   ```

With a shell, build it rather than encoding by hand:

```
node -e 'const [origin,org,id,ws,n]=process.argv.slice(1);const q=new URLSearchParams({ws,n,utm_campaign:"output_share",utm_source:"agent",utm_content:id});console.log(origin+"/?open="+encodeURIComponent(`/${encodeURIComponent(org)}/outputs/${encodeURIComponent(id)}?${q}`))' https://app.atlan.com acme output_01abc workspace_01xyz "Q3 churn report"
```

The link carries no version, so it opens the newest one and keeps working after the next share.
Without a slug, give the hand-off prompt alone and do not guess one.
