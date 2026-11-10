---
name: describe-verified-sql
description: "M2 : write the business question, name and description for each verified SQL, over the MERGED layer. The set of statements is fixed; only the words are yours."
---

# Describe verified SQL (M2)

Input: `$RUN/m2_prompt.json`. Output: JSON handed to `m2_describe.py --from <file>`, then
`m1_merge.py` applies it.

**Everything published about a statement, you write here.** There is no later stage that
improves it and no fallback that fills a gap : a statement you describe badly stays that way
until somebody re-authors it by hand.

The examples below use invented tables and columns, so that the rules travel to any estate.
**Do not let their neutral wording infect your answers.** They are deliberately generic
because they have to be; your answers must be the opposite, because the estate's own words are
what people type into the search box.

## The one rule

**You describe statements. You never decide which statements exist.**

The set was fixed upstream, by the BI compiler and the parser. An answer that adds a
statement, names one twice or leaves its `question` empty is refused, keyed by `vq_id`. A
statement you leave out is not refused: it is reported as undescribed and asked for again
(see **Working in sittings**), and it is never filled in for you.

This is what makes the layer re-runnable. Asked to *extract* objects from the same statements,
one author produced 41 and another 11 : both defensible, and the layer changed size on every
rebuild. Fixing the set upstream and leaving the prose here means a re-run gives the same
layer with, at most, different wording.

## Why these three fields, and in this order

The registry ranks a weighted full-text index. Four fields, and you write three of them:

| weight | field | what it holds |
|---|---|---|
| **A** highest | `name` | the business alias |
| **B** | `display_name` | **your `question`**, verbatim |
| **C** | `description` | your `description` |
| D lowest | `search_text` | the SQL's own identifiers, generated |

Weight drives the *ranking*; the match is over the whole index. So a field you leave thin is
not a weaker signal, it is vocabulary nobody can reach the statement by. `question` is the
most valuable thing you write : it lands at the highest weight anyone actually types into.

## What the bundle gives you

Each entry carries the `sql`, plus two fields computed from it that you should read **before**
the SQL, because they settle what the statement actually answers:

* **`outer_select`** : computed from the OUTERMOST select, seeing through any `SELECT *`
  wrapper: `returns` (its outputs), `group_by` (its grain), `is_aggregate` (it calls an
  aggregate) and `is_population_summary` (it aggregates with no grouping, so the answer is one
  row about the whole population, not the detail underneath). A `union_branches` count means the
  statement combines that many SELECTs; `returns` names the columns, which the first branch
  sets.
* **`restrictions`** : every filter the statement applies at any level, with `[in a CTE]`
  marked. A restriction inside a CTE narrows the answer exactly as hard as one in the outer
  WHERE, and is the one most often missed.

