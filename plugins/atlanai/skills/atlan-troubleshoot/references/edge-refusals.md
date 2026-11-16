# Cloudflare edge refusals

Read when a tool returns HTTP 403 with HTML headed `Attention Required!` / `Cloudflare`,
rather than a normal MCP JSON error. The edge blocked the HTTP request before a normal tool
response. This is not evidence of a SQL syntax error or a missing warehouse grant.

Keep the Ray ID, UTC time, endpoint, tool name and a credential-free reproduction. Ask the
endpoint owner to inspect that Security Event and correct a confirmed false positive.
The exact rule comes from the event; do not infer its ID from a similar incident.
Do not blindly retry, change credentials, or rewrite/encode the payload to evade the block.
A model endpoint's exception does not establish that the same exception is safe for MCP.

After an authorized operator corrects the rule, retry the original request unchanged and
retain the normal MCP result. A passing control query does not validate the blocked query.
