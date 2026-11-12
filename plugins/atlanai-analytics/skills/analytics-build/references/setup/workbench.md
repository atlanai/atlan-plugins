# Native conversation from setup to first answer

Read on starting or resuming setup. Claude and Codex own the interface: use their normal
messages, task list, code disclosures and question controls. Progress is
[the setup checklist](checklist.md) in chat and that task list when available. Open no side panel, browser view or
other surface unless the person asks for [a visual view](render.md), apart from the run's own
[side view](visuals.md).

## One useful result, one next action

Orient before tools. Use preset demo intent, or [the interview](intent.md) for own data:
card 1 and the chosen branch's ask, before any data is read, through the host's question
control or ordinary chat. Put what the person must read to decide (the use cases and their
tables, or what will be built) in the question's visible prompt, not only in commentary. Free text is always valid. Do not invent a tool or display a
menu of internal commands. A suggested or preselected choice is not an answer.

Use [the presentation flow](presentation.md) for the completed-collection review after the run's
checks. A provisional definition has no measured value. Keep SQL
and additional candidates available on request. Read the appropriate stage
procedure for the real engine action; the presentation helper never executes it.

| Saved stage | Host behavior |
| --- | --- |
| Inspect and Scope | Show what was found, establish business focus then purpose, and resolve remaining source scope. |
| Build | Resume the existing engine checkpoint for the whole selected collection; show factual progress. |
| Check | Check the exact definition and parameters. Preserve failed and untested status. |
| Preview | Show the sample before an existing destination or Keep local choice. |
| Publish | Carry the authorized plan through reconciliation, release and full readback. |
| Ready | Suggest evidenced questions and answer a question already supplied. |

## Remember choices, including after restart

`PROJECT journey status` returns the compact chat summary and a `continuation` for the host.
It does not advance anything. On every resume, read it before work. Follow the saved mode,
focus, source scope and authentication evidence; the host still checks current authorization.

After the first sample is shown, persist its exact `focus` from `experience --format json`
with `PROJECT journey select --focus <identity>`. Use structured arguments or proper quoting.
Repeat only when the person chooses another definition or an evidenced successor is shown.
Future experience calls use that saved identity, even after candidate ordering changes. A
missing or ambiguous selection stops the handoff instead of switching metrics. If a proposal
was merged, inspect source lineage and explain the successor before changing selection.
A revised definition retains focus but loses stale check/release claims until rechecked.

| User intent | Local action | Continue in the host |
| --- | --- | --- |
| Business focus | `PROJECT journey domain --answer <answer>` | Save the submitted domain, then ask the missing purpose. |
| Purpose | `PROJECT journey purpose --answer <answer>` | Reflect both answers and select relevant work. |
| Show details | `PROJECT journey details` | Show evidence only. No destination question. |
| Show more | `PROJECT preview --limit 5 --format markdown` | Show candidates without changing focus. |
| Keep local | `PROJECT journey keep-local` | Preserve drafts and stop publication work. |
| Pause | `PROJECT journey pause` | Stop scheduling work; let safe writes settle or cancel the host task safely. |
| Resume | `PROJECT journey resume` | Re-read current state and continue the saved checkpoint. |
| Choose another metric | `PROJECT journey select --focus <identity>` | Show the selected metric before continuing. |
| Change scope | Existing scope procedure | Invalidate stale candidates and receipts; keep missing focus explicit. |

The local choice file is separate from source/check/publication evidence. Pause is intent,
not proof a process was canceled. Inspect any in-flight host task before reporting it stopped.
Keep local never withdraws previously published definitions. Resume never clears a hold,
repairs a connection or authorizes a new publication target. If a chosen target and concrete
save plan are already authorized, do not add another confirmation.

## Recovery and the first real answer

On access failure, retain the visible sample and local work. Follow connection recovery using
the current host's sign-in, then verify a Registry read before continuing. Never reopen a
dismissed snapshot or restart extraction to repair sign-in. On a held definition, show its
missing input and next repair step; published alternatives require an explicit selection.

After confirmed release readback, keep the same metric in the handoff. Suggest only questions
supported by released formulas, dimensions, mandatory filters and the selected data path.
Continue directly if the person already supplied a question. Follow [retrieval](../../../analytics-ask/references/retrieval.md):
fetch fresh released context and execute fresh SQL. A warehouse plan is not an answer.
Report the value, population, source release and any truncation or unmeasured parity. Keep SQL
available in the native conversation. A failed query keeps the selected metric and run for
repair; it never becomes a fabricated success card.

## When the interface appears

Use the existing host surface at each meaningful moment. Do not create a new app shell.

| Moment | Native presentation |
| --- | --- |
| Start | A short opening message before tool work. |
| Start | The flow's checklist in chat and the host's task list when available, before the first helper call. |
| Inspection finishes | Step 1 ticked with its source count, then the business summary and focus choices in the native question. |
| Business focus is answered | A separate purpose question tailored to that focus. Wait for its answer. |
| Both answers are known | One sentence reflecting the goal and immediate action. No extra confirmation. |
| Meaningful work progresses | The full tracker in chat at each step change, with the task list updated from `PROJECT checklist`. |
| Details are requested | SQL and evidence in chat. |
| Input or access is missing | One contextual question or the host's existing sign-in interface. |
| The collection is checked | The context-built celebration and save choice from the presentation flow. |
| Saving is appropriate | Destination and actual create/revise/reuse/held counts. Ask only for missing authorization. |
| Context is released or kept local | The ready block, selected metric, available proof and supported questions; answer an existing question directly. |
| A fresh query finishes | Answer first; native chart or table when useful and supported, then compact provenance. |
| Pause or return | Saved result and next action, with the checklist rebuilt from the saved run, retaining both intent answers and metric selection. |

A question is a real native control only when its tool exists. Do not claim buttons, panels,
charts or live updates that the host does not support. Never pause for a cosmetic render.
