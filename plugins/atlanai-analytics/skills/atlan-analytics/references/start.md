# Start with context, finish with an answer

Use this for `start`, `setup`, `query-history`, a bare Analytics invocation, or a supplied
repository of queries and models the person wants to ask questions about. Claude Code has
the `/atlanai-analytics:start` shortcut. Keep the existing plugin name and namespace.
Other hosts can invoke the skill with the same mode arguments or use a natural prompt.

For a supplied project folder, or "Set up this project for analytics" in the current project,
follow [local project setup](project.md) and return. Its survey uses models, saved SQL and
query history together when available. It publishes both context types and suggests questions;
it waits for a question when the user has not supplied one. Explicit query-history-only and
metrics-only requests retain their routes below.

## Select the source, not an agent project

Use any source already supplied. For `query-history`, do not ask the person to choose it
again. Without a source, ask one concrete question: **Dashboard link or file** or
**Query history or a folder of SQL files**. If an actual accessible local example folder was
provided, offer it by name too; never invent a demo URL or claim a sample repo is installed.
If the person supplied both, use both within their stated scope. Do not require a dashboard.

Natural requests and explicit arguments select the same routes:

| Request | Route |
| --- | --- |
| `dashboard <Power BI link>` or "Convert this dashboard to an agent" | [Dashboard](dashboard.md) |
| `query-history <export or folder>` or "Use these queries so I can ask questions" | Context and first answer below |
| `metrics <scope>` | [Metrics only](metrics.md), stop after definitions |
| `build`, `eval`, `improve` | [Explicit agent work](full-agent.md) |

Only claim file formats the actual parser supports. A Tableau/public URL is not proof of
model access. Request a supported export when needed, and describe that limit. Do not launch
a browser tour, custom dashboard, localhost chat UI or a dedicated agent to complete setup.

## Context and first answer

1. Use [metrics](metrics.md), sections 0 through 6, with `.atlan-metrics/<slug>/` state. Record
   `entrypoint: query-history` and the original question in state so resumption preserves the
   first-answer goal. This is an orchestration wrapper around metrics mode: explicit metrics
   requests retain its stop gate; this entry continues into `atlan-analytics-ask` afterward.
2. Inspect only the supplied folder or bounded history scope. Reuse its existing database
   adapter. An export does not require a live query-history login. Do not request Snowflake
   when the supplied data runs locally. If table scope is clear from the supplied SQL and
   schema, state that scope and continue instead of repeating a questionnaire.
3. Explain a workspace once: "where your team will find and reuse these definitions".
   Verify and reuse a previously selected or explicitly supplied workspace. Otherwise ask
   by accessible workspace name, not ID. Keep write authorization and host permissions intact.
4. Mine, author and check the supported definitions. Explain found, usable and needs-work
   counts with plain reasons. Preview one actual metric inline when requested: calculation,
   population/time scope and check status. Never rely on a visual card to convey the result.
5. Review the exact usable definitions, destination and release actions together, obtain any
   missing authorization once, and finish the approved saves/releases. A narrower supported
   subset is useful; do not make unavailable definitions a gate for everything else.
6. Continue with `atlan-analytics-ask` for the user's question, or choose one representative
   supported question, state it and execute it. Re-fetch the released definition through MCP
   and run fresh read-only SQL. Say what answered and give up to three further questions.
   Do not call draft-only context published or use cached/eval results as the answer.
7. If release is unavailable, a successfully checked local definition can answer with the
   label "Local answer; not yet shared in Atlan". Failed or unexecuted definitions do not
   qualify. Without a data connection, preserve the work and state the one prerequisite for
   answering. Never claim completion of a question that did not run.

No agent registration or independent eval is required by this entry. Show independent
accuracy as Not run when absent. If the person wants deeper evaluation or a separate deployed
agent afterward, use the existing explicit modes. Do not claim this chat's usage appears on
an agent page without verified tracing.
