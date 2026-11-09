# Name the definitions behind an answer, as links

Read when an answer, a preview or a handoff cites a metric or saved query. People read
names, not ids: `metric_01… · rel_01…` tells them nothing and opens nothing.

## What to show

One line per metric or saved query the answer used: its name as a link to its page in
Atlan Trident, then its released version in plain words when the read returned one.

"This answer used [Gross margin](https://app.atlan.com/?open=…) (version 2),
[Rework cost](https://app.atlan.com/?open=…) and [Material waste](https://app.atlan.com/?open=…)."

Never print a bare artifact id or release id in an answer. Give ids in code only when the
person asks for them. The link's address carries the handle, so the definition is still
identified.

## How to build the link

The page for an artifact is `/<org>/brain/<kind>/<handle>`: kind `metric` with a `metric_…`
handle, or kind `sql_query` with a `sql_query_…` handle. It needs three things:

- **The dashboard origin** of the environment this connection reaches: `https://app.atlan.com`
  when the `atlan` server is `api.atlan.com`. Another environment has its own origin; never
  assume production.
- **The organization's slug**: the first segment of the person's Atlan Trident address
  (`https://app.atlan.com/acme/…`). Take it from the request, an address the person pasted,
  or `org` in `.atlan/share.json`. The registry does not return it and no workspace name is
  it. Never guess one: a wrong slug opens the home page.
- **The handle, workspace id and name** from the read that answered the question.

Add `ws` (the workspace id) and `n` (the name) as a form-encoded query, then percent-encode
the whole path once more after `<origin>/?open=`:

```
/acme/brain/metric/metric_01abc?ws=workspace_01xyz&n=Gross+margin
https://app.atlan.com/?open=%2Facme%2Fbrain%2Fmetric%2Fmetric_01abc%3Fws%3Dworkspace_01xyz%26n%3DGross%2Bmargin
```

With a shell, build it rather than encoding by hand:

```
python3 -c 'import sys,urllib.parse as u;o,org,kind,h,ws,n=sys.argv[1:];print(o+"/?open="+u.quote("/%s/brain/%s/%s?%s"%(u.quote(org,safe=""),kind,u.quote(h,safe=""),u.urlencode({"ws":ws,"n":n})),safe=""))' https://app.atlan.com acme metric metric_01abc workspace_01xyz "Gross margin"
```

The link opens the page in the Atlan app, or in the browser for someone without it.

## Without the slug

Show the names alone, with the workspace that holds them: "This answer used Gross margin,
Rework cost and Material waste, saved in Analytics Demo." Then ask once per conversation, in
one line: "Paste any Atlan Trident address from your browser and I will link each one."
Keep the slug for the rest of the conversation. Do not ask again after a refusal.

Definitions that exist only on this machine have no page: name them and say they are local.
