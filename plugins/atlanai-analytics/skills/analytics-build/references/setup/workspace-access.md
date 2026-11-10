# Help with the workspace decision

Use for questions about a setup's destination, access to it, or creating a new workspace.
Answer the question before resuming setup. An explanation-only request needs no source
survey, sample metric, Registry call, permission question or publication plan.

## New workspace requested

The Analytics workflow can list and select accessible workspaces. It does not create them
through a CLI or API. Keep local drafts intact. Give one paragraph under 70 words containing
the capability limit, the supported UI/admin action, and the later refresh and selection.
Do not offer a different destination unless asked, invent UI menu labels, or narrate a
future publication step the person has not requested.

For example:

"Analytics can list existing workspaces, but cannot create one through its CLI or API.
Your drafts remain saved locally. Create the workspace in Atlan's UI or ask an administrator;
then I can refresh your accessible workspaces and help you select it."

This is an explanation of capability, not evidence of a tool call or changed access.
Do not claim a workspace was created, selected or refreshed during an explanation-only turn.

## No accessible workspace

Use the latest successful current-host read if one was supplied. Do not repeatedly retry
the same read or suggest reinstalling an already-working connection. Explain that local
work is preserved and ask the person to request access to an existing workspace. Do not
introduce workspace creation when they have not asked for a new one.

If this is also the completed setup handoff, follow [the handoff rules](preview-publish.md):
retain the actual counts, one sample and its check status, and supported local exploration.
Do not imply that publishing is required to inspect or query a prepared local dataset.

## Existing destination selected

Use the exact accessible workspace named by the person. Once the necessary access exists,
resume from saved candidates with [reconciliation and publication](preview-publish.md).
Selecting a destination does not erase earlier holds, source reviews or definition evidence.
