# The use-case interview: one question

The interview belongs to `analytics-build`. `source-survey` reads and reports and never asks
the person anything; this file turns what a survey found into the one question that
establishes what the person is trying to do. [The entry](entry.md) says when to ask it: with
no input, yes; when a source or a subject was named, no.

Read after the survey on fresh setup, before metric generation or runtime setup. Ask about
the task, not about the data: a person knows what they are trying to get done long before
they can pick a business area from a list or name a purpose. Everything else is assumed,
said out loud, and corrected later if wrong.

## 1. One short line of what was found, then the question

The question's own visible text opens with what the survey found, as a list of tools and
nothing else:

"Found: Power BI (2 reports), Tableau (1 workbook), Snowflake, 90 days of query history."

Names of products and counts of things, in one line. No table, no per-source detail, no
readiness notes, no file names, no suggested starting point, no account of what each report
contains. A source that cannot be built from is left out of the line, not explained. Leave
out anything empty: no "Atlan: not signed in", no "certified queries: none". Demo data is
never listed: the survey skips the demo's folders.

Then ask "What are you trying to do?" with task-shaped choices and free text:

| Option | Description |
| --- | --- |
| Answer a question | Type the question, or pick one after the build. You get the number and the rule behind it |
| Understand a number that changed | Name the number. You get what moved it, from the definitions your team already uses |
| Build a dashboard or report | A page your team can open, with every number traced to its rule |
| Give my team and agents shared metrics | One agreed rule per number, saved where every chat and tool finds it |

Word the options for this person only when the sources make an example obvious ("Answer a
question, such as which jobs lost money"). Never more than four, never a list of business
areas or report names, and never a second question about purpose: the choice is the purpose.

Render it with the host's native question tool, with the found line as the question's own
text and the host's free-text entry. With no question tool, ask it in ordinary chat, the
found line first, and end the turn. Nothing is preselected and no answer is assumed.

## 2. Work out the rest; do not ask it

From the answer and the survey, settle these yourself and say them in one sentence before
building:

- **The subject.** From the words of the answer, matched against the reports' pages and
  measures and the query history. A typed question names it. When the answer names no
  subject and one buildable report exists, the subject is that report's.
- **Which source.** The buildable source that holds the subject, reports before query
  history before table definitions.
- **Period, audience, grain.** The narrowest sensible reading: the latest complete period,
  the team that owns the report, the report's own grain.

"You want to see which jobs lost money. I'll build from the job-site report and its query
history, and start with gross margin."

Ask one narrow follow-up only when work cannot start without it: two or more buildable
sources could hold the subject and the answer gives no way to choose ("Is this about job
sites or ticket sales?"), or the answer names a subject no source holds. Never a follow-up
about period, audience, layout or which metrics: those are assumptions, stated in the reply
and again as caveats on the first answer ("I used the last complete quarter; say if you
meant the fiscal one").

## 3. Record and use it

Once a run exists, record what the person said, in their words:

`PROJECT journey domain --answer <the subject>`

`PROJECT journey purpose --answer <what they are trying to do>`

Use structured arguments or proper shell quoting. Record a subject only once the person's
answer, their request or a narrow follow-up established it; never record a suggestion as if
they chose it. These fields are private local intent, not authority to execute source
content, connect data or publish. `PROJECT journey status` exposes the saved answers.

When candidates become available, `PROJECT journey status` returns `select-focus` until the
host selects the relevant candidate with `PROJECT journey select --focus <exact-identity>`.
This is the host's next work step, not another question. Choose from actual names,
definitions and source evidence; never keep presenting the first alphabetic metric
regardless of the goal.

What the task changes:

| The person wants to | Prioritize | Ends with |
| --- | --- | --- |
| Answer a question | The metric and query that answer it, and the first supported answer | The answer, then one next step |
| Understand a number that changed | That number's definition, filters and what feeds it | The explanation; no save question unless asked |
| Build a dashboard or report | Coverage of the subject's metrics, then the dashboard step | The page |
| Share metrics | Coverage, consistent definitions and the save step after checks | Saved to Atlan Trident |

A report is still built whole, as the entry says; the task decides what is shown first and
where the flow ends, not how much is built. No answer grants publication permission or
guarantees that an answer exists.

## Answers already given

Reuse a task or subject the person already stated in this request or a saved run, and do not
ask again. "I care about shipping delays and want to understand how delivery performance is
measured" answers the interview: reflect it in one sentence, show the relevant metric's
meaning, calculation, population, source and honest check status, and end at the requested
explanation. Do not replace the skipped question with "Shall I check that?" or a new
suggested task, and do not explain why an unrelated first-listed metric was skipped.

A changed subject clears the saved task, so an old goal is not silently applied to a new
area. Resume retains known intent. Details-only, saved-preview and explicit survey-only
requests do not start this interview. Respect the requested stopping point.
