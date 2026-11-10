---
name: analytics-start
description: Route an analytics request to the right skill, one of demo, build, ask, survey or metrics. Use when someone says start, set up analytics or help with my data without naming which. Not for a clear data question (analytics-ask).
user-invocable: false
---

# Route an analytics request

An index. It reads what the person wants, loads one skill, and does no other work: no
survey, no file search, no helper call, no sign-in. Do not announce the routing; the loaded
skill's first reply is the first thing the person reads.

## Route

Take the first row that fits.

| Signal | Load |
| --- | --- |
| An unfinished run in this folder | The skill that owns it: a demo run is `analytics-demo`'s, any other is `analytics-build`'s |
| A number, trend, breakdown, "why did it change" or a follow-up to an answer | `analytics-ask` |
| "Try", "demo", "show me how it works", or sample data | `analytics-demo` |
| "What is in this source", "can I use this", "is it ready", with no request to build | `analytics-survey` |
| "Create, change, fix, propose or publish this metric", naming a metric | `analytics-metrics` |
| A dashboard link, folder, BI export or warehouse to set up, or a business problem about their own data | `analytics-build` |
| Workspace access, or `metrics`, `connect`, `propose` or `dashboard` as a first word | `analytics-build`, which keeps those routes |
| First use, nothing supplied | `analytics-demo`, whose first question also offers own data |

In Claude Code load a skill with the Skill tool as `atlanai-analytics:<name>`; elsewhere,
read its `SKILL.md` beside this one and follow it. Pass the person's words through
unchanged, with any source, problem or question they gave.

## When it is unclear

Ask one native question, and nothing else in that message:

"What would you like to do?"

| Option | Description |
| --- | --- |
| Try a demo | Watch sample dashboards and queries become context you can question. About 15 minutes. |
| Build from my data | Turn a dashboard, folder or BI export of yours into metrics. |
| Ask a question | Get a number from metrics already published in Atlan. |

## Hand-offs between skills

Each skill hands on in one direction and carries only what is listed:

| From | To | Carries |
| --- | --- | --- |
| `analytics-demo` | `analytics-build` | Nothing. A demo run never becomes a build. |
| `analytics-build` | `analytics-ask` | The run, so answers read its context |
| `analytics-ask` | `analytics-build` | The unanswered question, as the problem |
| `analytics-build`, when the survey finds no readable source | `analytics-demo` | Nothing |

The Registry marketplace bundles these skills in `atlanai`. Use its installed namespace
instead of `atlanai-analytics:`; do not install this add-on beside that bundle.