Also `tables`, `aliases_seen` (what this estate's analysts called the output columns) and
`adoption` (how often it was run).

## Output

```json
{"queries": [
  {"vq_id": "<verbatim from the prompt>",
   "name": "<business alias>",
   "question": "<the business question(s), in one field>",
   "description": "<what it returns, over what population, and the choices a reader would misread>"}
]}
```

`question` is required; `name` and `description` are optional and must be **omitted** when the
statement does not show them. There are no other fields: anything else is ignored, not
published.

---

# Step 1 : work out what the statement answers

Do this before writing a word. Three questions, answered from the **outer** SELECT:

**1. What is one row?** Read `group_by`: its keys ARE the grain. When it is empty and
`is_population_summary` is true, the answer is one row about the whole population, not the
members beneath it.

**2. What does it return?** Every output in `outer_select.returns`. Not the CTEs' outputs.

**3. What is it restricted to?** Every entry in `restrictions`, including the ones in CTEs.

## The CTE trap

A statement's CTEs are workings; the final SELECT is the answer. The CTE is the first thing
you read and usually the most legible part of the query, which is exactly why it gets
described instead:

```sql
WITH per_user AS (
    SELECT user_id, COUNT(*) AS requests, COUNT(DISTINCT feature) AS features_used
    FROM usage_events GROUP BY user_id)     -- looks like per-user detail
SELECT COUNT(*)              AS total_users,
       ROUND(AVG(requests))  AS avg_requests,
       MEDIAN(requests)      AS median_requests   -- the ANSWER is one row about the population
FROM per_user
```

WRONG : *"How heavily does each user engage?"* That is the CTE.
RIGHT : *"What does typical usage look like : how many users, and the average and median
requests each?"* That is the statement.

## The quiet restrictions

A filter buried in a CTE, or written as an offset, is still a filter. Each of these changes
the number, and each gets dropped:

* **a relative age test** : `signup_date <= DATEADD('day', -60, CURRENT_DATE())` is not
  plumbing. It means "signed up at least 60 days ago" and it defines who is eligible;
* **an exclusion list** : a `NOT IN (…)` of internal or test values means the answer covers
  the outside world only;
* **a threshold** : `> 0`, `>= 2`, `HAVING COUNT(*) > 1` each carve the population;
* **a status set** : `status IN ('solved','closed')` means resolved cases, not all of them.

If dropping the clause would change the number it belongs in the description; if it changes
what a person would *call* the thing, it belongs in the question.

---

# Name the estate's things by their names

**The single most valuable words you write are the ones the business already uses**, because
they are the ones a person types. A product name, a funnel stage, a segment, a system, a
programme : if the statement is about it, say it.

Generic paraphrase is the most common way a technically correct description becomes
unfindable. It reads fine and matches nothing:

| written as | should be |
|---|---|
| "how much are they using the product" | "how much are they using *<the product's name>*" |
| "revenue up for renewal" | "*<the term the business uses for it>* up for renewal" |
| "the support system" | "*<the system's name>*" |
| "assistant usage" | "*<what they call the assistant>*" |
| "accounts at an early stage" | "*<the stage's actual name>*" |

Take the names from the tables, the `aliases_seen`, the literal values in `restrictions`, and
the comments in the SQL. A statement filtering `campaign = 'Spring Conference 2026'` is about
the Spring Conference, and the question says so.

The test: **strip your question of every proper noun. Does it still point at this statement?**
If yes, you have written a description of a category, not of this thing.

This applies to `name` and `description` equally. It does NOT license inventing a term : if
the estate has no word for something, plain English is right. What is wrong is replacing a
word it does have.

---

# Step 2 : `question`

**Every business question the statement answers, in one field**, as sentences in sequence,
each ending in a question mark. Not a list, not bullets.

## Write a question, never a restatement of the SELECT list

```
SQL:    SELECT COUNT(DISTINCT c.id) AS eligible_count
        FROM customers c
        LEFT JOIN plan_signoffs s ON s.customer_id = c.id
        WHERE c.is_deleted = FALSE AND c.type = 'Customer' AND c.segment <> 'Growth'

WRONG   "What is eligible_count?"
WRONG   "What is the count distinct of c.id where type = 'Customer'?"
RIGHT   "How many customer accounts are eligible for a plan sign-off?"
```

**Never name a SQL alias, column, table or CTE.** What is refused is an identifier :
anything with an underscore in it. An alias that is an ordinary word is fine: *"How many
accounts does each manager own?"* is right even where the column is called `accounts`. So is
the business's own shorthand, whatever it happens to be : judge by whether someone would say
it aloud in a meeting, not by whether it looks technical.

If the only way you can phrase it is with the column name, you have not worked out what the
statement means. Read the joins and the WHERE again.

## Cover everything it returns : without one question per column

Between them, the questions must account for every output. A column nobody asked about is a
column nobody finds this statement by. But a six-column statement is usually still ONE
question: the six columns are the answer to it. Name what it covers *inside* the question.

```
WRONG   "How is each manager doing?"                (covers nothing in particular)
WRONG   "How many accounts does each manager own?"
        "Which manager has the most renewals?"
        "How many meetings?"                        (one per column : and the first two are
                                                     answered by the same result)
RIGHT   "How is each manager doing on adoption : accounts owned, product active,
         meetings held and the revenue behind them?"
```

Write a **second** question only where the statement answers something a person would ask on a
separate occasion : a different output, not a different wording. A count and a share of that
count are two questions. A count and the ranking of that count are one.

And do not force unrelated things together to save a sentence. *How many accounts are engaged*
and *how much revenue sits behind them* are asked separately; a sentence stapling them with a
dash reads as a column list and answers neither. Merge when the parts are one question in the
business's mind; split when they are not.

## A wide table is described by its DIMENSIONS, not its columns

"Cover everything it returns" is sound at six columns and meaningless at a hundred. A wide
fact table does not have a hundred unrelated outputs : it has a handful of measures crossed
with a handful of cuts, and **the cuts are the answer**.

```
Returns 142 columns: weekly_s1s_created, weekly_fg_new_business_s1s, weekly_mg_expansion_arr,
prior_week_s1s, four_week_avg_expansion_s1s, wow_s1_change_pct, target_weekly_s1s,
pacing_status …

WRONG   "How many S1s did each rep create per week?"
        -> five of the hundred and forty-two. The question describes a counter.
RIGHT   "How is each rep pacing on S1s and pipeline week by week : split by field-generated
         versus marketing and by new business versus expansion, against prior week, a
         four-week average and their target?"
```

Read the column names for their **repeating parts**. `weekly_ / prior_week_ / four_week_avg_`
is a time comparison. `fg_ / mg_ / unknown_source_` is a source split. `new_business_ /
expansion_ / excluded_` is a type split. `_s1s / _arr` are the two measures. Name those six
things and you have covered all 142; list columns and you will cover none.

A `target` or a `pacing_status` column is never decoration : it means the statement exists to
judge performance against a number, and that is what it is FOR.

## The statement with no business framing

Some statements are plumbing: a row count, a value list, a lookup by id, a dump of every name
into an array. They still get a question, and the temptation is to reach for warehouse words
because there is no business story to tell.

Do not. "Rows", "table", "fact", "record set", "payload" are the vocabulary of the system, not
of the person. Ask what the thing IS to the business and count that:

```
WRONG   "How many rows are in the account engagement status fact?"
RIGHT   "How many account engagement records do we hold?"

WRONG   "Account Name and Type Lists"          (names the payload, not the question)
RIGHT   "All Account Names and Types"
```

A lookup is still a question : *"Which account matches this name?"* A value list is still a
question : *"What sales segments do we use?"* Plumbing does not license jargon; it just means
the question is short.

## Carry the filters as business language

A restriction that changes what the number means has to reach the reader, or they will take
the figure for something wider than it is. In the business's words, never the predicate:

| the statement says | the question says |
|---|---|
| `type = 'Customer'` | "customer accounts" |
| `segment <> 'Growth'` | "outside the Growth segment" |
| `event_time >= DATEADD('day', -180, …)` | "in the last six months" |
| `BETWEEN '2026-01-01' AND '2026-03-31'` | "in Q1 2026" |
| `is_deleted = FALSE` | *nothing : plumbing, not meaning* |

The test is whether a reader would reach a different conclusion without it. Soft-delete guards
are hygiene and stay out. A population exclusion, a time window, a status or a segment changes
the answer and belongs in.

Where a restriction is real but too fine for the question : the exact keyword list a match
uses, the tie-break that picks one row per entity : put it in `description`. The question
carries what changes the meaning; the description carries what changes the reading.

## Say what it is scoped TO

A statement restricted to one campaign, event, tag, region or cohort is **about that thing**. A
generic question over a scoped statement is not vague, it is wrong: it claims a population the
statement does not have, and it collides with every other statement doing the same job for a
different scope.

```
Statement restricts to: tags CONTAINS 'spring conference 2026'

WRONG   "Which target accounts have been covered?"
        -> reads as every target account there is. Any number of statements in the layer
           could carry the same sentence.
RIGHT   "For each account tagged for the Spring Conference, how many contacts were
         approached?"
```

The test: **could this sentence belong to a different statement in the layer?** If yes, it is
not describing this one. Estates repeat the same analysis per event, per quarter and per
segment, so the differentiator usually IS the scope.

Where a statement cross-references a second scope : the population is one thing, the
comparison another : say both. Describing only the first restriction is the same mistake as
describing the first CTE.

## Each question is complete on its own

It is read whole in a search result with no SQL beside it, so it carries the population, and
the period if the statement pins one.

```
WRONG   "How many accounts?"                    (of what? when?)
WRONG   "How many accounts signed off?"         (the statement is customers only)
RIGHT   "How many customer accounts signed off a plan in Q1 2026?"
```

Complete is not padded. Carry what a person would say; leave the rest to `description`.

## Shape follows the statement

* one number → **"how many / how much"**;
* grouped → the breakdown *and* the extreme, where people ask for both;
* a ratio → a **percentage** question saying what of what : *"what share of customers…"*,
  never *"what is the ratio"*;
* rows rather than an aggregate → **"which / who / what"**;
* a data-quality test rather than an analysis : it counts failures or returns rows that
  violate a rule → say it is a test: *"Is the order id ever missing?"* Dressing it up as a
  business question misleads whoever finds it.

## Structures that change the question

Structural, not domain-specific, and each one changes what the statement IS:

**Parameterised** (`?`, `:1`, a bind variable). The question is asked of *one* of something,
and says so: *"Which accounts is a given manager assigned to?"* Never invent a value for the
parameter : the parameter is the point.

**Hardcoded to one entity** (a literal id, email or domain in the WHERE). Ask the same shape of
question, and say in `description` that the subject is fixed in the statement rather than
supplied. A reader who assumes it is parameterised will reuse it wrongly.

**Top-N per group** (`ROW_NUMBER() … QUALIFY rn <= 3`, or a rank filter). "The top three per
account" is part of the question: *"Who are the leading users at each account?"* : not "who
are the users".

**Deduplication** (`ROW_NUMBER() … = 1`, `QUALIFY`, a MAX over a key). Say which row survives
and on what order : latest, highest, first : in `description`. Two readers will otherwise
assume different ones.

**UNION of branches.** The statement covers several populations combined, and the question
names the combination: *"How much usage is on production instances versus trials?"* Describing
one branch and ignoring the other is the CTE mistake in another costume.

**Aggregated into a list** (`LISTAGG`, `ARRAY_AGG`). The answer is one row holding a
collection, not one row per member. Say so in `description` : *"returned as one delimited list
rather than one row per account"* : or a reader will expect rows and get a string.

**Pivoted into conditional columns** (a run of `CASE WHEN … THEN 1`). Each column is a category
and the set of categories is part of the answer. Name what the categories ARE rather than
listing them one per question.

**A window that ranks or compares periods** (`LAG`, a previous-period rank). The comparison is
the question: *"how has their ranking moved against the previous period?"*

## What never earns a place

* **The ranking of a breakdown.** A statement grouped by region answers *"how many per
  region?"*; *"which region has the most?"* is how you READ that result.
* **A filter restated as its own question.** *"How many outside the Growth segment…"* is the
  headline again with a filter bolted on. The filter belongs inside the question.
* **A synonym pass.** Re-asking the same thing in other words adds nothing : the index already
  matches on the words you used.

Each of those dilutes the field and pushes the real question further from the reader.

---

# Step 3 : `name`

**The business alias: what a person calls this thing, in their words.** Title case, spaces,
ordinary English.

**A name is prose, never an identifier.** `plan_signoff_rate_q1_2026` is a filename. And
title-casing a table name is the same mistake wearing capitals: a table called
`rpt_mgr_leaning_in` holds what a person would call the *Manager Leaning-In Report*. Write what
the table IS : this applies to data-quality tests too: *Manager Leaning-In Report : Rep Id
Completeness*, never *Rpt Mgr Leaning In Rep Id Not-Null Test*.

This is weight A, the best position in the index. Spending it on characters nobody types is
the most expensive mistake available here.

**Every name must be unique across the layer.** A repeat is suffixed with a hex fragment :
`Account Name Check-90a1a485` : putting a machine artefact at the highest weight. Two
statements doing the same job on different tables are two names, distinguished by whatever
actually differs: the source, the scope, the subject.

`aliases_seen` shows what this estate's analysts called the output columns, and the table names
carry the rest. Take the vocabulary from there : whatever it is : and write it the way it is
spoken, not the way it is coded.

---

# Step 4 : `description`

One or two sentences: what it returns, over what population.

Then the non-obvious choices a reader would misread : a dedup, a keyword match, a hardcoded
window, a tie-break, a LEFT JOIN that keeps non-matching rows. In the same sentences; there is
no separate field for them.

```
"Eligible customer accounts against those with a sign-off dated in Q1 2026. Any one of the
 three sign-off roles qualifies an account, and the quarter is fixed in the statement rather
 than relative to the run date."
```

---

# Closed world

Assert only what the statement demonstrates. If it excludes nothing, it excludes nothing : do
not write "presumably excludes test accounts". An invented restriction is worse than a missing
one, because a consumer will apply it.

You are **question-blind**: you get analyst SQL, never a benchmark question and never an
expected answer. Do not go looking for either.

# Working in sittings

`--vq-id <id> …` bundles named statements; `--only-provisional` bundles those with no
description yet. Answer a domain at a time and pass every file to `--from` : what is missing
is reported and asked for again.

**Never pad.** An undescribed statement keeps the estate's own alias and is flagged, which is
correct. A guessed description is not.
