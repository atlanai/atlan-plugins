# The link to a shared output

The Atlan app's own Copy link builds this link for an output, and the share builds the same
one. It opens the output in the Atlan desktop app, or offers the download to someone who does
not have it yet. A colleague outside the output's workspace lands on a page that names the
workspace and offers to join it.

## What goes in it

- **The organization's slug**: the `name` of the `org_default` workspace that
  `registry__list_workspaces` returned in step 2, such as `acme`.
- **The output's handle**, from the create result: `output_…`.
- **Its workspace**, `workspace_…`, and its display name. The app uses these only to label the
  join page; they grant nothing.

## How to build it

1. The app path, with the query encoded as a form (a space becomes `+`, other characters
   outside letters, digits, `-`, `_`, `.` and `*` become `%XX`):

   ```
   /acme/outputs/output_01abc?ws=workspace_01xyz&n=Q3+churn+report&utm_campaign=output_share&utm_source=agent&utm_content=output_01abc
   ```

2. That whole path percent-encoded once more, after `https://app.atlan.com/?open=`:

   ```
   https://app.atlan.com/?open=%2Facme%2Foutputs%2Foutput_01abc%3Fws%3Dworkspace_01xyz%26n%3DQ3%2Bchurn%2Breport%26utm_campaign%3Doutput_share%26utm_source%3Dagent%26utm_content%3Doutput_01abc
   ```

With a shell, build it rather than encoding by hand:

```
node -e 'const [org,id,ws,n]=process.argv.slice(1);const q=new URLSearchParams({ws,n,utm_campaign:"output_share",utm_source:"agent",utm_content:id});console.log("https://app.atlan.com/?open="+encodeURIComponent(`/${encodeURIComponent(org)}/outputs/${encodeURIComponent(id)}?${q}`))' acme output_01abc workspace_01xyz "Q3 churn report"
```

The link carries no version, so it opens the newest one and keeps working after the next share.
Without an `org_default` workspace in the list there is no slug: give the hand-off prompt alone
and do not guess one.
