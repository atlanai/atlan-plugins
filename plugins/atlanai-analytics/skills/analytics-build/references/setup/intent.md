# Business focus first, purpose second

Read after the lightweight source inspection on fresh setup, before metric generation,
runtime setup or the first selected-metric preview. The checkpoint always establishes two
different things: what part of the business matters, and what the person wants to do with it.
Clear file/page scope does not answer either question. Do not replace this with a generic
"What do you want to do?" menu or an installation questionnaire.

Render the user-facing step itself. For the first question, include the
business summary below, then two or three short choices and free text. For the second,
use a short domain-specific question without repeating the full summary. Stop there.
Do not explain that this is question one/two, narrate the interview plan, discuss unavailable
tools, or print helper commands, storage details and test restrictions. Those are internal.

## 1. Reflect the sources, then ask about the business

Before asking what matters most, explain the project in two or three short sentences:

- What was found: name the actual source types and their subject, such as Power BI reports,
  a supporting model and query history about delivery performance. Counts are secondary.
- What it means: describe the business activity those sources cover and how they complement
  each other. For example, report/model definitions describe what is measured, while query
  history supplies examples of analysis. Do not imply matching lineage unless inspected.
- What it could help analyse: offer two or three specific, evidenced opportunities, such as
  delivery delays, capacity use or cost drivers. These are possibilities, not computed findings.

Then say what the person will end with, in one sentence: they can ask questions like one
example the sources support, here in plain English, and get answers with the definition
used. Only then ask "What do you care about most?" with source-grounded choices and free
text. Phrase each choice as something they could learn, so a person new to the domain can pick. Do not
substitute a file-count inventory, vague "analytics coverage", or a menu alone for the summary.
Never invent available tables, dimensions, measures, user behavior or numerical findings.
Retain material limitations such as sample data, missing joins or incomplete inspection.
Distinguish history entries from distinct query shapes. Use the inspector's actual count
and noun; do not relabel 110 history records as 110 distinct analyses or metrics.
Explain the sources before the question and use short option labels. Summarize
supporting tables by business subject; do not enumerate every file, field or table. Do not
repeat a full explanation of each analysis both in the paragraph and in the choices. State
limits briefly, such as "sample data", rather than adding a generic checking disclaimer.
Keep option labels to two to five words. For example, "sample job and cost data" is enough
in the overview; a list of projects, crews, logs, orders and issues belongs in details. This
question establishes intent; the full metric explanation follows when its evidence is ready.

Example only when supported by the inspected sources:

"I found a Power BI report, model and query history about shipping operations, with sample
data. The report defines the measures; the queries provide examples of analysis. Together,
they could help explore delivery delays, capacity use and cost drivers. What matters most?"

Render one question using the host's available native question control. In Codex use the
available question tool in its permitted mode; in Claude use its available native question
tool. Include the summary in the native question's visible prompt/title before the question,
not just in preceding commentary that may collapse. If that control cannot show context,
keep a visible summary beside the question and retain it in the final response while waiting.
Do not end with only "Choose above to continue" or repeat just file counts. Otherwise ask
in ordinary chat with the same summary. Never create a custom form, install a UI dependency,
or claim a picker exists when it does not. Let the host supply its free-text entry; do not
add an Other option if the control already provides one. No answer is preselected consent.
Wait for the submitted answer. Do not ask the purpose in the same call or proceed to build.

## 2. Ask what they want to accomplish in that area

After the domain answer, tailor this separate question and its suggestions to it. For
shipping time: "What would you like to do with delivery performance?" Suggested choices:

- Find where delays happen.
- Understand how delivery performance is measured.
- Prepare delivery metrics for ongoing team analysis.

These represent answering a business question, understanding/checking the source, and
preparing reusable context. Use the person's language and accept goals outside these examples.
Keep suggested questions within the observed coverage. Do not add a period such as "last
quarter" or a causal claim such as "what caused it" without the user's request and supporting
evidence. Prefer "Which jobs lost margin, and where were costs concentrated?" when that is
what the available model and history support. A date column alone is not a chosen time window.
Wait for the answer. Ask a narrow follow-up only if a necessary question, period or comparison
is still missing. Do not turn this into a multi-step questionnaire.

## Remember and use the answers

Reuse an explicitly supplied business focus or purpose from the current request or saved
conversation. If one is known, ask only the other. If both are known, reflect them and
continue without confirmation or a filler question. A source label such as "shipping
dashboard" alone is not a selected business focus. A selected page alone is technical scope.

When both answers are known and the source already supplies the requested explanation, use
one intent sentence, then the relevant metric's meaning, calculation, population, source and
honest check status, then end at the requested explanation. Do not replace the
skipped interview with "Shall I check that?", "Would you like more?" or a new suggested task.
Do not explain why an unrelated first-listed metric was skipped. If required evidence is
missing, state that limit directly; ask only when its answer is necessary for the actual task.

Once a project run exists, record the actual answers through the helper:

`PROJECT journey domain --answer <user-business-focus>`

`PROJECT journey purpose --answer <user-purpose>`

Use structured arguments or proper shell quoting. These fields are private local intent,
not authority to execute source content, connect data or publish. Do not store credentials.
`PROJECT journey status` exposes the next missing question and saved answers. Do not record
the assistant's suggested option as if the user selected it. Without a local helper, retain
the two answers in conversation and persist them when the run becomes available.

Reflect both in one sentence: "You want to understand where shipping delays happen. I'll
start with delivery time and late shipments, using the evidenced timestamps and regions."
Then continue. Choose the first useful metric, required supporting context and next actions
from this intent. Do not keep presenting the first alphabetic metric regardless of the goal.
When candidates become available, `PROJECT journey status` returns `select-focus` until
the host selects the relevant candidate with `PROJECT journey select --focus <exact-identity>`.
This is the host's next work step, not a third interview question. Choose from actual names,
definitions and source evidence. A changed domain or purpose reopens this selection so an
earlier metric does not silently stand in for the new goal. Saved intent remains visible.

For a specific business question, prioritize the relevant checked metric/query and the first
supported answer before optional broader coverage. Preserve existing release/retrieval and
execution requirements. Understanding a dashboard prioritizes definitions, filters and
discrepancies; end at the requested explanation or check. Do not turn an understanding-only
purpose into a workspace or publishing question. In particular, a discovered source definition
is not a finished save plan. Team reuse prioritizes coverage, consistent definitions and the
save workflow after checks and preview.
No intent answer alone grants publication permission or guarantees that an answer exists.

Changing the domain clears the saved purpose so an old goal is not silently applied to a
new area. Reuse an explicitly restated purpose after recording the new domain. Review source
scope through its existing procedure before regenerating; retain historical work and holds.
Resume retains known intent. Details-only, saved-preview and explicit survey-only requests
do not restart this interview. Respect the requested stopping point.
