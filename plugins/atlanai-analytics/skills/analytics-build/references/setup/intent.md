# The use-case interview: ask first, then look

The interview belongs to `analytics-build`. `source-survey` reads and reports and never asks
the person anything. With no input, the interview comes before any survey: what the person
is trying to do decides what to look for, so nothing is read until they have said it.
[The entry](entry.md) says when to ask: with no input, yes; when a source or a subject was
named, the request already answers it.

Ask about the task, not about the data: a person knows what they are trying to get done long
before they can pick a business area from a list or name a purpose. Everything else is
assumed, said out loud, and corrected later if wrong.

## 1. Ask what they are trying to do, before reading anything

The first thing the person sees is the opening and one question. The opening is the
question's own visible text, because a block printed before a question is lost:

"Atlan Analytics turns your dashboards and the queries your team already runs into context:
what your numbers mean and how they are worked out. With that context, you can ask your data
questions in plain English and see what each answer is based on.

First, let me understand what you're trying to do."

Then "What are you trying to do?" with task-shaped choices and free text:

| Option | Description |
| --- | --- |
| Answer a question | Type the question. You get the number and the rule behind it |
| Understand a number that changed | Name the number. You get what moved it, from the definitions your team already uses |
| Build a dashboard or report | A page your team can open, with every number traced to its rule |
| Give my team and agents shared metrics | One agreed rule per number, saved where every chat and tool finds it |

The free-text entry also takes what they have: a dashboard link, a file, a subject. Never
more than four options, never a list of business areas or report names, and never a second
question about purpose: the choice is the purpose. No survey, file listing, connector check
or Atlan call runs before the answer.

Render it with the host's native question tool. With no question tool, print the opening
and the question in ordinary chat and end the turn. Nothing is preselected.

## 2. Survey for what they said

Now run `source-survey` `setup`, with the answer as what to look for: the subject's words
decide which reports, tables and published metrics matter. When the answer names a subject
("pipeline", "churn by plan"), run `domain` for it as well. Say what is happening with the
survey's Doing and Why lines, then its result in one line of products and counts:

"Found: Power BI (2 reports), Snowflake, 90 days of query history."

No table, no readiness notes, no file names. A source that cannot be built from is left out.
Demo data is never listed.

From the answer and the survey, settle these yourself:

- **The subject.** From the words of the answer, matched against the reports' pages and
  measures and the query history. A typed question names it.
- **Which source.** The buildable source that holds the subject, reports before query
  history before table definitions.
- **Period, audience, grain.** The narrowest sensible reading: the latest complete period,
  the team that owns the report, the report's own grain. These are assumptions, stated in the
  reply and again as caveats on the first answer; never questions.

**One contextual question, only when the survey leaves a real choice.** Ask it from what was
found, with the found line as its text:

| The survey shows | Ask |
| --- | --- |
| Two or more buildable sources could hold what they said, or they named a task and no subject | "Which of these is it about?", one option per source, each named by product and subject ("Power BI: job sites and margins") |
| Nothing found holds the subject | "I found no source for `<subject>`. Where does it live?", with the found sources and free text |
| One buildable source holds it | Nothing: go to the confirmation |

Never a follow-up about period, audience, layout or which metrics.

## 2a. Confirm before building

Once the task, the subject and the source are known, check the source and show what will be
built, then ask once before any rule is written. This is one message, as the confirmation
question's own text:

1. **The source check**, one sentence: what will be read, in order, and that it can be
   reached. "I'll build from the job-site report (Power BI), 90 days of its query history
   and the columns of its 4 tables, all reachable."
2. **The inventory**, from [names](names.md): the metrics the context will hold, by page for
   a report, and two or three questions it will answer.
3. **The question**: "Build this?"

| Option | Description |
| --- | --- |
| Build it (recommended) | All N metrics from the whole report, checked on the warehouse. About how long |
| Change something | Say what: a different source, without query history, fewer metrics |

Nothing is generated, installed or pulled from query history before the person confirms.
This confirmation is asked in every entry, including when a source or a subject was named.

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

A report is still built whole unless the person changed that at the confirmation; the task
decides what is shown first and where the flow ends, not how much is built. No answer grants publication permission or
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
